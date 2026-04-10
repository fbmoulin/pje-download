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
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path

import aiohttp
from aiohttp import web
import redis.asyncio as redis
import structlog

log: structlog.BoundLogger = structlog.get_logger("kratos.dashboard-api")

# ─────────────────────────────────────────────
# CONFIGURAÇÃO
# ─────────────────────────────────────────────

from config import (
    APP_ENV,
    DASHBOARD_PORT as DEFAULT_PORT,
    DOWNLOAD_BASE_DIR as DEFAULT_OUTPUT,
    REDIS_URL,
    HEALTH_PORT as WORKER_HEALTH_PORT,
    WORKER_HEALTH_HOST,
    TRUST_X_FORWARDED_FOR,
    atomic_write_text,
    sanitize_filename,
)


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
MAX_BATCH_HISTORY = 100  # max completed batches kept in memory
RESULT_WAIT_TIMEOUT_SECS = 360


class DashboardState:
    """Estado global da dashboard — batches, progresso, histórico."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.batches: dict[str, BatchJob] = {}
        self.current_batch_id: str | None = None
        self._task: asyncio.Task | None = None
        self._progress_cache: dict | None = None
        self._progress_cache_time: float = 0.0
        self._worker_http: aiohttp.ClientSession | None = None
        self._redis: redis.Redis | None = None
        self._load_history()

    async def get_redis(self) -> redis.Redis:
        """Lazily create a Redis client used by the dashboard control plane."""
        if self._redis is None:
            self._redis = redis.from_url(REDIS_URL, decode_responses=True)
        await self._redis.ping()
        return self._redis

    def get_worker_http(self) -> aiohttp.ClientSession:
        """Reuse a single HTTP session for worker health polling."""
        if self._worker_http is None or self._worker_http.closed:
            timeout = aiohttp.ClientTimeout(total=2)
            self._worker_http = aiohttp.ClientSession(timeout=timeout)
        return self._worker_http

    async def close(self) -> None:
        """Release reusable resources held by dashboard state."""
        if self._worker_http is not None and not self._worker_http.closed:
            await self._worker_http.close()
        self._worker_http = None
        if self._redis is not None:
            await self._redis.close()
        self._redis = None

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
            except Exception as exc:
                log.warning(
                    "dashboard.history.load_failed",
                    file=str(report_file),
                    error=str(exc),
                )
        self._evict_old_batches()

    def _evict_old_batches(self) -> None:
        """Remove oldest completed batches when history exceeds limit."""
        completed = [
            (bid, job)
            for bid, job in self.batches.items()
            if job.status in ("done", "failed") and bid != self.current_batch_id
        ]
        if len(completed) <= MAX_BATCH_HISTORY:
            return
        completed.sort(key=lambda x: x[1].finished_at or "")
        to_remove = len(completed) - MAX_BATCH_HISTORY
        for bid, _ in completed[:to_remove]:
            del self.batches[bid]
        log.info("dashboard.evicted_batches", count=to_remove)

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

        batch_dir.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._run_batch(job))
        self.current_batch_id = batch_id

        log.info(
            "dashboard.batch.submitted", batch_id=batch_id, processos=len(processos)
        )
        return job

    def _progress_path(self, job: BatchJob) -> Path:
        return Path(job.output_dir) / "_progress.json"

    def _report_path(self, job: BatchJob) -> Path:
        return Path(job.output_dir) / "_report.json"

    def _result_queue(self, batch_id: str) -> str:
        return f"kratos:pje:results:{batch_id}"

    def _build_initial_progress(self, job: BatchJob) -> dict:
        processos = {
            numero: {
                "status": "queued",
                "phase": "waiting",
                "phase_detail": "Aguardando worker",
                "total_docs": 0,
                "docs_baixados": 0,
                "tamanho_bytes": 0,
                "erro": None,
                "duracao_s": None,
            }
            for numero in job.processos
        }
        return {
            "summary": {
                "total": len(job.processos),
                "done": 0,
                "failed": 0,
                "pending": len(job.processos),
            },
            "processos": processos,
        }

    def _persist_progress(self, job: BatchJob) -> None:
        Path(job.output_dir).mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self._progress_path(job),
            json.dumps(job.progress, ensure_ascii=False, indent=2),
        )

    def _persist_report(self, job: BatchJob) -> None:
        Path(job.output_dir).mkdir(parents=True, exist_ok=True)
        report = {
            "batch_id": job.id,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.finished_at,
            "status": job.status,
            "include_anexos": job.include_anexos,
            "error": job.error,
            **job.progress,
        }
        atomic_write_text(
            self._report_path(job),
            json.dumps(report, ensure_ascii=False, indent=2),
        )

    def _batch_job_payload(self, job: BatchJob, numero_processo: str) -> dict:
        safe_name = sanitize_filename(numero_processo)
        return {
            "jobId": f"{job.id}:{uuid.uuid4().hex[:8]}",
            "batchId": job.id,
            "numeroProcesso": numero_processo,
            "includeAnexos": job.include_anexos,
            "replyQueue": self._result_queue(job.id),
            "outputSubdir": f"{job.id}/{safe_name}",
            "gdriveUrl": job.gdrive_map.get(numero_processo),
        }

    def _apply_result(self, job: BatchJob, result: dict) -> str:
        numero = result.get("numeroProcesso", "")
        files = result.get("arquivosDownloaded") or []
        docs_baixados = len(files)
        tamanho_bytes = sum(int(item.get("tamanhoBytes", 0) or 0) for item in files)
        worker_status = result.get("status", "failed")
        error = result.get("errorMessage")
        status = "done" if worker_status == "success" else "failed"
        phase = "done" if status == "done" else "failed"

        processos = job.progress.setdefault("processos", {})
        processos[numero] = {
            "status": status,
            "phase": phase,
            "phase_detail": error if status == "done" and error else None,
            "total_docs": docs_baixados,
            "docs_baixados": docs_baixados,
            "tamanho_bytes": tamanho_bytes,
            "erro": error if status == "failed" else None,
            "duracao_s": None,
        }

        done = sum(1 for proc in processos.values() if proc.get("status") == "done")
        failed = sum(1 for proc in processos.values() if proc.get("status") == "failed")
        total = len(job.processos)
        job.progress["summary"] = {
            "total": total,
            "done": done,
            "failed": failed,
            "pending": max(total - done - failed, 0),
        }
        return worker_status

    def _fail_remaining_processes(
        self,
        job: BatchJob,
        pending: set[str],
        error: str,
    ) -> None:
        processos = job.progress.setdefault("processos", {})
        for numero in pending:
            current = processos.get(numero, {})
            if current.get("status") in ("done", "failed"):
                continue
            processos[numero] = {
                "status": "failed",
                "phase": "failed",
                "phase_detail": error,
                "total_docs": 0,
                "docs_baixados": 0,
                "tamanho_bytes": 0,
                "erro": error,
                "duracao_s": None,
            }

        done = sum(1 for proc in processos.values() if proc.get("status") == "done")
        failed = sum(1 for proc in processos.values() if proc.get("status") == "failed")
        total = len(job.processos)
        job.progress["summary"] = {
            "total": total,
            "done": done,
            "failed": failed,
            "pending": max(total - done - failed, 0),
        }

    async def _run_batch(self, job: BatchJob):
        """Executa um batch publicando jobs Redis e agregando resultados do worker."""
        reply_queue = self._result_queue(job.id)
        payloads_by_processo = {
            numero: self._batch_job_payload(job, numero) for numero in job.processos
        }
        serialized_payloads = {
            numero: json.dumps(payload, ensure_ascii=False)
            for numero, payload in payloads_by_processo.items()
        }

        try:
            redis_client = await self.get_redis()
            job.status = "running"
            job.started_at = datetime.now(UTC).isoformat()

            job.progress = self._build_initial_progress(job)
            self._persist_progress(job)
            await redis_client.delete(reply_queue)
            if serialized_payloads:
                await redis_client.rpush(
                    "kratos:pje:jobs",
                    *serialized_payloads.values(),
                )

            pending = set(job.processos)
            last_result_at = time.monotonic()

            while pending:
                item = await redis_client.blpop(reply_queue, timeout=5)
                if not item:
                    if time.monotonic() - last_result_at > RESULT_WAIT_TIMEOUT_SECS:
                        timeout_error = (
                            f"Worker timeout: batch sem resultados por "
                            f"{RESULT_WAIT_TIMEOUT_SECS}s"
                        )
                        self._fail_remaining_processes(job, pending, timeout_error)
                        job.error = timeout_error
                        log.error(
                            "dashboard.batch.result_timeout",
                            batch_id=job.id,
                            pending=len(pending),
                        )
                        break
                    continue

                _, result_json = item
                result = json.loads(result_json)
                numero = result.get("numeroProcesso")
                if numero not in pending:
                    continue

                pending.remove(numero)
                last_result_at = time.monotonic()
                worker_status = self._apply_result(job, result)
                self._persist_progress(job)

                if worker_status in {"session_expired", "captcha_required"} and pending:
                    fatal_error = result.get("errorMessage") or worker_status
                    for pending_numero in pending:
                        await redis_client.lrem(
                            "kratos:pje:jobs",
                            0,
                            serialized_payloads[pending_numero],
                        )
                    self._fail_remaining_processes(job, pending, fatal_error)
                    job.error = fatal_error
                    pending.clear()
                    self._persist_progress(job)
                    break

            job.finished_at = datetime.now(UTC).isoformat()
            summary = job.progress.get("summary", {})
            done = int(summary.get("done", 0) or 0)
            failed = int(summary.get("failed", 0) or 0)

            if failed > 0 and done == 0:
                job.status = "failed"
                if not job.error:
                    first_err = next(
                        (
                            proc.get("erro")
                            for proc in job.progress.get("processos", {}).values()
                            if proc.get("erro")
                        ),
                        "All processes failed",
                    )
                    job.error = first_err
            elif failed > 0:
                job.status = "done"
                if not job.error:
                    job.error = f"{failed}/{len(job.processos)} processos falharam"
            else:
                job.status = "done"

            self._persist_progress(job)
            self._persist_report(job)

            log.info(
                "dashboard.batch.complete",
                batch_id=job.id,
                status=job.status,
                done=done,
                failed=failed,
            )
            self._evict_old_batches()

        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            job.finished_at = datetime.now(UTC).isoformat()
            if not job.progress:
                job.progress = self._build_initial_progress(job)
            self._persist_progress(job)
            self._persist_report(job)
            log.error("dashboard.batch.failed", batch_id=job.id, error=str(exc))
            self._evict_old_batches()
        finally:
            if self._redis is not None:
                try:
                    await self._redis.delete(reply_queue)
                except Exception:
                    pass

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
_batch_lock = asyncio.Lock()

# ── Session login state ──
_login_running: bool = False
_login_task: asyncio.Task | None = None
_login_last_ok: bool | None = None  # resultado do último login


async def handle_status(request: web.Request) -> web.Response:
    """GET /api/status — Status geral incluindo health do worker."""
    if state is None:
        return web.json_response({"error": "Service not initialized"}, status=503)
    current = state.get_current_progress()

    # Consultar health do worker (:8006) — falha graciosamente se indisponível
    worker_status = "unknown"
    try:
        sess = state.get_worker_http()
        async with sess.get(
            f"http://{WORKER_HEALTH_HOST}:{WORKER_HEALTH_PORT}/health"
        ) as resp:
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
            "output_dir": state.output_dir.name,
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

    # ── gdrive_map validation (BUG-10) — done BEFORE lock ──
    gdrive_map = body.get("gdrive_map", {})
    if not isinstance(gdrive_map, dict):
        return web.json_response({"error": "gdrive_map deve ser um objeto"}, status=400)
    if len(gdrive_map) > MAX_BATCH_SIZE:
        return web.json_response(
            {"error": f"gdrive_map excede limite de {MAX_BATCH_SIZE} entradas"},
            status=422,
        )
    # Validate each URL is a GDrive folder (prevents SSRF)
    from gdrive_downloader import extract_folder_id

    invalid_urls = [url for url in gdrive_map.values() if not extract_folder_id(url)]
    if invalid_urls:
        return web.json_response(
            {"error": "gdrive_map contém URLs inválidas", "invalid": invalid_urls[:3]},
            status=400,
        )

    include_anexos = body.get("include_anexos", True)

    # ── Check + submit under lock (BUG-3) ──
    async with _batch_lock:
        if state.current_batch_id:
            current = state.batches.get(state.current_batch_id)
            if current and current.status in ("queued", "running"):
                return web.json_response(
                    {
                        "error": "Já existe um batch em execução",
                        "batch_id": state.current_batch_id,
                    },
                    status=409,
                )

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


async def handle_session_status(request: web.Request) -> web.Response:
    """GET /api/session/status — Estado da sessão PJe salva em disco."""
    from pje_session import SESSION_FILE

    exists = SESSION_FILE.exists()
    modified_at: str | None = None
    if exists:
        import os

        modified_at = datetime.fromtimestamp(
            os.path.getmtime(SESSION_FILE), tz=UTC
        ).isoformat()

    return web.json_response(
        {
            "file_exists": exists,
            "login_running": _login_running,
            "last_login_ok": _login_last_ok,
            "modified_at": modified_at,
        }
    )


async def handle_session_verify(request: web.Request) -> web.Response:
    """POST /api/session/verify — Valida a sessão salva (abre browser headless)."""
    from pje_session import PJeSessionClient

    try:
        client = PJeSessionClient()
        valid = await client.is_valid()
        return web.json_response({"valid": valid})
    except FileNotFoundError:
        return web.json_response(
            {"valid": False, "error": "Sessão não encontrada"}, status=404
        )
    except Exception as exc:
        log.error("dashboard.session.verify_error", error=str(exc))
        return web.json_response(
            {"valid": False, "error": "Erro interno na verificação"}, status=500
        )


async def handle_session_login(request: web.Request) -> web.Response:
    """POST /api/session/login — Dispara login interativo no browser local."""
    global _login_running, _login_task, _login_last_ok

    if _login_running:
        return web.json_response({"error": "Login já em andamento"}, status=409)

    # Set flag BEFORE create_task to prevent TOCTOU race
    _login_running = True

    async def _do_login() -> None:
        global _login_running, _login_last_ok
        try:
            from pje_session import interactive_login

            ok = await interactive_login()
            _login_last_ok = ok
            log.info("dashboard.session.login_done", ok=ok)
            import audit
            import config

            audit.log_access(
                audit.AuditEntry(
                    event_type="session_login",
                    processo_numero="",
                    fonte="dashboard",
                    tribunal=config.MNI_TRIBUNAL,
                    status="success" if ok else "error",
                )
            )
        except Exception as exc:
            _login_last_ok = False
            log.error("dashboard.session.login_error", error=str(exc))
        finally:
            _login_running = False

    _login_task = asyncio.create_task(_do_login())
    return web.json_response(
        {"message": "Login iniciado — complete no browser que será aberto"}, status=202
    )


async def handle_metrics(request: web.Request) -> web.Response:
    """GET /metrics — Prometheus text exposition format."""
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    import metrics as m

    return web.Response(
        body=generate_latest(m.REGISTRY),
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )


async def handle_index(request: web.Request) -> web.Response:
    """GET / — Serve a dashboard HTML."""
    html_path = Path(__file__).parent / "dashboard.html"
    if html_path.exists():
        text = await asyncio.to_thread(html_path.read_text, "utf-8")
        return web.Response(text=text, content_type="text/html")
    return web.Response(text="Dashboard HTML não encontrado", status=404)


# ─────────────────────────────────────────────
# APP SETUP
# ─────────────────────────────────────────────


async def _on_cleanup(app: web.Application) -> None:
    """Cancela batch em execução ao encerrar o servidor. Saves progress first."""
    if state and state.current_batch_id:
        job = state.batches.get(state.current_batch_id)
        if job and job.progress:
            try:
                progress_path = Path(job.output_dir) / "_progress.json"
                from config import atomic_write_text

                atomic_write_text(
                    progress_path,
                    json.dumps(job.progress, ensure_ascii=False, indent=2),
                )
                log.info(
                    "dashboard.shutdown.progress_saved",
                    batch_id=state.current_batch_id,
                    path=str(progress_path),
                )
            except Exception as exc:
                log.warning("dashboard.shutdown.progress_save_failed", error=str(exc))

    if state and state._task and not state._task.done():
        state._task.cancel()
        try:
            await state._task
        except asyncio.CancelledError:
            pass
        log.info("dashboard.shutdown.batch_cancelled")

    if state:
        await state.close()


def _validate_runtime_config() -> None:
    """Fail fast on insecure production dashboard configuration."""
    from config import DASHBOARD_API_KEY

    if APP_ENV == "production" and not DASHBOARD_API_KEY:
        raise RuntimeError("DASHBOARD_API_KEY is required when APP_ENV=production")


def create_app(output_dir: Path) -> web.Application:
    """Cria a aplicação aiohttp."""
    global state
    _validate_runtime_config()
    state = DashboardState(output_dir)

    app = web.Application()
    # Middleware stack (order matters: CORS first, then rate limit, then auth)
    app.middlewares.append(cors_middleware)
    app.middlewares.append(rate_limit_middleware)
    app.middlewares.append(api_key_middleware)

    app.router.add_get("/", handle_index)
    app.router.add_get("/metrics", handle_metrics)
    app.router.add_get("/api/status", handle_status)
    app.router.add_post("/api/download", handle_download)
    app.router.add_get("/api/progress", handle_progress)
    app.router.add_get("/api/history", handle_history)
    app.router.add_get("/api/batch/{id}", handle_batch_detail)
    app.router.add_get("/api/session/status", handle_session_status)
    app.router.add_post("/api/session/login", handle_session_login)
    app.router.add_post("/api/session/verify", handle_session_verify)

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


def _get_rate_limit_ip(request: web.Request) -> str:
    """Resolve the client IP used by rate limiting.

    `X-Forwarded-For` is only trusted when explicitly enabled in config.
    """

    if TRUST_X_FORWARDED_FOR:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            candidate = forwarded.split(",")[0].strip()
            if candidate:
                return candidate
    return request.remote or "unknown"


@web.middleware
async def rate_limit_middleware(request: web.Request, handler):
    """Simple sliding-window rate limiter for POST endpoints."""
    if request.method != "POST":
        return await handler(request)

    ip = _get_rate_limit_ip(request)
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
async def api_key_middleware(request: web.Request, handler):
    """Require API key for mutating endpoints. Skipped when DASHBOARD_API_KEY is empty."""
    from config import DASHBOARD_API_KEY

    if not DASHBOARD_API_KEY:
        return await handler(request)  # No key configured = dev mode

    if request.method != "POST":
        return await handler(request)

    provided = request.headers.get("X-API-Key", "")
    if not provided or not hmac.compare_digest(provided, DASHBOARD_API_KEY):
        return web.json_response({"error": "Unauthorized"}, status=401)

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
                "Access-Control-Allow-Headers": "Content-Type, X-API-Key",
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
