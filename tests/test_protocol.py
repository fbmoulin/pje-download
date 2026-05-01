"""Round-trip and validation tests for protocol.py typed Redis message helpers."""

from __future__ import annotations

import json

import pytest

from protocol import (
    DeadLetterEntry,
    ProgressMessage,
    ResultMessage,
    dead_letter_to_json,
    job_from_json,
    progress_to_json,
    result_to_json,
)


class TestJobFromJson:
    def test_job_from_json_happy_path(self):
        """Valid JSON with all required + optional fields parses correctly."""
        data = {
            "jobId": "abc-123",
            "numeroProcesso": "0001234-56.2024.8.08.0000",
            "batchId": "batch-001",
            "replyQueue": "kratos:pje:reply:batch-001",
            "outputSubdir": "batch-001/proc",
            "gdriveUrl": "https://drive.google.com/folder/xyz",
            "includeAnexos": True,
            "delayEntreProcessos": 2.5,
        }
        msg = job_from_json(json.dumps(data))
        assert msg["jobId"] == "abc-123"
        assert msg["numeroProcesso"] == "0001234-56.2024.8.08.0000"
        assert msg["batchId"] == "batch-001"
        assert msg["includeAnexos"] is True
        assert msg["delayEntreProcessos"] == 2.5

    def test_job_from_json_minimal(self):
        """Only required fields (jobId + numeroProcesso) parses without error."""
        raw = json.dumps({"jobId": "j1", "numeroProcesso": "0000001-00.2024.8.08.0001"})
        msg = job_from_json(raw)
        assert msg["jobId"] == "j1"
        assert msg["numeroProcesso"] == "0000001-00.2024.8.08.0001"

    def test_job_from_json_invalid_json(self):
        """Malformed JSON raises json.JSONDecodeError."""
        with pytest.raises(json.JSONDecodeError):
            job_from_json("{not valid json")

    def test_job_from_json_missing_job_id(self):
        """Missing jobId raises ValueError."""
        raw = json.dumps({"numeroProcesso": "0000001-00.2024.8.08.0001"})
        with pytest.raises(ValueError, match="jobId"):
            job_from_json(raw)

    def test_job_from_json_missing_numero_processo(self):
        """Missing numeroProcesso raises ValueError."""
        raw = json.dumps({"jobId": "j99"})
        with pytest.raises(ValueError, match="numeroProcesso"):
            job_from_json(raw)


class TestResultToJson:
    def test_result_to_json_roundtrip(self):
        """result_to_json serialises and json.loads recovers all keys."""
        msg: ResultMessage = {
            "jobId": "j1",
            "numeroProcesso": "0000001-00.2024.8.08.0001",
            "status": "done",
            "arquivosDownloaded": [{"nome": "doc.pdf", "tamanhoBytes": 1024}],
            "errorMessage": None,
            "downloadedAt": "2026-04-18T12:00:00+00:00",
        }
        recovered = json.loads(result_to_json(msg))
        assert recovered["jobId"] == "j1"
        assert recovered["status"] == "done"
        assert recovered["arquivosDownloaded"][0]["nome"] == "doc.pdf"
        assert recovered["errorMessage"] is None


class TestProgressToJson:
    def test_progress_to_json_has_event_type(self):
        """Serialised ProgressMessage always contains eventType == 'progress'."""
        msg: ProgressMessage = {
            "eventType": "progress",
            "jobId": "j2",
            "numeroProcesso": "0000002-00.2024.8.08.0001",
            "status": "running",
            "phase": "downloading",
            "phase_detail": "Baixando documento 3 de 10",
            "total_docs": 10,
            "docs_baixados": 3,
            "tamanho_bytes": 512000,
            "erro": None,
            "updatedAt": "2026-04-18T12:01:00+00:00",
        }
        recovered = json.loads(progress_to_json(msg))
        assert recovered["eventType"] == "progress"
        assert recovered["phase"] == "downloading"
        assert recovered["docs_baixados"] == 3


class TestDeadLetterToJson:
    def test_dead_letter_to_json_roundtrip(self):
        """dead_letter_to_json serialises and json.loads recovers all keys."""
        entry: DeadLetterEntry = {
            "reason": "invalid_json",
            "payload": "{bad",
            "details": {"error": "Expecting property name"},
            "timestamp": "2026-04-18T12:02:00+00:00",
        }
        recovered = json.loads(dead_letter_to_json(entry))
        assert recovered["reason"] == "invalid_json"
        assert recovered["payload"] == "{bad"
        assert recovered["details"]["error"] == "Expecting property name"
        assert "timestamp" in recovered
