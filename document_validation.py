"""Small transport/content guards; these do not fully parse or certify a PDF."""

import base64
import os
import re
import tempfile
from contextlib import suppress
from html.parser import HTMLParser
from pathlib import Path

from file_utils import DiskWriteError

_ASCII_WHITESPACE = b" \t\r\n\v\f"


def decode_document_base64(payload: str | bytes) -> bytes:
    """Decode strictly, allowing only ASCII whitespace introduced by transport."""
    if isinstance(payload, str):
        payload = payload.encode("ascii")
    if not isinstance(payload, bytes):
        raise ValueError("Conteúdo Base64 ausente ou inválido")
    return base64.b64decode(payload.translate(None, _ASCII_WHITESPACE), validate=True)


class _LoginFormDetector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.password_input = False
        self.login_form = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and (attributes.get("type") or "").lower() == "password":
            self.password_input = True
        if tag == "form":
            action = attributes.get("action") or ""
            self.login_form |= bool(
                re.search(
                    r"(?:^|[/_-])(?:login|logon|signin|autentica\w*)(?:[/.?_-]|$)",
                    action,
                    re.I,
                )
            )


def validate_document_content(
    content: bytes, mime_type: str = "", filename: str = ""
) -> None:
    """Reject empty/authentication payloads and obviously incomplete claimed PDFs.

    PDF checks are deliberately limited to a version header and terminal %%EOF.
    They do not establish page integrity, digital signatures or semantic validity.
    Legitimate HTML judicial documents remain allowed when not labelled as PDF.
    """
    if not content or not content.strip(_ASCII_WHITESPACE):
        raise ValueError("Documento vazio")
    mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    claims_pdf = mime == "application/pdf" or str(filename).lower().endswith(".pdf")
    prefix = content[:2048].lstrip(_ASCII_WHITESPACE).lower()
    html_content = mime in {"text/html", "application/xhtml+xml"} or prefix.startswith(
        (b"<!doctype html", b"<html", b"<form")
    )
    if html_content:
        detector = _LoginFormDetector()
        detector.feed(content.decode("utf-8", errors="replace"))
        if detector.password_input or detector.login_form:
            raise ValueError("Resposta de autenticação recebida em vez de documento")
    if claims_pdf or content.startswith(b"%PDF-"):
        if not re.match(rb"%PDF-\d\.\d(?:\r\n|\r|\n)", content):
            raise ValueError("Documento declarado PDF sem assinatura PDF válida")
        if not content.rstrip(_ASCII_WHITESPACE).endswith(b"%%EOF"):
            raise ValueError("PDF incompleto: marcador final %%EOF ausente")


def atomic_write_document(destination: Path, content: bytes) -> None:
    """Publish a validated payload via a neighboring temporary; no power-loss guarantee."""
    temporary = None
    try:
        fd, name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        temporary = Path(name)
        os.close(fd)
        temporary.write_bytes(content)
        os.replace(temporary, destination)
    except OSError as exc:
        raise DiskWriteError(exc.errno, exc.strerror or str(exc)) from exc
    finally:
        if temporary is not None:
            # Preserve the original write error even if the filesystem also
            # refuses cleanup; never turn a DiskWriteError into a generic error.
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
