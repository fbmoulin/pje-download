"""Tests for tools/prompt_gate.py — the deterministic core (spec 2026-10-03, Tasks 1-2).

No literal CPF/CNPJ/CNJ-shaped value may appear in this file: the repo's pre-push
gate (tools/validate_br_pii.py) and gitleaks would block the push. Every such value
is assembled at runtime by the helpers below.
"""

import json
from pathlib import Path

import pytest

from tools.prompt_gate import (
    MARCADORES_DOD,
    Resultado,
    avaliar,
    detectar_pii,
)

RAIZ = Path(__file__).resolve().parents[1]
CASOS = json.loads((RAIZ / "tests/fixtures/prompt_gate_cases.json").read_text("utf-8"))


def _avaliar(prompt, primeiro=True, bloquear=frozenset()):
    return avaliar(prompt, RAIZ, primeiro, bloquear)


# --- contrato -------------------------------------------------------------


def test_resultado_e_imutavel_e_tem_o_contrato_da_spec():
    r = Resultado("passa", (), "")
    assert (r.decisao, r.regras, r.motivo) == ("passa", (), "")
    with pytest.raises(AttributeError):
        r.decisao = "aviso"  # type: ignore[misc]


def test_prompt_completo_passa_sem_regras():
    r = _avaliar(
        "Em worker.py, trate o timeout do blpop; pronto quando "
        "pytest tests/test_worker.py -q passar"
    )
    assert r == Resultado("passa", (), "")


# --- isenções e vaga de primeiro prompt -----------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "/review",
        "/refine-prompt melhora o tratamento de erro do worker sem quebrar nada",
        "por que o dashboard demora tanto para carregar a lista de lotes?",
        "sim",
        "pode seguir com a opção A",
        "!! refatora tudo que achar necessário no projeto inteiro agora",
    ],
)
def test_isencoes_passam_sem_regras(prompt):
    assert _avaliar(prompt) == Resultado("passa", (), "")


def test_fora_do_primeiro_prompt_qualidade_nao_roda():
    vago = "melhora o código do projeto inteiro e deixa mais robusto"
    assert _avaliar(vago, primeiro=True).decisao == "aviso"
    assert _avaliar(vago, primeiro=False) == Resultado("passa", (), "")


# --- alvo_ou_sintoma ------------------------------------------------------


def test_arquivo_existente_satisfaz_alvo():
    r = _avaliar("corrija worker.py para não perder o resultado; deve passar no pytest")
    assert "alvo_ou_sintoma" not in r.regras


def test_caminho_inexistente_dispara_alvo():
    r = _avaliar("corrija o arquivo xyz_inexistente.py; deve passar no pytest")
    assert "alvo_ou_sintoma" in r.regras


def test_sem_caminho_nem_sintoma_dispara_alvo():
    r = _avaliar("deixa o download mais rápido e confiável; deve passar no pytest")
    assert "alvo_ou_sintoma" in r.regras


@pytest.mark.parametrize(
    "sintoma",
    [
        "o /health retorna 503",
        "o worker levanta KeyError ao ler o resultado",
        "aparece um Traceback no log do dashboard",
        "o teste test_rate_limit_eviction falha",
        "a chamada devolve HTTP 401",
    ],
)
def test_sintoma_reproduzivel_satisfaz_alvo(sintoma):
    r = _avaliar(f"{sintoma}; investigue e corrija, deve passar no pytest")
    assert "alvo_ou_sintoma" not in r.regras


# --- possui_dod -----------------------------------------------------------


@pytest.mark.parametrize("marcador", MARCADORES_DOD)
def test_cada_marcador_de_dod_e_reconhecido(marcador):
    r = _avaliar(f"ajuste worker.py para logar o jobId; {marcador} ok")
    assert "possui_dod" not in r.regras, marcador


def test_sem_dod_dispara_regra():
    r = _avaliar("ajuste worker.py para logar o jobId em todas as fases")
    assert r.regras == ("possui_dod",)
    assert r.decisao == "aviso"


