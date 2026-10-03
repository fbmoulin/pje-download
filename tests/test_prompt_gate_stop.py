"""Tests for the Stop hook of tools/prompt_gate.py (spec Task 6).

Each test builds a throwaway git repo, takes the per-prompt snapshot through the real
UserPromptSubmit entry point, changes files "during the turn", then runs --stop.
ruff is replaced by a fake (PROMPT_GATE_RUFF) so the tests need neither uvx nor network.
"""

import json
import os
import stat
import subprocess
import sys

import pytest

from tests.test_prompt_gate_hook import SCRIPT

COMPLETO = "Em a.py, ajuste o valor; pronto quando o pytest passar"


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def ambiente(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("X = 1\n")
    (repo / "b.py").write_text("Y = 1\n")
    (repo / "tests" / "__init__.py").write_text("")
    (repo / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")

    log = tmp_path / "ruff.log"
    fake = tmp_path / "fake-ruff"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        'echo "${FAKE_RUFF_OUT:-All checks passed!}"\n'
        'exit "${FAKE_RUFF_RC:-0}"\n'
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "CLAUDE_PROJECT_DIR": str(repo),
        "PROMPT_GATE_RUFF": str(fake),
        "PROMPT_GATE_PYTHON": sys.executable,
    }
    return {"repo": repo, "env": env, "log": log}


def _prompt(amb, sessao="s1"):
    entrada = json.dumps({"prompt": COMPLETO, "session_id": sessao})
    p = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=entrada,
        capture_output=True,
        text=True,
        env=amb["env"],
        timeout=30,
    )
    assert p.returncode == 0, p.stderr


def _stop(amb, sessao="s1", ativo=False, env=None):
    entrada = json.dumps({"session_id": sessao, "stop_hook_active": ativo})
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "--stop"],
        input=entrada,
        capture_output=True,
        text=True,
        env={**amb["env"], **(env or {})},
        timeout=180,
    )
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


def _ruff_recebeu(amb):
    return amb["log"].read_text().split() if amb["log"].exists() else []


# --- conjunto de arquivos do turno -------------------------------------------


def test_turno_sem_mudanca_nao_checa_nada(ambiente):
    _prompt(ambiente)
    assert _stop(ambiente) is None
    assert _ruff_recebeu(ambiente) == []


def test_wip_anterior_ao_prompt_nao_e_checado(ambiente):
    (ambiente["repo"] / "a.py").write_text("X = 2\n")  # sujo ANTES do prompt
    (ambiente["repo"] / "rascunho.py").write_text("Z = 0\n")  # não rastreado, antes
    _prompt(ambiente)
    assert _stop(ambiente) is None
    assert _ruff_recebeu(ambiente) == []


def test_wip_anterior_editado_de_novo_no_turno_e_checado(ambiente):
    (ambiente["repo"] / "a.py").write_text("X = 2\n")
    _prompt(ambiente)
    (ambiente["repo"] / "a.py").write_text("X = 3\n")
    _stop(ambiente)
    assert "a.py" in _ruff_recebeu(ambiente)


def test_arquivo_novo_staged_e_commitado_no_turno_sao_checados(ambiente):
    repo = ambiente["repo"]
    _prompt(ambiente)
    (repo / "novo.py").write_text("N = 1\n")
    (repo / "b.py").write_text("Y = 2\n")
    _git(repo, "add", "b.py")
    (repo / "c.py").write_text("C = 1\n")
    _git(repo, "add", "c.py")
    _git(repo, "commit", "-qm", "turno")
    _stop(ambiente)
    assert {"novo.py", "b.py", "c.py"} <= set(_ruff_recebeu(ambiente))


def test_arquivo_apagado_no_turno_nao_vai_ao_ruff(ambiente):
    repo = ambiente["repo"]
    _prompt(ambiente)
    (repo / "b.py").unlink()
    (repo / "a.py").write_text("X = 9\n")
    _stop(ambiente)
    recebidos = _ruff_recebeu(ambiente)
    assert "a.py" in recebidos and "b.py" not in recebidos


