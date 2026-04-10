"""Tests for batch_downloader — BatchProgress, load_processos_from_file, concurrent batch."""

from __future__ import annotations

import asyncio
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import threading

import pytest

# Patch heavy imports before loading module
os.environ.setdefault("MNI_USERNAME", "user")
os.environ.setdefault("MNI_PASSWORD", "pass")

from batch_downloader import (
    BatchProgress,
    ProcessoStatus,
    load_processos_from_file,
)


# ─────────────────────────────────────────────
# BatchProgress
# ─────────────────────────────────────────────


class TestBatchProgress:
    def test_add_and_get(self):
        bp = BatchProgress()
        bp.add("1234567-89.2024.8.08.0001")
        ps = bp.get("1234567-89.2024.8.08.0001")
        assert ps.status == "pending"
        assert ps.numero == "1234567-89.2024.8.08.0001"

    def test_duplicate_add_is_noop(self):
        bp = BatchProgress()
        bp.add("abc")
        bp.add("abc")
        assert bp.total == 1

    def test_counts(self):
        bp = BatchProgress()
        for n, s in [
            ("a", "done"),
            ("b", "failed"),
            ("c", "pending"),
            ("d", "downloading"),
        ]:
            bp.add(n)
            bp.processos[n].status = s
        assert bp.done == 1
        assert bp.failed == 1
        assert bp.pending == 2  # pending + downloading

    def test_save_and_load_roundtrip(self, tmp_path):
        p = tmp_path / "_progress.json"
        bp = BatchProgress(progress_file=p)
        bp.add("1234567-89.2024.8.08.0001")
        bp.processos["1234567-89.2024.8.08.0001"].status = "done"
        bp.processos["1234567-89.2024.8.08.0001"].docs_baixados = 5
        bp.save(force=True)

        bp2 = BatchProgress.load(p)
        ps = bp2.get("1234567-89.2024.8.08.0001")
        assert ps.status == "done"
        assert ps.docs_baixados == 5

    def test_load_corrupt_file_returns_fresh(self, tmp_path):
        p = tmp_path / "_progress.json"
        p.write_text("NOT JSON", encoding="utf-8")
        bp = BatchProgress.load(p)
        assert bp.total == 0

    def test_load_nonexistent_file_returns_fresh(self, tmp_path):
        p = tmp_path / "_missing.json"
        bp = BatchProgress.load(p)
        assert bp.total == 0

    def test_load_resets_in_progress_to_pending(self, tmp_path):
        p = tmp_path / "_progress.json"
        bp = BatchProgress(progress_file=p)
        bp.add("a")
        bp.processos["a"].status = "downloading"
        bp.save(force=True)

        bp2 = BatchProgress.load(p)
        assert bp2.processos["a"].status == "pending"

    def test_save_atomic_write(self, tmp_path):
        p = tmp_path / "_progress.json"
        bp = BatchProgress(progress_file=p)
        bp.add("x")
        bp.save(force=True)
        assert p.exists()
        assert not (tmp_path / "_progress.tmp").exists()

    def test_save_is_thread_safe(self, tmp_path, monkeypatch):
        p = tmp_path / "_progress.json"
        bp = BatchProgress(progress_file=p)
        bp.add("x")
        bp.processos["x"].status = "downloading"

        original_write_text = Path.write_text
        active = {"count": 0, "max": 0}
        active_lock = threading.Lock()

        def wrapped_write_text(self, *args, **kwargs):
            if self.name.endswith(".tmp"):
                with active_lock:
                    active["count"] += 1
                    active["max"] = max(active["max"], active["count"])
                time.sleep(0.02)
                try:
                    return original_write_text(self, *args, **kwargs)
                finally:
                    with active_lock:
                        active["count"] -= 1
            return original_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", wrapped_write_text)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(bp.save, True) for _ in range(4)]
            for future in futures:
                future.result()

        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["summary"]["total"] == 1
        assert active["max"] == 1
        assert not (tmp_path / "_progress.tmp").exists()

    def test_duracao_s_without_fim(self):
        ps = ProcessoStatus(numero="x")
        ps.inicio = 1000.0
        assert ps.duracao_s == 0.0

    def test_duracao_s_with_inicio_and_fim(self):
        ps = ProcessoStatus(numero="x")
        ps.inicio = 1000.0
        ps.fim = 1002.5
        assert ps.duracao_s == 2.5


