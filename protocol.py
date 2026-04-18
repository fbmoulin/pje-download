"""Typed Redis message protocol for pje-download.

Defines the four message shapes exchanged between ``dashboard_api.py`` (producer)
and ``worker.py`` (consumer) via Redis queues.  This module has **zero side effects**
at import time — it only declares types and pure helper functions.

Queue topology
--------------
- ``kratos:pje:jobs``       — ``JobMessage`` payloads pushed by dashboard
- ``<reply_queue>``         — ``ResultMessage`` and ``ProgressMessage`` pushed by worker
- ``kratos:pje:dead-letter`` — ``DeadLetterEntry`` pushed on malformed payloads

Field names are fixed by the n8n control-plane contract — do **not** rename them.
"""

from __future__ import annotations

import json
from typing import NotRequired, TypedDict


# ─────────────────────────────────────────────
# MESSAGE TYPES
# ─────────────────────────────────────────────


class JobMessage(TypedDict):
    """Payload pushed by dashboard_api to ``kratos:pje:jobs``.

    Required fields are the minimum the worker needs to run a job.
    Optional fields enable batching, progress routing, and output control.
    """

    jobId: str
    numeroProcesso: str
    batchId: NotRequired[str | None]
    replyQueue: NotRequired[str | None]
    outputSubdir: NotRequired[str | None]
    gdriveUrl: NotRequired[str | None]
    includeAnexos: NotRequired[bool]
    delayEntreProcessos: NotRequired[float]


class ResultMessage(TypedDict):
    """Terminal result published by worker to the reply queue.

    ``batchId`` is attached by ``consume_queue`` when the originating job had one.
    """

    jobId: str
    numeroProcesso: str
    status: str  # "done" | "failed" | "session_expired" | "captcha_required"
    arquivosDownloaded: list
    errorMessage: str | None
    downloadedAt: str  # ISO 8601 datetime
    batchId: NotRequired[str | None]


class ProgressMessage(TypedDict):
    """Interim progress event published by worker to the reply queue.

    ``eventType`` is always ``"progress"`` — used by the dashboard to distinguish
    these from terminal ``ResultMessage`` payloads.
    """

    eventType: str  # always "progress"
    jobId: str
    numeroProcesso: str
    status: str
    phase: str
    phase_detail: str | None
    total_docs: int
    docs_baixados: int
    tamanho_bytes: int
    erro: str | None
    updatedAt: str  # ISO 8601 datetime
    batchId: NotRequired[str | None]


class DeadLetterEntry(TypedDict):
    """Record published to ``kratos:pje:dead-letter`` for malformed payloads."""

    reason: str
    payload: str  # raw bytes/string of the original message
    details: dict
    timestamp: str  # ISO 8601 datetime


# ─────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────


def job_from_json(raw: str | bytes) -> JobMessage:
    """Deserialise a ``JobMessage`` from JSON bytes/string.

    Raises:
        json.JSONDecodeError: if ``raw`` is not valid JSON.
        ValueError: if ``jobId`` or ``numeroProcesso`` is missing.
    """
    data: dict = json.loads(raw)
    missing = [key for key in ("jobId", "numeroProcesso") if key not in data]
    if missing:
        raise ValueError(f"JobMessage missing required fields: {missing}")
    return data  # type: ignore[return-value]


def result_to_json(msg: ResultMessage) -> str:
    """Serialise a ``ResultMessage`` to a JSON string (UTF-8, no ASCII escaping)."""
    return json.dumps(msg, ensure_ascii=False)


def progress_to_json(msg: ProgressMessage) -> str:
    """Serialise a ``ProgressMessage`` to a JSON string (UTF-8, no ASCII escaping)."""
    return json.dumps(msg, ensure_ascii=False)


def dead_letter_to_json(entry: DeadLetterEntry) -> str:
    """Serialise a ``DeadLetterEntry`` to a JSON string (UTF-8, no ASCII escaping)."""
    return json.dumps(entry, ensure_ascii=False)