# --- multi_tarefa ---------------------------------------------------------


def test_lista_numerada_com_tres_itens_dispara_multi_tarefa():
    prompt = (
        "Em worker.py, pronto quando o pytest passar:\n"
        "1. renomeie a fila\n2. troque o backoff\n3. adicione métricas\n4. limpe logs"
    )
    assert "multi_tarefa" in _avaliar(prompt).regras


def test_lista_com_marcadores_dispara_multi_tarefa():
    prompt = (
        "Em dashboard_api.py, deve passar no pytest:\n"
        "- mude o CORS\n- mude o rate limit\n- mude a auth"
    )
    assert "multi_tarefa" in _avaliar(prompt).regras


def test_dois_itens_nao_disparam_multi_tarefa():
    prompt = "Em worker.py, deve passar no pytest:\n1. renomeie a fila\n2. ajuste o log"
    assert "multi_tarefa" not in _avaliar(prompt).regras


# --- decisão e motivo -----------------------------------------------------


def test_regra_em_bloquear_vira_bloqueio():
    r = _avaliar(
        "ajuste worker.py para logar o jobId em todas as fases",
        bloquear=frozenset({"possui_dod"}),
    )
    assert r.decisao == "bloqueia"
    assert r.regras == ("possui_dod",)


def test_regra_fora_de_bloquear_continua_aviso():
    r = _avaliar(
        "ajuste worker.py para logar o jobId em todas as fases",
        bloquear=frozenset({"multi_tarefa"}),
    )
    assert r.decisao == "aviso"


def test_motivo_cita_cada_regra_disparada():
    r = _avaliar("melhora tudo do projeto e deixa mais robusto e rápido")
    assert set(r.regras) == {"alvo_ou_sintoma", "possui_dod"}
    for regra in r.regras:
        assert regra in r.motivo


def test_regras_tem_ordem_estavel():
    r = _avaliar(
        "melhora o projeto:\n- um item\n- outro item\n- mais um item\n- e outro"
    )
    assert r.regras == ("alvo_ou_sintoma", "possui_dod", "multi_tarefa")


# --- PII (Task 2) ---------------------------------------------------------
# Valores montados em tempo de execução a partir de sementes curtas: nenhum
# literal com forma de CPF/CNPJ/CNJ entra no git.


def _dv_mod11(valores, pesos):
    resto = sum(v * p for v, p in zip(valores, pesos)) % 11
    return 0 if resto < 2 else 11 - resto


def _cpf(semente9: str) -> str:
    d = [int(c) for c in semente9]
    d.append(_dv_mod11(d, range(10, 1, -1)))
    d.append(_dv_mod11(d, range(11, 1, -1)))
    return "".join(map(str, d))


def _cnpj(semente12: str) -> str:
    v = [ord(c) - 48 for c in semente12]
    for n in (12, 13):
        pesos = [((n - 1 - i) % 8) + 2 for i in range(n)]
        v.append(_dv_mod11(v, pesos))
    return semente12 + "".join(str(x) for x in v[12:])


def _pontuar_cpf(c: str) -> str:
    return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"


def _pontuar_cnpj(c: str) -> str:
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"


def _invalidar(valor: str) -> str:
    return valor[:-1] + str((int(valor[-1]) + 1) % 10)


CPF = _cpf("5" + "29982" + "247")
CNPJ = _cnpj("11" + "222333" + "0001")
CNPJ_ALFA = _cnpj("12ABC345" + "01DE")