# ─────────────────────────────────────────────
# load_processos_from_file
# ─────────────────────────────────────────────


class TestLoadProcessosFromFile:
    def test_txt_file(self, tmp_path):
        f = tmp_path / "nums.txt"
        f.write_text(
            "1234567-89.2024.8.08.0001\n9876543-21.2023.8.08.0002\n", encoding="utf-8"
        )
        result = load_processos_from_file(f)
        assert len(result) == 2
        assert result[0] == "1234567-89.2024.8.08.0001"

    def test_txt_ignores_comments(self, tmp_path):
        f = tmp_path / "nums.txt"
        f.write_text("# comment\n1234567-89.2024.8.08.0001\n", encoding="utf-8")
        result = load_processos_from_file(f)
        assert result == ["1234567-89.2024.8.08.0001"]

    def test_json_list_of_strings(self, tmp_path):
        f = tmp_path / "nums.json"
        f.write_text(json.dumps(["1234567-89.2024.8.08.0001"]), encoding="utf-8")
        result = load_processos_from_file(f)
        assert result == ["1234567-89.2024.8.08.0001"]

    def test_json_list_of_dicts(self, tmp_path):
        f = tmp_path / "nums.json"
        data = [{"numero": "1234567-89.2024.8.08.0001"}]
        f.write_text(json.dumps(data), encoding="utf-8")
        result = load_processos_from_file(f)
        assert result == ["1234567-89.2024.8.08.0001"]

    def test_csv_with_numero_column(self, tmp_path):
        f = tmp_path / "nums.csv"
        f.write_text("numero\n1234567-89.2024.8.08.0001\n", encoding="utf-8")
        result = load_processos_from_file(f)
        assert result == ["1234567-89.2024.8.08.0001"]

    def test_csv_bom_utf8_sig(self, tmp_path):
        """Excel-exported CSVs have BOM marker — must be handled via utf-8-sig."""
        f = tmp_path / "nums.csv"
        # Write with BOM
        f.write_bytes(b"\xef\xbb\xbfnumero\n1234567-89.2024.8.08.0001\n")
        result = load_processos_from_file(f)
        assert result == ["1234567-89.2024.8.08.0001"]

    def test_csv_fallback_first_column(self, tmp_path):
        f = tmp_path / "nums.csv"
        f.write_text("col_a,col_b\n1234567-89.2024.8.08.0001,extra\n", encoding="utf-8")
        result = load_processos_from_file(f)
        assert "1234567-89.2024.8.08.0001" in result

    def test_csv_without_header_keeps_first_row(self, tmp_path):
        f = tmp_path / "nums.csv"
        f.write_text(
            "1234567-89.2024.8.08.0001\n9876543-21.2023.8.08.0002\n",
            encoding="utf-8",
        )
        result = load_processos_from_file(f)
        assert result == [
            "1234567-89.2024.8.08.0001",
            "9876543-21.2023.8.08.0002",
        ]


