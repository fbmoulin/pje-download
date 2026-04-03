"""Tests for batch_downloader — BatchProgress, load_processos_from_file, concurrent batch."""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

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

    from batch_downloader import download_batch

    progress = await download_batch(
        numeros=["1234567-89.2024.8.08.0001"],
        output_dir=tmp_path,
    )

    assert progress.failed == 1
    ps = progress.processos["1234567-89.2024.8.08.0001"]
    assert ps.status == "failed"
    assert "MNI_USERNAME" in ps.erro or "MNI_PASSWORD" in ps.erro
