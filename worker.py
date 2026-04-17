"""
PJe Session Worker
==================
Worker especializado para download semiassistido de processos do PJe.

Estratégia (em ordem de preferência):
  1. MNI SOAP (intercomunicação via WSDL — mais rápido, sem browser)
  2. API oficial REST do PJe (quando disponível)
  3. Login manual + browser automation via Playwright
  4. Encerramento gracioso quando sessão expira

Referência: https://docs.pje.jus.br/manuais-basicos/padroes-de-api-do-pje/
MNI WSDL TJES: https://sistemas.tjes.jus.br/pje/intercomunicacao?wsdl
Playwright auth: https://playwright.dev/docs/auth
Playwright downloads: https://playwright.dev/docs/downloads

AVISO: Este worker não tenta quebrar CAPTCHA.
O foco é REDUZIR a exposição ao CAPTCHA, não combatê-lo.
"""

import asyncio
import hashlib
import json
import random
import signal
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import redis.asyncio as redis
import structlog
from playwright.async_api import BrowserContext, Page, async_playwright

import metrics
from mni_client import MNIClient, MNIResult

log: structlog.BoundLogger = structlog.get_logger("kratos.pje-worker")

# ─────────────────────────────────────────────
# CONFIGURAÇÃO (centralizada em config.py)
# ─────────────────────────────────────────────

from config import (
    PJE_BASE_URL,
    SESSION_STATE_PATH,
    DOWNLOAD_BASE_DIR,
    SESSION_TIMEOUT_MINUTES,
    REDIS_URL,
    MAX_DOCS_PER_SESSION,
    DOWNLOAD_DELAY_SECS,
    HEALTH_PORT,
    HEALTH_BIND_HOST,
    CONCURRENT_DOWNLOADS,
    MNI_ENABLED,
    sha256_file,
    sanitize_filename,
    unique_path,
)

DOWNLOAD_BASE_DIR.mkdir(parents=True, exist_ok=True)
DEAD_LETTER_QUEUE = "kratos:pje:dead-letter"


def _unique_filename(directory: Path, filename: str) -> str:
    """Return a non-colliding filename in directory."""
    return unique_path(directory / filename).name


