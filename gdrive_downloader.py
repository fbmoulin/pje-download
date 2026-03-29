"""
Google Drive Folder Downloader
==============================

Baixa todos os arquivos de uma pasta pública do Google Drive.
Usado para processos antigos do PJe cujos documentos escaneados
estão armazenados no Google Drive.

Estratégias (em ordem):
  1. gdown (biblioteca especializada em Google Drive)
  2. Requests + parsing HTML da página da pasta
  3. Playwright (fallback para pastas que exigem interação)

Uso:
    from gdrive_downloader import download_gdrive_folder
    files = await download_gdrive_folder(
        "https://drive.google.com/drive/folders/ABC123",
        Path("./downloads/processo_antigo"),
    )
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from pathlib import Path

import structlog

log: structlog.BoundLogger = structlog.get_logger("kratos.gdrive")


# ─────────────────────────────────────────────
# UTILITÁRIOS
# ─────────────────────────────────────────────


def extract_folder_id(url: str) -> str | None:
    """Extrai o folder ID de uma URL do Google Drive."""
    # Formatos conhecidos:
    # https://drive.google.com/drive/folders/FOLDER_ID
    # https://drive.google.com/drive/folders/FOLDER_ID?usp=sharing
    # https://drive.google.com/drive/u/0/folders/FOLDER_ID
    patterns = [
        r"drive\.google\.com/drive(?:/u/\d+)?/folders/([a-zA-Z0-9_-]+)",
        r"drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)",
        r"drive\.google\.com/folderview\?id=([a-zA-Z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def _file_info(path: Path) -> dict:
    """Gera dict de informações de um arquivo baixado."""
    file_size = path.stat().st_size
    with open(path, "rb") as f:
        checksum = hashlib.file_digest(f, "sha256").hexdigest()
    return {
        "nome": path.name,
        "tipo": path.suffix.lstrip(".") or "bin",
        "tamanhoBytes": file_size,
        "localPath": str(path),
        "checksum": checksum,
        "fonte": "google_drive",
    }


# ─────────────────────────────────────────────
# ESTRATÉGIA 1: gdown
# ─────────────────────────────────────────────


async def _try_gdown(folder_url: str, output_dir: Path) -> list[dict] | None:
    """
    Tenta baixar pasta usando gdown (pip install gdown).
    Funciona bem com pastas públicas do Google Drive.
    """
    try:
        import gdown
    except ImportError:
        log.warning("gdrive.gdown.not_installed", hint="pip install gdown")
        return None

    try:
        log.info("gdrive.gdown.start", url=folder_url, output=str(output_dir))

        # gdown.download_folder retorna lista de paths dos arquivos baixados
        downloaded = await asyncio.to_thread(
            gdown.download_folder,
            url=folder_url,
            output=str(output_dir),
            quiet=False,
            use_cookies=False,
        )

        if not downloaded:
            log.warning("gdrive.gdown.no_files")
            return None

        files = []
        for file_path in downloaded:
            p = Path(file_path)
            if p.exists() and p.is_file():
                files.append(_file_info(p))

        log.info("gdrive.gdown.success", count=len(files))
        return files if files else None

    except Exception as exc:
        log.warning("gdrive.gdown.failed", error=str(exc))
        return None


# ─────────────────────────────────────────────
# ESTRATÉGIA 2: Requests + parsing HTML
# ─────────────────────────────────────────────


async def _try_requests_parse(folder_id: str, output_dir: Path) -> list[dict] | None:
    """
    Baixa arquivos de pasta pública do Google Drive usando requests.
    Parseia a página HTML da pasta para extrair IDs dos arquivos,
    depois baixa cada um via URL de download direto.
    """
    import requests

    try:
        log.info("gdrive.requests.start", folder_id=folder_id)

        # Acessar página da pasta
        folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        )

        resp = await asyncio.to_thread(session.get, folder_url)
        if resp.status_code != 200:
            log.warning("gdrive.requests.folder_error", status=resp.status_code)
            return None

        # Extrair file IDs do HTML da pasta
        # Google Drive embeds file data em scripts JS na página
        # Pattern: /file/d/FILE_ID ou "FILE_ID" em contextos de dados
        file_ids = set()

        # Padrão 1: links de arquivo no HTML
        for match in re.finditer(r"/file/d/([a-zA-Z0-9_-]{20,})", resp.text):
            file_ids.add(match.group(1))

        # Padrão 2: IDs em arrays JavaScript da página
        for match in re.finditer(r'\["([a-zA-Z0-9_-]{25,60})"', resp.text):
            candidate = match.group(1)
            # Filtrar IDs que parecem ser file IDs (não folder IDs que já temos)
            if candidate != folder_id and len(candidate) > 20:
                file_ids.add(candidate)

        if not file_ids:
            log.warning("gdrive.requests.no_files_found")
            return None

        log.info("gdrive.requests.files_found", count=len(file_ids))

        # Baixar cada arquivo
        files = []
        for i, file_id in enumerate(file_ids):
            try:
                # URL de download direto do Google Drive
                dl_url = f"https://drive.google.com/uc?export=download&id={file_id}"
                dl_resp = await asyncio.to_thread(
                    session.get, dl_url, stream=True, allow_redirects=True
                )

                if dl_resp.status_code != 200:
                    log.warning(
                        "gdrive.requests.file_error",
                        file_id=file_id,
                        status=dl_resp.status_code,
                    )
                    continue

                # Extrair nome do arquivo do header Content-Disposition
                cd = dl_resp.headers.get("Content-Disposition", "")
                filename_match = re.search(r'filename="?([^";\n]+)"?', cd)
                if filename_match:
                    filename = filename_match.group(1).strip()
                else:
                    filename = f"gdrive_{file_id}.pdf"

                # Verificar se é página de confirmação (arquivos grandes)
                content_type = dl_resp.headers.get("Content-Type", "")
                if "text/html" in content_type:
                    # Google Drive pede confirmação para arquivos grandes
                    confirm_match = re.search(r"confirm=([a-zA-Z0-9_-]+)", dl_resp.text)
                    if confirm_match:
                        confirm_url = (
                            f"https://drive.google.com/uc?export=download"
                            f"&confirm={confirm_match.group(1)}&id={file_id}"
                        )
                        dl_resp = await asyncio.to_thread(
                            session.get, confirm_url, stream=True
                        )

                dest = output_dir / filename
                # Evitar sobrescrever — adicionar sufixo se necessário
                if dest.exists():
                    stem = dest.stem
                    suffix = dest.suffix
                    dest = output_dir / f"{stem}_{file_id[:8]}{suffix}"

                # Salvar conteúdo
                content = dl_resp.content
                dest.write_bytes(content)

                files.append(_file_info(dest))
                log.info(
                    "gdrive.requests.file_saved",
                    filename=dest.name,
                    size=len(content),
                    index=i + 1,
                )

                # Pausa entre downloads
                await asyncio.sleep(0.5)

            except Exception as exc:
                log.warning(
                    "gdrive.requests.file_failed", file_id=file_id, error=str(exc)
                )
                continue

        return files if files else None

    except Exception as exc:
        log.warning("gdrive.requests.failed", error=str(exc))
        return None


# ─────────────────────────────────────────────
# ESTRATÉGIA 3: Playwright (fallback)
# ─────────────────────────────────────────────


async def _try_playwright_download(
    folder_url: str, output_dir: Path
) -> list[dict] | None:
    """
    Usa Playwright para navegar até a pasta do Google Drive,
    selecionar todos os arquivos e baixar via interface web.
    Fallback para quando requests/gdown falham.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.warning("gdrive.playwright.not_installed")
        return None

    try:
        log.info("gdrive.playwright.start", url=folder_url)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                accept_downloads=True,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            await page.goto(folder_url, wait_until="networkidle")
            await asyncio.sleep(3)  # Esperar renderização

            # Encontrar links de arquivo na página
            file_links = await page.locator('a[href*="/file/d/"]').all()
            if not file_links:
                # Tentar outro seletor
                file_links = await page.locator("[data-id]").all()

            if not file_links:
                log.warning("gdrive.playwright.no_files")
                await browser.close()
                return None

            log.info("gdrive.playwright.files_found", count=len(file_links))

            files = []
            for i, link in enumerate(file_links):
                try:
                    href = await link.get_attribute("href")
                    if not href or "/file/d/" not in href:
                        continue

                    # Extrair file ID e montar URL de download
                    fid_match = re.search(r"/file/d/([a-zA-Z0-9_-]+)", href)
                    if not fid_match:
                        continue
                    file_id = fid_match.group(1)

                    # Navegar para download direto em nova aba
                    dl_url = f"https://drive.google.com/uc?export=download&id={file_id}"
                    dl_page = await context.new_page()

                    try:
                        async with dl_page.expect_download(timeout=60_000) as dl_info:
                            await dl_page.goto(dl_url)
                        download = await dl_info.value
                        filename = (
                            download.suggested_filename or f"gdrive_{file_id}.pdf"
                        )
                        dest = output_dir / filename
                        if dest.exists():
                            dest = (
                                output_dir / f"{dest.stem}_{file_id[:8]}{dest.suffix}"
                            )
                        await download.save_as(str(dest))
                        files.append(_file_info(dest))
                        log.info(
                            "gdrive.playwright.file_saved",
                            filename=dest.name,
                            index=i + 1,
                        )
                    except Exception:
                        # Pode ser página de confirmação — tentar clicar botão
                        try:
                            btn = dl_page.locator(
                                'a[href*="confirm="], form[action*="uc"] input[type="submit"]'
                            )
                            if await btn.count() > 0:
                                async with dl_page.expect_download(
                                    timeout=60_000
                                ) as dl2:
                                    await btn.first.click()
                                download2 = await dl2.value
                                filename2 = (
                                    download2.suggested_filename
                                    or f"gdrive_{file_id}.pdf"
                                )
                                dest2 = output_dir / filename2
                                await download2.save_as(str(dest2))
                                files.append(_file_info(dest2))
                        except Exception as inner_exc:
                            log.warning(
                                "gdrive.playwright.file_failed",
                                file_id=file_id,
                                error=str(inner_exc),
                            )
                    finally:
                        await dl_page.close()

                    await asyncio.sleep(1)
                except Exception as exc:
                    log.warning("gdrive.playwright.link_error", index=i, error=str(exc))

            await browser.close()
            return files if files else None

    except Exception as exc:
        log.warning("gdrive.playwright.failed", error=str(exc))
        return None


