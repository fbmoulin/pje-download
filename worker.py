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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import redis.asyncio as redis
import structlog
from playwright.async_api import BrowserContext, Page, async_playwright

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
    CONCURRENT_DOWNLOADS,
    MNI_ENABLED,
    sanitize_filename,
    unique_path,
)

DOWNLOAD_BASE_DIR.mkdir(parents=True, exist_ok=True)


def _unique_filename(directory: Path, filename: str) -> str:
    """Return a non-colliding filename in directory."""
    return unique_path(directory / filename).name


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
        self.session_started_at: datetime | None = None
        self.mni_client: MNIClient | None = None
        self.docs_downloaded_count: int = 0
        self._health_status: str = "starting"
        self._last_error: str | None = None
        self._session_lock_fh: Any | None = None
        self._health_cache: dict | None = None
        self._health_cache_time: float = 0.0

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

    async def init(self) -> None:
        self.redis = redis.from_url(REDIS_URL, decode_responses=True)

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
            log.info("pje.session.mni_available", note="playwright_is_fallback_only")

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

        self._health_status = "processing"

        safe_name = sanitize_filename(numero_processo)
        output_dir = DOWNLOAD_BASE_DIR / safe_name
        if not output_dir.resolve().is_relative_to(DOWNLOAD_BASE_DIR.resolve()):
            raise ValueError(f"Path traversal detected: {numero_processo}")
        output_dir.mkdir(parents=True, exist_ok=True)

        downloaded_files: list[dict] = []

        try:
            log.info("pje.download.start", processo=numero_processo, job_id=job_id)

            # ── ESTRATÉGIA 1: MNI SOAP ──
            if self.mni_client is not None:
                mni_files = await self._try_mni_download(
                    numero_processo, output_dir, tipos_documento
                )
                if mni_files:
                    downloaded_files.extend(mni_files)
                    log.info(
                        "pje.download.mni_success",
                        count=len(downloaded_files),
                        processo=numero_processo,
                    )
                    await self._log_job_result(
                        job_id, numero_processo, downloaded_files
                    )
                    self._health_status = "ready"
                    return self._result(
                        job_id, numero_processo, "success", downloaded_files
                    )

            # ── Estratégias 2 e 3 precisam de sessão Playwright ──
            if self.is_session_expired():
                log.warning("pje.download.session_expired", job_id=job_id)
                self._health_status = "session_expired"
                return self._result(job_id, numero_processo, "session_expired")

            # ── ESTRATÉGIA 2: API REST ──
            api_result = await self._try_official_api(numero_processo, output_dir)
            if api_result:
                downloaded_files.extend(api_result)
                log.info("pje.download.api_success", count=len(downloaded_files))
            else:
                # ── ESTRATÉGIA 3: Browser automation ──
                # Verificar CAPTCHA antes de navegar
                if await self._detect_captcha():
                    self._health_status = "captcha_required"
                    return self._result(
                        job_id,
                        numero_processo,
                        "captcha_required",
                        error="CAPTCHA detectado — intervenção manual necessária",
                    )

                browser_files = await self._download_via_browser(
                    numero_processo, output_dir, tipos_documento
                )
                downloaded_files.extend(browser_files)
                log.info("pje.download.browser_success", count=len(downloaded_files))

            await self._log_job_result(job_id, numero_processo, downloaded_files)
            self._health_status = "ready"
            return self._result(job_id, numero_processo, "success", downloaded_files)

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
                return self._result(job_id, numero_processo, "session_expired")

            self._health_status = "ready"
            return self._result(job_id, numero_processo, "failed", error=str(e))

    # ──────────────────────
    # ESTRATÉGIA 1: MNI SOAP
    # ──────────────────────

    async def _try_mni_download(
        self,
        numero_processo: str,
        output_dir: Path,
        tipos_documento: list | None = None,
    ) -> list | None:
        """
        Tenta baixar documentos via MNI SOAP (estratégia de 2 fases).

        Fase 1: consultarProcesso sem IDs → retorna metadados dos docs
        Fase 2: download_documentos faz novas chamadas com IDs em batch
                 para obter o conteúdo binário de cada documento

        Retorna lista de arquivos salvos ou None se falhar.
        """
        try:
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
                return None

            if not result.processo.documentos:
                log.warning(
                    "pje.mni.no_documents",
                    processo=numero_processo,
                )
                return None

            log.info(
                "pje.mni.phase1_complete",
                processo=numero_processo,
                total_docs=len(result.processo.documentos),
            )

            # Fase 2: download em batches (dentro de download_documentos)
            files = await self.mni_client.download_documentos(
                result.processo, output_dir, tipos_documento
            )
            return files if files else None

        except Exception as exc:
            log.warning("pje.mni.download_error", error=str(exc))
            return None

    # ──────────────────────
    # ESTRATÉGIA 2: API REST
    # ──────────────────────

    async def _try_official_api(
        self, numero_processo: str, output_dir: Path
    ) -> list | None:
        """
        Tenta usar a API oficial REST do PJe para download.
        Referência: https://docs.pje.jus.br/manuais-basicos/padroes-de-api-do-pje/
        """
        try:
            processo_id = numero_processo.replace(".", "").replace("-", "")

            response = await self.page.request.get(
                f"{PJE_BASE_URL}/api/processos/{processo_id}/documentos",
                headers={"Accept": "application/json"},
            )

            if response.status == 200:
                docs = await response.json()
                files: list[dict] = []
                for doc in docs.get("documentos", []):
                    file_info = await self._download_document_api(doc, output_dir)
                    if file_info:
                        files.append(file_info)
                return files if files else None

        except Exception as exc:
            log.warning("pje.api.unavailable", reason=str(exc))
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
    ) -> list:
        """
        Download via browser automation com Playwright.

        Sub-estratégias (em ordem):
        3a. Botão "full download" nos autos digitais (baixa tudo de uma vez)
        3b. Download individual de cada documento (fallback)
        """
        # 3a: Tentar full download via autos digitais
        full_files = await self._try_full_download_button(numero_processo, output_dir)
        if full_files:
            return full_files

        # 3b: Fallback — download individual
        return await self._download_docs_individually(
            numero_processo, output_dir, tipos_documento
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
                content = dest.read_bytes()
                checksum = hashlib.sha256(content).hexdigest()

                log.info(
                    "pje.browser.full_download.success",
                    filename=filename,
                    size=len(content),
                    processo=numero_processo,
                )

                self.docs_downloaded_count += 1

                return [
                    {
                        "nome": filename,
                        "tipo": "completo",
                        "tamanhoBytes": len(content),
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
                            content2 = dest2.read_bytes()
                            checksum2 = hashlib.sha256(content2).hexdigest()

                            log.info(
                                "pje.browser.full_download.dialog_success",
                                filename=filename2,
                                size=len(content2),
                            )
                            self.docs_downloaded_count += 1
                            return [
                                {
                                    "nome": filename2,
                                    "tipo": "completo",
                                    "tamanhoBytes": len(content2),
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
            return await self._download_docs_sequential(doc_links, output_dir)

        # Download concurrently via separate pages
        sem = asyncio.Semaphore(CONCURRENT_DOWNLOADS)

        async def _fetch_one(idx: int, url: str) -> dict | None:
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
                        content = dest.read_bytes()
                        checksum = hashlib.sha256(content).hexdigest()
                        self.docs_downloaded_count += 1
                        log.info(
                            "pje.browser.individual.doc_saved",
                            filename=dest.name,
                            size=len(content),
                        )
                        return {
                            "nome": dest.name,
                            "tipo": "pdf",
                            "tamanhoBytes": len(content),
                            "localPath": str(dest),
                            "checksum": checksum,
                            "fonte": "browser_individual",
                        }
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
        self, doc_links: list, output_dir: Path
    ) -> list:
        """Fallback: sequential click-based download when hrefs unavailable."""
        files: list[dict] = []
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
                content = dest.read_bytes()
                checksum = hashlib.sha256(content).hexdigest()
                files.append(
                    {
                        "nome": dest.name,
                        "tipo": "pdf",
                        "tamanhoBytes": len(content),
                        "localPath": str(dest),
                        "checksum": checksum,
                        "fonte": "browser_individual",
                    }
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

    async def consume_queue(self) -> None:
        """
        Consome jobs da fila Redis (alimentada pelo n8n control plane).
        """
        if self.redis is None:
            raise RuntimeError("Worker not initialized — call init() first")

        log.info("pje.queue.waiting")
        self._health_status = "consuming"

        while True:
            # Verificar expiração de sessão (MNI não depende de sessão)
            if self.mni_client is None and self.is_session_expired():
                log.warning("pje.queue.session_timeout", action="shutting_down")
                await self.invalidate_session()
                self._health_status = "session_expired"
                break

            # BLPOP: aguarda até 5s por um job
            try:
                result = await self.redis.blpop("kratos:pje:jobs", timeout=5)
            except (redis.ConnectionError, redis.TimeoutError) as exc:
                log.error("pje.queue.redis_error", error=str(exc))
                self._last_error = f"redis:{exc}"
                await asyncio.sleep(5)
                continue

            if not result:
                continue

            _, job_json = result
            try:
                job = json.loads(job_json)
            except json.JSONDecodeError as exc:
                log.error("pje.queue.invalid_json", error=str(exc))
                continue

            if "jobId" not in job or "numeroProcesso" not in job:
                log.error("pje.queue.missing_fields", keys=list(job.keys()))
                continue

            log.info(
                "pje.queue.job_received",
                job_id=job["jobId"],
                processo=job["numeroProcesso"],
            )

            result_data = await self.download_process(job)

            # Publicar resultado para o n8n
            await self.redis.lpush(
                "kratos:pje:results",
                json.dumps(result_data),
            )

            # Encerrar se sessão expirou e MNI não disponível
            if result_data["status"] == "session_expired" and self.mni_client is None:
                log.warning("pje.queue.session_expired_mid_job", action="shutting_down")
                break

            # Encerrar se CAPTCHA detectado e MNI não disponível
            if result_data["status"] == "captcha_required" and self.mni_client is None:
                log.warning("pje.queue.captcha_detected", action="shutting_down")
                break

    # ──────────────────────
    # HEALTH ENDPOINT
    # ──────────────────────

    async def start_health_server(self) -> None:
        """Inicia servidor HTTP minimalista para health checks."""
        from aiohttp import web

        app = web.Application()
        app.router.add_get("/health", self._health_handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", HEALTH_PORT)
        await site.start()
        log.info("pje.health.started", port=HEALTH_PORT)

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

    async with async_playwright() as playwright:
        session_ok = await worker.load_session(playwright)
        if not session_ok:
            log.error("pje.main.session_init_failed", action="aborting")
            return

        try:
            await worker.consume_queue()
        finally:
            await worker.close()

    log.info("pje.main.shutdown", status="graceful")


if __name__ == "__main__":
    asyncio.run(main())
