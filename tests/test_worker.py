"""Tests for worker.py — session lock release, MNI credentials fail-fast."""

from __future__ import annotations

import json
from unittest.mock import ANY, AsyncMock, MagicMock, patch
import pytest
from prometheus_client import generate_latest


def _load_worker_module():
    """Import worker with heavy dependencies mocked out."""
    import importlib
    import os

    import redis as _real_redis

    # Ensure DOWNLOAD_BASE_DIR points somewhere writable before module-level mkdir runs
    os.environ.setdefault("DOWNLOAD_BASE_DIR", "/tmp/pje-test-downloads")
    os.environ.setdefault("SESSION_STATE_PATH", "/tmp/pje-test-session.json")

    mock_redis_module = MagicMock()
    mock_redis_module.from_url = MagicMock(return_value=AsyncMock())
    # Preserve real exception classes so `except redis.ConnectionError` works
    mock_redis_module.ConnectionError = _real_redis.ConnectionError
    mock_redis_module.TimeoutError = _real_redis.TimeoutError
    mock_playwright_module = MagicMock()

    with patch.dict(
        "sys.modules",
        {
            "redis": mock_redis_module,
            "redis.asyncio": mock_redis_module,
            "playwright": mock_playwright_module,
            "playwright.async_api": mock_playwright_module,
            "mni_client": MagicMock(),
        },
    ):
        import worker as w

        importlib.reload(w)
        return w


