"""
Batch Downloader — Download de múltiplos processos via MNI SOAP.
================================================================

Recebe uma lista de números de processo (via CSV, JSON ou argumento)
e baixa todos os documentos de cada um, com controle de progresso,
retomada e relatório final.

Uso:
    python batch_downloader.py --input processos.csv --output ./downloads
    python batch_downloader.py --processos "1234-56.2024.8.08.0012,5678-90.2024.8.08.0012"
    python batch_downloader.py --input processos.json --skip-anexos

Formatos de input aceitos:
    CSV:  uma coluna "numero" ou primeira coluna sem header
    JSON: lista de strings ou lista de objetos com campo "numero"
    TXT:  um número por linha
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path

import structlog

log: structlog.BoundLogger = structlog.get_logger("kratos.batch-downloader")

# NOTA: mni_client é importado DEPOIS de _load_env() para que as env vars
# MNI_USERNAME/MNI_PASSWORD/MNI_TRIBUNAL sejam lidas corretamente.
# Veja _load_env() e o import lazy dentro de download_batch().


# ─────────────────────────────────────────────
# CONTROLE DE PROGRESSO
# ─────────────────────────────────────────────


@dataclass
class ProcessoStatus:
    """Status de download de um processo individual."""

    numero: str
    status: str = "pending"  # pending | downloading | done | failed | skipped
    phase: str = "waiting"  # waiting | gdrive | mni_metadata | mni_download | saving | done | failed
    phase_detail: str = ""  # detalhe textual da fase atual (ex: "Doc 3/15")
    total_docs: int = 0
    docs_baixados: int = 0
    tamanho_bytes: int = 0
    erro: str | None = None
    inicio: float | None = None
    fim: float | None = None

    @property
    def duracao_s(self) -> float:
        if self.inicio and self.fim:
            return round(self.fim - self.inicio, 1)
        return 0.0


@dataclass
class BatchProgress:
    """Controle de progresso do batch inteiro."""

    processos: dict[str, ProcessoStatus] = field(default_factory=dict)
    progress_file: Path | None = None
    _last_save: float = field(default=0.0, repr=False)
    _save_interval: float = field(default=0.5, repr=False)

    def add(self, numero: str) -> None:
        if numero not in self.processos:
            self.processos[numero] = ProcessoStatus(numero=numero)

    def get(self, numero: str) -> ProcessoStatus:
        return self.processos[numero]

    @property
    def total(self) -> int:
        return len(self.processos)

    @property
    def done(self) -> int:
        return sum(1 for p in self.processos.values() if p.status == "done")

    @property
    def failed(self) -> int:
        return sum(1 for p in self.processos.values() if p.status == "failed")

    @property
    def pending(self) -> int:
        return sum(
            1 for p in self.processos.values() if p.status in ("pending", "downloading")
        )

    def save(self, force: bool = False) -> None:
        """Persiste progresso em disco para retomada (debounced 500ms)."""
        if not self.progress_file:
            return
        now = time.monotonic()
        if not force and (now - self._last_save) < self._save_interval:
            return
        self._last_save = now
        data = {
            "updated_at": datetime.now(UTC).isoformat(),
            "summary": {
                "total": self.total,
                "done": self.done,
                "failed": self.failed,
                "pending": self.pending,
            },
            "processos": {
                num: {
                    "status": ps.status,
                    "phase": ps.phase,
                    "phase_detail": ps.phase_detail,
                    "total_docs": ps.total_docs,
                    "docs_baixados": ps.docs_baixados,
                    "tamanho_bytes": ps.tamanho_bytes,
                    "erro": ps.erro,
                    "duracao_s": ps.duracao_s,
                }
                for num, ps in self.processos.items()
            },
        }
        # Atomic write: temp file + rename prevents corruption
        tmp = self.progress_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.progress_file)

    @classmethod
    def load(cls, path: Path) -> "BatchProgress":
        """Carrega progresso de execução anterior para retomada."""
        progress = cls(progress_file=path)
        if not path.exists():
            return progress
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning(
                "batch.progress.corrupt",
                path=str(path),
                error=str(exc),
                note="Starting fresh — corrupt progress file ignored",
            )
            return progress
        for num, info in data.get("processos", {}).items():
            ps = ProcessoStatus(numero=num)
            ps.status = info.get("status", "pending")
            ps.phase = info.get("phase", "waiting")
            ps.phase_detail = info.get("phase_detail", "")
            ps.total_docs = info.get("total_docs", 0)
            ps.docs_baixados = info.get("docs_baixados", 0)
            ps.tamanho_bytes = info.get("tamanho_bytes", 0)
            ps.erro = info.get("erro")
            # Se já concluído, manter; senão, resetar para pending
            if ps.status not in ("done", "skipped"):
                ps.status = "pending"
                ps.phase = "waiting"
            progress.processos[num] = ps
        return progress


# ─────────────────────────────────────────────
# LEITURA DE INPUT
# ─────────────────────────────────────────────


def load_processos_from_file(path: Path) -> list[str]:
    """Lê lista de números de processo de CSV, JSON ou TXT."""
    text = path.read_text(encoding="utf-8-sig").strip()
    suffix = path.suffix.lower()

    if suffix == ".json":
        data = json.loads(text)
        if isinstance(data, list):
            if data and isinstance(data[0], str):
                return [n.strip() for n in data if n.strip()]
            return [
                item.get("numero", item.get("numeroProcesso", "")).strip()
                for item in data
                if isinstance(item, dict)
            ]
        return []

    if suffix == ".csv":
        reader = csv.DictReader(text.splitlines())
        # Tentar campo "numero" ou "numeroProcesso"
        rows = list(reader)
        if rows:
            for field_name in ("numero", "numeroProcesso", "processo"):
                if field_name in rows[0]:
                    return [
                        r[field_name].strip() for r in rows if r[field_name].strip()
                    ]
        # Fallback: primeira coluna
        reader2 = csv.reader(text.splitlines())
        next(reader2, None)  # skip header
        return [row[0].strip() for row in reader2 if row and row[0].strip()]

    # TXT: um número por linha
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    ]


# ─────────────────────────────────────────────
# BATCH DOWNLOADER
# ─────────────────────────────────────────────


async def download_batch(
    numeros: list[str],
    output_dir: Path,
    incluir_anexos: bool = True,
    batch_size: int = 5,
    delay_entre_processos: float = 2.0,
    resume: bool = True,
    gdrive_url_map: dict[str, str] | None = None,
) -> BatchProgress:
    """
    Baixa documentos de uma lista de processos via MNI SOAP.

    Args:
        numeros: Lista de números CNJ de processos
        output_dir: Diretório base de saída
        incluir_anexos: Se True (padrão), baixa docs vinculados
        batch_size: Docs por chamada SOAP na fase 2
        delay_entre_processos: Pausa em segundos entre processos
        resume: Se True, retoma de onde parou (lê progress.json)
        gdrive_url_map: Mapa {numero_processo: gdrive_folder_url} para processos
                        antigos cujo link do Google Drive já é conhecido

    Returns:
        BatchProgress com status de cada processo
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    progress_file = output_dir / "_progress.json"

    # Carregar progresso anterior se existir
    if resume and progress_file.exists():
        progress = BatchProgress.load(progress_file)
        log.info("batch.resume", previously_done=progress.done)
    else:
        progress = BatchProgress(progress_file=progress_file)

    # Adicionar processos novos
    for num in numeros:
        progress.add(num)
    progress.save(force=True)

    # Import lazy — garante que _load_env() já rodou antes
    from mni_client import MNIClient, MNIResult
    from gdrive_downloader import (
        is_processo_antigo,
        download_gdrive_folder,
    )

    if gdrive_url_map is None:
        gdrive_url_map = {}

    # Validar credenciais MNI antes de tentar qualquer chamada SOAP
    import os

    mni_user = os.getenv("MNI_USERNAME", "")
    mni_pass = os.getenv("MNI_PASSWORD", "")
    if not mni_user or not mni_pass:
        error_msg = "MNI_USERNAME ou MNI_PASSWORD não configurados"
        log.error("batch.mni_credentials_missing")
        for ps in progress.processos.values():
            if ps.status == "pending":
                ps.status = "failed"
                ps.phase = "failed"
                ps.phase_detail = error_msg
                ps.erro = error_msg
        progress.save(force=True)
        return progress

    # Inicializar cliente MNI
    client = MNIClient()
    health = await client.health_check()
    if health["status"] != "healthy":
        error_msg = health.get("error", "MNI unhealthy")
        log.error("batch.mni_unhealthy", error=error_msg)
        # Mark all processes as failed so dashboard shows the error
        for ps in progress.processos.values():
            if ps.status == "pending":
                ps.status = "failed"
                ps.phase = "failed"
                ps.phase_detail = f"MNI indisponivel: {error_msg[:80]}"
                ps.erro = error_msg
        progress.save(force=True)
        return progress

    # Detectar processos antigos
    antigos = [n for n in numeros if is_processo_antigo(n)]
    if antigos:
        log.info(
            "batch.processos_antigos_detected",
            count=len(antigos),
            numeros=antigos,
            note="Processos antigos podem ter docs escaneados no Google Drive",
        )

    log.info(
        "batch.start",
        total=progress.total,
        already_done=progress.done,
        to_download=progress.pending,
        incluir_anexos=incluir_anexos,
        processos_antigos=len(antigos),
    )

    start_time = time.monotonic()

    import os as _os

    concurrent = int(_os.getenv("CONCURRENT_DOWNLOADS", "3"))
    sem = asyncio.Semaphore(concurrent)

    async def _download_one(i: int, numero: str, ps: ProcessoStatus) -> None:
        if ps.status in ("done", "skipped"):
            return

        async with sem:
            ps.status = "downloading"
            ps.phase = "starting"
            ps.phase_detail = ""
            ps.inicio = time.monotonic()
            progress.save()

            safe_name = re.sub(r'[<>:"/\\|?*]', "_", numero)
            proc_dir = output_dir / safe_name
            proc_dir.mkdir(parents=True, exist_ok=True)

            log.info(
                "batch.processo.start",
                processo=numero,
                index=i + 1,
                total=progress.total,
            )

            try:
                all_files: list[dict] = []

                # ── PROCESSO ANTIGO: Google Drive + MNI ──
                if is_processo_antigo(numero):
                    gdrive_url = gdrive_url_map.get(numero)
                    if gdrive_url:
                        ps.phase = "gdrive"
                        ps.phase_detail = "Baixando pasta Google Drive"
                        progress.save()
                        log.info(
                            "batch.processo.antigo_gdrive",
                            processo=numero,
                            gdrive_url=gdrive_url,
                        )
                        gdrive_dir = proc_dir / "escaneados_gdrive"
                        gdrive_files = await download_gdrive_folder(
                            gdrive_url, gdrive_dir
                        )
                        all_files.extend(gdrive_files)
                        log.info(
                            "batch.processo.gdrive_done",
                            processo=numero,
                            gdrive_docs=len(gdrive_files),
                        )
                    else:
                        log.warning(
                            "batch.processo.antigo_sem_gdrive",
                            processo=numero,
                            note="Processo antigo sem link do Google Drive; tentando MNI apenas",
                        )

                # ── MNI SOAP: Fase 1 (metadados) ──
                ps.phase = "mni_metadata"
                ps.phase_detail = "Consultando metadados via MNI SOAP"
                progress.save()
                result: MNIResult = await client.consultar_processo(
                    numero,
                    incluir_documentos=True,
                    incluir_cabecalho=True,
                )

                if not result.success:
                    # Se já baixou do GDrive, não é falha total
                    if all_files:
                        log.warning(
                            "batch.processo.mni_failed_gdrive_ok",
                            processo=numero,
                            error=result.error,
                            gdrive_docs=len(all_files),
                        )
                        ps.docs_baixados = len(all_files)
                        ps.tamanho_bytes = sum(f["tamanhoBytes"] for f in all_files)
                        ps.status = "done"
                        ps.phase = "done"
                        ps.phase_detail = (
                            f"GDrive OK ({len(all_files)} docs), MNI falhou"
                        )
                        ps.fim = time.monotonic()
                        progress.save(force=True)
                        return
                    else:
                        ps.status = "failed"
                        ps.phase = "failed"
                        ps.phase_detail = result.error or "consulta falhou"
                        ps.erro = result.error or "consulta falhou"
                        ps.fim = time.monotonic()
                        log.warning(
                            "batch.processo.failed",
                            processo=numero,
                            error=ps.erro,
                        )
                        progress.save(force=True)
                        return

                total_docs = len(result.processo.documentos)
                total_vinc = sum(len(d.vinculados) for d in result.processo.documentos)
                ps.total_docs = total_docs + (total_vinc if incluir_anexos else 0)

                # ── MNI SOAP: Fase 2 (download) ──
                ps.phase = "mni_download"
                ps.phase_detail = f"Baixando {ps.total_docs} docs via MNI SOAP"
                progress.save()
                mni_files = await client.download_documentos(
                    result.processo,
                    proc_dir,
                    batch_size=batch_size,
                    incluir_anexos=incluir_anexos,
                )
                all_files.extend(mni_files)

                ps.docs_baixados = len(all_files)
                ps.tamanho_bytes = sum(f["tamanhoBytes"] for f in all_files)
                ps.status = "done"
                ps.phase = "done"
                ps.phase_detail = f"{ps.docs_baixados} docs, {round(ps.tamanho_bytes / 1024 / 1024, 1)} MB"
                ps.fim = time.monotonic()

                log.info(
                    "batch.processo.done",
                    processo=numero,
                    docs=ps.docs_baixados,
                    size_mb=round(ps.tamanho_bytes / 1024 / 1024, 2),
                    duracao_s=ps.duracao_s,
                    is_antigo=is_processo_antigo(numero),
                )

            except Exception as exc:
                ps.status = "failed"
                ps.phase = "failed"
                ps.phase_detail = str(exc)[:100]
                ps.erro = str(exc)
                ps.fim = time.monotonic()
                log.error(
                    "batch.processo.error",
                    processo=numero,
                    error=str(exc),
                )

            progress.save(force=True)

            # Pausa de cortesia antes de liberar o slot do semáforo
            await asyncio.sleep(delay_entre_processos)

    tasks = [
        _download_one(i, numero, ps)
        for i, (numero, ps) in enumerate(progress.processos.items())
    ]
    await asyncio.gather(*tasks)

    elapsed = time.monotonic() - start_time

    # Relatório final
    total_bytes = sum(ps.tamanho_bytes for ps in progress.processos.values())
    total_docs = sum(ps.docs_baixados for ps in progress.processos.values())
    log.info(
        "batch.complete",
        total_processos=progress.total,
        done=progress.done,
        failed=progress.failed,
        total_docs=total_docs,
        total_mb=round(total_bytes / 1024 / 1024, 2),
        elapsed_s=round(elapsed, 1),
    )

    # Salvar relatório final
    report = {
        "completed_at": datetime.now(UTC).isoformat(),
        "elapsed_seconds": round(elapsed, 1),
        "total_processos": progress.total,
        "done": progress.done,
        "failed": progress.failed,
        "total_documentos": total_docs,
        "total_bytes": total_bytes,
        "incluir_anexos": incluir_anexos,
        "processos": {
            num: {
                "status": ps.status,
                "phase": ps.phase,
                "phase_detail": ps.phase_detail,
                "docs": ps.docs_baixados,
                "bytes": ps.tamanho_bytes,
                "erro": ps.erro,
                "duracao_s": ps.duracao_s,
            }
            for num, ps in progress.processos.items()
        },
    }
    report_path = output_dir / "_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info("batch.report_saved", path=str(report_path))

    return progress