# ─────────────────────────────────────────────
# FUNÇÃO PRINCIPAL
# ─────────────────────────────────────────────


async def download_gdrive_folder(
    folder_url: str,
    output_dir: Path,
    strategy: str = "auto",
) -> list[dict]:
    """
    Baixa todos os arquivos de uma pasta pública do Google Drive.

    Args:
        folder_url: URL completa da pasta do Google Drive
        output_dir: Diretório de destino para os arquivos
        strategy: "auto" (tenta todas), "gdown", "requests", "playwright"

    Returns:
        Lista de dicts com informações dos arquivos baixados
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    folder_id = extract_folder_id(folder_url)
    if not folder_id:
        log.error("gdrive.invalid_url", url=folder_url)
        return []

    log.info(
        "gdrive.download.start",
        folder_id=folder_id,
        output=str(output_dir),
        strategy=strategy,
    )

    start = time.monotonic()
    files: list[dict] | None = None

    # Estratégia 1: gdown
    if strategy in ("auto", "gdown"):
        files = await _try_gdown(folder_url, output_dir)
        if files:
            elapsed = time.monotonic() - start
            log.info(
                "gdrive.download.complete",
                strategy="gdown",
                count=len(files),
                elapsed_s=round(elapsed, 1),
            )
            return files

    # Estratégia 2: requests + parsing
    if strategy in ("auto", "requests"):
        files = await _try_requests_parse(folder_id, output_dir)
        if files:
            elapsed = time.monotonic() - start
            log.info(
                "gdrive.download.complete",
                strategy="requests",
                count=len(files),
                elapsed_s=round(elapsed, 1),
            )
            return files

    # Estratégia 3: Playwright
    if strategy in ("auto", "playwright"):
        files = await _try_playwright_download(folder_url, output_dir)
        if files:
            elapsed = time.monotonic() - start
            log.info(
                "gdrive.download.complete",
                strategy="playwright",
                count=len(files),
                elapsed_s=round(elapsed, 1),
            )
            return files

    elapsed = time.monotonic() - start
    log.error(
        "gdrive.download.all_strategies_failed",
        folder_id=folder_id,
        elapsed_s=round(elapsed, 1),
    )
    return []


# ─────────────────────────────────────────────
# EXTRAÇÃO DE LINK DO GOOGLE DRIVE DO PJE
# ─────────────────────────────────────────────


async def extract_gdrive_link_from_pje(
    page,
    numero_processo: str,
    pje_base_url: str = "https://pje.tjes.jus.br/pje",
) -> str | None:
    """
    Navega até os autos digitais de um processo antigo no PJe
    e extrai o link do Google Drive que contém os documentos escaneados.

    Args:
        page: Playwright Page já autenticada no PJe
        numero_processo: Número CNJ do processo
        pje_base_url: URL base do PJe

    Returns:
        URL da pasta do Google Drive ou None se não encontrado
    """
    try:
        log.info("gdrive.pje.extract_start", processo=numero_processo)

        # Navegar para autos digitais
        autos_url = f"{pje_base_url}/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam"
        await page.goto(autos_url, wait_until="networkidle")
        await asyncio.sleep(2)

        # Pesquisar o processo
        input_selector = (
            '[id*="numeroProcesso"], [id*="nrProcesso"], input[name*="processo"]'
        )
        input_el = page.locator(input_selector).first
        if await input_el.count() > 0:
            await input_el.fill(numero_processo)
            await page.keyboard.press("Enter")
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(3)

        # Ir para autos digitais (Angular)
        autos_ng_url = f"{pje_base_url}/ng2/dev.seam#/autos-digitais"
        await page.goto(autos_ng_url, wait_until="networkidle")
        await asyncio.sleep(4)

        # Obter todo o conteúdo da página
        page_content = await page.content()

        # Procurar links do Google Drive no HTML
        gdrive_patterns = [
            r'(https?://drive\.google\.com/drive/folders/[a-zA-Z0-9_-]+[^"\'<\s]*)',
            r'(https?://drive\.google\.com/open\?id=[a-zA-Z0-9_-]+[^"\'<\s]*)',
            r'(https?://drive\.google\.com/folderview\?id=[a-zA-Z0-9_-]+[^"\'<\s]*)',
        ]

        for pattern in gdrive_patterns:
            match = re.search(pattern, page_content)
            if match:
                url = match.group(1)
                log.info("gdrive.pje.link_found", url=url, processo=numero_processo)
                return url

        # Fallback: procurar em links clicáveis na página
        all_links = await page.locator('a[href*="drive.google.com"]').all()
        for link in all_links:
            href = await link.get_attribute("href")
            if href and (
                "folders/" in href or "folderview" in href or "open?id=" in href
            ):
                log.info(
                    "gdrive.pje.link_found_via_href", url=href, processo=numero_processo
                )
                return href

        # Fallback 2: procurar em iframes (às vezes o link está embeddado)
        iframes = await page.locator('iframe[src*="drive.google.com"]').all()
        for iframe in iframes:
            src = await iframe.get_attribute("src")
            if src:
                folder_id = extract_folder_id(src)
                if folder_id:
                    url = f"https://drive.google.com/drive/folders/{folder_id}"
                    log.info(
                        "gdrive.pje.link_found_via_iframe",
                        url=url,
                        processo=numero_processo,
                    )
                    return url

        # Fallback 3: abrir cada documento e procurar links de GDrive dentro
        doc_links = await page.locator('a[href*="documento"], [class*="doc"]').all()
        for doc_link in doc_links[:10]:  # Limitar a 10 docs
            try:
                await doc_link.click()
                await asyncio.sleep(2)
                inner_content = await page.content()
                for pattern in gdrive_patterns:
                    match = re.search(pattern, inner_content)
                    if match:
                        url = match.group(1)
                        log.info(
                            "gdrive.pje.link_found_in_doc",
                            url=url,
                            processo=numero_processo,
                        )
                        return url
            except Exception:
                continue

        log.warning("gdrive.pje.no_link_found", processo=numero_processo)
        return None

    except Exception as exc:
        log.error("gdrive.pje.extract_failed", processo=numero_processo, error=str(exc))
        return None


# ─────────────────────────────────────────────
# DETECÇÃO DE PROCESSO ANTIGO
# ─────────────────────────────────────────────


def is_processo_antigo(numero_processo: str) -> bool:
    """
    Detecta se um processo é antigo (escaneado/Google Drive).

    Processos novos do PJe começam com "5" no primeiro dígito
    (ex: 5008407-35.2024.8.08.0012).
    Processos antigos/migrados começam com "0" ou outro dígito
    (ex: 0126923-56.2011.8.08.0012).

    Returns:
        True se o processo é antigo (não começa com "5")
    """
    numero_limpo = numero_processo.strip()
    if not numero_limpo:
        return False
    return not numero_limpo.startswith("5")
