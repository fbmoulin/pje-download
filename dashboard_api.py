"""
Dashboard API — Backend para controle da automação de download PJe.
===================================================================

Servidor HTTP leve (aiohttp) que expõe endpoints REST para:
  - Submeter processos para download
  - Acompanhar progresso em tempo real
  - Consultar histórico de batches
  - Estatísticas gerais

Uso:
    python dashboard_api.py [--port 8007] [--output ./downloads]

Endpoints:
    GET  /api/status          → Status geral do worker
    POST /api/download        → Submeter processos para download
    GET  /api/progress        → Progresso do batch atual
    GET  /api/history         → Histórico de batches anteriores
    GET  /api/batch/:id       → Detalhes de um batch específico
    GET  /                    → Serve a dashboard HTML
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path

import aiohttp
from aiohttp import web
import structlog

log: structlog.BoundLogger = structlog.get_logger("kratos.dashboard-api")

# ─────────────────────────────────────────────
# CONFIGURAÇÃO
# ─────────────────────────────────────────────

from config import DASHBOARD_PORT as DEFAULT_PORT, DOWNLOAD_BASE_DIR as DEFAULT_OUTPUT


# ─────────────────────────────────────────────
# ESTADO GLOBAL
# ─────────────────────────────────────────────


@dataclass
class BatchJob:
    """Representa um batch de download submetido."""

    id: str
    processos: list[str]
    status: str = "queued"  # queued | running | done | failed
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    output_dir: str = ""
    include_anexos: bool = True
    gdrive_map: dict[str, str] = field(default_factory=dict)
    progress: dict = field(default_factory=dict)
    error: str | None = None


MAX_BATCH_SIZE = 500  # máximo de processos por batch


class DashboardState:
    """Estado global da dashboard — batches, progresso, histórico."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.batches: dict[str, BatchJob] = {}
        self.current_batch_id: str | None = None
        self._task: asyncio.Task | None = None
        self._progress_cache: dict | None = None
        self._progress_cache_time: float = 0.0
        self._load_history()

    def _load_history(self):
        """Carrega histórico de batches anteriores dos _report.json em disco."""
        if not self.output_dir.exists():
            return
        for report_file in self.output_dir.glob("*/_report.json"):
            try:
                data = json.loads(report_file.read_text(encoding="utf-8"))
                batch_id = report_file.parent.name
                job = BatchJob(
                    id=batch_id,
                    processos=list(data.get("processos", {}).keys()),
                    status="done",
                    finished_at=data.get("completed_at", ""),
                    output_dir=str(report_file.parent),
                    progress=data,
                )
                self.batches[batch_id] = job
            except Exception:
                pass

    async def submit_batch(
        self,
        processos: list[str],
        include_anexos: bool = True,
        gdrive_map: dict[str, str] | None = None,
    ) -> BatchJob:
        """Submete um novo batch de download."""
        batch_id = (
            datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        )
        batch_dir = self.output_dir / batch_id

        job = BatchJob(
            id=batch_id,
            processos=processos,
            status="queued",
            created_at=datetime.now(UTC).isoformat(),
            output_dir=str(batch_dir),
            include_anexos=include_anexos,
            gdrive_map=gdrive_map or {},
        )
        self.batches[batch_id] = job

        # Iniciar download em background
        self._task = asyncio.create_task(self._run_batch(job))
        self.current_batch_id = batch_id

        log.info(
            "dashboard.batch.submitted", batch_id=batch_id, processos=len(processos)
        )
        return job

    async def _run_batch(self, job: BatchJob):
        """Executa o batch downloader em background."""
        try:
            job.status = "running"
            job.started_at = datetime.now(UTC).isoformat()

            from batch_downloader import _load_env, download_batch

            _load_env()

            progress = await download_batch(
                numeros=job.processos,
                output_dir=Path(job.output_dir),
                incluir_anexos=job.include_anexos,
                gdrive_url_map=job.gdrive_map if job.gdrive_map else None,
            )

            job.finished_at = datetime.now(UTC).isoformat()
            job.progress = {
                "total": progress.total,
                "done": progress.done,
                "failed": progress.failed,
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
                    for num, ps in progress.processos.items()
                },
            }

            # Determine final status based on actual results
            if progress.failed > 0 and progress.done == 0:
                job.status = "failed"
                # Collect first error for display
                first_err = next(
                    (ps.erro for ps in progress.processos.values() if ps.erro),
                    "All processes failed",
                )
                job.error = first_err
            elif progress.failed > 0:
                job.status = "done"  # partial success
                job.error = f"{progress.failed}/{progress.total} processos falharam"
            else:
                job.status = "done"

            log.info(
                "dashboard.batch.complete",
                batch_id=job.id,
                status=job.status,
                done=progress.done,
                failed=progress.failed,
            )

        except Exception as exc:
            # Recover partial progress if available
            progress_file = Path(job.output_dir) / "_progress.json"
            if progress_file.exists():
                try:
                    job.progress = json.loads(progress_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
            job.status = "failed"
            job.error = str(exc)
            job.finished_at = datetime.now(UTC).isoformat()
            log.error("dashboard.batch.failed", batch_id=job.id, error=str(exc))

    def get_current_progress(self) -> dict | None:
        """Retorna progresso do batch atual — em memória (TTL 1s) durante execução."""
        if not self.current_batch_id:
            return None
        job = self.batches.get(self.current_batch_id)
        if not job:
            return None

        # Se já terminou, retornar progresso final (já em memória)
        if job.status in ("done", "failed"):
            self._progress_cache = None  # limpar cache ao terminar
            return {"batch_id": job.id, "status": job.status, **job.progress}

        # Durante execução: servir do cache em memória (TTL 1s)
        now = time.monotonic()
        if self._progress_cache is not None and (now - self._progress_cache_time) < 1.0:
            return {"batch_id": job.id, "status": "running", **self._progress_cache}

        # Cache expirado — ler do disco e atualizar cache
        progress_file = Path(job.output_dir) / "_progress.json"
        if progress_file.exists():
            try:
                data = json.loads(progress_file.read_text(encoding="utf-8"))
                self._progress_cache = data
                self._progress_cache_time = now
                return {"batch_id": job.id, "status": "running", **data}
            except Exception:
                pass

        return {"batch_id": job.id, "status": job.status, "processos": {}}


# ─────────────────────────────────────────────
# HANDLERS HTTP
# ─────────────────────────────────────────────

state: DashboardState | None = None


async def handle_status(request: web.Request) -> web.Response:
    """GET /api/status — Status geral incluindo health do worker."""
    if state is None:
        return web.json_response({"error": "Service not initialized"}, status=503)
    current = state.get_current_progress()

    # Consultar health do worker (:8006) — falha graciosamente se indisponível
    worker_status = "unknown"
    try:
        timeout = aiohttp.ClientTimeout(total=2)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get("http://localhost:8006/health") as resp:
                if resp.status == 200:
                    worker_data = await resp.json()
                    worker_status = worker_data.get("status", "unknown")
                else:
                    worker_status = "unhealthy"
    except Exception:
        worker_status = "unreachable"

    return web.json_response(
        {
            "service": "pje-dashboard",
            "status": "running",
            "total_batches": len(state.batches),
            "current_batch": state.current_batch_id,
            "current_status": current["status"] if current else "idle",
            "output_dir": str(state.output_dir.resolve()),
            "worker_status": worker_status,
        }
    )


async def handle_download(request: web.Request) -> web.Response:
    """POST /api/download — Submeter processos para download."""
    if state is None:
        return web.json_response({"error": "Service not initialized"}, status=503)
    try:
        body = await request.json()
        # aiohttp pode retornar string se content-type não for detectado
        if isinstance(body, str):
            body = json.loads(body)
    except Exception:
        return web.json_response({"error": "JSON inválido"}, status=400)

    if not isinstance(body, dict):
        return web.json_response({"error": "JSON deve ser um objeto"}, status=400)

    processos_raw = body.get("processos", [])
    if isinstance(processos_raw, str):
        processos_raw = [p.strip() for p in processos_raw.split(",") if p.strip()]

    if not processos_raw:
        return web.json_response({"error": "Nenhum processo informado"}, status=400)

    # Validar formato dos números
    from config import is_valid_processo

    processos = []
    invalidos = []
    for p in processos_raw:
        p = p.strip().strip('"').strip("'")
        if not p:
            continue
        if is_valid_processo(p):
            processos.append(p)
        else:
            invalidos.append(p)

    if invalidos:
        log.warning("dashboard.invalid_processos", invalidos=invalidos)

    if not processos:
        return web.json_response(
            {"error": "Nenhum processo com formato CNJ válido", "invalidos": invalidos},
            status=400,
        )

    if len(processos) > MAX_BATCH_SIZE:
        return web.json_response(
            {
                "error": f"Máximo de {MAX_BATCH_SIZE} processos por batch (enviado: {len(processos)})",
            },
            status=422,
        )

    # Verificar se já há batch em execução
    if state.current_batch_id:
        current = state.batches.get(state.current_batch_id)
        if current and current.status == "running":
            return web.json_response(
                {
                    "error": "Já existe um batch em execução",
                    "batch_id": state.current_batch_id,
                },
                status=409,
            )

    gdrive_map = body.get("gdrive_map", {})
    include_anexos = body.get("include_anexos", True)

    job = await state.submit_batch(processos, include_anexos, gdrive_map)

    return web.json_response(
        {
            "batch_id": job.id,
            "processos": len(job.processos),
            "status": job.status,
        },
        status=201,
    )


async def handle_progress(request: web.Request) -> web.Response:
    """GET /api/progress — Progresso do batch atual."""
    if state is None:
        return web.json_response({"error": "Service not initialized"}, status=503)
    current = state.get_current_progress()
    if not current:
        return web.json_response(
            {"status": "idle", "message": "Nenhum batch em execução"}
        )
    return web.json_response(current)


async def handle_history(request: web.Request) -> web.Response:
    """GET /api/history — Histórico de todos os batches."""
    if state is None:
        return web.json_response({"error": "Service not initialized"}, status=503)
    history = []
    for batch_id, job in sorted(state.batches.items(), reverse=True):
        total_docs = 0
        total_bytes = 0
        if job.progress and "processos" in job.progress:
            procs = job.progress["processos"]
            if isinstance(procs, dict):
                for p_info in procs.values():
                    if isinstance(p_info, dict):
                        total_docs += p_info.get("docs_baixados", p_info.get("docs", 0))
                        total_bytes += p_info.get(
                            "tamanho_bytes", p_info.get("bytes", 0)
                        )
        history.append(
            {
                "batch_id": batch_id,
                "processos": len(job.processos),
                "status": job.status,
                "created_at": job.created_at,
                "finished_at": job.finished_at,
                "total_docs": total_docs,
                "total_bytes": total_bytes,
                "error": job.error,
            }
        )
    return web.json_response(history)


async def handle_batch_detail(request: web.Request) -> web.Response:
    """GET /api/batch/{id} — Detalhes de um batch específico."""
    if state is None:
        return web.json_response({"error": "Service not initialized"}, status=503)
    batch_id = request.match_info["id"]
    job = state.batches.get(batch_id)
    if not job:
        return web.json_response({"error": "Batch não encontrado"}, status=404)

    # Se batch em execução, ler progresso em tempo real
    if job.status == "running":
        progress_file = Path(job.output_dir) / "_progress.json"
        if progress_file.exists():
            try:
                live = json.loads(progress_file.read_text(encoding="utf-8"))
                job.progress = live
            except Exception:
                pass

    return web.json_response(
        {
            "batch_id": job.id,
            "processos": job.processos,
            "status": job.status,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "output_dir": job.output_dir,
            "include_anexos": job.include_anexos,
            "gdrive_map": job.gdrive_map,
            "progress": job.progress,
            "error": job.error,
        }
    )


async def handle_index(request: web.Request) -> web.Response:
    """GET / — Serve a dashboard HTML."""
    html_path = Path(__file__).parent / "dashboard.html"
    if html_path.exists():
        return web.Response(
            text=html_path.read_text(encoding="utf-8"),
            content_type="text/html",
        )
    return web.Response(text="Dashboard HTML não encontrado", status=404)


# ─────────────────────────────────────────────
# APP SETUP
# ─────────────────────────────────────────────


async def _on_cleanup(app: web.Application) -> None:
    """Cancela batch em execução ao encerrar o servidor."""
    if state and state._task and not state._task.done():
        state._task.cancel()
        try:
            await state._task
        except asyncio.CancelledError:
            pass
        log.info("dashboard.shutdown.batch_cancelled")


def create_app(output_dir: Path) -> web.Application:
    """Cria a aplicação aiohttp."""
    global state
    state = DashboardState(output_dir)

    app = web.Application()
    # Middleware stack (order matters: CORS first, then rate limit)
    app.middlewares.append(cors_middleware)
    app.middlewares.append(rate_limit_middleware)

    app.router.add_get("/", handle_index)
    app.router.add_get("/api/status", handle_status)
    app.router.add_post("/api/download", handle_download)
    app.router.add_get("/api/progress", handle_progress)
    app.router.add_get("/api/history", handle_history)
    app.router.add_get("/api/batch/{id}", handle_batch_detail)

    # Serve static files (CSS, JS)
    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.router.add_static("/static", static_dir, show_index=False)

    app.on_cleanup.append(_on_cleanup)

    return app


# ─────────────────────────────────────────────
# RATE LIMITING (in-memory, per-IP)
# ─────────────────────────────────────────────

_rate_buckets: dict[str, list[float]] = {}
_rate_bucket_last_seen: dict[str, float] = {}
RATE_LIMIT_MAX = 10  # max requests
RATE_LIMIT_WINDOW = 60.0  # per N seconds
_BUCKET_EXPIRE = 300.0  # purge IPs inactive for 5 minutes

# Restrict CORS to localhost only — this service handles sensitive judicial docs
_ALLOWED_ORIGINS = {
    "http://localhost",
    "http://127.0.0.1",
    "http://localhost:8007",
    "http://127.0.0.1:8007",
}


def _purge_stale_buckets(now: float) -> None:
    """Remove buckets for IPs that haven't been seen in _BUCKET_EXPIRE seconds."""
    stale = [
        ip for ip, last in _rate_bucket_last_seen.items() if now - last > _BUCKET_EXPIRE
    ]
    for ip in stale:
        _rate_buckets.pop(ip, None)
        _rate_bucket_last_seen.pop(ip, None)


@web.middleware
async def rate_limit_middleware(request: web.Request, handler):
    """Simple sliding-window rate limiter for POST endpoints."""
    if request.method != "POST":
        return await handler(request)

    ip = request.remote or "unknown"
    now = time.monotonic()

    # Periodic cleanup of stale buckets (every ~100 requests on average)
    if len(_rate_buckets) > 50:
        _purge_stale_buckets(now)

    bucket = _rate_buckets.setdefault(ip, [])
    _rate_bucket_last_seen[ip] = now
    # Prune old entries
    bucket[:] = [t for t in bucket if now - t < RATE_LIMIT_WINDOW]
    if len(bucket) >= RATE_LIMIT_MAX:
        return web.json_response(
            {
                "error": f"Rate limit exceeded ({RATE_LIMIT_MAX}/{RATE_LIMIT_WINDOW:.0f}s)"
            },
            status=429,
        )
    bucket.append(now)
    return await handler(request)


@web.middleware
async def cors_middleware(request: web.Request, handler):
    """Middleware para CORS — restringe a origens localhost."""
    origin = request.headers.get("Origin", "")
    allow_origin = origin if origin in _ALLOWED_ORIGINS else "http://localhost"

    if request.method == "OPTIONS":
        return web.Response(
            headers={
                "Access-Control-Allow-Origin": allow_origin,
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
                "Vary": "Origin",
            }
        )
    response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = allow_origin
    response.headers["Vary"] = "Origin"
    return response


# ─────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────


def _load_env():
    """Carrega .env do projeto."""
    from config import load_env

    load_env()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Dashboard API para PJe Worker")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Porta HTTP")
    parser.add_argument(
        "--output", default=str(DEFAULT_OUTPUT), help="Diretório de downloads"
    )
    args = parser.parse_args()

    _load_env()

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
    )

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    app = create_app(output_dir)
    print(f"Dashboard API rodando em http://localhost:{args.port}")
    print(f"Downloads em: {output_dir.resolve()}")
    web.run_app(app, host="0.0.0.0", port=args.port, print=None)


if __name__ == "__main__":
    main()