def test_so_python_e_checado(ambiente):
    _prompt(ambiente)
    (ambiente["repo"] / "notas.md").write_text("x\n")
    assert _stop(ambiente) is None


# --- decisões ----------------------------------------------------------------


def test_lint_com_violacao_bloqueia(ambiente):
    _prompt(ambiente)
    (ambiente["repo"] / "a.py").write_text("import os\n")
    saida = _stop(
        ambiente, env={"FAKE_RUFF_RC": "1", "FAKE_RUFF_OUT": "F401 Found 1 error."}
    )
    assert saida["decision"] == "block"
    assert "F401" in saida["reason"]


def test_falha_da_ferramenta_de_lint_so_avisa(ambiente):
    _prompt(ambiente)
    (ambiente["repo"] / "a.py").write_text("X = 5\n")
    saida = _stop(
        ambiente, env={"FAKE_RUFF_RC": "2", "FAKE_RUFF_OUT": "error: network"}
    )
    assert "decision" not in saida
    assert "ruff" in saida["systemMessage"]


def test_teste_falhando_bloqueia_com_resumo(ambiente):
    repo = ambiente["repo"]
    _prompt(ambiente)
    (repo / "tests" / "test_a.py").write_text("def test_a():\n    assert 1 == 2\n")
    saida = _stop(ambiente)
    assert saida["decision"] == "block"
    assert "test_a" in saida["reason"]
    assert len(saida["reason"].splitlines()) <= 25


def test_modulo_mapeia_para_todos_os_test_mod_star(ambiente):
    repo = ambiente["repo"]
    (repo / "tests" / "test_a_extra.py").write_text("def test_x():\n    assert False\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "extra")
    _prompt(ambiente)
    (repo / "a.py").write_text("X = 7\n")
    saida = _stop(ambiente)
    assert saida["decision"] == "block"
    assert "test_a_extra" in saida["reason"]


def test_modulo_sem_teste_nao_roda_pytest(ambiente):
    _prompt(ambiente)
    (ambiente["repo"] / "b.py").write_text("Y = 3\n")
    assert _stop(ambiente) is None


def test_erro_de_coleta_so_avisa(ambiente):
    _prompt(ambiente)
    (ambiente["repo"] / "tests" / "test_a.py").write_text(
        "import modulo_que_nao_existe\n"
    )
    saida = _stop(ambiente)
    assert "decision" not in saida
    assert "pytest" in saida["systemMessage"]


def test_python_sem_pytest_so_avisa(ambiente, tmp_path):
    sem_pytest = tmp_path / "py-sem-pytest"
    sem_pytest.write_text("#!/usr/bin/env bash\nexit 1\n")
    sem_pytest.chmod(sem_pytest.stat().st_mode | stat.S_IEXEC)
    _prompt(ambiente)
    (ambiente["repo"] / "tests" / "test_a.py").write_text(
        "def test_a():\n    assert 0\n"
    )
    saida = _stop(ambiente, env={"PROMPT_GATE_PYTHON": str(sem_pytest)})
    assert "decision" not in saida
    assert "pytest" in saida["systemMessage"]


# --- curto-circuitos -----------------------------------------------------------


def test_stop_hook_active_encerra(ambiente):
    _prompt(ambiente)
    (ambiente["repo"] / "tests" / "test_a.py").write_text(
        "def test_a():\n    assert 0\n"
    )
    assert _stop(ambiente, ativo=True) is None


def test_kill_switch_encerra(ambiente):
    _prompt(ambiente)
    (ambiente["repo"] / "tests" / "test_a.py").write_text(
        "def test_a():\n    assert 0\n"
    )
    assert _stop(ambiente, env={"PROMPT_GATE_DISABLE": "1"}) is None


def test_sem_snapshot_nao_checa_nada(ambiente):
    (ambiente["repo"] / "tests" / "test_a.py").write_text(
        "def test_a():\n    assert 0\n"
    )
    assert _stop(ambiente, sessao="nunca-viu-prompt") is None


def test_stdin_invalido_nao_quebra_o_stop(ambiente):
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "--stop"],
        input="lixo",
        capture_output=True,
        text=True,
        env=ambiente["env"],
        timeout=30,
    )
    assert p.returncode == 0
