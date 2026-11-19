"""Consulta só de cabeçalho pelo MNI (tools/consultar_cabecalho.py).

Spec: docs/specs/2026-11-16-consulta-cabecalho.md. Números CNJ e nomes são
montados em tempo de execução (gitleaks e o pre-push barram literais).
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mni_client import MNIClient, MNIProcesso, MNIResult
from tools import consultar_cabecalho as cc


def cnj(seq: int) -> str:
    return "{:07d}-{:02d}.{}.{}.{:02d}.{}".format(seq, 1, 2026, 8, 8, "0012")


def _nome(prefixo: str, i: int) -> str:
    return f"{prefixo} Teste {i}"


def _pessoa(nome: str, tipo: str | None):
    return (
        SimpleNamespace(nome=nome, tipoPessoa=tipo)
        if tipo is not None
        else SimpleNamespace(nome=nome)
    )


def _resposta(partes_at, partes_pa):
    polo_at = SimpleNamespace(parte=[SimpleNamespace(pessoa=p) for p in partes_at])
    polo_pa = SimpleNamespace(parte=[SimpleNamespace(pessoa=p) for p in partes_pa])
    dados = SimpleNamespace(
        classeProcessual=7,
        assunto=[SimpleNamespace(codigoNacional=11806, principal=True)],
        polo=[polo_at, polo_pa],
    )
    return SimpleNamespace(
        processo=SimpleNamespace(dadosBasicos=dados, documento=[], movimento=[])
    )


# ── parser ───────────────────────────────────────────────────────────


class TestParserTipoPessoa:
    def test_partes_carregam_polo_e_tipo_de_pessoa(self):
        client = MNIClient(tribunal="TJES", username="u", password="p")
        resp = _resposta(
            [_pessoa(_nome("Autor", 1), "fisica")],
            [_pessoa(_nome("Banco", 2), "JURIDICA")],
        )
        proc = client._parse_processo(resp, cnj(1))
        assert proc.partes == [
            {"polo": "AT", "nome": _nome("Autor", 1), "tipo_pessoa": "fisica"},
            {"polo": "PA", "nome": _nome("Banco", 2), "tipo_pessoa": "juridica"},
        ]

    def test_polo_ativo_e_passivo_continuam_iguais(self):
        client = MNIClient(tribunal="TJES", username="u", password="p")
        resp = _resposta(
            [_pessoa(_nome("Autor", 1), "fisica")],
            [_pessoa(_nome("Banco", 2), "juridica")],
        )
        proc = client._parse_processo(resp, cnj(1))
        assert proc.polo_ativo == [_nome("Autor", 1)]
        assert proc.polo_passivo == [_nome("Banco", 2)]
        assert proc.assuntos == ["cod:11806 (principal)"]

    def test_tipo_ausente_vira_texto_vazio(self):
        client = MNIClient(tribunal="TJES", username="u", password="p")
        resp = _resposta([_pessoa(_nome("Autor", 1), None)], [])
        proc = client._parse_processo(resp, cnj(1))
        assert proc.partes[0]["tipo_pessoa"] == ""


# ── ferramenta ───────────────────────────────────────────────────────


def _processo(numero: str) -> MNIProcesso:
    p = MNIProcesso(numero=numero, classe="7", assuntos=["cod:7772 (principal)"])
    p.partes = [
        {"polo": "AT", "nome": _nome("Autor", 1), "tipo_pessoa": "fisica"},
        {"polo": "PA", "nome": _nome("Banco", 2), "tipo_pessoa": "juridica"},
        {"polo": "PA", "nome": _nome("Fiador", 3), "tipo_pessoa": "fisica"},
    ]
    p.movimentacoes = [
        {"data": f"2026111{i}", "descricao": f"mov {i}", "complemento": "x"}
        for i in range(3)
    ]
    return p


@pytest.fixture
def fake_client():
    client = MagicMock()
    client.tribunal = "TJES"

    async def _consultar(numero, **kwargs):
        return MNIResult(success=True, processo=_processo(numero), status="success")

    client.consultar_processo = AsyncMock(side_effect=_consultar)
    with (
        patch("mni_client.MNIClient", return_value=client),
        patch.object(cc, "_load_env"),
        patch("audit.log_access") as log_access,
    ):
        client.log_access = log_access
        yield client


def _rodar(capsys, argv):
    code = cc.main(argv)
    return code, capsys.readouterr()


class TestFerramenta:
    def test_nao_pede_documentos(self, fake_client, capsys):
        code, _ = _rodar(capsys, [cnj(1), "--pausa", "0"])
        assert code == 0
        kwargs = fake_client.consultar_processo.await_args.kwargs
        assert kwargs["incluir_documentos"] is False
        assert kwargs["incluir_cabecalho"] is True
        assert kwargs["incluir_movimentacoes"] is True

    def test_saida_json_com_cabecalho(self, fake_client, capsys):
        code, out = _rodar(capsys, [f"{cnj(1)},{cnj(2)}", "--pausa", "0"])
        dados = json.loads(out.out)
        assert code == 0
        assert [p["numero"] for p in dados["processos"]] == [cnj(1), cnj(2)]
        p = dados["processos"][0]
        assert p["assuntos"] == ["cod:7772 (principal)"]
        assert [x["nome"] for x in p["polos"]["PA"]] == [
            _nome("Banco", 2),
            _nome("Fiador", 3),
        ]
        assert p["movimentos"][-1] == {"data": "20261112", "descricao": "mov 2"}
        assert dados["invalidos"] == 0

    def test_so_pessoa_juridica_omite_pessoas_fisicas(self, fake_client, capsys):
        _, out = _rodar(capsys, [cnj(1), "--so-pessoa-juridica", "--pausa", "0"])
        texto = out.out
        assert _nome("Autor", 1) not in texto
        assert _nome("Fiador", 3) not in texto
        p = json.loads(texto)["processos"][0]
        assert p["polos"] == {
            "PA": [{"nome": _nome("Banco", 2), "tipo_pessoa": "juridica"}]
        }
        assert p["partes_omitidas"] == 2

    def test_sem_movimentos(self, fake_client, capsys):
        _, out = _rodar(capsys, [cnj(1), "--sem-movimentos", "--pausa", "0"])
        assert (
            fake_client.consultar_processo.await_args.kwargs["incluir_movimentacoes"]
            is False
        )
        assert "movimentos" not in json.loads(out.out)["processos"][0]

    def test_invalidos_nao_sao_consultados(self, fake_client, capsys):
        code, out = _rodar(capsys, [f"{cnj(1)},123,{cnj(1)}", "--pausa", "0"])
        assert code == 0
        assert fake_client.consultar_processo.await_count == 1
        assert json.loads(out.out)["invalidos"] == 1

    def test_falha_de_um_processo_da_codigo_1(self, fake_client, capsys):
        async def _consultar(numero, **kwargs):
            if numero == cnj(2):
                return MNIResult(
                    success=False, error="não encontrado", status="not_found"
                )
            return MNIResult(success=True, processo=_processo(numero), status="success")

        fake_client.consultar_processo.side_effect = _consultar
        code, out = _rodar(capsys, [f"{cnj(1)},{cnj(2)}", "--pausa", "0"])
        ps = json.loads(out.out)["processos"]
        assert code == 1
        assert ps[1] == {
            "numero": cnj(2),
            "status": "not_found",
            "erro": "não encontrado",
        }

    def test_cada_consulta_vai_para_a_auditoria(self, fake_client, capsys):
        _rodar(capsys, [f"{cnj(1)},{cnj(2)}", "--pausa", "0"])
        entradas = [c.args[0] for c in fake_client.log_access.call_args_list]
        assert [e.event_type for e in entradas] == ["cabecalho_consultado"] * 2
        assert {e.fonte for e in entradas} == {"mni_soap"}
        assert [e.processo_numero for e in entradas] == [cnj(1), cnj(2)]

    def test_limite_de_processos(self, fake_client, capsys):
        lista = ",".join(cnj(i) for i in range(1, cc.MAX_PROCESSOS + 2))
        code, out = _rodar(capsys, [lista])
        assert code == 2
        fake_client.consultar_processo.assert_not_awaited()

    def test_sem_numero_valido(self, fake_client, capsys):
        code, _ = _rodar(capsys, ["abc"])
        assert code == 2

    def test_arquivo_e_saida_0600(self, fake_client, capsys, tmp_path):
        lista = tmp_path / "lista.txt"
        lista.write_text(f"{cnj(1)}\n{cnj(2)}\n", encoding="utf-8")
        destino = tmp_path / "cab.json"
        code, out = _rodar(
            capsys, ["--arquivo", str(lista), "--saida", str(destino), "--pausa", "0"]
        )
        assert code == 0
        assert out.out == ""
        assert len(json.loads(destino.read_text(encoding="utf-8"))["processos"]) == 2
        assert oct(os.stat(destino).st_mode & 0o777) == "0o600"

    def test_erro_ao_construir_cliente_nao_vaza_mensagem(self, capsys):
        with (
            patch("mni_client.MNIClient", side_effect=ValueError("segredo-xyz")),
            patch.object(cc, "_load_env"),
        ):
            code, out = _rodar(capsys, [cnj(1)])
        assert code == 2
        assert "segredo-xyz" not in out.err
        assert "ValueError" in out.err

    def test_pausa_negativa(self, fake_client, capsys):
        code, _ = _rodar(capsys, [cnj(1), "--pausa", "-1"])
        assert code == 2

    def test_parse_numeros_aceita_separadores_e_tira_repetidos(self):
        assert cc.parse_numeros(f" {cnj(1)};{cnj(2)}\n{cnj(1)} ") == [cnj(1), cnj(2)]


def test_saida_existente_com_0644_vira_0600(fake_client, capsys, tmp_path):
    destino = tmp_path / "cab.json"
    destino.write_text("{}", encoding="utf-8")
    os.chmod(destino, 0o644)
    code, _ = _rodar(capsys, [cnj(1), "--saida", str(destino), "--pausa", "0"])
    assert code == 0
    assert oct(os.stat(destino).st_mode & 0o777) == "0o600"


def test_logs_do_mni_nao_sujam_o_json(capsys):
    import structlog

    client = MagicMock()
    client.tribunal = "TJES"

    async def _consultar(numero, **kwargs):
        structlog.get_logger("kratos.mni-client").info(
            "mni.consultar_processo.start", processo=numero
        )
        return MNIResult(success=True, processo=_processo(numero), status="success")

    client.consultar_processo = AsyncMock(side_effect=_consultar)
    try:
        with (
            patch("mni_client.MNIClient", return_value=client),
            patch.object(cc, "_load_env"),
            patch("audit.log_access"),
        ):
            code = cc.main([cnj(1), "--pausa", "0"])
        out = capsys.readouterr()
    finally:
        structlog.reset_defaults()
    assert code == 0
    json.loads(out.out)
    assert "mni.consultar_processo.start" in out.err