# ─────────────────────────────────────────────
# download_batch — concurrent semaphore
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_batch_uses_semaphore(tmp_path, monkeypatch):
    """download_batch deve processar CONCURRENT_DOWNLOADS processos em paralelo."""
    monkeypatch.setenv("CONCURRENT_DOWNLOADS", "3")
    monkeypatch.setenv("MNI_USERNAME", "u")
    monkeypatch.setenv("MNI_PASSWORD", "p")

    active = []
    peak = [0]

    async def fake_consultar(numero, **kwargs):
        active.append(numero)
        peak[0] = max(peak[0], len(active))
        await asyncio.sleep(0.05)
        active.remove(numero)
        result = MagicMock()
        result.success = False
        result.error = "fake"
        return result

    mock_client = AsyncMock()
    mock_client.health_check.return_value = {
        "status": "healthy",
        "tribunal": "T",
        "operations": [],
        "latency_ms": 1,
    }
    mock_client.consultar_processo.side_effect = fake_consultar

    numeros = [f"100000{i}-01.2024.8.08.0001" for i in range(6)]

    from batch_downloader import download_batch

    # MNIClient is lazily imported inside download_batch — patch the source module
    mock_class = MagicMock(return_value=mock_client)
    with (
        patch("mni_client.MNIClient", mock_class),
        patch("gdrive_downloader.is_processo_antigo", return_value=False),
        patch(
            "gdrive_downloader.download_gdrive_folder",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        await download_batch(
            numeros=numeros,
            output_dir=tmp_path,
            delay_entre_processos=0.0,
        )

    # Semaphore(3) means at most 3 concurrent
    assert peak[0] <= 3
    assert peak[0] > 1  # must actually be concurrent


@pytest.mark.asyncio
async def test_download_batch_missing_credentials(tmp_path, monkeypatch):
    """Sem credenciais MNI, todos os processos devem falhar com mensagem clara."""
    monkeypatch.delenv("MNI_USERNAME", raising=False)
    monkeypatch.delenv("MNI_PASSWORD", raising=False)
    import pje_session

    monkeypatch.setattr(pje_session, "SESSION_FILE", tmp_path / "missing.json")

    from batch_downloader import download_batch

    progress = await download_batch(
        numeros=["1234567-89.2024.8.08.0001"],
        output_dir=tmp_path,
    )

    assert progress.failed == 1
    ps = progress.processos["1234567-89.2024.8.08.0001"]
    assert ps.status == "failed"
    assert "MNI_USERNAME" in ps.erro or "MNI_PASSWORD" in ps.erro


@pytest.mark.asyncio
async def test_download_batch_allows_gdrive_only_without_mni_credentials(
    tmp_path, monkeypatch
):
    """Processos antigos com GDrive devem continuar funcionando sem credenciais MNI."""
    monkeypatch.delenv("MNI_USERNAME", raising=False)
    monkeypatch.delenv("MNI_PASSWORD", raising=False)
    import pje_session

    monkeypatch.setattr(pje_session, "SESSION_FILE", tmp_path / "missing.json")

    from batch_downloader import download_batch

    gdrive_file = {
        "nome": "scan.pdf",
        "tipo": "pdf",
        "tamanhoBytes": 10,
        "localPath": str(tmp_path / "scan.pdf"),
        "checksum": "abc123",
        "fonte": "google_drive",
    }

    with (
        patch("gdrive_downloader.is_processo_antigo", return_value=True),
        patch(
            "gdrive_downloader.download_gdrive_folder",
            new_callable=AsyncMock,
            return_value=[gdrive_file],
        ),
        patch("mni_client.MNIClient") as mock_mni,
    ):
        progress = await download_batch(
            numeros=["1234567-89.2024.8.08.0001"],
            output_dir=tmp_path,
            gdrive_url_map={
                "1234567-89.2024.8.08.0001": "https://drive.google.com/drive/folders/ABC123"
            },
            delay_entre_processos=0.0,
        )

    assert progress.done == 1
    assert progress.failed == 0
    ps = progress.processos["1234567-89.2024.8.08.0001"]
    assert ps.status == "done"
    assert ps.docs_baixados == 1
    mock_mni.assert_not_called()


@pytest.mark.asyncio
async def test_download_batch_complements_annexes_with_session(tmp_path, monkeypatch):
    """MNI success with pending annexes should complement via saved PJe session."""
    monkeypatch.setenv("MNI_USERNAME", "u")
    monkeypatch.setenv("MNI_PASSWORD", "p")
    import pje_session

    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pje_session, "SESSION_FILE", session_file)

    from batch_downloader import download_batch

    result = MagicMock()
    result.success = True
    result.processo = MagicMock(
        documentos=[MagicMock(vinculados=[MagicMock()], tipo="pdf")]
    )

    mock_client = AsyncMock()
    mock_client.health_check.return_value = {
        "status": "healthy",
        "tribunal": "T",
        "operations": [],
        "latency_ms": 1,
    }
    mock_client.consultar_processo.return_value = result
    mock_client.download_documentos.return_value = [
        {
            "nome": "principal.pdf",
            "tamanhoBytes": 10,
            "localPath": str(tmp_path / "principal.pdf"),
            "checksum": "dup",
            "fonte": "mni",
        }
    ]

    mock_pje_client = AsyncMock()
    mock_pje_client.download_processo.return_value = [
        {
            "nome": "principal-copy.pdf",
            "tamanhoBytes": 10,
            "localPath": str(tmp_path / "principal-copy.pdf"),
            "checksum": "dup",
            "fonte": "pje_api",
        },
        {
            "nome": "anexo.pdf",
            "tamanhoBytes": 5,
            "localPath": str(tmp_path / "anexo.pdf"),
            "checksum": "annex",
            "fonte": "pje_api",
        },
    ]

    with (
        patch("mni_client.MNIClient", return_value=mock_client),
        patch("pje_session.PJeSessionClient", return_value=mock_pje_client),
        patch("gdrive_downloader.is_processo_antigo", return_value=False),
        patch(
            "gdrive_downloader.download_gdrive_folder",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        progress = await download_batch(
            numeros=["1234567-89.2024.8.08.0001"],
            output_dir=tmp_path,
            delay_entre_processos=0.0,
        )

    ps = progress.processos["1234567-89.2024.8.08.0001"]
    assert ps.status == "done"
    assert ps.docs_baixados == 2
    mock_pje_client.download_processo.assert_awaited_once_with(
        "1234567-89.2024.8.08.0001",
        tmp_path / "1234567-89.2024.8.08.0001",
        include_anexos=True,
        include_principais=False,
    )
    assert (
        mock_pje_client.download_processo.await_args.kwargs["include_principais"]
        is False
    )


@pytest.mark.asyncio
async def test_download_batch_reports_pending_annexes_without_session(
    tmp_path, monkeypatch
):
    """MNI-only runs should no longer hide annexes that remain pending."""
    monkeypatch.setenv("MNI_USERNAME", "u")
    monkeypatch.setenv("MNI_PASSWORD", "p")
    import pje_session

    monkeypatch.setattr(pje_session, "SESSION_FILE", tmp_path / "missing-session.json")

    from batch_downloader import download_batch

    result = MagicMock()
    result.success = True
    result.processo = MagicMock(
        documentos=[MagicMock(vinculados=[MagicMock(), MagicMock()], tipo="pdf")]
    )

    mock_client = AsyncMock()
    mock_client.health_check.return_value = {
        "status": "healthy",
        "tribunal": "T",
        "operations": [],
        "latency_ms": 1,
    }
    mock_client.consultar_processo.return_value = result
    mock_client.download_documentos.return_value = [
        {
            "nome": "principal.pdf",
            "tamanhoBytes": 10,
            "localPath": str(tmp_path / "principal.pdf"),
            "checksum": "main",
            "fonte": "mni",
        }
    ]

    with (
        patch("mni_client.MNIClient", return_value=mock_client),
        patch("gdrive_downloader.is_processo_antigo", return_value=False),
        patch(
            "gdrive_downloader.download_gdrive_folder",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        progress = await download_batch(
            numeros=["1234567-89.2024.8.08.0001"],
            output_dir=tmp_path,
            delay_entre_processos=0.0,
        )

    ps = progress.processos["1234567-89.2024.8.08.0001"]
    assert ps.status == "done"
    assert "anexo" in ps.phase_detail.lower()
    assert ps.erro is not None


@pytest.mark.asyncio
async def test_download_batch_warns_when_annex_complement_returns_empty(
    tmp_path, monkeypatch
):
    """Annex-only complement must surface a warning when the session returns no files."""
    monkeypatch.setenv("MNI_USERNAME", "u")
    monkeypatch.setenv("MNI_PASSWORD", "p")
    import pje_session

    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pje_session, "SESSION_FILE", session_file)

    from batch_downloader import download_batch

    result = MagicMock()
    result.success = True
    result.processo = MagicMock(
        documentos=[MagicMock(vinculados=[MagicMock()], tipo="pdf")]
    )

    mock_client = AsyncMock()
    mock_client.health_check.return_value = {
        "status": "healthy",
        "tribunal": "T",
        "operations": [],
        "latency_ms": 1,
    }
    mock_client.consultar_processo.return_value = result
    mock_client.download_documentos.return_value = [
        {
            "nome": "principal.pdf",
            "tamanhoBytes": 10,
            "localPath": str(tmp_path / "principal.pdf"),
            "checksum": "main",
            "fonte": "mni",
        }
    ]

    mock_pje_client = AsyncMock()
    mock_pje_client.download_processo.return_value = []

    with (
        patch("mni_client.MNIClient", return_value=mock_client),
        patch("pje_session.PJeSessionClient", return_value=mock_pje_client),
        patch("gdrive_downloader.is_processo_antigo", return_value=False),
        patch(
            "gdrive_downloader.download_gdrive_folder",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        progress = await download_batch(
            numeros=["1234567-89.2024.8.08.0001"],
            output_dir=tmp_path,
            delay_entre_processos=0.0,
        )

    ps = progress.processos["1234567-89.2024.8.08.0001"]
    assert ps.status == "done"
    assert ps.erro is not None
    assert "não retornou anexos" in ps.erro


# ─────────────────────────────────────────────
# Batch audit trail
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_audit_started(tmp_path, monkeypatch):
    """download_batch logs audit entry with event_type='batch_started'."""
    monkeypatch.setenv("MNI_USERNAME", "u")
    monkeypatch.setenv("MNI_PASSWORD", "p")

    async def fake_consultar(numero, **kwargs):
        result = MagicMock()
        result.success = False
        result.error = "fake"
        return result

    mock_client = AsyncMock()
    mock_client.health_check.return_value = {
        "status": "healthy",
        "tribunal": "T",
        "operations": [],
        "latency_ms": 1,
    }
    mock_client.consultar_processo.side_effect = fake_consultar

    mock_class = MagicMock(return_value=mock_client)
    numeros = ["1000001-01.2024.8.08.0001"]

    from batch_downloader import download_batch

    with (
        patch("mni_client.MNIClient", mock_class),
        patch("gdrive_downloader.is_processo_antigo", return_value=False),
        patch(
            "gdrive_downloader.download_gdrive_folder",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch("audit.log_access") as mock_audit,
    ):
        await download_batch(
            numeros=numeros,
            output_dir=tmp_path,
            delay_entre_processos=0.0,
        )

    # Find the batch_started call
    started_calls = [
        c for c in mock_audit.call_args_list if c[0][0].event_type == "batch_started"
    ]
    assert len(started_calls) == 1
    entry = started_calls[0][0][0]
    assert entry.fonte == "batch"
    assert entry.status == "success"
    assert "1000001-01.2024.8.08.0001" in entry.processo_numero


@pytest.mark.asyncio
async def test_batch_audit_completed(tmp_path, monkeypatch):
    """download_batch logs audit entry with event_type='batch_completed'."""
    monkeypatch.setenv("MNI_USERNAME", "u")
    monkeypatch.setenv("MNI_PASSWORD", "p")

    async def fake_consultar(numero, **kwargs):
        result = MagicMock()
        result.success = False
        result.error = "fake"
        return result

    mock_client = AsyncMock()
    mock_client.health_check.return_value = {
        "status": "healthy",
        "tribunal": "T",
        "operations": [],
        "latency_ms": 1,
    }
    mock_client.consultar_processo.side_effect = fake_consultar

    mock_class = MagicMock(return_value=mock_client)
    numeros = ["2000001-01.2024.8.08.0001"]

    from batch_downloader import download_batch

    with (
        patch("mni_client.MNIClient", mock_class),
        patch("gdrive_downloader.is_processo_antigo", return_value=False),
        patch(
            "gdrive_downloader.download_gdrive_folder",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch("audit.log_access") as mock_audit,
    ):
        await download_batch(
            numeros=numeros,
            output_dir=tmp_path,
            delay_entre_processos=0.0,
        )

    # Find the batch_completed call
    completed_calls = [
        c for c in mock_audit.call_args_list if c[0][0].event_type == "batch_completed"
    ]
    assert len(completed_calls) == 1
    entry = completed_calls[0][0][0]
    assert entry.fonte == "batch"
    assert entry.status == "success"
    assert entry.duracao_s is not None
    assert "2000001-01.2024.8.08.0001" in entry.processo_numero
