"""Shared helpers for downloaded-file metadata lists.

Previously duplicated across worker.py, dashboard_api.py, and
batch_downloader.py. Centralised here so a schema change (e.g., renaming
``tamanhoBytes``, adding a new dedupe key) ripples through one place.

Two helpers live here:

- :func:`total_bytes` — sum the ``tamanhoBytes`` field defensively. Tolerates
  missing keys, ``None``, and string values (the MNI SOAP response sometimes
  returns a string). Returned 0 on empty input.
- :func:`merge_file_lists` — dedupe-and-merge variadic file-metadata groups.
  Prefers the ``checksum`` field as dedupe key; falls back to
  ``nome|tamanhoBytes|fonte`` for items that don't have a checksum.

Zero module-level side effects — safe to import anywhere, including at test
collection time.
"""

from __future__ import annotations

from typing import Iterable


def total_bytes(files: Iterable[dict]) -> int:
    """Sum ``tamanhoBytes`` across a sequence of file-metadata dicts.

    Tolerates missing keys, ``None`` values, and string representations of
    integers. Returns 0 on empty input or when every entry is missing the
    field — never raises ``KeyError`` or ``TypeError``.

    Callers were previously copy-pasting the equivalent of::

        sum(int(item.get("tamanhoBytes", 0) or 0) for item in files)

    across 17+ sites in worker.py, dashboard_api.py, and batch_downloader.py.
    The duplication caused bug B3 (KeyError on missing field in one path)
    that this helper closes by construction.
    """
    total = 0
    for item in files:
        value = item.get("tamanhoBytes", 0)
        if value is None:
            continue
        try:
            total += int(value)
        except (TypeError, ValueError):
            # Defensive: a string like "" or a malformed source.
            continue
    return total


def merge_file_lists(*groups: list[dict]) -> list[dict]:
    """Merge file-metadata lists, preferring checksum-based deduplication.

    Identical behaviour to the previous ``_merge_downloaded_files`` copies in
    worker.py and batch_downloader.py (verbatim dedupe): if an item has a
    ``checksum``, that wins; otherwise the key is ``nome|tamanhoBytes|fonte``.
    The first occurrence of a given key is kept, subsequent duplicates are
    dropped.

    Called from download-strategy orchestration to merge results from GDrive,
    MNI SOAP, PJe official API, and Playwright-browser fallbacks.
    """
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