# ─────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────


def _load_env():
    """Carrega .env do projeto."""
    from config import load_env

    load_env()


def main():
    parser = argparse.ArgumentParser(
        description="Download em lote de processos PJe via MNI SOAP",
    )
    parser.add_argument(
        "--input",
        "-i",
        help="Arquivo com lista de processos (CSV, JSON ou TXT)",
    )
    parser.add_argument(
        "--processos",
        "-p",
        help="Números de processo separados por vírgula",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="./downloads",
        help="Diretório de saída (padrão: ./downloads)",
    )
    parser.add_argument(
        "--skip-anexos",
        action="store_true",
        help="Não baixar documentos vinculados (anexos)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="Docs por chamada SOAP (padrão: 5)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Pausa em segundos entre processos (padrão: 2.0)",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Não retomar de execução anterior",
    )
    parser.add_argument(
        "--gdrive-map",
        help="Arquivo JSON com mapa {numero_processo: gdrive_url} para processos antigos",
    )

    args = parser.parse_args()

    # Carregar env
    _load_env()

    # Configurar logging
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
    )

    # Obter lista de processos
    numeros: list[str] = []
    if args.input:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"ERRO: arquivo não encontrado: {args.input}")
            sys.exit(1)
        numeros = load_processos_from_file(input_path)
    elif args.processos:
        numeros = [
            n.strip().strip('"').strip("'")
            for n in args.processos.split(",")
            if n.strip()
        ]
    else:
        print("ERRO: forneça --input ou --processos")
        parser.print_help()
        sys.exit(1)

    if not numeros:
        print("ERRO: nenhum número de processo encontrado")
        sys.exit(1)

    # Carregar mapa de Google Drive (processos antigos)
    gdrive_map: dict[str, str] = {}
    if args.gdrive_map:
        gmap_path = Path(args.gdrive_map)
        if gmap_path.exists():
            gdrive_map = json.loads(gmap_path.read_text(encoding="utf-8"))
            print(f"Google Drive map: {len(gdrive_map)} processos com link")
        else:
            print(f"AVISO: arquivo gdrive-map não encontrado: {args.gdrive_map}")

    # Detectar processos antigos
    from gdrive_downloader import is_processo_antigo

    antigos = [n for n in numeros if is_processo_antigo(n)]

    print(f"Processos a baixar: {len(numeros)}")
    if antigos:
        print(f"Processos antigos (escaneados): {len(antigos)}")
        sem_link = [n for n in antigos if n not in gdrive_map]
        if sem_link:
            print(f"  AVISO: {len(sem_link)} antigos SEM link do Google Drive:")
            for n in sem_link:
                print(f"    - {n}")
    print(f"Output: {Path(args.output).resolve()}")
    print(f"Anexos: {'SIM' if not args.skip_anexos else 'NÃO'}")
    print()

    # Executar
    progress = asyncio.run(
        download_batch(
            numeros=numeros,
            output_dir=Path(args.output),
            incluir_anexos=not args.skip_anexos,
            batch_size=args.batch_size,
            delay_entre_processos=args.delay,
            resume=not args.no_resume,
            gdrive_url_map=gdrive_map if gdrive_map else None,
        )
    )

    # Resumo final
    print()
    print("=" * 60)
    print(f"CONCLUÍDO: {progress.done}/{progress.total} processos")
    if progress.failed:
        print(f"FALHAS: {progress.failed}")
        for num, ps in progress.processos.items():
            if ps.status == "failed":
                print(f"  ✗ {num}: {ps.erro}")
    print("=" * 60)


if __name__ == "__main__":
    main()
