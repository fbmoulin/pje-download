#!/usr/bin/env python3
"""Consulta só o cabeçalho de processos pelo MNI, sem baixar documentos.

POR QUE ESTE ARQUIVO EXISTE
----------------------------
O gabinete cruza a fila do PJe com os temas repetitivos do STJ que suspendem o
1º grau. O export do PJe só traz o assunto principal; o cabeçalho do MNI traz
classe, todos os assuntos (código nacional da TPU), as partes com o tipo de
pessoa e as movimentações. Esta ferramenta pede ``consultarProcesso`` com
``incluirDocumentos=False``: nenhum documento é baixado nem gravado em disco.
Spec: ``docs/specs/2026-11-16-consulta-cabecalho.md``.

Uso (dentro do contêiner do worker, na VPS):
    python -I tools/consultar_cabecalho.py <cnj1,cnj2,...> [--so-pessoa-juridica]
    python -I tools/consultar_cabecalho.py --arquivo lista.txt --saida cabecalhos.json

Saída: JSON em stdout (ou em ``--saida``, gravado com permissão 0600).
``--so-pessoa-juridica`` tira das listas de partes as pessoas físicas e as
substitui pela contagem: é o modo a usar quando a saída sai da VPS.

Códigos de saída:
    0 = todos os processos consultados com sucesso
    1 = pelo menos um processo falhou (os demais estão na saída)
    2 = uso inválido ou erro de configuração (nada foi consultado)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

MAX_PROCESSOS = 50
MAX_MOVIMENTOS = 30
PESSOA_JURIDICA = "juridica"


def _load_env() -> None:
    """Carrega o .env ANTES de construir o MNIClient (CLAUDE.md, Env Loading)."""
    from config import load_env

    load_env()


def parse_numeros(texto: str) -> list[str]:
    """Separa por vírgula, ponto e vírgula, espaço ou quebra de linha; tira repetidos."""
    vistos: list[str] = []
    for bruto in (
        texto.replace(";", ",").replace("\n", ",").replace(" ", ",").split(",")
    ):
        n = bruto.strip()
        if n and n not in vistos:
            vistos.append(n)
    return vistos


def _partes(processo, so_pessoa_juridica: bool) -> dict:
    """Agrupa as partes por polo; no modo PJ, pessoas físicas viram só contagem."""
    polos: dict[str, list[dict]] = {}
    omitidas = 0
    for parte in getattr(processo, "partes", []) or []:
        tipo = parte.get("tipo_pessoa", "")
        if so_pessoa_juridica and tipo != PESSOA_JURIDICA:
            omitidas += 1
            continue
        polos.setdefault(parte.get("polo", ""), []).append(
            {"nome": parte.get("nome", ""), "tipo_pessoa": tipo}
        )
    out = {"polos": polos}
    if so_pessoa_juridica:
        out["partes_omitidas"] = omitidas
    return out


def _resumo(
    numero: str, result, so_pessoa_juridica: bool, com_movimentos: bool
) -> dict:
    status = result.status or ("success" if result.success else "error")
    item = {"numero": numero, "status": status}
    if not result.success or result.processo is None:
        item["erro"] = (result.error or "falha sem mensagem")[:200]
        return item
    p = result.processo
    item["classe"] = p.classe
    item["assuntos"] = list(p.assuntos)
    item.update(_partes(p, so_pessoa_juridica))
    if com_movimentos:
        movs = list(p.movimentacoes)[-MAX_MOVIMENTOS:]
        item["movimentos"] = [
            {"data": m.get("data", ""), "descricao": m.get("descricao", "")}
            for m in movs
        ]
    return item


def _auditar(
    numero: str, tribunal: str, status: str, duracao: float, erro: str | None
) -> None:
    from audit import AuditEntry, log_access

    log_access(
        AuditEntry(
            event_type="cabecalho_consultado",
            processo_numero=numero,
            fonte="mni_soap",
            tribunal=tribunal,
            status="success" if status == "success" else "error",
            erro=erro,
            duracao_s=round(duracao, 3),
        )
    )


async def consultar(
    numeros: list[str],
    *,
    so_pessoa_juridica: bool,
    com_movimentos: bool,
    pausa_s: float,
) -> dict:
    from mni_client import MNIClient

    client = MNIClient()
    itens = []
    for i, numero in enumerate(numeros):
        if i and pausa_s > 0:
            await asyncio.sleep(pausa_s)
        t0 = time.monotonic()
        result = await client.consultar_processo(
            numero,
            incluir_documentos=False,
            incluir_cabecalho=True,
            incluir_movimentacoes=com_movimentos,
        )
        item = _resumo(numero, result, so_pessoa_juridica, com_movimentos)
        _auditar(
            numero,
            client.tribunal,
            item["status"],
            time.monotonic() - t0,
            item.get("erro"),
        )
        itens.append(item)
    return {
        "gerado_em": datetime.now(UTC).isoformat(timespec="seconds"),
        "tribunal": client.tribunal,
        "so_pessoa_juridica": so_pessoa_juridica,
        "processos": itens,
    }


def _gravar(caminho: Path, texto: str) -> None:
    fd = os.open(str(caminho), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(texto)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Cabeçalho de processos pelo MNI, sem documentos."
    )
    ap.add_argument(
        "numeros", nargs="?", default="", help="números CNJ separados por vírgula"
    )
    ap.add_argument("--arquivo", type=Path, help="arquivo com um número CNJ por linha")
    ap.add_argument(
        "--saida", type=Path, help="grava o JSON neste arquivo (0600) em vez de stdout"
    )
    ap.add_argument(
        "--so-pessoa-juridica", action="store_true", help="omite pessoas físicas"
    )
    ap.add_argument(
        "--sem-movimentos", action="store_true", help="não pede movimentações"
    )
    ap.add_argument(
        "--pausa", type=float, default=1.0, help="segundos entre consultas (padrão 1)"
    )
    args = ap.parse_args(argv)

    texto = args.numeros
    if args.arquivo:
        try:
            texto += "," + args.arquivo.read_text(encoding="utf-8")
        except OSError as exc:
            print(
                f"não foi possível ler --arquivo: {type(exc).__name__}", file=sys.stderr
            )
            return 2
    numeros = parse_numeros(texto)

    from config import is_valid_processo

    validos = [n for n in numeros if is_valid_processo(n)]
    invalidos = len(numeros) - len(validos)
    if not validos:
        print("nenhum número CNJ válido informado", file=sys.stderr)
        return 2
    if len(validos) > MAX_PROCESSOS:
        print(f"no máximo {MAX_PROCESSOS} processos por chamada", file=sys.stderr)
        return 2
    if args.pausa < 0:
        print("--pausa não pode ser negativa", file=sys.stderr)
        return 2

    _load_env()
    try:
        saida = asyncio.run(
            consultar(
                validos,
                so_pessoa_juridica=args.so_pessoa_juridica,
                com_movimentos=not args.sem_movimentos,
                pausa_s=args.pausa,
            )
        )
    except (
        Exception
    ) as exc:  # cliente não construído (tribunal desconhecido, credencial ausente)
        print(f"consulta não executada: {type(exc).__name__}", file=sys.stderr)
        return 2
    saida["invalidos"] = invalidos

    texto_json = json.dumps(saida, ensure_ascii=False, indent=2)
    if args.saida:
        _gravar(args.saida, texto_json + "\n")
    else:
        print(texto_json)
    return 0 if all(p["status"] == "success" for p in saida["processos"]) else 1


if __name__ == "__main__":
    sys.exit(main())