class TestInvalidateSession:
    """invalidate_session() must release the session lock."""

    @pytest.mark.asyncio
    async def test_invalidate_session_releases_lock(self):
        """Lock file handle must be closed and cleared after invalidate_session()."""
        w = _load_worker_module()
        worker = w.PJeSessionWorker()

        released = []
        worker._release_session_lock = lambda: released.append(True)
        worker.page = None
        worker.context = None
        worker._browser = None

        await worker.invalidate_session()
        assert released == [True]

    @pytest.mark.asyncio
    async def test_invalidate_session_clears_browser_refs(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()

        mock_page = AsyncMock()
        mock_ctx = AsyncMock()
        mock_browser = AsyncMock()
        worker.page = mock_page
        worker.context = mock_ctx
        worker._browser = mock_browser
        worker._release_session_lock = lambda: None

        await worker.invalidate_session()

        mock_page.close.assert_awaited_once()
        mock_ctx.close.assert_awaited_once()
        mock_browser.close.assert_awaited_once()
        assert worker.page is None
        assert worker.context is None
        assert worker._browser is None


class TestWorkerInit:
    """init() must log error and skip MNI when credentials are missing."""

    @pytest.mark.asyncio
    async def test_missing_mni_username_logs_error(self, monkeypatch):
        monkeypatch.setenv("MNI_USERNAME", "")
        monkeypatch.setenv("MNI_PASSWORD", "pass")

        w = _load_worker_module()
        w.MNI_ENABLED = True

        worker = w.PJeSessionWorker()
        logged = []

        def fake_error(event, **kwargs):
            logged.append(event)

        worker_log = MagicMock()
        worker_log.error = fake_error

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        with (
            patch.object(w, "log", worker_log),
            patch.object(w.redis, "from_url", return_value=mock_redis),
        ):
            await worker.init()

        assert any("credentials" in e for e in logged)
        assert worker.mni_client is None

    @pytest.mark.asyncio
    async def test_mni_available_does_not_start_browser(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.mni_client = MagicMock()

        playwright = MagicMock()
        playwright.chromium.launch = AsyncMock(side_effect=AssertionError("launch"))

        result = await worker.load_session(playwright)

        assert result is True
        assert worker.session_valid is False
        assert worker.fallback_ready is False
        assert worker.session_started_at is not None
        playwright.chromium.launch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_health_reports_fallback_readiness_separately(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.redis = AsyncMock()
        worker.redis.ping = AsyncMock(return_value=True)
        worker.mni_client = MagicMock()
        worker._health_status = "ready"
        worker.session_valid = False
        worker.fallback_ready = False

        response = await worker._health_handler(MagicMock())
        body = json.loads(response.text)

        assert response.status == 200
        assert body["session_valid"] is False
        assert body["fallback_ready"] is False

    @pytest.mark.asyncio
    async def test_missing_mni_password_leaves_client_none(self, monkeypatch):
        monkeypatch.setenv("MNI_USERNAME", "user")
        monkeypatch.setenv("MNI_PASSWORD", "")

        w = _load_worker_module()
        w.MNI_ENABLED = True

        worker = w.PJeSessionWorker()
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        with (
            patch.object(w, "log", MagicMock()),
            patch.object(w.redis, "from_url", return_value=mock_redis),
        ):
            await worker.init()

        assert worker.mni_client is None


class TestUniqueFilename:
    def test_no_collision(self, tmp_path):
        w = _load_worker_module()
        name = w._unique_filename(tmp_path, "doc.pdf")
        assert name == "doc.pdf"

    def test_collision_appends_suffix(self, tmp_path):
        w = _load_worker_module()
        (tmp_path / "doc.pdf").write_bytes(b"x")
        name = w._unique_filename(tmp_path, "doc.pdf")
        assert name == "doc_1.pdf"

    def test_multiple_collisions(self, tmp_path):
        w = _load_worker_module()
        (tmp_path / "doc.pdf").write_bytes(b"x")
        (tmp_path / "doc_1.pdf").write_bytes(b"x")
        name = w._unique_filename(tmp_path, "doc.pdf")
        assert name == "doc_2.pdf"


class TestIsSessionExpired:
    def test_no_session_returns_true(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.session_started_at = None
        assert worker.is_session_expired() is True

    def test_recent_session_not_expired(self):
        from datetime import datetime, UTC

        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.session_started_at = datetime.now(UTC)
        assert worker.is_session_expired() is False

    def test_old_session_expired(self):
        from datetime import datetime, timedelta, UTC

        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.session_started_at = datetime.now(UTC) - timedelta(minutes=120)
        assert worker.is_session_expired() is True


class TestDetectCaptcha:
    @pytest.mark.asyncio
    async def test_no_page_returns_false(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.page = None
        assert await worker._detect_captcha() is False

    @pytest.mark.asyncio
    async def test_captcha_in_content(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        mock_page = AsyncMock()
        mock_page.content.return_value = '<div class="g-recaptcha">challenge</div>'
        mock_page.url = "https://pje.tjes.jus.br/pje/login.seam"
        worker.page = mock_page
        with patch.object(w, "log", MagicMock()):
            assert await worker._detect_captcha() is True

    @pytest.mark.asyncio
    async def test_no_captcha_in_clean_page(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        mock_page = AsyncMock()
        mock_page.content.return_value = "<html><body>Normal PJe page</body></html>"
        worker.page = mock_page
        assert await worker._detect_captcha() is False

    @pytest.mark.asyncio
    async def test_content_error_returns_false(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        mock_page = AsyncMock()
        mock_page.content.side_effect = Exception("page closed")
        worker.page = mock_page
        assert await worker._detect_captcha() is False


class TestResultHelper:
    def test_success_result(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        r = worker._result(
            "job1", "5000001-00.2024.8.08.0001", "success", [{"nome": "doc.pdf"}]
        )
        assert r["status"] == "success"
        assert r["jobId"] == "job1"
        assert r["numeroProcesso"] == "5000001-00.2024.8.08.0001"
        assert len(r["arquivosDownloaded"]) == 1

    def test_failed_result_with_error(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        r = worker._result(
            "job2", "5000002-00.2024.8.08.0001", "failed", error="timeout"
        )
        assert r["status"] == "failed"
        assert r["errorMessage"] == "timeout"

    def test_partial_success_result_with_error(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        r = worker._result(
            "job2",
            "5000002-00.2024.8.08.0001",
            "partial_success",
            [{"nome": "doc.pdf"}],
            error="faltam anexos",
        )
        assert r["status"] == "partial_success"
        assert r["errorMessage"] == "faltam anexos"
        assert len(r["arquivosDownloaded"]) == 1

    def test_result_without_files(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        r = worker._result("job3", "5000003-00.2024.8.08.0001", "session_expired")
        assert r["arquivosDownloaded"] == []

    def test_result_has_timestamp(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        r = worker._result("j", "5000001-00.2024.8.08.0001", "success")
        assert "downloadedAt" in r
        assert "T" in r["downloadedAt"]  # ISO format


class TestLogJobResult:
    @pytest.mark.asyncio
    async def test_writes_json_to_logs_dir(self, tmp_path):
        w = _load_worker_module()
        w.DOWNLOAD_BASE_DIR = tmp_path
        worker = w.PJeSessionWorker()
        files = [{"nome": "doc.pdf", "tamanhoBytes": 1024}]
        await worker._log_job_result("j1", "5000001-00.2024.8.08.0001", files)
        log_file = tmp_path / "_logs" / "j1.json"
        assert log_file.exists()
        import json

        data = json.loads(log_file.read_text())
        assert data["jobId"] == "j1"
        assert len(data["arquivos"]) == 1


class TestWorkerClose:
    @pytest.mark.asyncio
    async def test_close_all_resources(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        mock_page = AsyncMock()
        mock_ctx = AsyncMock()
        mock_browser = AsyncMock()
        mock_runner = AsyncMock()
        mock_runner.cleanup = AsyncMock()
        mock_redis = AsyncMock()
        worker.page = mock_page
        worker.context = mock_ctx
        worker._browser = mock_browser
        worker.redis = mock_redis
        worker._health_runner = mock_runner
        await worker.close()
        mock_runner.cleanup.assert_awaited_once()
        mock_page.close.assert_awaited_once()
        mock_ctx.close.assert_awaited_once()
        mock_browser.close.assert_awaited_once()
        mock_redis.close.assert_awaited_once()
        assert worker._health_runner is None

    @pytest.mark.asyncio
    async def test_close_with_none_resources(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.page = None
        worker.context = None
        worker._browser = None
        worker.redis = None
        await worker.close()  # Should not raise


def _patch_redis_exceptions(w):
    """Ensure worker module's redis mock has real exception classes."""
    from redis.exceptions import ConnectionError, TimeoutError

    w.redis.ConnectionError = ConnectionError
    w.redis.TimeoutError = TimeoutError


class TestRedisInitRetry:
    """init() retries Redis connection with exponential backoff."""

    @pytest.mark.asyncio
    async def test_init_succeeds_on_first_try(self):
        w = _load_worker_module()
        _patch_redis_exceptions(w)
        w.MNI_ENABLED = False
        worker = w.PJeSessionWorker()
        mock_r = AsyncMock()
        mock_r.ping = AsyncMock(return_value=True)
        with patch.object(w.redis, "from_url", return_value=mock_r):
            await worker.init()
        assert worker.redis is mock_r

    @pytest.mark.asyncio
    async def test_init_retries_on_connection_error(self):
        from redis import ConnectionError as RedisConnectionError

        w = _load_worker_module()
        _patch_redis_exceptions(w)
        w.MNI_ENABLED = False
        worker = w.PJeSessionWorker()
        mock_r_fail = AsyncMock()
        mock_r_fail.ping = AsyncMock(side_effect=RedisConnectionError("down"))
        mock_r_ok = AsyncMock()
        mock_r_ok.ping = AsyncMock(return_value=True)
        with (
            patch.object(w.redis, "from_url", side_effect=[mock_r_fail, mock_r_ok]),
            patch.object(w, "log", MagicMock()),
            patch("worker.asyncio.sleep", new_callable=AsyncMock),
        ):
            await worker.init(max_redis_retries=2)
        assert worker.redis is mock_r_ok

    @pytest.mark.asyncio
    async def test_init_raises_after_max_retries(self):
        from redis import ConnectionError as RedisConnectionError

        w = _load_worker_module()
        _patch_redis_exceptions(w)
        w.MNI_ENABLED = False
        worker = w.PJeSessionWorker()
        mock_r = AsyncMock()
        mock_r.ping = AsyncMock(side_effect=RedisConnectionError("down"))
        with (
            patch.object(w.redis, "from_url", return_value=mock_r),
            patch.object(w, "log", MagicMock()),
            patch("worker.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(RedisConnectionError),
        ):
            await worker.init(max_redis_retries=2)


class TestConsumeQueueShutdown:
    """consume_queue() exits gracefully on shutdown_event."""

    @pytest.mark.asyncio
    async def test_shutdown_event_breaks_loop(self):
        import asyncio

        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        mock_r = AsyncMock()
        mock_r.blpop = AsyncMock(return_value=None)
        worker.redis = mock_r

        shutdown = asyncio.Event()
        shutdown.set()  # immediate shutdown

        with patch.object(w, "log", MagicMock()):
            await worker.consume_queue(shutdown)
        # Should return without hanging

    @pytest.mark.asyncio
    async def test_backoff_logged_with_consecutive_count(self):
        import asyncio
        from redis import ConnectionError as RedisConnectionError

        w = _load_worker_module()
        _patch_redis_exceptions(w)
        worker = w.PJeSessionWorker()
        mock_r = AsyncMock()
        call_count = 0

        async def fail_twice_then_stop(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise asyncio.CancelledError
            raise RedisConnectionError("down")

        mock_r.blpop = fail_twice_then_stop
        worker.redis = mock_r
        worker.mni_client = MagicMock()  # prevent session expiry check

        logged_errors = []
        mock_log = MagicMock()
        mock_log.error = lambda event, **kw: logged_errors.append(kw)
        mock_log.info = MagicMock()
        mock_log.warning = MagicMock()

        with patch.object(w, "log", mock_log):
            try:
                await worker.consume_queue()
            except asyncio.CancelledError:
                pass

        # Should have 2 error logs with increasing consecutive count
        assert len(logged_errors) == 2
        assert logged_errors[0]["consecutive"] == 1
        assert logged_errors[1]["consecutive"] == 2
        # Retry_in should increase (backoff)
        assert logged_errors[1]["retry_in"] > logged_errors[0]["retry_in"]

    @pytest.mark.asyncio
    async def test_invalid_json_is_sent_to_dead_letter_queue(self):
        import asyncio

        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        mock_r = AsyncMock()
        shutdown = asyncio.Event()

        async def blpop(*args, **kwargs):
            shutdown.set()
            return ("kratos:pje:jobs", "{invalid")

        mock_r.blpop = blpop
        worker.redis = mock_r
        worker.mni_client = MagicMock()

        with patch.object(w, "log", MagicMock()):
            await worker.consume_queue(shutdown)

        mock_r.lpush.assert_any_await(
            w.DEAD_LETTER_QUEUE,
            ANY,
        )

    @pytest.mark.asyncio
    async def test_missing_fields_are_sent_to_dead_letter_queue(self):
        import asyncio

        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        mock_r = AsyncMock()
        shutdown = asyncio.Event()

        async def blpop(*args, **kwargs):
            shutdown.set()
            return ("kratos:pje:jobs", json.dumps({"jobId": "J1"}))

        mock_r.blpop = blpop
        worker.redis = mock_r
        worker.mni_client = MagicMock()

        with patch.object(w, "log", MagicMock()):
            await worker.consume_queue(shutdown)

        mock_r.lpush.assert_any_await(
            w.DEAD_LETTER_QUEUE,
            ANY,
        )

    @pytest.mark.asyncio
    async def test_result_is_published_to_job_reply_queue(self):
        import asyncio

        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        mock_r = AsyncMock()
        shutdown = asyncio.Event()

        async def blpop(*args, **kwargs):
            shutdown.set()
            return (
                "kratos:pje:jobs",
                json.dumps(
                    {
                        "jobId": "J1",
                        "batchId": "batch-1",
                        "replyQueue": "kratos:pje:results:batch-1",
                        "numeroProcesso": "5000001-00.2024.8.08.0001",
                    }
                ),
            )

        mock_r.blpop = blpop
        worker.redis = mock_r
        worker.mni_client = MagicMock()
        worker.download_process = AsyncMock(
            return_value={
                "jobId": "J1",
                "numeroProcesso": "5000001-00.2024.8.08.0001",
                "status": "success",
                "arquivosDownloaded": [],
                "errorMessage": None,
            }
        )
        worker._publish_result = AsyncMock()

        await worker.consume_queue(shutdown)

        worker._publish_result.assert_awaited_once()
        assert worker._publish_result.await_args.kwargs["queue_name"] == (
            "kratos:pje:results:batch-1"
        )


class TestPublishResult:
    """_publish_result() retries on Redis failure."""

    @pytest.mark.asyncio
    async def test_publish_succeeds(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        mock_r = AsyncMock()
        worker.redis = mock_r
        await worker._publish_result({"jobId": "J1", "status": "success"})
        mock_r.rpush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_publish_can_target_batch_reply_queue(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        mock_r = AsyncMock()
        worker.redis = mock_r

        await worker._publish_result(
            {"jobId": "J1", "status": "success"},
            queue_name="kratos:pje:results:batch-123",
        )

        mock_r.rpush.assert_awaited_once_with(
            "kratos:pje:results:batch-123",
            ANY,
        )

    @pytest.mark.asyncio
    async def test_publish_result_updates_metric(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.redis = AsyncMock()

        await worker._publish_result({"jobId": "J1", "status": "partial_success"})

        output = generate_latest(w.metrics.REGISTRY).decode()
        assert "pje_worker_results_total" in output
        assert 'status="partial_success"' in output

    @pytest.mark.asyncio
    async def test_publish_retries_on_failure(self):
        from redis import ConnectionError as RedisConnectionError

        w = _load_worker_module()
        _patch_redis_exceptions(w)
        worker = w.PJeSessionWorker()
        mock_r = AsyncMock()
        mock_r.rpush = AsyncMock(side_effect=[RedisConnectionError("down"), None])
        worker.redis = mock_r
        with (
            patch.object(w, "log", MagicMock()),
            patch("worker.asyncio.sleep", new_callable=AsyncMock),
        ):
            await worker._publish_result({"jobId": "J1"})
        assert mock_r.rpush.await_count == 2

    @pytest.mark.asyncio
    async def test_publish_falls_back_to_local_log(self, tmp_path):
        from redis import ConnectionError as RedisConnectionError

        w = _load_worker_module()
        _patch_redis_exceptions(w)
        worker = w.PJeSessionWorker()
        mock_r = AsyncMock()
        mock_r.rpush = AsyncMock(side_effect=RedisConnectionError("down"))
        worker.redis = mock_r
        local_log = AsyncMock()
        with (
            patch.object(w, "log", MagicMock()),
            patch("worker.asyncio.sleep", new_callable=AsyncMock),
            patch.object(worker, "_log_job_result", local_log),
        ):
            await worker._publish_result(
                {
                    "jobId": "J1",
                    "numeroProcesso": "5000001-00.2024.8.08.0001",
                    "arquivosDownloaded": [{"nome": "doc.pdf"}],
                },
                max_retries=2,
            )
        local_log.assert_awaited_once_with(
            "J1",
            "5000001-00.2024.8.08.0001",
            [{"nome": "doc.pdf"}],
        )

    @pytest.mark.asyncio
    async def test_publish_progress_uses_reply_queue(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        mock_r = AsyncMock()
        worker.redis = mock_r

        await worker._publish_progress(
            {
                "jobId": "J1",
                "batchId": "batch-1",
                "numeroProcesso": "5000001-00.2024.8.08.0001",
                "replyQueue": "kratos:pje:results:batch-1",
            },
            "mni_metadata",
            "Consultando metadados",
        )

        mock_r.rpush.assert_awaited_once_with(
            "kratos:pje:results:batch-1",
            ANY,
        )

        output = generate_latest(w.metrics.REGISTRY).decode()
        assert "pje_worker_progress_events_total" in output
        assert 'phase="mni_metadata"' in output


class TestDownloadProcess:
    @pytest.mark.asyncio
    async def test_output_subdir_is_respected(self, tmp_path):
        w = _load_worker_module()
        w.DOWNLOAD_BASE_DIR = tmp_path
        worker = w.PJeSessionWorker()
        worker.mni_client = MagicMock()
        worker._try_mni_download = AsyncMock(
            return_value=(
                [
                    {
                        "nome": "doc.pdf",
                        "checksum": "abc",
                        "tamanhoBytes": 10,
                        "localPath": str(tmp_path / "batch-1" / "proc" / "doc.pdf"),
                        "fonte": "mni",
                    }
                ],
                0,
            )
        )
        worker._log_job_result = AsyncMock()
        worker.is_session_expired = MagicMock(return_value=False)

        result = await worker.download_process(
            {
                "jobId": "J0",
                "numeroProcesso": "5000000-00.2024.8.08.0001",
                "outputSubdir": "batch-1/proc",
            }
        )

        assert result["status"] == "success"
        assert (tmp_path / "batch-1" / "proc").is_dir()

    @pytest.mark.asyncio
    async def test_gdrive_url_is_downloaded_before_other_strategies(self, tmp_path):
        w = _load_worker_module()
        w.DOWNLOAD_BASE_DIR = tmp_path
        worker = w.PJeSessionWorker()
        worker.mni_client = None
        worker.page = None
        worker.context = None
        worker._try_official_api = AsyncMock(return_value=None)
        worker._download_via_browser = AsyncMock(return_value=None)
        worker._log_job_result = AsyncMock()
        worker.is_session_expired = MagicMock(return_value=False)
        worker._publish_progress = AsyncMock()

        with patch(
            "gdrive_downloader.download_gdrive_folder",
            AsyncMock(
                return_value=[
                    {
                        "nome": "scan.pdf",
                        "checksum": "g1",
                        "tamanhoBytes": 10,
                        "fonte": "google_drive",
                    }
                ]
            ),
        ) as mock_gdrive:
            result = await worker.download_process(
                {
                    "jobId": "JG",
                    "numeroProcesso": "0126923-56.2011.8.08.0012",
                    "gdriveUrl": "https://drive.google.com/drive/folders/ABC123",
                }
            )

        mock_gdrive.assert_awaited_once()
        assert result["status"] == "partial_success"
        assert any(
            item["fonte"] == "google_drive" for item in result["arquivosDownloaded"]
        )
        worker._publish_progress.assert_awaited()

    @pytest.mark.asyncio
    async def test_mni_pending_annexes_returns_warning(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.mni_client = MagicMock()
        worker.page = None
        worker.context = None
        worker._try_mni_download = AsyncMock(
            return_value=(
                [
                    {
                        "nome": "doc.pdf",
                        "checksum": "abc",
                        "tamanhoBytes": 10,
                        "fonte": "mni",
                    }
                ],
                2,
            )
        )
        worker._log_job_result = AsyncMock()
        worker.is_session_expired = MagicMock(return_value=False)

        result = await worker.download_process(
            {
                "jobId": "J1",
                "numeroProcesso": "5000001-00.2024.8.08.0001",
                "includeAnexos": True,
            }
        )

        assert result["status"] == "partial_success"
        assert result["errorMessage"]
        assert "anexo" in result["errorMessage"]
        worker._log_job_result.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mni_plus_api_merges_without_duplicates(self):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.mni_client = MagicMock()
        worker.page = AsyncMock()
        worker.context = AsyncMock()
        worker._try_mni_download = AsyncMock(
            return_value=(
                [
                    {
                        "nome": "principal.pdf",
                        "checksum": "dup",
                        "tamanhoBytes": 10,
                        "fonte": "mni",
                    }
                ],
                1,
            )
        )
        worker._try_official_api = AsyncMock(
            return_value=[
                {
                    "nome": "principal-copy.pdf",
                    "checksum": "dup",
                    "tamanhoBytes": 10,
                    "fonte": "api_rest",
                },
                {
                    "nome": "anexo.pdf",
                    "checksum": "annex",
                    "tamanhoBytes": 5,
                    "fonte": "api_rest",
                },
            ]
        )
        worker._download_via_browser = AsyncMock(return_value=None)
        worker._log_job_result = AsyncMock()
        worker.is_session_expired = MagicMock(return_value=False)

        result = await worker.download_process(
            {
                "jobId": "J2",
                "numeroProcesso": "5000002-00.2024.8.08.0001",
                "includeAnexos": True,
            }
        )

        assert result["status"] == "success"
        assert len(result["arquivosDownloaded"]) == 2
        assert {f["checksum"] for f in result["arquivosDownloaded"]} == {
            "dup",
            "annex",
        }
        assert "duplicatas" in result["errorMessage"]
        worker._try_official_api.assert_awaited_once()
        assert worker._try_official_api.await_args.kwargs["incluir_principais"] is False


class TestOfficialApiFallback:
    @pytest.mark.asyncio
    async def test_accepts_list_payload(self, tmp_path):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        response = AsyncMock()
        response.status = 200
        response.json = AsyncMock(
            return_value=[
                {"id": "1", "tipo": "pdf"},
                {"id": "2", "tipo": "anexo"},
            ]
        )
        worker.page = AsyncMock()
        worker.page.request.get = AsyncMock(return_value=response)
        worker._download_document_api = AsyncMock(
            side_effect=[
                {
                    "nome": "principal.pdf",
                    "checksum": "doc-1",
                    "tamanhoBytes": 10,
                    "fonte": "api_rest",
                },
                {
                    "nome": "anexo.pdf",
                    "checksum": "doc-2",
                    "tamanhoBytes": 5,
                    "fonte": "api_rest",
                },
            ]
        )

        files = await worker._try_official_api(
            "5000003-00.2024.8.08.0001",
            tmp_path,
            incluir_anexos=True,
        )

        assert len(files) == 2
        assert worker._download_document_api.await_count == 2

    @pytest.mark.asyncio
    async def test_emits_incremental_progress_callback(self, tmp_path):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        response = AsyncMock()
        response.status = 200
        response.json = AsyncMock(
            return_value=[
                {"id": "1", "tipo": "pdf"},
                {"id": "2", "tipo": "anexo"},
            ]
        )
        worker.page = AsyncMock()
        worker.page.request.get = AsyncMock(return_value=response)
        worker._download_document_api = AsyncMock(
            side_effect=[
                {
                    "nome": "principal.pdf",
                    "checksum": "doc-1",
                    "tamanhoBytes": 10,
                    "fonte": "api_rest",
                },
                {
                    "nome": "anexo.pdf",
                    "checksum": "doc-2",
                    "tamanhoBytes": 5,
                    "fonte": "api_rest",
                },
            ]
        )
        progress_cb = AsyncMock()

        await worker._try_official_api(
            "5000003-00.2024.8.08.0001",
            tmp_path,
            incluir_anexos=True,
            progress_cb=progress_cb,
        )

        assert progress_cb.await_count == 2
        first = progress_cb.await_args_list[0].kwargs
        second = progress_cb.await_args_list[1].kwargs
        assert first["completed"] == 1
        assert first["total"] == 2
        assert first["local_bytes"] == 10
        assert second["completed"] == 2
        assert second["local_bytes"] == 15

    @pytest.mark.asyncio
    async def test_skips_annex_when_disabled(self, tmp_path):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        response = AsyncMock()
        response.status = 200
        response.json = AsyncMock(
            return_value=[
                {"id": "1", "tipo": "pdf"},
                {"id": "2", "tipo": "anexo"},
            ]
        )
        worker.page = AsyncMock()
        worker.page.request.get = AsyncMock(return_value=response)
        worker._download_document_api = AsyncMock(
            return_value={
                "nome": "principal.pdf",
                "checksum": "doc-1",
                "tamanhoBytes": 10,
                "fonte": "api_rest",
            }
        )

        files = await worker._try_official_api(
            "5000004-00.2024.8.08.0001",
            tmp_path,
            incluir_anexos=False,
        )

        assert len(files) == 1
        worker._download_document_api.assert_awaited_once()


class TestBrowserFallback:
    @pytest.mark.asyncio
    async def test_full_download_emits_progress_callback(self, tmp_path):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.page = AsyncMock()
        worker.context = AsyncMock()
        worker._try_full_download_button = AsyncMock(
            return_value=[
                {
                    "nome": "full.zip",
                    "checksum": "zip-1",
                    "tamanhoBytes": 11,
                    "fonte": "browser_full_download",
                }
            ]
        )
        progress_cb = AsyncMock()

        files = await worker._download_via_browser(
            "5000005-00.2024.8.08.0001",
            tmp_path,
            progress_cb=progress_cb,
        )

        assert len(files) == 1
        progress_cb.assert_awaited_once()
        kwargs = progress_cb.await_args.kwargs
        assert kwargs["completed"] == 1
        assert kwargs["total"] == 1
        assert kwargs["local_bytes"] == 11

    @pytest.mark.asyncio
    async def test_skips_full_download_when_only_annexes_are_pending(self, tmp_path):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.page = AsyncMock()
        worker.context = AsyncMock()
        worker._try_full_download_button = AsyncMock(
            return_value=[{"nome": "full.zip"}]
        )
        worker._download_docs_individually = AsyncMock(
            return_value=[
                {
                    "nome": "anexo.pdf",
                    "checksum": "annex",
                    "tamanhoBytes": 5,
                    "fonte": "browser_individual",
                }
            ]
        )

        files = await worker._download_via_browser(
            "5000005-00.2024.8.08.0001",
            tmp_path,
            allow_full_download=False,
        )

        worker._try_full_download_button.assert_not_awaited()
        worker._download_docs_individually.assert_awaited_once()
        assert files[0]["nome"] == "anexo.pdf"

    @pytest.mark.asyncio
    async def test_sequential_download_emits_incremental_progress(self, tmp_path):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.page = MagicMock()
        worker._detect_captcha = AsyncMock(return_value=False)

        class DownloadCtx:
            async def __aenter__(self):
                class Holder:
                    value = None

                holder = Holder()
                download = AsyncMock()
                download.suggested_filename = "doc.pdf"

                async def save_as(path):
                    from pathlib import Path as _Path

                    _Path(path).write_bytes(b"abc")

                download.save_as.side_effect = save_as

                async def _value():
                    return download

                holder.value = _value()
                return holder

            async def __aexit__(self, exc_type, exc, tb):
                return False

        worker.page.expect_download = MagicMock(return_value=DownloadCtx())
        link = AsyncMock()
        progress_cb = AsyncMock()

        files = await worker._download_docs_sequential(
            [link],
            tmp_path,
            progress_cb=progress_cb,
        )

        assert len(files) == 1
        progress_cb.assert_awaited_once()
        kwargs = progress_cb.await_args.kwargs
        assert kwargs["completed"] == 1
        assert kwargs["total"] == 1
        assert kwargs["local_bytes"] == 3


class TestMniOptimization:
    @pytest.mark.asyncio
    async def test_filtered_types_do_not_trigger_annex_tracking(self, tmp_path):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()

        processo = MagicMock(
            documentos=[
                MagicMock(vinculados=[MagicMock(), MagicMock()], tipo="sentenca")
            ]
        )
        worker.mni_client = AsyncMock()
        worker.mni_client.consultar_processo.return_value = MagicMock(
            success=True,
            processo=processo,
        )
        worker.mni_client.download_documentos = AsyncMock(return_value=[])

        files, anexos_pendentes, total_docs = await worker._try_mni_download(
            "5000005-00.2024.8.08.0001",
            tmp_path,
            tipos_documento=["sentenca"],
            incluir_anexos=True,
        )

        assert files is None
        assert anexos_pendentes == 0
        assert total_docs == 1
        worker.mni_client.download_documentos.assert_awaited_once_with(
            processo,
            tmp_path,
            ["sentenca"],
            incluir_anexos=False,
            progress_cb=None,
        )

    @pytest.mark.asyncio
    async def test_try_mni_download_reports_expected_total_with_annexes(self, tmp_path):
        w = _load_worker_module()
        worker = w.PJeSessionWorker()

        anexo_a = MagicMock()
        anexo_b = MagicMock()
        processo = MagicMock(
            documentos=[
                MagicMock(vinculados=[anexo_a, anexo_b], tipo="sentenca"),
                MagicMock(vinculados=[], tipo="decisao"),
            ]
        )
        worker.mni_client = AsyncMock()
        worker.mni_client.consultar_processo.return_value = MagicMock(
            success=True,
            processo=processo,
        )
        progress_cb = AsyncMock()
        worker.mni_client.download_documentos = AsyncMock(
            return_value=[{"nome": "a.pdf"}]
        )

        files, anexos_pendentes, total_docs = await worker._try_mni_download(
            "5000005-00.2024.8.08.0001",
            tmp_path,
            incluir_anexos=True,
            progress_cb=progress_cb,
        )

        assert files == [{"nome": "a.pdf"}]
        assert anexos_pendentes == 2
        assert total_docs == 4
        worker.mni_client.download_documentos.assert_awaited_once_with(
            processo,
            tmp_path,
            None,
            incluir_anexos=True,
            progress_cb=progress_cb,
        )


class TestOfficialApiNoCookieLeak:
    """Audit P1: `_try_official_api` logs ``str(exc)`` from response.json().
    Playwright error payloads for PJe 5xx pages can include response body
    (Set-Cookie, session tokens). Logs should carry status / type only.
    """

    @pytest.mark.asyncio
    async def test_html_error_body_in_exception_not_logged(self, tmp_path):
        import structlog

        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        response = AsyncMock()
        # Vulnerable path: status 200 but body is HTML (session expired login
        # redirect etc.). response.json() raises; current code catches with
        # `except Exception: log.warning(reason=str(exc))` which dumps the
        # HTML — and some PJe login pages echo Set-Cookie in the body.
        response.status = 200
        response.json = AsyncMock(
            side_effect=Exception(
                "Set-Cookie: JSESSIONID=SECRET_SESSION_TOKEN; <html>server error</html>"
            )
        )
        worker.page = AsyncMock()
        worker.page.request.get = AsyncMock(return_value=response)

        with structlog.testing.capture_logs() as logs:
            result = await worker._try_official_api(
                "5000003-00.2024.8.08.0001", tmp_path
            )

        assert result is None
        for r in logs:
            dump = repr(r)
            assert "SECRET_SESSION_TOKEN" not in dump, (
                f"cookie leaked into log record: {dump!r}"
            )
            assert "JSESSIONID" not in dump


class TestMniCancelledPropagation:
    """Audit P1: `_try_mni_download`'s broad ``except Exception`` swallowed
    ``CancelledError`` too, so a SIGTERM mid-SOAP would report the job as
    "no documents" instead of preserving partial state / letting the loop
    shut down cleanly.
    """

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self, tmp_path):
        import asyncio

        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.mni_client = AsyncMock()
        worker.mni_client.consultar_processo = AsyncMock(
            side_effect=asyncio.CancelledError()
        )

        with pytest.raises(asyncio.CancelledError):
            await worker._try_mni_download(
                "5000001-00.2024.8.08.0001",
                tmp_path,
                incluir_anexos=True,
            )

    @pytest.mark.asyncio
    async def test_other_exceptions_still_swallowed(self, tmp_path):
        """Regression: non-cancel errors still fall back to the silent
        return path so the strategy cascade can try the next one.
        """
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.mni_client = AsyncMock()
        worker.mni_client.consultar_processo = AsyncMock(
            side_effect=RuntimeError("tribunal reset peer")
        )

        files, anexos, total = await worker._try_mni_download(
            "5000001-00.2024.8.08.0001",
            tmp_path,
            incluir_anexos=True,
        )
        assert files is None
        assert anexos == 0
        assert total == 0


# ─────────────────────────────────────────────
# Browser fallback (strategy 3) — audit P0.2
# worker.py:1003-1361 was 0% covered. These are characterization tests
# that pin current behaviour at the Playwright-Page boundary; each one
# tightens a narrow path without needing a real browser.
# ─────────────────────────────────────────────


def _fake_locator(count: int = 0, visible: bool = True):
    """Build a Playwright-locator stub with ``.first``, ``.count()``,
    ``.is_visible()``, ``.click()``, ``.fill()``, ``.get_attribute()``.
    """
    loc = MagicMock()
    loc.count = AsyncMock(return_value=count)
    loc.is_visible = AsyncMock(return_value=visible)
    loc.click = AsyncMock()
    loc.fill = AsyncMock()
    loc.get_attribute = AsyncMock(return_value=None)
    loc.all = AsyncMock(return_value=[])
    loc.first = loc  # chainable .first
    loc.locator = MagicMock(return_value=loc)
    return loc


class TestDownloadViaBrowser:
    @pytest.mark.asyncio
    async def test_returns_none_when_session_not_initialized(self, tmp_path):
        """Without `page` + `context`, the browser strategy is a no-op."""
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.page = None
        worker.context = None

        result = await worker._download_via_browser(
            "5000001-00.2024.8.08.0001", tmp_path
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_happy_path_returns_full_download_and_fires_progress(self, tmp_path):
        """When the full-download button succeeds the result short-circuits
        past the individual-download fallback.
        """
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.page = AsyncMock()
        worker.context = AsyncMock()

        full_files = [
            {"nome": "completo.pdf", "tipo": "completo", "tamanhoBytes": 12345}
        ]
        worker._try_full_download_button = AsyncMock(return_value=full_files)
        worker._download_docs_individually = AsyncMock()  # must NOT be called
        progress = AsyncMock()

        result = await worker._download_via_browser(
            "5000001-00.2024.8.08.0001",
            tmp_path,
            progress_cb=progress,
        )

        assert result == full_files
        worker._download_docs_individually.assert_not_awaited()
        progress.assert_awaited_once()
        kwargs = progress.await_args.kwargs
        assert kwargs["completed"] == 1
        assert kwargs["total"] == 1
        assert kwargs["local_bytes"] == 12345

    @pytest.mark.asyncio
    async def test_allow_full_download_false_skips_to_individual(self, tmp_path):
        """When called for the anexos top-up we must NOT redownload the whole
        process; go straight to individual documents.
        """
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.page = AsyncMock()
        worker.context = AsyncMock()
        worker._try_full_download_button = AsyncMock(
            return_value=["should-not-be-used"]
        )
        worker._download_docs_individually = AsyncMock(
            return_value=[{"nome": "anexo.pdf"}]
        )

        result = await worker._download_via_browser(
            "5000001-00.2024.8.08.0001",
            tmp_path,
            allow_full_download=False,
        )

        worker._try_full_download_button.assert_not_awaited()
        worker._download_docs_individually.assert_awaited_once()
        assert result == [{"nome": "anexo.pdf"}]

    @pytest.mark.asyncio
    async def test_full_download_empty_result_falls_through_to_individual(
        self, tmp_path
    ):
        """If strategy 3a returns [] or None, strategy 3b is attempted."""
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.page = AsyncMock()
        worker.context = AsyncMock()
        worker._try_full_download_button = AsyncMock(return_value=None)
        worker._download_docs_individually = AsyncMock(return_value=[{"nome": "x.pdf"}])

        result = await worker._download_via_browser(
            "5000001-00.2024.8.08.0001", tmp_path
        )
        assert result == [{"nome": "x.pdf"}]
        worker._download_docs_individually.assert_awaited_once()


class TestTryFullDownloadButton:
    @pytest.mark.asyncio
    async def test_captcha_after_first_navigation_aborts(self, tmp_path):
        """If PJe shows a CAPTCHA the method returns None — the strategy
        cascade should then try the next option, not retry.
        """
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.page = AsyncMock()
        worker.page.locator = MagicMock(return_value=_fake_locator())
        worker._detect_captcha = AsyncMock(return_value=True)

        result = await worker._try_full_download_button(
            "5000001-00.2024.8.08.0001", tmp_path
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_button_not_found_returns_none(self, tmp_path, monkeypatch):
        """When every download-button selector yields ``count=0`` the
        method exits cleanly without a spurious click.
        """
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.page = AsyncMock()
        # Every locator call returns "no elements"
        worker.page.locator = MagicMock(return_value=_fake_locator(count=0))
        worker._detect_captcha = AsyncMock(return_value=False)
        # _try_full_download_button does a couple of asyncio.sleep — shortcut
        monkeypatch.setattr(w.asyncio, "sleep", AsyncMock())

        result = await worker._try_full_download_button(
            "5000001-00.2024.8.08.0001", tmp_path
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_unexpected_exception_is_swallowed_and_logged(self, tmp_path):
        """Any error during navigation is logged and returns None — it must
        NEVER propagate out of the strategy cascade.
        """
        w = _load_worker_module()
        worker = w.PJeSessionWorker()
        worker.page = AsyncMock()
        worker.page.goto = AsyncMock(side_effect=RuntimeError("net broke"))

        result = await worker._try_full_download_button(
            "5000001-00.2024.8.08.0001", tmp_path
        )
        assert result is None
