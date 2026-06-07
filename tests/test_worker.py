@@
 class TestConsumeQueueShutdown:
@@
     @pytest.mark.asyncio
     async def test_shutdown_event_breaks_loop(self):
@@
         with patch.object(w, "log", MagicMock()):
             await worker.consume_queue(shutdown)
         # Should return without hanging
@@
     @pytest.mark.asyncio
     async def test_backoff_logged_with_consecutive_count(self):
@@
         assert logged_errors[1]["retry_in"] > logged_errors[0]["retry_in"]
+
+    @pytest.mark.asyncio
+    async def test_session_expired_mid_job_exits_loop(self):
+        """
+        Verify that the consume_queue loop exits after processing a job whose result status is "session_expired" when no MNI fallback is available.
+        
+        This ensures the worker does not repeatedly retry jobs that fail due to an invalidated PJe session and instead stops consuming further jobs in that condition.
+        """
+        import asyncio
+
+        w = _load_worker_module()
+        worker = w.PJeSessionWorker()
+        mock_r = AsyncMock()
+        shutdown = asyncio.Event()
+
+        called_count = 0
+
+        async def blpop_one_job(*args, **kwargs):
+            """Return one BLPOP item and increment called_count."""
+            nonlocal called_count
+            called_count += 1
+            return (
+                "kratos:pje:jobs",
+                json.dumps(
+                    {
+                        "jobId": "J-expire",
+                        "batchId": "batch-1",
+                        "replyQueue": "kratos:pje:results:batch-1",
+                        "numeroProcesso": "1234567-00.2024.8.08.0001",
+                    }
+                ),
+            )
+
+        mock_r.blpop = blpop_one_job
+        worker.redis = mock_r
+        worker.mni_client = None  # no MNI fallback — session expiry must break loop
+        worker.is_session_expired = MagicMock(return_value=False)
+        worker.download_process = AsyncMock(
+            return_value={
+                "jobId": "J-expire",
+                "numeroProcesso": "1234567-00.2024.8.08.0001",
+                "status": "session_expired",
+                "arquivosDownloaded": [],
+                "errorMessage": "session expired",
+            }
+        )
+        worker._publish_result = AsyncMock()
+
+        with patch.object(w, "log", MagicMock()):
+            await asyncio.wait_for(worker.consume_queue(shutdown), timeout=2)
+
+        # Must have processed exactly one job then exited (not looped forever).
+        assert called_count == 1, (
+            f"Expected loop to exit after session_expired, but blpop was called {called_count} times"
+        )
+        worker._publish_result.assert_awaited_once()