def _merge_downloaded_files(*groups: list[dict]) -> list[dict]:
    """Merge file metadata lists, preferring checksum-based deduplication."""
    merged: list[dict] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            key = (
                item.get("checksum")
                or f"{item.get('nome')}|{item.get('tamanhoBytes')}|{item.get('fonte')}"
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


# Padrões conhecidos de CAPTCHA no PJe
CAPTCHA_INDICATORS = [
    "captcha",
    "recaptcha",
    "g-recaptcha",
    "desafio de segurança",
    "challenge",
    "hcaptcha",
]


# ─────────────────────────────────────────────
# WORKER PRINCIPAL
# ─────────────────────────────────────────────


class PJeSessionWorker:
    """
    Worker de sessão PJe com 3 estratégias de download:
    1. MNI SOAP (sem browser, mais rápido)
    2. API REST do PJe (via browser autenticado)
    3. Browser automation com Playwright (fallback)
    """

    def __init__(self) -> None:
        self.redis: redis.Redis | None = None
        self._browser: Any | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.session_valid: bool = False
        self.fallback_ready: bool = False
        self.session_started_at: datetime | None = None
        self.mni_client: MNIClient | None = None
        self.docs_downloaded_count: int = 0
        self._health_status: str = "starting"
        self._last_error: str | None = None
        self._session_lock_fh: Any | None = None
        self._health_cache: dict | None = None
        self._health_cache_time: float = 0.0
        self._health_runner: Any | None = None

    def _acquire_session_lock(self) -> bool:
        """Acquire advisory lock on session state file (prevents multi-instance corruption)."""
        lock_path = SESSION_STATE_PATH.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import fcntl

            self._session_lock_fh = open(lock_path, "w")
            fcntl.flock(self._session_lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except ImportError:
            # fcntl unavailable (Windows) — proceed without locking
            log.warning("pje.session.lock_unavailable", reason="fcntl not available")
            return True
        except OSError as exc:
            # Lock held by another process — do NOT proceed
            log.error("pje.session.lock_held", path=str(lock_path), error=str(exc))
            if self._session_lock_fh:
                self._session_lock_fh.close()
                self._session_lock_fh = None
            return False

    def _release_session_lock(self) -> None:
        """Release session state file lock."""
        if self._session_lock_fh:
            try:
                import fcntl

                fcntl.flock(self._session_lock_fh.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            self._session_lock_fh.close()
            self._session_lock_fh = None

    async def init(self, max_redis_retries: int = 5) -> None:
        for attempt in range(max_redis_retries):
            try:
                self.redis = redis.from_url(REDIS_URL, decode_responses=True)
                await self.redis.ping()
                break
            except (redis.ConnectionError, redis.TimeoutError, OSError) as exc:
                if attempt == max_redis_retries - 1:
                    raise
                delay = min(2**attempt + random.uniform(0, 1), 30)
                log.warning(
                    "pje.redis.init_retry",
                    attempt=attempt + 1,
                    delay=round(delay, 1),
                    error=str(exc),
                )
                await asyncio.sleep(delay)

        # Inicializar cliente MNI se habilitado e credenciais configuradas
        if MNI_ENABLED:
            import os as _os

            if not _os.getenv("MNI_USERNAME") or not _os.getenv("MNI_PASSWORD"):
                log.error(
                    "pje.mni.credentials_missing",
                    reason="MNI_USERNAME ou MNI_PASSWORD não configurados — worker iniciará sem MNI",
                )
            else:
                try:
                    self.mni_client = MNIClient()
                    health = await self.mni_client.health_check()
                    if health["status"] == "healthy":
                        log.info(
                            "pje.mni.ready",
                            tribunal=health["tribunal"],
                            operations=health["operations"],
                            latency_ms=health["latency_ms"],
                        )
                    else:
                        # Keep the client alive — health check failure may be transient
                        # (e.g. WSDL fetch blocked at startup). Individual process
                        # requests will retry and produce proper per-request errors.
                        log.warning(
                            "pje.mni.unhealthy_at_startup",
                            error=health.get("error"),
                            note="client kept alive — will retry on first process request",
                        )
                except Exception as exc:
                    log.warning("pje.mni.init_failed", error=str(exc))
                    self.mni_client = None

        self._health_status = "ready"

    # ──────────────────────
    # SESSÃO PLAYWRIGHT
    # ──────────────────────

    async def load_session(self, playwright) -> bool:
        """
        Tenta carregar sessão autenticada salva.
        Se não existir ou estiver expirada, aguarda login manual.

        Se o MNI estiver disponível, Playwright é opcional (usado como fallback).
        """
        if self.mni_client is not None:
            # MNI disponível: não iniciar Chromium no boot. Isso evita depender
            # de browser visível quando o caminho principal já resolve o fluxo.
            self.session_valid = False
            self.fallback_ready = False
            self.session_started_at = datetime.now(UTC)
            log.info("pje.session.mni_available", note="playwright_deferred")
            return True

        self._browser = await playwright.chromium.launch(
            headless=False
        )  # headless=False para login manual

        if SESSION_STATE_PATH.exists() and self._acquire_session_lock():
            log.info("pje.session.loading", path=str(SESSION_STATE_PATH))
            self.context = await self._browser.new_context(
                storage_state=str(SESSION_STATE_PATH)
            )
            self.page = await self.context.new_page()

            # Verificar se sessão ainda é válida
            await self.page.goto(f"{PJE_BASE_URL}/login.seam")

            # Detectar CAPTCHA antes de verificar login
            if await self._detect_captcha():
                log.warning("pje.session.captcha_on_load")
                return self.mni_client is not None  # OK se MNI disponível

            if "login" not in self.page.url.lower():
                log.info("pje.session.reused")
                self.session_valid = True
                self.fallback_ready = True
                self.session_started_at = datetime.now(UTC)
                return True

            log.warning("pje.session.expired_on_load", reason="redirected_to_login")

        else:
            log.info("pje.session.not_found", action="awaiting_manual_login")
            self.context = await self._browser.new_context()
            self.page = await self.context.new_page()
            await self.page.goto(f"{PJE_BASE_URL}/login.seam")

        # Login manual: aguardar usuário fazer login na janela aberta
        log.info(
            "pje.session.manual_login_required",
            message="FAÇA LOGIN MANUALMENTE NA JANELA DO BROWSER",
        )

        try:
            await self.page.wait_for_url(
                lambda url: "login" not in url.lower(),
                timeout=300_000,  # 5 min
            )
        except Exception:
            log.error("pje.session.manual_login_timeout")
            # Se MNI está disponível, pode continuar sem browser
            return self.mni_client is not None

        # Salvar estado da sessão para reuso.
        # Lock may not be held yet (else-branch: no session file existed).
        # Only acquire if not already held to avoid opening a second file handle (H15).
        if self._session_lock_fh is None:
            self._acquire_session_lock()
        await self.context.storage_state(path=str(SESSION_STATE_PATH))
        log.info("pje.session.saved", path=str(SESSION_STATE_PATH))

        self.session_valid = True
        self.fallback_ready = True
        self.session_started_at = datetime.now(UTC)
        return True

    def is_session_expired(self) -> bool:
        """Verifica se a sessão atingiu o tempo limite operacional."""
        if not self.session_started_at:
            return True
        elapsed = (datetime.now(UTC) - self.session_started_at).total_seconds() / 60
        return elapsed > SESSION_TIMEOUT_MINUTES

    async def invalidate_session(self) -> None:
        """Remove sessão salva e fecha recursos do browser."""
        for resource in (self.page, self.context, self._browser):
            if resource:
                try:
                    await resource.close()
                except Exception:
                    pass
        self.page = None
        self.context = None
        self._browser = None
        self._release_session_lock()
        if SESSION_STATE_PATH.exists():
            SESSION_STATE_PATH.unlink()
        self.session_valid = False
        self.fallback_ready = False
        self.session_started_at = None
        log.info("pje.session.invalidated")

    # ──────────────────────
    # DETECÇÃO DE CAPTCHA
    # ──────────────────────

    async def _detect_captcha(self) -> bool:
        """
        Detecta se a página atual contém um desafio CAPTCHA.
        Retorna True se CAPTCHA for detectado.
        """
        if self.page is None:
            return False
        try:
            page_content = await self.page.content()
            content_lower = page_content.lower()
            for indicator in CAPTCHA_INDICATORS:
                if indicator in content_lower:
                    log.warning(
                        "pje.captcha.detected",
                        indicator=indicator,
                        url=self.page.url,
                    )
                    self._last_error = f"captcha_detected:{indicator}"
                    return True
        except Exception:
            pass
        return False

    # ──────────────────────
    # DOWNLOAD ORQUESTRADO
    # ──────────────────────

    async def download_process(self, job: dict) -> dict:
        """
        Baixa documentos de um processo judicial no PJe.

        Fluxo com 3 estratégias em cascata:
        1. MNI SOAP (se disponível) — mais rápido, sem browser
        2. API REST do PJe (via browser autenticado)
        3. Browser automation com Playwright (fallback)
        """
        job_id: str = job["jobId"]
        numero_processo: str = job["numeroProcesso"]
        tipos_documento: list | None = job.get("tiposDocumento")
        incluir_anexos: bool = job.get("includeAnexos", job.get("include_anexos", True))
        gdrive_url: str | None = job.get("gdriveUrl")

        self._health_status = "processing"

        safe_name = sanitize_filename(numero_processo)
        output_subdir = job.get("outputSubdir")
        output_dir = (
            DOWNLOAD_BASE_DIR / Path(output_subdir)
            if output_subdir
            else DOWNLOAD_BASE_DIR / safe_name
        )
        if not output_dir.resolve().is_relative_to(DOWNLOAD_BASE_DIR.resolve()):
            raise ValueError(
                f"Path traversal detected: {output_subdir or numero_processo}"
            )
        output_dir.mkdir(parents=True, exist_ok=True)

        downloaded_files: list[dict] = []
        anexos_pendentes = 0
        expected_total_docs = 0

        def _make_incremental_download_progress_cb(detail_prefix: str):
            base_docs = len(downloaded_files)
            base_bytes = sum(
                int(item.get("tamanhoBytes", 0) or 0) for item in downloaded_files
            )

            async def _callback(
                *,
                file_info: dict,
                completed: int,
                total: int,
                local_bytes: int,
            ) -> None:
                del file_info
                await self._publish_progress(
                    job,
                    "mni_download",
                    f"{detail_prefix}: {completed}/{max(total, completed)} docs",
                    total_docs=base_docs + max(total, completed),
                    docs_baixados=base_docs + completed,
                    tamanho_bytes=base_bytes + local_bytes,
                )

            return _callback

        try:
            log.info("pje.download.start", processo=numero_processo, job_id=job_id)
            await self._publish_progress(job, "starting", "Job recebido pelo worker")

            if gdrive_url:
                from gdrive_downloader import download_gdrive_folder

                await self._publish_progress(
                    job,
                    "gdrive",
                    "Baixando pasta Google Drive",
                    docs_baixados=len(downloaded_files),
                    tamanho_bytes=sum(
                        int(item.get("tamanhoBytes", 0) or 0)
                        for item in downloaded_files
                    ),
                )
                gdrive_dir = output_dir / "escaneados_gdrive"
                gdrive_files = await download_gdrive_folder(gdrive_url, gdrive_dir)
                downloaded_files = _merge_downloaded_files(
                    downloaded_files, gdrive_files or []
                )
                if gdrive_files:
                    await self._publish_progress(
                        job,
                        "gdrive",
                        f"GDrive: {len(gdrive_files)} docs",
                        docs_baixados=len(downloaded_files),
                        tamanho_bytes=sum(
                            int(item.get("tamanhoBytes", 0) or 0)
                            for item in downloaded_files
                        ),
                    )
                if (
                    downloaded_files
                    and self.mni_client is None
                    and (self.page is None or self.context is None)
                ):
                    warning = (
                        "Google Drive retornou arquivos, mas nao ha MNI nem sessao PJe "
                        "para complementar o processo"
                    )
                    await self._publish_progress(
                        job,
                        "partial",
                        warning,
                        status="partial",
                        docs_baixados=len(downloaded_files),
                        tamanho_bytes=sum(
                            int(item.get("tamanhoBytes", 0) or 0)
                            for item in downloaded_files
                        ),
                    )
                    await self._log_job_result(
                        job_id, numero_processo, downloaded_files
                    )
                    self._health_status = "ready"
                    return self._result(
                        job_id,
                        numero_processo,
                        "partial_success",
                        downloaded_files,
                        error=warning,
                    )

            # ── ESTRATÉGIA 1: MNI SOAP ──
            if self.mni_client is not None:
                await self._publish_progress(
                    job,
                    "mni_metadata",
                    "Consultando metadados via MNI SOAP",
                    docs_baixados=len(downloaded_files),
                    tamanho_bytes=sum(
                        int(item.get("tamanhoBytes", 0) or 0)
                        for item in downloaded_files
                    ),
                )
                mni_result = await self._try_mni_download(
                    numero_processo,
                    output_dir,
                    tipos_documento,
                    incluir_anexos=incluir_anexos,
                    progress_cb=_make_incremental_download_progress_cb("MNI SOAP"),
                )
                if len(mni_result) == 3:
                    mni_files, anexos_pendentes, expected_total_docs = mni_result
                else:
                    mni_files, anexos_pendentes = mni_result
                    expected_total_docs = len(mni_files or [])
                await self._publish_progress(
                    job,
                    "mni_metadata",
                    f"Metadados MNI: {expected_total_docs} docs esperados",
                    total_docs=max(expected_total_docs, len(downloaded_files)),
                    docs_baixados=len(downloaded_files),
                    tamanho_bytes=sum(
                        int(item.get("tamanhoBytes", 0) or 0)
                        for item in downloaded_files
                    ),
                )
                if mni_files:
                    downloaded_files = _merge_downloaded_files(
                        downloaded_files, mni_files
                    )
                    await self._publish_progress(
                        job,
                        "mni_download",
                        f"Documentos baixados: {len(downloaded_files)}",
                        total_docs=max(expected_total_docs, len(downloaded_files)),
                        docs_baixados=len(downloaded_files),
                        tamanho_bytes=sum(
                            int(item.get("tamanhoBytes", 0) or 0)
                            for item in downloaded_files
                        ),
                    )
                    log.info(
                        "pje.download.mni_success",
                        count=len(downloaded_files),
                        processo=numero_processo,
                        anexos_pendentes=anexos_pendentes,
                    )
                    if not anexos_pendentes:
                        await self._log_job_result(
                            job_id, numero_processo, downloaded_files
                        )
                        self._health_status = "ready"
                        await self._publish_progress(
                            job,
                            "done",
                            f"Concluído: {len(downloaded_files)} docs",
                            total_docs=max(expected_total_docs, len(downloaded_files)),
                            docs_baixados=len(downloaded_files),
                            tamanho_bytes=sum(
                                int(item.get("tamanhoBytes", 0) or 0)
                                for item in downloaded_files
                            ),
                        )
                        return self._result(
                            job_id, numero_processo, "success", downloaded_files
                        )

            # ── Estratégias 2 e 3 precisam de sessão Playwright ──
            if anexos_pendentes and (self.page is None or self.context is None):
                warning = (
                    f"MNI baixou documentos principais, mas {anexos_pendentes} anexo(s) "
                    "permanecem pendentes sem sessão PJe disponível"
                )
                await self._publish_progress(
                    job,
                    "partial",
                    warning,
                    status="partial",
                    total_docs=max(expected_total_docs, len(downloaded_files)),
                    docs_baixados=len(downloaded_files),
                    tamanho_bytes=sum(
                        int(item.get("tamanhoBytes", 0) or 0)
                        for item in downloaded_files
                    ),
                )
                log.warning(
                    "pje.download.anexos_pending",
                    processo=numero_processo,
                    anexos_pendentes=anexos_pendentes,
                )
                await self._log_job_result(job_id, numero_processo, downloaded_files)
                self._health_status = "ready"
                return self._result(
                    job_id,
                    numero_processo,
                    "partial_success",
                    downloaded_files,
                    error=warning,
                )

            if self.is_session_expired():
                log.warning("pje.download.session_expired", job_id=job_id)
                self._health_status = "session_expired"
                return self._result(job_id, numero_processo, "session_expired")

            # ── ESTRATÉGIA 2: API REST ──
            await self._publish_progress(
                job,
                "mni_download",
                "Tentando API REST autenticada",
                docs_baixados=len(downloaded_files),
                tamanho_bytes=sum(
                    int(item.get("tamanhoBytes", 0) or 0) for item in downloaded_files
                ),
            )

            api_result = await self._try_official_api(
                numero_processo,
                output_dir,
                tipos_documento=tipos_documento,
                incluir_anexos=incluir_anexos,
                incluir_principais=not (downloaded_files and anexos_pendentes > 0),
                progress_cb=_make_incremental_download_progress_cb("API REST"),
            )
            if api_result:
                downloaded_files = _merge_downloaded_files(downloaded_files, api_result)
                await self._publish_progress(
                    job,
                    "mni_download",
                    f"Documentos baixados: {len(downloaded_files)}",
                    total_docs=max(expected_total_docs, len(downloaded_files)),
                    docs_baixados=len(downloaded_files),
                    tamanho_bytes=sum(
                        int(item.get("tamanhoBytes", 0) or 0)
                        for item in downloaded_files
                    ),
                )
                log.info("pje.download.api_success", count=len(downloaded_files))
            else:
                # ── ESTRATÉGIA 3: Browser automation ──
                # Verificar CAPTCHA antes de navegar
                if await self._detect_captcha():
                    self._health_status = "captcha_required"
                    await self._publish_progress(
                        job,
                        "failed",
                        "CAPTCHA detectado — intervenção manual necessária",
                        docs_baixados=len(downloaded_files),
                        tamanho_bytes=sum(
                            int(item.get("tamanhoBytes", 0) or 0)
                            for item in downloaded_files
                        ),
                    )
                    return self._result(
                        job_id,
                        numero_processo,
                        "captcha_required",
                        error="CAPTCHA detectado — intervenção manual necessária",
                    )

                await self._publish_progress(
                    job,
                    "mni_download",
                    "Fallback no browser",
                    docs_baixados=len(downloaded_files),
                    tamanho_bytes=sum(
                        int(item.get("tamanhoBytes", 0) or 0)
                        for item in downloaded_files
                    ),
                )
                browser_files = await self._download_via_browser(
                    numero_processo,
                    output_dir,
                    tipos_documento,
                    allow_full_download=not (downloaded_files and anexos_pendentes > 0),
                    progress_cb=_make_incremental_download_progress_cb("Browser"),
                )
                if not browser_files:
                    if downloaded_files:
                        warning = (
                            "Google Drive retornou arquivos, mas PJe/MNI nao "
                            "retornou documentos complementares"
                        )
                        await self._log_job_result(
                            job_id, numero_processo, downloaded_files
                        )
                        self._health_status = "ready"
                        await self._publish_progress(
                            job,
                            "partial",
                            warning,
                            status="partial",
                            total_docs=max(expected_total_docs, len(downloaded_files)),
                            docs_baixados=len(downloaded_files),
                            tamanho_bytes=sum(
                                int(item.get("tamanhoBytes", 0) or 0)
                                for item in downloaded_files
                            ),
                        )
                        return self._result(
                            job_id,
                            numero_processo,
                            "partial_success",
                            downloaded_files,
                            error=warning,
                        )
                    self._health_status = "ready"
                    await self._publish_progress(
                        job,
                        "failed",
                        "Browser fallback indisponível ou sem arquivos",
                        total_docs=max(expected_total_docs, len(downloaded_files)),
                        docs_baixados=len(downloaded_files),
                        tamanho_bytes=sum(
                            int(item.get("tamanhoBytes", 0) or 0)
                            for item in downloaded_files
                        ),
                    )
                    return self._result(
                        job_id,
                        numero_processo,
                        "failed",
                        error="Browser fallback indisponível ou sem arquivos",
                    )
                downloaded_files = _merge_downloaded_files(
                    downloaded_files, browser_files
                )
                await self._publish_progress(
                    job,
                    "mni_download",
                    f"Documentos baixados: {len(downloaded_files)}",
                    total_docs=max(expected_total_docs, len(downloaded_files)),
                    docs_baixados=len(downloaded_files),
                    tamanho_bytes=sum(
                        int(item.get("tamanhoBytes", 0) or 0)
                        for item in downloaded_files
                    ),
                )
                log.info("pje.download.browser_success", count=len(downloaded_files))

            await self._log_job_result(job_id, numero_processo, downloaded_files)
            self._health_status = "ready"
            warning = None
            if anexos_pendentes and incluir_anexos:
                warning = (
                    f"MNI indicou {anexos_pendentes} anexo(s) vinculados; "
                    "resultado complementar via API/browser pode conter duplicatas filtradas"
                )
            await self._publish_progress(
                job,
                "done",
                warning or f"Concluído: {len(downloaded_files)} docs",
                total_docs=max(expected_total_docs, len(downloaded_files)),
                docs_baixados=len(downloaded_files),
                tamanho_bytes=sum(
                    int(item.get("tamanhoBytes", 0) or 0) for item in downloaded_files
                ),
            )
            return self._result(
                job_id,
                numero_processo,
                "success",
                downloaded_files,
                error=warning,
            )

        except Exception as e:
            log.error("pje.download.failed", job_id=job_id, error=str(e))
            self._last_error = str(e)

            # Checar se sessão expirou durante a operação
            try:
                session_lost = (
                    self.page is not None and "login" in self.page.url.lower()
                )
            except Exception:
                session_lost = True
            if session_lost:
                await self.invalidate_session()
                self._health_status = "session_expired"
                await self._publish_progress(
                    job,
                    "failed",
                    "Sessão expirada",
                    total_docs=max(expected_total_docs, len(downloaded_files)),
                    docs_baixados=len(downloaded_files),
                    tamanho_bytes=sum(
                        int(item.get("tamanhoBytes", 0) or 0)
                        for item in downloaded_files
                    ),
                )
                return self._result(job_id, numero_processo, "session_expired")

            self._health_status = "ready"
            await self._publish_progress(
                job,
                "failed",
                str(e),
                total_docs=max(expected_total_docs, len(downloaded_files)),
                docs_baixados=len(downloaded_files),
                tamanho_bytes=sum(
                    int(item.get("tamanhoBytes", 0) or 0) for item in downloaded_files
                ),
            )
            return self._result(job_id, numero_processo, "failed", error=str(e))

    # ──────────────────────
    # ESTRATÉGIA 1: MNI SOAP
    # ──────────────────────

    async def _try_mni_download(
        self,
        numero_processo: str,
        output_dir: Path,
        tipos_documento: list | None = None,
        incluir_anexos: bool = True,
        progress_cb=None,
    ) -> tuple[list[dict] | None, int, int]:
        """
        Tenta baixar documentos via MNI SOAP (estratégia de 2 fases).

        Fase 1: consultarProcesso sem IDs → retorna metadados dos docs
        Fase 2: download_documentos faz novas chamadas com IDs em batch
                 para obter o conteúdo binário de cada documento

        Retorna lista de arquivos salvos e quantidade de anexos pendentes.
        """
        try:
            tipos_normalizados = (
                {t.lower() for t in tipos_documento} if tipos_documento else None
            )
            incluir_vinculados = incluir_anexos and (
                tipos_normalizados is None or "anexo" in tipos_normalizados
            )

            # Fase 1: obter metadados (lista de documentos sem conteúdo)
            result: MNIResult = await self.mni_client.consultar_processo(
                numero_processo,
                incluir_documentos=True,
                incluir_cabecalho=True,
            )

            if not result.success:
                log.warning(
                    "pje.mni.consulta_failed",
                    processo=numero_processo,
                    error=result.error,
                )
                return None, 0, 0

            if not result.processo.documentos:
                log.warning(
                    "pje.mni.no_documents",
                    processo=numero_processo,
                )
                return None, 0, 0

            expected_total_docs = 0
            for doc in result.processo.documentos:
                if tipos_normalizados and doc.tipo.lower() not in tipos_normalizados:
                    continue
                expected_total_docs += 1
                if incluir_vinculados and doc.vinculados:
                    expected_total_docs += len(doc.vinculados)

            anexos_pendentes = (
                sum(len(doc.vinculados) for doc in result.processo.documentos)
                if incluir_vinculados
                else 0
            )

            log.info(
                "pje.mni.phase1_complete",
                processo=numero_processo,
                total_docs=len(result.processo.documentos),
                expected_total_docs=expected_total_docs,
                anexos_pendentes=anexos_pendentes,
            )

            # Fase 2: download em batches (dentro de download_documentos)
            files = await self.mni_client.download_documentos(
                result.processo,
                output_dir,
                tipos_documento,
                incluir_anexos=incluir_vinculados,
                progress_cb=progress_cb,
            )
            return (files if files else None), anexos_pendentes, expected_total_docs

        except Exception as exc:
            log.warning("pje.mni.download_error", error=str(exc))
            return None, 0, 0

    # ──────────────────────
    # ESTRATÉGIA 2: API REST
    # ──────────────────────

    async def _try_official_api(
        self,
        numero_processo: str,
        output_dir: Path,
        tipos_documento: list | None = None,
        incluir_anexos: bool = True,
        incluir_principais: bool = True,
        progress_cb=None,
    ) -> list | None:
        """
        Tenta usar a API oficial REST do PJe para download.
        Referência: https://docs.pje.jus.br/manuais-basicos/padroes-de-api-do-pje/
        """
        try:
            if self.page is None:
                log.warning(
                    "pje.api.browser_unavailable",
                    processo=numero_processo,
                    note="browser not initialized; skipping REST fallback",
                )
                return None
            processo_id = numero_processo.replace(".", "").replace("-", "")

            response = await self.page.request.get(
                f"{PJE_BASE_URL}/api/processos/{processo_id}/documentos",
                headers={"Accept": "application/json"},
            )

            if response.status == 200:
                try:
                    docs = await response.json()
                except Exception as json_exc:
                    # PJe sometimes returns 200 with HTML (session-expiry
                    # redirect). The exception text may echo Set-Cookie or
                    # session tokens from the body — never log ``str(exc)``.
                    log.warning(
                        "pje.api.invalid_json",
                        processo=numero_processo,
                        status=response.status,
                        error_type=type(json_exc).__name__,
                    )
                    return None
                if isinstance(docs, list):
                    docs_list = docs
                else:
                    docs_list = docs.get("content") or docs.get("documentos") or []
                files: list[dict] = []
                local_bytes = 0
                tipos_normalizados = (
                    {t.lower() for t in tipos_documento} if tipos_documento else None
                )
                filtered_docs: list[dict] = []
                for doc in docs_list:
                    doc_tipo = str(doc.get("tipo", "pdf")).lower()
                    if not incluir_anexos and doc_tipo == "anexo":
                        continue
                    if not incluir_principais and doc_tipo != "anexo":
                        continue
                    if tipos_normalizados and doc_tipo not in tipos_normalizados:
                        continue
                    filtered_docs.append(doc)

                total_docs = len(filtered_docs)
                for doc in filtered_docs:
                    file_info = await self._download_document_api(doc, output_dir)
                    if file_info:
                        files.append(file_info)
                        local_bytes += int(file_info.get("tamanhoBytes", 0) or 0)
                        if progress_cb is not None:
                            await progress_cb(
                                file_info=file_info,
                                completed=len(files),
                                total=total_docs,
                                local_bytes=local_bytes,
                            )
                return files if files else None

        except Exception as exc:
            # Network-layer errors only; JSON parse failures handled above
            # without leaking body text into structured logs.
            log.warning(
                "pje.api.unavailable",
                processo=numero_processo,
                error_type=type(exc).__name__,
            )
        return None

    async def _download_document_api(self, doc: dict, output_dir: Path) -> dict | None:
        """Download de documento individual via API REST do PJe."""
        try:
            doc_id = doc.get("id")
            response = await self.page.request.get(
                f"{PJE_BASE_URL}/api/documentos/{doc_id}/download",
            )
            if response.status == 200:
                raw_name = doc.get("nome", f"{doc_id}.pdf")
                filename = _unique_filename(output_dir, raw_name)
                dest = output_dir / filename
                content = await response.body()
                dest.write_bytes(content)
                checksum = hashlib.sha256(content).hexdigest()
                return {
                    "nome": filename,
                    "tipo": doc.get("tipo", "pdf"),
                    "tamanhoBytes": len(content),
                    "localPath": str(dest),
                    "checksum": checksum,
                    "fonte": "api_rest",
                }
        except Exception as exc:
            log.warning(
                "pje.document_download_failed", doc_id=doc.get("id"), error=str(exc)
            )
        return None

    # ──────────────────────
    # ESTRATÉGIA 3: BROWSER
    # ──────────────────────

    async def _download_via_browser(
        self,
        numero_processo: str,
        output_dir: Path,
        tipos_documento: list | None = None,
        allow_full_download: bool = True,
        progress_cb=None,
    ) -> list | None:
        """
        Download via browser automation com Playwright.

        Sub-estratégias (em ordem):
        3a. Botão "full download" nos autos digitais (baixa tudo de uma vez)
        3b. Download individual de cada documento (fallback)
        """
        if self.page is None or self.context is None:
            log.warning(
                "pje.browser.unavailable",
                processo=numero_processo,
                note="browser fallback skipped because session was not initialized",
            )
            return None

        # 3a: Tentar full download via autos digitais apenas quando ainda
        # precisamos dos documentos principais. No complemento de anexos, o
        # full download rebaixa o processo inteiro e desperdiça banda/IO.
        if allow_full_download:
            full_files = await self._try_full_download_button(
                numero_processo, output_dir
            )
            if full_files:
                if progress_cb is not None:
                    await progress_cb(
                        file_info=full_files[0],
                        completed=len(full_files),
                        total=len(full_files),
                        local_bytes=sum(
                            int(item.get("tamanhoBytes", 0) or 0) for item in full_files
                        ),
                    )
                return full_files

        # 3b: Fallback — download individual
        return await self._download_docs_individually(
            numero_processo,
            output_dir,
            tipos_documento,
            progress_cb=progress_cb,
        )

    async def _try_full_download_button(
        self,
        numero_processo: str,
        output_dir: Path,
    ) -> list | None:
        """
        Estratégia 3a: Usa o botão "full download" na toolbar dos autos digitais.

        URL: {PJE_BASE_URL}/ng2/dev.seam#/autos-digitais
        O botão fica na barra superior e gera um PDF/ZIP com todos os
        documentos do processo.

        Fluxo:
        1. Navegar para autos digitais do processo
        2. Localizar botão de download completo na toolbar
        3. Clicar e aguardar download (pode demorar para processos grandes)
        4. Salvar arquivo no output_dir
        """
        try:
            log.info(
                "pje.browser.full_download.start",
                processo=numero_processo,
            )

            # Navegar para autos digitais do processo
            # O PJe Angular usa hash routing — precisamos do processo aberto
            autos_url = f"{PJE_BASE_URL}/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam"
            await self.page.goto(autos_url, wait_until="networkidle")

            if await self._detect_captcha():
                log.warning("pje.browser.full_download.captcha")
                return None

            # Pesquisar o processo para abrir seus detalhes
            input_selector = (
                '[id*="numeroProcesso"], [id*="nrProcesso"], input[name*="processo"]'
            )
            input_el = self.page.locator(input_selector).first
            if await input_el.count() > 0:
                await input_el.fill(numero_processo)
                await self.page.keyboard.press("Enter")
                await self.page.wait_for_load_state("networkidle")
                await asyncio.sleep(2)

            # Tentar navegar para autos digitais via Angular
            autos_ng_url = f"{PJE_BASE_URL}/ng2/dev.seam#/autos-digitais"
            await self.page.goto(autos_ng_url, wait_until="networkidle")
            await asyncio.sleep(3)  # Angular pode demorar a renderizar

            if await self._detect_captcha():
                log.warning("pje.browser.full_download.captcha_on_autos")
                return None

            # Localizar o botão de download completo na toolbar
            # Seletores possíveis para o botão (baseado na interface PJe):
            # - Ícone de download na toolbar superior
            # - Botão com tooltip "Download completo" / "Baixar autos"
            download_btn_selectors = [
                'button[title*="ownload"]',
                'button[title*="aixar"]',
                'button[title*="utos"]',
                'a[title*="ownload"]',
                'a[title*="aixar"]',
                '[class*="download"]',
                'button:has(i[class*="download"])',
                'button:has(i[class*="cloud"])',
                'button:has(span[class*="download"])',
                ".toolbar button:has(i.fa-download)",
                ".toolbar a:has(i.fa-download)",
                "pje-botao-download",
                '[id*="download"]',
                "button:has(mat-icon)",
            ]

            download_btn = None
            for selector in download_btn_selectors:
                try:
                    el = self.page.locator(selector).first
                    if await el.count() > 0 and await el.is_visible():
                        download_btn = el
                        log.info(
                            "pje.browser.full_download.button_found",
                            selector=selector,
                        )
                        break
                except Exception:
                    continue

            if download_btn is None:
                log.warning("pje.browser.full_download.button_not_found")
                return None

            # Clicar no botão e capturar o download
            # Downloads de processo inteiro podem demorar bastante
            try:
                async with self.page.expect_download(
                    timeout=300_000  # 5 min para processos grandes
                ) as download_info:
                    await download_btn.click()

                download = await download_info.value
                raw_name = (
                    download.suggested_filename or f"{numero_processo}_completo.pdf"
                )
                filename = _unique_filename(output_dir, raw_name)
                dest = output_dir / filename

                await download.save_as(str(dest))
                checksum, size = sha256_file(dest)

                log.info(
                    "pje.browser.full_download.success",
                    filename=filename,
                    size=size,
                    processo=numero_processo,
                )

                self.docs_downloaded_count += 1

                return [
                    {
                        "nome": filename,
                        "tipo": "completo",
                        "tamanhoBytes": size,
                        "localPath": str(dest),
                        "checksum": checksum,
                        "fonte": "browser_full_download",
                    }
                ]

            except Exception as dl_exc:
                # Pode ser que o botão abra um diálogo ou popup ao invés de download direto
                log.warning(
                    "pje.browser.full_download.download_failed",
                    error=str(dl_exc),
                    note="button may open dialog instead of direct download",
                )

                # Tentar detectar popup/dialog de opções de download
                try:
                    dialog = self.page.locator(
                        '[class*="dialog"], [class*="modal"], mat-dialog-container'
                    )
                    if await dialog.count() > 0:
                        # Procurar botão de confirmar dentro do dialog
                        confirm = dialog.locator(
                            'button:has-text("OK"), button:has-text("Baixar"), button:has-text("Download"), button:has-text("Confirmar")'
                        )
                        if await confirm.count() > 0:
                            async with self.page.expect_download(
                                timeout=300_000
                            ) as dl2:
                                await confirm.first.click()
                            download2 = await dl2.value
                            raw_name2 = (
                                download2.suggested_filename
                                or f"{numero_processo}_completo.pdf"
                            )
                            filename2 = _unique_filename(output_dir, raw_name2)
                            dest2 = output_dir / filename2
                            await download2.save_as(str(dest2))
                            checksum2, size2 = sha256_file(dest2)

                            log.info(
                                "pje.browser.full_download.dialog_success",
                                filename=filename2,
                                size=size2,
                            )
                            self.docs_downloaded_count += 1
                            return [
                                {
                                    "nome": filename2,
                                    "tipo": "completo",
                                    "tamanhoBytes": size2,
                                    "localPath": str(dest2),
                                    "checksum": checksum2,
                                    "fonte": "browser_full_download",
                                }
                            ]
                except Exception:
                    pass

                return None

        except Exception as exc:
            log.warning(
                "pje.browser.full_download.error",
                error=str(exc),
                processo=numero_processo,
            )
            return None

    async def _download_docs_individually(
        self,
        numero_processo: str,
        output_dir: Path,
        tipos_documento: list | None = None,
        progress_cb=None,
    ) -> list:
        """
        Estratégia 3b (fallback): Download individual de cada documento
        via ConsultaDocumento.

        Coleta hrefs first, then downloads concurrently via separate
        browser pages (limited by CONCURRENT_DOWNLOADS semaphore).
        """
        files: list[dict] = []

        await self.page.goto(
            f"{PJE_BASE_URL}/Processo/ConsultaDocumento/listView.seam",
            wait_until="networkidle",
        )

        if await self._detect_captcha():
            log.warning("pje.browser.individual.captcha_after_navigation")
            return files

        await self.page.fill('[id*="numeroProcesso"]', numero_processo)
        await self.page.keyboard.press("Enter")
        await self.page.wait_for_load_state("networkidle")

        if await self._detect_captcha():
            log.warning("pje.browser.individual.captcha_after_search")
            return files

        doc_links = await self.page.locator('a[href*="documento"]').all()
        log.info(
            "pje.browser.individual.docs_found",
            count=len(doc_links),
            processo=numero_processo,
        )

        # Collect hrefs from main page first
        hrefs: list[tuple[int, str]] = []
        for i, link in enumerate(doc_links[:MAX_DOCS_PER_SESSION]):
            try:
                href = await link.get_attribute("href")
                if href:
                    # Make absolute URL if relative
                    if href.startswith("/"):
                        href = PJE_BASE_URL.rsplit("/", 1)[0] + href
                    hrefs.append((i, href))
            except Exception:
                continue

        if not hrefs:
            # Fallback: sequential click-based download
            return await self._download_docs_sequential(
                doc_links,
                output_dir,
                progress_cb=progress_cb,
            )

        # Download concurrently via separate pages
        sem = asyncio.Semaphore(CONCURRENT_DOWNLOADS)
        progress_lock = asyncio.Lock()
        completed = 0
        local_bytes = 0
        total_docs = len(hrefs)

        async def _fetch_one(idx: int, url: str) -> dict | None:
            nonlocal completed, local_bytes
            async with sem:
                try:
                    dl_page = await self.context.new_page()
                    try:
                        async with dl_page.expect_download(timeout=30_000) as dl_info:
                            await dl_page.goto(url)
                        download = await dl_info.value
                        filename = download.suggested_filename or f"doc_{idx:03d}.pdf"
                        dest = output_dir / _unique_filename(output_dir, filename)
                        await download.save_as(str(dest))
                        checksum, size = sha256_file(dest)
                        self.docs_downloaded_count += 1
                        log.info(
                            "pje.browser.individual.doc_saved",
                            filename=dest.name,
                            size=size,
                        )
                        file_info = {
                            "nome": dest.name,
                            "tipo": "pdf",
                            "tamanhoBytes": size,
                            "localPath": str(dest),
                            "checksum": checksum,
                            "fonte": "browser_individual",
                        }
                        if progress_cb is not None:
                            async with progress_lock:
                                completed += 1
                                local_bytes += size
                                await progress_cb(
                                    file_info=file_info,
                                    completed=completed,
                                    total=total_docs,
                                    local_bytes=local_bytes,
                                )
                        return file_info
                    finally:
                        await dl_page.close()
                except Exception as e:
                    log.warning(
                        "pje.browser.individual.doc_failed", index=idx, error=str(e)
                    )
                    return None

        results = await asyncio.gather(*[_fetch_one(i, url) for i, url in hrefs])
        files = [r for r in results if r is not None]
        return files

    async def _download_docs_sequential(
        self,
        doc_links: list,
        output_dir: Path,
        progress_cb=None,
    ) -> list:
        """Fallback: sequential click-based download when hrefs unavailable."""
        files: list[dict] = []
        local_bytes = 0
        total_docs = min(len(doc_links), MAX_DOCS_PER_SESSION)
        for i, link in enumerate(doc_links[:MAX_DOCS_PER_SESSION]):
            try:
                if i > 0 and i % 10 == 0 and await self._detect_captcha():
                    log.warning(
                        "pje.browser.individual.captcha_mid_download", downloaded=i
                    )
                    break
                async with self.page.expect_download(timeout=30_000) as download_info:
                    await link.click()
                download = await download_info.value
                filename = download.suggested_filename or f"doc_{i:03d}.pdf"
                dest = output_dir / _unique_filename(output_dir, filename)
                await download.save_as(str(dest))
                checksum, size = sha256_file(dest)
                files.append(
                    {
                        "nome": dest.name,
                        "tipo": "pdf",
                        "tamanhoBytes": size,
                        "localPath": str(dest),
                        "checksum": checksum,
                        "fonte": "browser_individual",
                    }
                )
                local_bytes += size
                if progress_cb is not None:
                    await progress_cb(
                        file_info=files[-1],
                        completed=len(files),
                        total=total_docs,
                        local_bytes=local_bytes,
                    )
                self.docs_downloaded_count += 1
                await asyncio.sleep(DOWNLOAD_DELAY_SECS)
            except Exception as e:
                log.warning("pje.browser.individual.doc_failed", index=i, error=str(e))
                continue
        return files

    # ──────────────────────
    # QUEUE CONSUMER
    # ──────────────────────

    async def _publish_result(
        self,
        result_data: dict,
        max_retries: int = 3,
        queue_name: str = "kratos:pje:results",
    ) -> None:
        """Publish job result to Redis with retry. Falls back to local log on failure."""
        result_json = json.dumps(result_data)
        for attempt in range(max_retries):
            try:
                await self.redis.rpush(queue_name, result_json)
                metrics.worker_results_total.labels(
                    status=result_data.get("status", "unknown")
                ).inc()
                return
            except (redis.ConnectionError, redis.TimeoutError, OSError) as exc:
                if attempt == max_retries - 1:
                    metrics.worker_publish_failures_total.labels(kind="result").inc()
                    log.error(
                        "pje.queue.result_publish_failed",
                        job_id=result_data.get("jobId"),
                        error=str(exc),
                        note="result saved to local log only",
                    )
                    await self._log_job_result(
                        result_data.get("jobId", ""),
                        result_data.get("numeroProcesso", ""),
                        result_data.get("arquivosDownloaded", []),
                    )
                    return
                delay = min(2**attempt + random.uniform(0, 0.5), 10)
                log.warning(
                    "pje.queue.result_publish_retry",
                    attempt=attempt + 1,
                    delay=round(delay, 1),
                )
                await asyncio.sleep(delay)

    async def _publish_progress(
        self,
        job: dict,
        phase: str,
        phase_detail: str = "",
        *,
        status: str | None = None,
        total_docs: int = 0,
        docs_baixados: int = 0,
        tamanho_bytes: int = 0,
        erro: str | None = None,
    ) -> None:
        """Emit lightweight per-process progress events to the batch reply queue."""
        if self.redis is None:
            return

        queue_name = job.get("replyQueue")
        if not queue_name:
            return

        resolved_status = status or (
            phase if phase in {"done", "failed"} else "running"
        )

        payload = {
            "eventType": "progress",
            "jobId": job.get("jobId", ""),
            "batchId": job.get("batchId"),
            "numeroProcesso": job.get("numeroProcesso", ""),
            "status": resolved_status,
            "phase": phase,
            "phase_detail": phase_detail,
            "total_docs": total_docs,
            "docs_baixados": docs_baixados,
            "tamanho_bytes": tamanho_bytes,
            "erro": erro,
            "updatedAt": datetime.now(UTC).isoformat(),
        }
        try:
            await self.redis.rpush(queue_name, json.dumps(payload, ensure_ascii=False))
            metrics.worker_progress_events_total.labels(
                phase=phase,
                status=resolved_status,
            ).inc()
        except (redis.ConnectionError, redis.TimeoutError, OSError) as exc:
            metrics.worker_publish_failures_total.labels(kind="progress").inc()
            log.warning(
                "pje.queue.progress_publish_failed",
                job_id=job.get("jobId"),
                queue=queue_name,
                error=str(exc),
            )

    async def _publish_dead_letter(
        self,
        payload: str,
        reason: str,
        details: dict | None = None,
    ) -> None:
        """Publish malformed queue payloads to a dead-letter queue."""
        if self.redis is None:
            log.warning("pje.queue.dead_letter_skipped", reason=reason)
            return

        entry = {
            "reason": reason,
            "payload": payload,
            "details": details or {},
            "timestamp": datetime.now(UTC).isoformat(),
        }
        try:
            await self.redis.lpush(
                DEAD_LETTER_QUEUE, json.dumps(entry, ensure_ascii=False)
            )
            metrics.worker_dead_letters_total.labels(reason=reason).inc()
        except (redis.ConnectionError, redis.TimeoutError, OSError) as exc:
            metrics.worker_publish_failures_total.labels(kind="dead_letter").inc()
            log.warning(
                "pje.queue.dead_letter_publish_failed",
                reason=reason,
                error=str(exc),
            )

    async def consume_queue(self, shutdown_event: asyncio.Event | None = None) -> None:
        """
        Consome jobs da fila Redis (alimentada pelo n8n control plane).
        Exits gracefully when shutdown_event is set.
        """
        if self.redis is None:
            raise RuntimeError("Worker not initialized — call init() first")

        log.info("pje.queue.waiting")
        self._health_status = "consuming"
        consecutive_errors = 0

        while not (shutdown_event and shutdown_event.is_set()):
            # Verificar expiração de sessão (MNI não depende de sessão)
            if self.mni_client is None and self.is_session_expired():
                log.warning("pje.queue.session_timeout", action="shutting_down")
                await self.invalidate_session()
                self._health_status = "session_expired"
                break

            # BLPOP: aguarda até 5s por um job
            try:
                result = await self.redis.blpop("kratos:pje:jobs", timeout=5)
                consecutive_errors = 0  # reset on success
            except (redis.ConnectionError, redis.TimeoutError) as exc:
                consecutive_errors += 1
                delay = min(2**consecutive_errors + random.uniform(0, 1), 60)
                log.error(
                    "pje.queue.redis_error",
                    error=str(exc),
                    retry_in=round(delay, 1),
                    consecutive=consecutive_errors,
                )
                self._last_error = f"redis:{exc}"
                await asyncio.sleep(delay)
                continue

            if not result:
                continue

            _, job_json = result
            try:
                job = json.loads(job_json)
            except json.JSONDecodeError as exc:
                log.error("pje.queue.invalid_json", error=str(exc))
                await self._publish_dead_letter(
                    job_json,
                    "invalid_json",
                    details={"error": str(exc)},
                )
                continue

            missing_fields = [
                key for key in ("jobId", "numeroProcesso") if key not in job
            ]
            if missing_fields:
                log.error("pje.queue.missing_fields", keys=list(job.keys()))
                await self._publish_dead_letter(
                    json.dumps(job, ensure_ascii=False),
                    "missing_fields",
                    details={"missing": missing_fields, "keys": list(job.keys())},
                )
                continue

            log.info(
                "pje.queue.job_received",
                job_id=job["jobId"],
                processo=job["numeroProcesso"],
            )

            result_data = await self.download_process(job)
            if job.get("batchId"):
                result_data["batchId"] = job["batchId"]

            # Publicar resultado para o n8n (com retry)
            await self._publish_result(
                result_data,
                queue_name=job.get("replyQueue", "kratos:pje:results"),
            )

            # Encerrar se sessão expirou e MNI não disponível
            if result_data["status"] == "session_expired" and self.mni_client is None:
                log.warning("pje.queue.session_expired_mid_job", action="shutting_down")
                break

            # Encerrar se CAPTCHA detectado e MNI não disponível
            if result_data["status"] == "captcha_required" and self.mni_client is None:
                log.warning("pje.queue.captcha_detected", action="shutting_down")
                break

        if shutdown_event and shutdown_event.is_set():
            log.info("pje.queue.shutdown_requested", status="draining")

    # ──────────────────────
    # HEALTH ENDPOINT
    # ──────────────────────

    async def start_health_server(self) -> None:
        """Inicia servidor HTTP minimalista para health checks."""
        from aiohttp import web

        if self._health_runner is not None:
            return

        app = web.Application()
        app.router.add_get("/health", self._health_handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, HEALTH_BIND_HOST, HEALTH_PORT)
        await site.start()
        self._health_runner = runner
        log.info("pje.health.started", host=HEALTH_BIND_HOST, port=HEALTH_PORT)

    async def stop_health_server(self) -> None:
        """Encerra o servidor de health checks e libera a porta."""
        if self._health_runner is None:
            return
        try:
            await self._health_runner.cleanup()
        finally:
            self._health_runner = None

    async def _health_handler(self, request):
        """Handler do endpoint /health with deep checks."""
        import shutil
        from aiohttp import web

        checks: dict = {}
        healthy = self._health_status in ("ready", "consuming")

        # MNI connectivity (cached 30s — prevents 503 cascade during tribunal slowness)
        import time

        now = time.monotonic()
        if self.mni_client is not None:
            if self._health_cache and (now - self._health_cache_time) < 30.0:
                checks["mni"] = self._health_cache.get("status", "unknown")
            else:
                try:
                    mni_health = await asyncio.wait_for(
                        self.mni_client.health_check(), timeout=5.0
                    )
                    self._health_cache = mni_health
                    self._health_cache_time = now
                    checks["mni"] = mni_health["status"]
                except Exception:
                    checks["mni"] = "unreachable"
            # MNI status does NOT affect overall healthy — worker can process from Redis
        else:
            checks["mni"] = "disabled"

        # Redis connectivity
        if self.redis is not None:
            try:
                await asyncio.wait_for(self.redis.ping(), timeout=3.0)
                checks["redis"] = "healthy"
            except Exception:
                checks["redis"] = "unreachable"
                healthy = False
        else:
            checks["redis"] = "not_initialized"

        # Disk space
        try:
            usage = shutil.disk_usage(DOWNLOAD_BASE_DIR)
            free_mb = usage.free / 1_048_576
            checks["disk_free_mb"] = round(free_mb, 1)
            if free_mb < 100:
                checks["disk"] = "low"
                healthy = False
            else:
                checks["disk"] = "ok"
        except Exception:
            checks["disk"] = "unknown"

        status_code = 200 if healthy else 503
        body = {
            "service": "pje-worker",
            "status": self._health_status,
            "healthy": healthy,
            "checks": checks,
            "mni_enabled": self.mni_client is not None,
            "session_valid": self.session_valid,
            "fallback_ready": self.fallback_ready,
            "docs_downloaded": self.docs_downloaded_count,
            "uptime_minutes": round(
                (datetime.now(UTC) - self.session_started_at).total_seconds() / 60, 1
            )
            if self.session_started_at
            else 0,
        }
        return web.json_response(body, status=status_code)

    # ──────────────────────
    # UTILITÁRIOS
    # ──────────────────────

    def _result(
        self,
        job_id: str,
        numero_processo: str,
        status: str,
        files: list | None = None,
        error: str | None = None,
    ) -> dict:
        return {
            "jobId": job_id,
            "numeroProcesso": numero_processo,
            "status": status,
            "arquivosDownloaded": files or [],
            "errorMessage": error,
            "downloadedAt": datetime.now(UTC).isoformat(),
        }

    async def _log_job_result(
        self, job_id: str, numero_processo: str, files: list
    ) -> None:
        log_path = DOWNLOAD_BASE_DIR / f"_logs/{job_id}.json"
        log_path.parent.mkdir(exist_ok=True)
        log_path.write_text(
            json.dumps(
                {
                    "jobId": job_id,
                    "numeroProcesso": numero_processo,
                    "totalArquivos": len(files),
                    "arquivos": files,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                indent=2,
                ensure_ascii=False,
            )
        )

    async def close(self) -> None:
        await self.stop_health_server()
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self._browser:
            await self._browser.close()
        if self.redis:
            await self.redis.close()
        self._release_session_lock()


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────


async def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
    )

    worker = PJeSessionWorker()
    await worker.init()

    # Iniciar health endpoint em background
    await worker.start_health_server()

    # Graceful shutdown via signal
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _signal_handler(sig: int) -> None:
        log.info("pje.signal.received", signal=sig)
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler, sig)

    async with async_playwright() as playwright:
        session_ok = await worker.load_session(playwright)
        if not session_ok:
            log.error("pje.main.session_init_failed", action="aborting")
            return

        try:
            await worker.consume_queue(shutdown_event)
        finally:
            await worker.close()

    log.info("pje.main.shutdown", status="graceful")


if __name__ == "__main__":
    asyncio.run(main())
