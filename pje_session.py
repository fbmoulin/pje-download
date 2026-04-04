"""
PJe Session — Login interativo com persistência de sessão Playwright.
======================================================================

Fluxo:
  1. Abre browser VISÍVEL (não headless) — usuário resolve CAPTCHA + MFA uma vez
  2. Salva session state (cookies + localStorage) em arquivo JSON
  3. Uso posterior: carrega session salva, faz chamadas autenticadas sem re-login

Uso:
  # Login manual (primeira vez ou quando sessão expira):
  python pje_session.py login

  # Testar sessão salva:
  python pje_session.py test

  # Download de processo via sessão salva:
  python pje_session.py download 5000001-02.2024.8.08.0001 ./downloads
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import structlog

log = structlog.get_logger("kratos.pje-session")

# ─────────────────────────────────────────────
# CONFIGURAÇÃO
# ─────────────────────────────────────────────

from config import PJE_BASE_URL, SESSION_STATE_PATH

SESSION_FILE = SESSION_STATE_PATH
LOGIN_URL = (
    "https://sso.cloud.pje.jus.br/auth/realms/pje/protocol/openid-connect/auth"
    "?response_type=code&client_id=pje-tjes-1g"
    f"&redirect_uri={PJE_BASE_URL}/login.seam"
    "&state=pje-session&login=true&scope=openid"
)
DOCUMENTS_API = PJE_BASE_URL + "/api/v2/processos/{numero}/documentos"
DOCUMENT_BINARY = PJE_BASE_URL + "/api/v2/documentos/{id}/conteudo"


# ─────────────────────────────────────────────
# LOGIN INTERATIVO
# ─────────────────────────────────────────────


async def interactive_login(session_file: Path = SESSION_FILE) -> bool:
    """
    Abre browser visível para login manual.
    Salva a sessão após redirect bem-sucedido para o PJe.
    Retorna True se o login foi concluído com sucesso.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        ctx = await browser.new_context(
            viewport=None,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()

        log.info("pje.session.login_start", url=LOGIN_URL)
        print("\n>>> Abrindo browser para login no PJe...")
        print(">>> Complete o CAPTCHA, informe CPF/senha e o código MFA.")
        print(">>> O browser fechará automaticamente após o login.\n")

        await page.goto(LOGIN_URL)

        # Single try/finally ensures browser.close() always runs exactly once
        try:
            # Aguarda redirect para o PJe (indica login bem-sucedido)
            try:
                await page.wait_for_url(
                    lambda url: PJE_BASE_URL in url and "login.seam" not in url,
                    timeout=300_000,  # 5 min para o usuário completar
                )
            except Exception:
                current = page.url
                if PJE_BASE_URL not in current:
                    log.error("pje.session.login_timeout")
                    return False

            # Salva estado da sessão
            state = await ctx.storage_state()
            session_file.write_text(json.dumps(state, indent=2, ensure_ascii=False))
            log.info("pje.session.saved", path=str(session_file))
            print(f"\n>>> Sessão salva em {session_file}")
            return True
        finally:
            await browser.close()


# ─────────────────────────────────────────────
# CLIENTE AUTENTICADO
# ─────────────────────────────────────────────


class PJeSessionClient:
    """
    Cliente que usa sessão Playwright salva para acessar o PJe.
    Tenta API REST primeiro; fallback para scraping via browser se necessário.
    """

    def __init__(self, session_file: Path = SESSION_FILE) -> None:
        self.session_file = session_file

    def _load_state(self) -> dict:
        if not self.session_file.exists():
            raise FileNotFoundError(
                f"Sessão não encontrada: {self.session_file}\n"
                "Execute: python pje_session.py login"
            )
        return json.loads(self.session_file.read_text())

    async def is_valid(self) -> bool:
        """Verifica se a sessão salva ainda é válida."""
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                ctx = await browser.new_context(
                    storage_state=self._load_state(),
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page = await ctx.new_page()
                await page.goto(PJE_BASE_URL + "/painel.seam")
                valid = "painel" in page.url or "home" in page.url.lower()
                await browser.close()
                return valid
        except Exception as exc:
            log.warning("pje.session.check_failed", error=str(exc))
            return False

    async def download_processo(
        self,
        numero: str,
        output_dir: Path,
        include_anexos: bool = True,
    ) -> list[dict]:
        """
        Baixa documentos de um processo via API REST do PJe.
        Retorna lista de dicts com nome, path, tamanho.
        """
        from playwright.async_api import async_playwright

        output_dir.mkdir(parents=True, exist_ok=True)
        downloaded: list[dict] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(
                storage_state=self._load_state(),
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                accept_downloads=True,
            )

            try:
                downloaded = await self._try_api(
                    ctx, numero, output_dir, include_anexos
                )
                if not downloaded:
                    downloaded = await self._try_browser(ctx, numero, output_dir)
            finally:
                await browser.close()

        return downloaded

    async def _try_api(
        self,
        ctx,
        numero: str,
        output_dir: Path,
        include_anexos: bool,
    ) -> list[dict]:
        """Tenta baixar via API REST do PJe."""
        page = await ctx.new_page()
        downloaded: list[dict] = []

        try:
            # Carrega um endpoint leve para ter cookies na sessão
            await page.goto(
                PJE_BASE_URL + "/painel.seam", wait_until="domcontentloaded"
            )

            # Lista documentos via API
            api_url = DOCUMENTS_API.format(
                numero=numero.replace(".", "%2E").replace("-", "%2D")
            )
            resp = await page.request.get(api_url)

            if resp.status == 401 or resp.status == 403:
                log.warning("pje.session.api_auth_failed", status=resp.status)
                await page.close()
                return []

            if not resp.ok:
                log.warning("pje.session.api_error", status=resp.status, url=api_url)
                await page.close()
                return []

            docs = await resp.json()
            if not isinstance(docs, list):
                docs = docs.get("content") or docs.get("documentos") or []

            log.info("pje.session.api_docs", count=len(docs), numero=numero)

            for doc in docs:
                if not include_anexos and doc.get("tipo", "").lower() == "anexo":
                    continue

                doc_id = doc.get("id") or doc.get("idDocumento")
                nome = _safe_filename(
                    doc.get("nome") or doc.get("descricao") or f"doc_{doc_id}"
                )

                bin_url = DOCUMENT_BINARY.format(id=doc_id)
                bin_resp = await page.request.get(bin_url)
                if bin_resp.ok:
                    content = await bin_resp.body()
                    ext = _guess_ext(bin_resp.headers.get("content-type", ""), nome)
                    dest = output_dir / f"{nome}{ext}"
                    dest = _unique_path(dest)
                    dest.write_bytes(content)
                    downloaded.append(
                        {
                            "nome": dest.name,
                            "localPath": str(dest),
                            "tamanhoBytes": len(content),
                            "fonte": "pje_api",
                        }
                    )
                    log.info("pje.session.doc_saved", nome=dest.name, size=len(content))

        except Exception as exc:
            log.warning("pje.session.api_attempt_failed", error=str(exc))

        await page.close()
        return downloaded

    async def _try_browser(self, ctx, numero: str, output_dir: Path) -> list[dict]:
        """Fallback: navega pelo browser para baixar documentos."""
        page = await ctx.new_page()
        downloaded: list[dict] = []

        try:
            search_url = f"{PJE_BASE_URL}/consultaPublica/listView.seam"
            await page.goto(search_url, wait_until="networkidle", timeout=30_000)

            # Verifica se foi redirecionado para login (sessão expirada)
            if "login" in page.url.lower() or "sso.cloud" in page.url:
                log.error("pje.session.expired")
                await page.close()
                return []

            # Preenche número do processo na busca
            await page.fill(
                "input[id*='numeroProcesso'], input[name*='numeroProcesso']", numero
            )
            await page.keyboard.press("Enter")
            await page.wait_for_load_state("networkidle", timeout=15_000)

            # Clica no primeiro resultado
            link = page.locator("a[href*='processo'], td a").first
            if await link.count() > 0:
                await link.click()
                await page.wait_for_load_state("networkidle", timeout=15_000)

                # Captura downloads via botão de download
                async with page.expect_download(timeout=30_000) as dl_info:
                    await page.click(
                        "a[id*='download'], button[id*='download'], a[title*='Download']"
                    )
                dl = await dl_info.value
                dest = output_dir / dl.suggested_filename
                await dl.save_as(dest)
                downloaded.append(
                    {
                        "nome": dest.name,
                        "localPath": str(dest),
                        "tamanhoBytes": dest.stat().st_size,
                        "fonte": "pje_browser",
                    }
                )

        except Exception as exc:
            log.warning("pje.session.browser_attempt_failed", error=str(exc))

        await page.close()
        return downloaded


# ─────────────────────────────────────────────
# UTILITÁRIOS
# ─────────────────────────────────────────────


def _safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()[:120]


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 1
    while (path.parent / f"{stem}_{i}{suffix}").exists():
        i += 1
    return path.parent / f"{stem}_{i}{suffix}"


def _guess_ext(content_type: str, nome: str) -> str:
    if "." in nome:
        return ""
    if "pdf" in content_type:
        return ".pdf"
    if "html" in content_type:
        return ".html"
    if "xml" in content_type:
        return ".xml"
    return ".bin"


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────


async def _main() -> None:
    structlog.configure(
        processors=[
            structlog.dev.ConsoleRenderer(),
        ]
    )

    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "login":
        ok = await interactive_login()
        sys.exit(0 if ok else 1)

    elif cmd == "test":
        client = PJeSessionClient()
        valid = await client.is_valid()
        print(
            "Sessão válida ✓"
            if valid
            else "Sessão expirada — execute: python pje_session.py login"
        )
        sys.exit(0 if valid else 1)

    elif cmd == "download":
        if len(sys.argv) < 4:
            print("Uso: python pje_session.py download NUMERO_PROCESSO OUTPUT_DIR")
            sys.exit(1)
        numero = sys.argv[2]
        output = Path(sys.argv[3])
        client = PJeSessionClient()
        docs = await client.download_processo(numero, output)
        print(f"Baixados: {len(docs)} documento(s)")
        for d in docs:
            print(f"  {d['nome']} ({d['tamanhoBytes']} bytes)")

    else:
        print(__doc__)


if __name__ == "__main__":
    asyncio.run(_main())
