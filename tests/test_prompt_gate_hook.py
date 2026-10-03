"""Tests for the UserPromptSubmit entry point of tools/prompt_gate.py (spec Task 4).

The hook is exercised as Claude Code runs it: a subprocess fed the hook JSON on stdin.
Only the keys the gate uses are asserted (the CLI adds fields between versions).
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.test_prompt_gate import CPF, _pontuar_cpf

RAIZ = Path(__file__).resolve().parents[1]
SCRIPT = RAIZ / "tools" / "prompt_gate.py"
SETTINGS = RAIZ / ".claude" / "settings.json"

VAGO = "melhora o código do projeto inteiro e deixa tudo mais robusto"
COMPLETO = "Em worker.py, trate o timeout do blpop; pronto quando o pytest passar"


@pytest.fixture
def estado(tmp_path):
    return tmp_path / "state"


def _rodar(
    prompt, estado, *, sessao="s1", env=None, cwd=None, stdin=None, script=SCRIPT
):
    entrada = stdin
    if entrada is None:
        entrada = json.dumps(
            {
                "prompt": prompt,
                "session_id": sessao,
                "cwd": str(RAIZ),
                "hook_event_name": "UserPromptSubmit",
            }
        )
    ambiente = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(estado.parent),
        "XDG_STATE_HOME": str(estado),
        "CLAUDE_PROJECT_DIR": str(RAIZ),
    }
    ambiente.update(env or {})
    return subprocess.run(
        [sys.executable, str(script)],
        input=entrada,
        capture_output=True,
        text=True,
        env=ambiente,
        cwd=cwd or RAIZ,
        timeout=30,
    )


def _saida_json(proc):
    return json.loads(proc.stdout) if proc.stdout.strip() else None


# --- PII: fail-closed -----------------------------------------------------


@pytest.mark.parametrize("valor", [CPF, _pontuar_cpf(CPF)])
def test_cpf_bloqueia_com_exit_2_sem_ecoar_o_valor(estado, valor):
    p = _rodar(f"use {valor} no teste do worker.py", estado)
    assert p.returncode == 2
    assert "pii_cpf" in p.stderr
    digitos = "".join(c for c in valor if c.isdigit())
    assert digitos not in p.stderr and valor not in p.stderr
    assert p.stdout == ""


def test_kill_switch_libera_ate_pii(estado):
    p = _rodar(f"consulta {CPF}", estado, env={"PROMPT_GATE_DISABLE": "1"})
    assert p.returncode == 0 and p.stdout == ""


@pytest.mark.parametrize("bruto", ["isto não é json", "{}", '{"prompt": 42}', ""])
def test_stdin_invalido_falha_fechado(estado, bruto):
    p = _rodar(None, estado, stdin=bruto)
    assert p.returncode == 2
    assert "PROMPT_GATE_DISABLE=1" in p.stderr


def test_import_quebrado_falha_fechado(estado, tmp_path):
    # Cópia isolada do script, sem validate_br_pii ao lado nem pacote tools/.
    isolado = tmp_path / "isolado"
    isolado.mkdir()
    shutil.copy(SCRIPT, isolado / "prompt_gate.py")
    p = _rodar(COMPLETO, estado, script=isolado / "prompt_gate.py", cwd=tmp_path)
    assert p.returncode == 2
    assert "PROMPT_GATE_DISABLE=1" in p.stderr


def test_pii_decidida_sem_git_no_path(estado):
    sem_git = {"PATH": str(Path(sys.executable).parent / "nao-existe")}
    assert _rodar(f"consulta {CPF} agora", estado, env=sem_git).returncode == 2
    # sem git o snapshot falha, mas a qualidade continua (fail-open) e não quebra
    p = _rodar(VAGO, estado, env=sem_git)
    assert p.returncode == 0
    assert _saida_json(p) is not None


# --- qualidade: advisory --------------------------------------------------


def test_prompt_vago_gera_aviso_com_id_e_comando_label(estado):
    p = _rodar(VAGO, estado)
    assert p.returncode == 0
    saida = _saida_json(p)
    ctx = saida["hookSpecificOutput"]
    assert ctx["hookEventName"] == "UserPromptSubmit"
    assert "alvo_ou_sintoma" in ctx["additionalContext"]
    msg = saida["systemMessage"]
    assert "--label" in msg
    rotulo = msg.split("[", 1)[1].split("]", 1)[0]
    assert len(rotulo) == 8 and f"--label {rotulo}" in msg


def test_prompt_completo_nao_gera_saida(estado):
    p = _rodar(COMPLETO, estado)
    assert p.returncode == 0 and p.stdout == ""


def test_so_o_primeiro_prompt_avaliado_e_checado(estado):
    assert _saida_json(_rodar(VAGO, estado)) is not None
    segundo = _rodar(VAGO, estado)
    assert segundo.returncode == 0 and segundo.stdout == ""
    # outra sessão tem a própria vaga
    assert _saida_json(_rodar(VAGO, estado, sessao="s2")) is not None


def test_prompt_isento_nao_consome_a_vaga(estado):
    for isento in ("/refine-prompt melhora o worker", "por que o worker trava?", "sim"):
        assert _rodar(isento, estado).stdout == ""
    assert _saida_json(_rodar(VAGO, estado)) is not None


def test_prompt_completo_consome_a_vaga(estado):
    _rodar(COMPLETO, estado)
    assert (estado / "pje-prompt-gate" / "s1" / "avaliado").exists()
    assert _rodar(VAGO, estado).stdout == ""


def test_block_rules_promove_regra_a_bloqueio(estado):
    p = _rodar(VAGO, estado, env={"PROMPT_GATE_BLOCK_RULES": "possui_dod, outra"})
    assert p.returncode == 2
    assert "possui_dod" in p.stderr and "!!" in p.stderr


def test_roda_de_outro_cwd_usando_claude_project_dir(estado, tmp_path):
    p = _rodar(
        "ajuste worker.py para logar o jobId em todas as fases", estado, cwd=tmp_path
    )
    assert p.returncode == 0
    regras = _saida_json(p)["hookSpecificOutput"]["additionalContext"]
    assert "possui_dod" in regras and "alvo_ou_sintoma" not in regras


def test_session_id_hostil_nao_escapa_do_diretorio_de_estado(estado):
    _rodar(VAGO, estado, sessao="../../fora")
    base = estado / "pje-prompt-gate"
    sessoes = [d.name for d in base.iterdir() if d.is_dir()]
    assert sessoes == ["fora"]  # só o nome, sem "../"
    assert not (estado.parent.parent / "fora").exists()
    assert all(p.is_relative_to(estado) for p in estado.rglob("*"))


# --- snapshot do turno (insumo do Stop, Task 6) ---------------------------


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_snapshot_registra_head_e_py_sujos_e_nao_rastreados(estado, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("x = 1\n")
    (repo / "notas.md").write_text("n\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    (repo / "a.py").write_text("x = 2\n")
    (repo / "novo.py").write_text("y = 1\n")
    _rodar(COMPLETO, estado, env={"CLAUDE_PROJECT_DIR": str(repo)})
    snap = json.loads((estado / "pje-prompt-gate" / "s1" / "turno.json").read_text())
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    assert snap["head"] == head
    assert set(snap["arquivos"]) == {"a.py", "novo.py"}
    assert all(len(h) == 64 for h in snap["arquivos"].values())


def test_estados_com_mais_de_30_dias_sao_apagados(estado):
    velho = estado / "pje-prompt-gate" / "sessao-velha"
    velho.mkdir(parents=True)
    antigo = time.time() - 31 * 86400
    os.utime(velho, (antigo, antigo))
    _rodar(COMPLETO, estado)
    assert not velho.exists()
    assert (estado / "pje-prompt-gate" / "s1").exists()


# --- .claude/settings.json ------------------------------------------------


def _config():
    return json.loads(SETTINGS.read_text("utf-8"))


def _comando_user_prompt_submit():
    blocos = _config()["hooks"]["UserPromptSubmit"]
    comandos = [h for b in blocos for h in b["hooks"] if h["type"] == "command"]
    assert len(comandos) == 1
    return comandos[0]


def test_settings_registra_o_hook_com_caminho_absoluto_e_timeout():
    hook = _comando_user_prompt_submit()
    assert "$CLAUDE_PROJECT_DIR" in hook["command"]
    assert hook["timeout"] >= 10


def test_settings_nunca_fixa_kill_switch_nem_block_rules():
    env = _config().get("env", {})
    assert "PROMPT_GATE_DISABLE" not in env
    assert "PROMPT_GATE_BLOCK_RULES" not in env


def test_settings_nao_registra_hook_de_prompt_llm():
    # D1=B: hook type "prompt" não tem modo advisory (premortem F2).
    for blocos in _config()["hooks"].values():
        for b in blocos:
            assert all(h["type"] == "command" for h in b["hooks"])


def test_comando_do_settings_executa_e_bloqueia_cpf(estado, tmp_path):
    hook = _comando_user_prompt_submit()
    ambiente = {
        **os.environ,
        **_config().get("env", {}),
        "CLAUDE_PROJECT_DIR": str(RAIZ),
        "XDG_STATE_HOME": str(estado),
    }
    ambiente.pop("PROMPT_GATE_DISABLE", None)
    entrada = json.dumps({"prompt": f"consulta {CPF} agora", "session_id": "s9"})
    p = subprocess.run(
        ["bash", "-c", hook["command"]],
        input=entrada,
        capture_output=True,
        text=True,
        env=ambiente,
        cwd=tmp_path,
        timeout=30,
    )
    assert p.returncode == 2, p.stderr


def test_settings_registra_o_stop_hook_com_timeout_maior_que_o_do_pytest():
    blocos = _config()["hooks"]["Stop"]
    (hook,) = [h for b in blocos for h in b["hooks"]]
    assert hook["type"] == "command"
    assert "$CLAUDE_PROJECT_DIR" in hook["command"] and "--stop" in hook["command"]
    assert hook["timeout"] >= 150  # pytest interno tem 120 s
