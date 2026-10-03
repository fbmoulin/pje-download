"""Tests for the prompt-gate telemetry, --label and --report (spec Task 5).

Telemetry lives outside the repo and never stores the prompt text or a hash of it.
"""

import json
import subprocess
import sys

import pytest

from tests.test_prompt_gate import CPF, _pontuar_cpf
from tests.test_prompt_gate_hook import COMPLETO, RAIZ, SCRIPT, VAGO, _rodar


@pytest.fixture
def estado(tmp_path):
    return tmp_path / "state"


def _hook(prompt, estado, sessao="s1", prompt_id="p1", env=None):
    entrada = json.dumps(
        {"prompt": prompt, "session_id": sessao, "prompt_id": prompt_id}
    )
    return _rodar(None, estado, stdin=entrada, env=env)


def _telemetria(estado):
    arq = estado / "pje-prompt-gate" / "telemetria.jsonl"
    if not arq.exists():
        return []
    return [json.loads(linha) for linha in arq.read_text("utf-8").splitlines()]


def _cli(estado, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env={"XDG_STATE_HOME": str(estado), "PATH": ""},
        timeout=30,
    )


def _id_do_aviso(proc):
    msg = json.loads(proc.stdout)["systemMessage"]
    return msg.split("[", 1)[1].split("]", 1)[0]


# --- registro ---------------------------------------------------------------


def test_aviso_gera_registro_com_o_id_mostrado_ao_usuario(estado):
    p = _hook(VAGO, estado)
    (reg,) = _telemetria(estado)
    assert reg["id"] == _id_do_aviso(p)
    assert reg["decisao"] == "aviso"
    assert reg["regras"] == ["alvo_ou_sintoma", "possui_dod"]
    assert (reg["session_id"], reg["prompt_id"]) == ("s1", "p1")
    assert isinstance(reg["latencia_ms"], (int, float)) and reg["latencia_ms"] >= 0
    assert "ts" in reg


def test_prompt_avaliado_sem_regras_tambem_e_registrado(estado):
    _hook(COMPLETO, estado)
    (reg,) = _telemetria(estado)
    assert reg["decisao"] == "passa" and reg["regras"] == []


def test_prompt_isento_ou_fora_da_vaga_nao_gera_registro(estado):
    _hook("por que o worker trava?", estado)
    _hook(COMPLETO, estado)
    _hook(VAGO, estado, prompt_id="p2")  # vaga já consumida
    assert len(_telemetria(estado)) == 1


def test_telemetria_nunca_guarda_texto_nem_hash_do_prompt(estado):
    _hook(VAGO, estado)
    bruto = (estado / "pje-prompt-gate" / "telemetria.jsonl").read_text("utf-8")
    for palavra in ("robusto", "projeto", "melhora"):
        assert palavra not in bruto
    reg = _telemetria(estado)[0]
    assert set(reg) == {
        "ts",
        "id",
        "session_id",
        "prompt_id",
        "regras",
        "decisao",
        "latencia_ms",
    }


@pytest.mark.parametrize("valor", [CPF, _pontuar_cpf(CPF)])
def test_registro_de_pii_so_tem_regra_e_decisao(estado, valor):
    p = _hook(f"use {valor} no teste do worker.py", estado)
    assert p.returncode == 2
    (reg,) = _telemetria(estado)
    assert set(reg) == {"ts", "regras", "decisao"}
    assert reg == {"ts": reg["ts"], "regras": ["pii_cpf"], "decisao": "bloqueia"}
    bruto = (estado / "pje-prompt-gate" / "telemetria.jsonl").read_text("utf-8")
    assert CPF not in bruto and CPF[:3] not in bruto


def test_telemetria_fica_fora_do_repo(estado):
    _hook(VAGO, estado)
    arq = estado / "pje-prompt-gate" / "telemetria.jsonl"
    assert arq.exists()
    assert not arq.resolve().is_relative_to(RAIZ)


def test_kill_switch_nao_registra(estado):
    _hook(VAGO, estado, env={"PROMPT_GATE_DISABLE": "1"})
    assert _telemetria(estado) == []


# --- --label ------------------------------------------------------------------


def test_label_grava_rotulo_para_id_existente(estado):
    rotulo = _id_do_aviso(_hook(VAGO, estado))
    p = _cli(estado, "--label", rotulo, "fp")
    assert p.returncode == 0, p.stderr
    rotulos = (estado / "pje-prompt-gate" / "rotulos.jsonl").read_text("utf-8")
    (linha,) = [json.loads(x) for x in rotulos.splitlines()]
    assert (linha["id"], linha["rotulo"]) == (rotulo, "fp")


@pytest.mark.parametrize("args", [["naoexiste", "fp"], ["{id}", "talvez"], ["{id}"]])
def test_label_rejeita_id_desconhecido_ou_rotulo_invalido(estado, args):
    rotulo = _id_do_aviso(_hook(VAGO, estado))
    p = _cli(estado, "--label", *[a.format(id=rotulo) for a in args])
    assert p.returncode != 0
    assert not (estado / "pje-prompt-gate" / "rotulos.jsonl").exists()


def test_ultimo_rotulo_vale(estado):
    rotulo = _id_do_aviso(_hook(VAGO, estado))
    _cli(estado, "--label", rotulo, "fp")
    _cli(estado, "--label", rotulo, "tp")
    p = _cli(estado, "--report")
    linha = next(x for x in p.stdout.splitlines() if x.startswith("possui_dod"))
    assert linha.split()[1:5] == ["1", "1", "0", "1"]  # avisos rotulados fp tp


# --- --report -----------------------------------------------------------------


def test_report_conta_por_regra_e_lista_pendentes(estado):
    ids = []
    for i in range(3):
        ids.append(_id_do_aviso(_hook(VAGO, estado, sessao=f"s{i}", prompt_id=f"p{i}")))
    _hook(COMPLETO, estado, sessao="s9")
    _hook(f"consulta {CPF} agora", estado, sessao="s8")
    _cli(estado, "--label", ids[0], "fp")
    _cli(estado, "--label", ids[1], "tp")
    p = _cli(estado, "--report")
    assert p.returncode == 0, p.stderr
    linhas = p.stdout.splitlines()
    alvo = next(x for x in linhas if x.startswith("alvo_ou_sintoma"))
    assert alvo.split()[1:5] == ["3", "2", "1", "1"]
    assert "prompts avaliados: 4" in p.stdout
    assert "bloqueios por PII: 1" in p.stdout
    assert ids[2] in p.stdout and "s2" in p.stdout and "p2" in p.stdout
    assert ids[0] not in p.stdout.split("pendentes", 1)[1]


def test_report_sem_telemetria_nao_quebra(estado):
    p = _cli(estado, "--report")
    assert p.returncode == 0
    assert "prompts avaliados: 0" in p.stdout