FORMAS = {
    "cpf-nu": (CPF, "pii_cpf"),
    "cpf-pontuado": (_pontuar_cpf(CPF), "pii_cpf"),
    "cnpj-nu": (CNPJ, "pii_cnpj"),
    "cnpj-pontuado": (_pontuar_cnpj(CNPJ), "pii_cnpj"),
    "cnpj-alfanumerico": (_pontuar_cnpj(CNPJ_ALFA), "pii_cnpj"),
}
CONTEXTOS = {
    "texto-comum": (
        "Em worker.py, use o documento {v} no teste; pronto quando o pytest passar",
        True,
    ),
    "pergunta": ("o documento {v} desse autor bate com o dos autos?", True),
    "bang": ("!! consulta {v} e segue com a correção do worker", True),
    "slash": ("/refine-prompt consulta {v} no mni_client", True),
    "curto": ("consulta {v}", True),
    "nao-primeiro": (
        "agora troca o valor fixo do teste por {v} e roda tudo de novo",
        False,
    ),
}


def test_helpers_geram_documentos_validos():
    from tools.validate_br_pii import cnpj_valido, cpf_valido

    assert cpf_valido(CPF) and not cpf_valido(_invalidar(CPF))
    assert cnpj_valido(CNPJ) and cnpj_valido(CNPJ_ALFA)
    assert not cnpj_valido(_invalidar(CNPJ))


@pytest.mark.parametrize("forma", FORMAS)
@pytest.mark.parametrize("contexto", CONTEXTOS)
def test_pii_bloqueia_em_qualquer_forma_e_contexto(forma, contexto):
    valor, regra = FORMAS[forma]
    molde, primeiro = CONTEXTOS[contexto]
    r = _avaliar(molde.format(v=valor), primeiro=primeiro)
    assert r.decisao == "bloqueia"
    assert r.regras == (regra,)


@pytest.mark.parametrize("forma", FORMAS)
def test_motivo_nunca_contem_o_documento(forma):
    valor, _ = FORMAS[forma]
    r = _avaliar(f"Em worker.py use {valor} no teste; pronto quando o pytest passar")
    digitos = "".join(c for c in valor if c.isalnum())
    assert valor not in r.motivo
    assert digitos not in r.motivo
    assert digitos[:3] not in r.motivo and digitos[-2:] not in r.motivo


def test_cpf_e_cnpj_juntos_disparam_as_duas_regras_em_ordem():
    r = _avaliar(f"cruze {CNPJ} com {CPF} no relatório de partes do lote")
    assert r.regras == ("pii_cpf", "pii_cnpj")


@pytest.mark.parametrize(
    "valor",
    [
        _invalidar(CPF),
        _pontuar_cpf(_invalidar(CPF)),
        _invalidar(CNPJ),
        _pontuar_cnpj(_invalidar(CNPJ)),
        "1" * 11,  # repetido: passa no módulo 11, mas é placeholder
    ],
)
def test_digito_verificador_invalido_nao_bloqueia(valor):
    assert detectar_pii(f"use {valor} como placeholder no teste") == ()


def test_numero_cnj_nao_e_pii():
    cnj = "0012345" + "-" + "67" + "." + "2024" + ".8." + "08" + "." + "0001"
    sem_pontos = "".join(c for c in cnj if c.isdigit())
    for v in (cnj, sem_pontos):
        assert detectar_pii(f"baixe o processo {v} pelo MNI no worker.py") == ()


def test_kill_switch_nao_e_responsabilidade_de_avaliar():
    # avaliar() sempre checa PII; o PROMPT_GATE_DISABLE é tratado em main() (Task 4).
    assert _avaliar(f"consulta {CPF}").decisao == "bloqueia"


# --- fixture rotulada -----------------------------------------------------


def test_fixture_tem_pelo_menos_40_casos_e_sem_digitos_longos():
    assert len(CASOS) >= 40
    for caso in CASOS:
        # nada com 11+ dígitos seguidos: PII nunca entra na fixture
        assert not any(len(t) >= 11 and t.isdigit() for t in caso["prompt"].split())


@pytest.mark.parametrize("caso", CASOS, ids=[c["id"] for c in CASOS])
def test_fixture_rotulada(caso):
    r = _avaliar(caso["prompt"], primeiro=caso.get("primeiro_prompt", True))
    assert list(r.regras) == caso["esperado"], caso["prompt"]
