"""Tests for audit_sync.py — Phase 2 audit trail to Railway Postgres."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import audit_sync


def _make_jsonl(path: Path, entries: list[dict]) -> None:
    """Write JSON-L file from entries, each \\n-terminated (atomic)."""
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


def _audit_entry(**overrides) -> dict:
    base = {
        "event_type": "document_saved",
        "processo_numero": "0001234-56.2024.8.08.0001",
        "fonte": "mni_soap",
        "tribunal": "TJES",
        "status": "success",
        "timestamp": "2026-04-17T04:00:00+00:00",
        "documento_id": None,
        "documento_tipo": None,
        "documento_nome": None,
        "tamanho_bytes": None,
        "checksum_sha256": None,
        "batch_id": None,
        "client_ip": None,
        "api_key_hash": None,
        "erro": None,
        "duracao_s": None,
    }
    base.update(overrides)
    return base


def _factory_kwargs(**overrides) -> dict:
    """Default kwargs for create_syncer — override per test."""
    defaults = dict(
        enabled=True,
        database_url="postgres://u:p@host:5432/db",
        audit_dir=Path("/tmp/audit_test"),
        interval_secs=300,
        batch_size=100,
        catchup_days=7,
        retention_days=90,
        drain_timeout_secs=5.0,
        app_env="development",
        auto_migrate=False,
    )
    defaults.update(overrides)
    return defaults


class TestScrubUrl:
    def test_scrubs_password_from_postgres_url(self):
        url = "postgres://user:SECRET@host.railway.app:5432/db?sslmode=require"
        assert audit_sync._scrub_url(url) == (
            "postgres://user:***@host.railway.app:5432/db?sslmode=require"
        )

    def test_noop_when_no_password(self):
        url = "postgres://host.railway.app:5432/db"
        assert audit_sync._scrub_url(url) == url

    def test_scrubs_special_chars_in_password(self):
        # passwords often contain /, @, :, %, etc
        url = "postgres://user:p%40ss%2Fword@host:5432/db"
        assert "p%40ss" not in audit_sync._scrub_url(url)
        assert audit_sync._scrub_url(url).endswith(":***@host:5432/db")

    def test_empty_url(self):
        assert audit_sync._scrub_url("") == ""


class TestParseCompleteLines:
    def test_happy_path_three_full_lines(self):
        data = b'{"a":1}\n{"a":2}\n{"a":3}\n'
        parsed, consumed, malformed = audit_sync._parse_complete_lines(data)
        assert [p["a"] for p in parsed] == [1, 2, 3]
        assert consumed == len(data)
        assert malformed == 0

    def test_trailing_partial_line_is_not_consumed(self):
        # Two \n-terminated lines + partial tail (no trailing newline)
        data = b'{"a":1}\n{"a":2}\n{"a":3'  # last line truncated mid-write
        parsed, consumed, malformed = audit_sync._parse_complete_lines(data)
        assert [p["a"] for p in parsed] == [1, 2]
        # cursor MUST stop before the partial line so next tick picks it up
        assert consumed == len(b'{"a":1}\n{"a":2}\n')
        assert malformed == 0

    def test_malformed_terminated_line_is_skipped_and_counted(self):
        data = b'{"a":1}\nnot-json\n{"a":3}\n'
        parsed, consumed, malformed = audit_sync._parse_complete_lines(data)
        assert [p["a"] for p in parsed] == [1, 3]
        assert consumed == len(data)  # consumed past the bad line
        assert malformed == 1

    def test_empty_input(self):
        parsed, consumed, malformed = audit_sync._parse_complete_lines(b"")
        assert parsed == []
        assert consumed == 0
        assert malformed == 0

    def test_only_partial_line(self):
        data = b'{"incomplete'
        parsed, consumed, malformed = audit_sync._parse_complete_lines(data)
        assert parsed == []
        assert consumed == 0  # do not advance past a partial line
        assert malformed == 0

    def test_blank_lines_skipped(self):
        data = b'{"a":1}\n\n{"a":2}\n'
        parsed, consumed, malformed = audit_sync._parse_complete_lines(data)
        assert [p["a"] for p in parsed] == [1, 2]
        assert consumed == len(data)
        assert malformed == 0  # blank lines are not "malformed"


class TestCursor:
    def test_cursor_path_sibling_of_jsonl(self, tmp_path: Path):
        jsonl = tmp_path / "audit-2026-04-17.jsonl"
        assert audit_sync._cursor_path(jsonl) == (
            tmp_path / "audit-2026-04-17.jsonl.cursor"
        )

    def test_load_cursor_missing_returns_zero(self, tmp_path: Path):
        jsonl = tmp_path / "audit-2026-04-17.jsonl"
        jsonl.write_bytes(b'{"a":1}\n')
        assert audit_sync._load_cursor(jsonl) == 0

    def test_save_then_load_cursor_roundtrip(self, tmp_path: Path):
        jsonl = tmp_path / "audit-2026-04-17.jsonl"
        jsonl.write_bytes(b'{"a":1}\n' * 10)  # 80 bytes
        audit_sync._save_cursor(jsonl, 40)
        assert audit_sync._load_cursor(jsonl) == 40

    def test_load_cursor_clamps_when_greater_than_filesize(self, tmp_path: Path):
        jsonl = tmp_path / "audit-2026-04-17.jsonl"
        jsonl.write_bytes(b'{"a":1}\n')  # 8 bytes
        audit_sync._save_cursor(jsonl, 999)  # absurd offset
        # File shrank or cursor corrupted → reset to 0, don't crash
        assert audit_sync._load_cursor(jsonl) == 0

    def test_save_cursor_is_atomic_no_partial_tmp(self, tmp_path: Path):
        jsonl = tmp_path / "audit-2026-04-17.jsonl"
        jsonl.write_bytes(b"x" * 100)
        audit_sync._save_cursor(jsonl, 50)
        cursor = audit_sync._cursor_path(jsonl)
        assert cursor.exists()
        # tmp file must not linger
        tmp = cursor.with_suffix(cursor.suffix + ".tmp")
        assert not tmp.exists()

    def test_save_cursor_overwrites(self, tmp_path: Path):
        jsonl = tmp_path / "audit-2026-04-17.jsonl"
        jsonl.write_bytes(b"x" * 100)
        audit_sync._save_cursor(jsonl, 30)
        audit_sync._save_cursor(jsonl, 70)
        assert audit_sync._load_cursor(jsonl) == 70

    def test_load_cursor_with_corrupted_file_returns_zero(self, tmp_path: Path):
        jsonl = tmp_path / "audit-2026-04-17.jsonl"
        jsonl.write_bytes(b"x" * 100)
        cursor = audit_sync._cursor_path(jsonl)
        cursor.write_text("not json at all")
        assert audit_sync._load_cursor(jsonl) == 0


class TestCreateSyncer:
    def test_disabled_returns_none(self, tmp_path: Path):
        result = audit_sync.create_syncer(
            **_factory_kwargs(enabled=False, audit_dir=tmp_path)
        )
        assert result is None

    def test_missing_url_in_production_raises(self, tmp_path: Path):
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            audit_sync.create_syncer(
                **_factory_kwargs(
                    enabled=True,
                    database_url="",
                    app_env="production",
                    audit_dir=tmp_path,
                )
            )

    def test_missing_url_in_development_returns_none_with_warning(
        self, tmp_path: Path, caplog
    ):
        caplog.set_level(logging.WARNING, logger="kratos.audit_sync")
        result = audit_sync.create_syncer(
            **_factory_kwargs(
                enabled=True,
                database_url="",
                app_env="development",
                audit_dir=tmp_path,
            )
        )
        assert result is None
        assert any("DATABASE_URL" in r.message for r in caplog.records)

    def test_enabled_with_url_returns_syncer(self, tmp_path: Path):
        syncer = audit_sync.create_syncer(
            **_factory_kwargs(enabled=True, audit_dir=tmp_path)
        )
        assert isinstance(syncer, audit_sync.AuditSyncer)

    def test_factory_does_not_connect_to_postgres(self, tmp_path: Path):
        # Must be lazy — pool created inside run_forever, not at factory time
        syncer = audit_sync.create_syncer(
            **_factory_kwargs(
                database_url="postgres://u:p@nonexistent.invalid/db",
                audit_dir=tmp_path,
            )
        )
        assert syncer is not None
        assert syncer._pool is None

    def test_retention_shorter_than_catchup_warns(self, tmp_path: Path, caplog):
        caplog.set_level(logging.WARNING, logger="kratos.audit_sync")
        audit_sync.create_syncer(
            **_factory_kwargs(
                retention_days=3,  # less than catchup 7
                catchup_days=7,
                audit_dir=tmp_path,
            )
        )
        assert any(
            "retention" in r.message.lower() and "catchup" in r.message.lower()
            for r in caplog.records
        )

    def test_retention_equal_to_catchup_plus_margin_does_not_warn(
        self, tmp_path: Path, caplog
    ):
        caplog.set_level(logging.WARNING, logger="kratos.audit_sync")
        audit_sync.create_syncer(
            **_factory_kwargs(
                retention_days=9,  # catchup (7) + margin (2)
                catchup_days=7,
                audit_dir=tmp_path,
            )
        )
        assert not any(
            "retention" in r.message.lower() and "catchup" in r.message.lower()
            for r in caplog.records
        )

    def test_syncer_scrubs_password_in_repr(self, tmp_path: Path):
        syncer = audit_sync.create_syncer(
            **_factory_kwargs(
                database_url="postgres://u:LEAKED@h/db", audit_dir=tmp_path
            )
        )
        assert syncer is not None
        assert "LEAKED" not in repr(syncer)


class TestTick:
    @pytest.fixture
    def syncer_with_mocked_db(self, tmp_path: Path):
        """Returns a syncer ready for _tick with pool+insert mocked."""
        syncer = audit_sync.create_syncer(
            **_factory_kwargs(audit_dir=tmp_path, batch_size=100)
        )
        assert syncer is not None
        syncer._ensure_pool = AsyncMock()
        syncer._insert_batch = AsyncMock()
        return syncer

    @pytest.mark.asyncio
    async def test_tick_inserts_rows_and_advances_cursor(
        self, tmp_path: Path, syncer_with_mocked_db
    ):
        jsonl = tmp_path / f"audit-{date.today()}.jsonl"
        _make_jsonl(jsonl, [_audit_entry(documento_id=f"D{i}") for i in range(3)])

        await syncer_with_mocked_db._tick()

        assert syncer_with_mocked_db._insert_batch.await_count == 1
        inserted = syncer_with_mocked_db._insert_batch.await_args.args[0]
        assert [r["documento_id"] for r in inserted] == ["D0", "D1", "D2"]
        assert audit_sync._load_cursor(jsonl) == jsonl.stat().st_size

    @pytest.mark.asyncio
    async def test_tick_insert_failure_leaves_cursor_untouched(
        self, tmp_path: Path, syncer_with_mocked_db
    ):
        jsonl = tmp_path / f"audit-{date.today()}.jsonl"
        _make_jsonl(jsonl, [_audit_entry()])
        syncer_with_mocked_db._insert_batch.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await syncer_with_mocked_db._tick()

        assert audit_sync._load_cursor(jsonl) == 0

    @pytest.mark.asyncio
    async def test_tick_chunks_into_batches(
        self, tmp_path: Path, syncer_with_mocked_db
    ):
        syncer_with_mocked_db.batch_size = 50
        jsonl = tmp_path / f"audit-{date.today()}.jsonl"
        _make_jsonl(jsonl, [_audit_entry(documento_id=f"D{i}") for i in range(120)])

        await syncer_with_mocked_db._tick()

        # 120 rows in chunks of 50 → 3 calls (50, 50, 20)
        assert syncer_with_mocked_db._insert_batch.await_count == 3
        sizes = [
            len(c.args[0]) for c in syncer_with_mocked_db._insert_batch.await_args_list
        ]
        assert sizes == [50, 50, 20]
        assert audit_sync._load_cursor(jsonl) == jsonl.stat().st_size

    @pytest.mark.asyncio
    async def test_tick_ignores_files_older_than_catchup_window(
        self, tmp_path: Path, syncer_with_mocked_db
    ):
        syncer_with_mocked_db.catchup_days = 7
        old = date.today() - timedelta(days=100)
        old_jsonl = tmp_path / f"audit-{old}.jsonl"
        _make_jsonl(old_jsonl, [_audit_entry()])

        await syncer_with_mocked_db._tick()

        assert syncer_with_mocked_db._insert_batch.await_count == 0

    @pytest.mark.asyncio
    async def test_tick_picks_up_rotated_file_on_next_run(
        self, tmp_path: Path, syncer_with_mocked_db
    ):
        today = tmp_path / f"audit-{date.today()}.jsonl"
        _make_jsonl(today, [_audit_entry(documento_id="A")])
        await syncer_with_mocked_db._tick()
        assert syncer_with_mocked_db._insert_batch.await_count == 1

        # simulate rotation: new file appears (e.g. date rollover)
        tomorrow = tmp_path / f"audit-{date.today() + timedelta(days=1)}.jsonl"
        _make_jsonl(tomorrow, [_audit_entry(documento_id="B")])

        await syncer_with_mocked_db._tick()
        # Second tick must pick up tomorrow's file (today's has nothing new)
        assert syncer_with_mocked_db._insert_batch.await_count == 2
        last_call = syncer_with_mocked_db._insert_batch.await_args_list[-1]
        assert last_call.args[0][0]["documento_id"] == "B"

    @pytest.mark.asyncio
    async def test_tick_handles_vanished_file_referenced_by_cursor(
        self, tmp_path: Path, syncer_with_mocked_db
    ):
        # Cursor exists for a file that was deleted (retention kicked in)
        ghost = tmp_path / f"audit-{date.today() - timedelta(days=2)}.jsonl"
        _make_jsonl(ghost, [_audit_entry()])
        audit_sync._save_cursor(ghost, 50)
        ghost.unlink()

        # Should NOT raise
        await syncer_with_mocked_db._tick()
        assert syncer_with_mocked_db._insert_batch.await_count == 0

    @pytest.mark.asyncio
    async def test_tick_no_op_when_cursor_at_eof(
        self, tmp_path: Path, syncer_with_mocked_db
    ):
        jsonl = tmp_path / f"audit-{date.today()}.jsonl"
        _make_jsonl(jsonl, [_audit_entry()])
        # Cursor already points past EOF-equivalent
        audit_sync._save_cursor(jsonl, jsonl.stat().st_size)

        await syncer_with_mocked_db._tick()

        assert syncer_with_mocked_db._insert_batch.await_count == 0

    @pytest.mark.asyncio
    async def test_tick_stops_at_partial_tail_line(
        self, tmp_path: Path, syncer_with_mocked_db
    ):
        jsonl = tmp_path / f"audit-{date.today()}.jsonl"
        # Full line + partial (no newline)
        jsonl.write_text(
            json.dumps(_audit_entry()) + "\n" + '{"incomplete',
            encoding="utf-8",
        )

        await syncer_with_mocked_db._tick()

        # One complete line synced; cursor stops before the partial line
        assert syncer_with_mocked_db._insert_batch.await_count == 1
        cursor_pos = audit_sync._load_cursor(jsonl)
        assert cursor_pos < jsonl.stat().st_size
        # Exactly at the byte after the newline
        assert cursor_pos == len(json.dumps(_audit_entry()) + "\n")


class TestRunForever:
    @pytest.fixture
    def live_syncer(self, tmp_path: Path):
        syncer = audit_sync.create_syncer(
            **_factory_kwargs(
                audit_dir=tmp_path,
                interval_secs=1,  # fast tick for tests
                drain_timeout_secs=1.0,
            )
        )
        assert syncer is not None
        syncer._ensure_pool = AsyncMock()
        syncer._insert_batch = AsyncMock()
        return syncer

    @pytest.mark.asyncio
    async def test_run_forever_exits_promptly_on_shutdown(self, live_syncer):
        import asyncio

        task = asyncio.create_task(live_syncer.run_forever())
        await asyncio.sleep(0.05)  # let it start
        live_syncer.shutdown.set()
        # Should exit within the drain timeout
        await asyncio.wait_for(task, timeout=2.0)

    @pytest.mark.asyncio
    async def test_run_forever_keeps_looping_after_tick_exception(self, live_syncer):
        import asyncio

        calls = {"n": 0}

        async def flaky_tick():
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RuntimeError("transient")

        live_syncer._tick = flaky_tick
        live_syncer.interval_secs = 0  # no sleep between ticks
        task = asyncio.create_task(live_syncer.run_forever())
        # Wait until at least 3 ticks ran
        for _ in range(50):
            if calls["n"] >= 3:
                break
            await asyncio.sleep(0.02)
        live_syncer.shutdown.set()
        await asyncio.wait_for(task, timeout=2.0)
        assert calls["n"] >= 3  # survived 2 exceptions and continued


class TestLagMetric:
    @pytest.mark.asyncio
    async def test_health_snapshot_lag_none_when_nothing_synced(self, tmp_path: Path):
        syncer = audit_sync.create_syncer(**_factory_kwargs(audit_dir=tmp_path))
        assert syncer is not None
        snap = syncer.health_snapshot()
        assert snap["lag_seconds_event_time"] is None
        assert snap["rows_total"] == 0

    @pytest.mark.asyncio
    async def test_health_snapshot_lag_is_event_time_based(self, tmp_path: Path):
        from datetime import datetime, UTC, timedelta

        syncer = audit_sync.create_syncer(**_factory_kwargs(audit_dir=tmp_path))
        assert syncer is not None
        syncer._ensure_pool = AsyncMock()
        syncer._insert_batch = AsyncMock()

        ten_min_ago = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        jsonl = tmp_path / f"audit-{date.today()}.jsonl"
        _make_jsonl(jsonl, [_audit_entry(timestamp=ten_min_ago)])

        await syncer._tick()

        snap = syncer.health_snapshot()
        # ~600 seconds ago ± a few seconds of scheduling jitter
        assert 595 < snap["lag_seconds_event_time"] < 610
        assert snap["rows_total"] == 1


class TestPasswordNeverLogged:
    @pytest.mark.asyncio
    async def test_no_password_in_logs_during_lifecycle(self, tmp_path: Path, caplog):
        """Run factory → tick → failure → shutdown, assert SECRET never
        leaks into any log record."""
        import asyncio

        caplog.set_level(logging.DEBUG)
        syncer = audit_sync.create_syncer(
            **_factory_kwargs(
                database_url=("postgres://user:SUPER_SECRET_PW@h.railway.app:5432/db"),
                audit_dir=tmp_path,
                interval_secs=0,
            )
        )
        assert syncer is not None
        syncer._ensure_pool = AsyncMock()
        # Force failure path that historically leaked credentials
        syncer._insert_batch = AsyncMock(side_effect=RuntimeError("oops"))

        jsonl = tmp_path / f"audit-{date.today()}.jsonl"
        _make_jsonl(jsonl, [_audit_entry()])

        task = asyncio.create_task(syncer.run_forever())
        for _ in range(50):
            if any("tick_failed" in r.message for r in caplog.records):
                break
            await asyncio.sleep(0.02)
        syncer.shutdown.set()
        await asyncio.wait_for(task, timeout=2.0)

        # Also trigger health snapshot (renders URL)
        snap = syncer.health_snapshot()
        assert "SUPER_SECRET_PW" not in json.dumps(snap)

        # No log record — formatted message OR args — may contain the pw
        for rec in caplog.records:
            formatted = rec.getMessage()
            assert "SUPER_SECRET_PW" not in formatted
            assert "SUPER_SECRET_PW" not in str(rec.args or "")


class TestEnsurePool:
    """Pool configuration guards against Railway DB restart — kill stale
    sockets before the next sync tick picks them up (audit P1)."""

    @pytest.mark.asyncio
    async def test_pool_created_with_inactive_lifetime_cap(
        self, tmp_path: Path, monkeypatch
    ):
        import asyncpg

        syncer = audit_sync.create_syncer(**_factory_kwargs(audit_dir=tmp_path))
        assert syncer is not None
        create_pool_mock = AsyncMock(return_value=object())  # fake pool
        monkeypatch.setattr(asyncpg, "create_pool", create_pool_mock)

        await syncer._ensure_pool()

        create_pool_mock.assert_awaited_once()
        kwargs = create_pool_mock.await_args.kwargs
        assert kwargs.get("max_inactive_connection_lifetime") == 30.0, (
            "without max_inactive_connection_lifetime dead Railway sockets "
            "survive in the pool and the next tick retries against them"
        )
        assert kwargs.get("max_size") == 1, (
            "write-only audit sync every 300s only needs one connection; "
            "max_size=3 holds unnecessary slots on the Railway connection limit"
        )


class TestInsertBatch:
    @pytest.mark.asyncio
    async def test_insert_batch_retries_then_succeeds(
        self, tmp_path: Path, monkeypatch
    ):
        import asyncio as _asyncio

        syncer = audit_sync.create_syncer(**_factory_kwargs(audit_dir=tmp_path))
        assert syncer is not None

        # Pool.acquire must return an async context manager yielding conn
        conn = AsyncMock()
        # Fail twice, succeed third time
        conn.executemany.side_effect = [
            RuntimeError("transient"),
            RuntimeError("transient"),
            None,
        ]

        class FakePool:
            def acquire(self):
                return _AcquireCM(conn)

        class _AcquireCM:
            def __init__(self, c):
                self._c = c

            async def __aenter__(self):
                return self._c

            async def __aexit__(self, *args):
                return None

        syncer._pool = FakePool()
        # No real sleeping in retry backoff
        monkeypatch.setattr(_asyncio, "sleep", AsyncMock())

        await syncer._insert_batch([_audit_entry()])
        assert conn.executemany.await_count == 3

    @pytest.mark.asyncio
    async def test_insert_batch_raises_after_max_attempts(
        self, tmp_path: Path, monkeypatch
    ):
        import asyncio as _asyncio

        syncer = audit_sync.create_syncer(**_factory_kwargs(audit_dir=tmp_path))
        assert syncer is not None

        conn = AsyncMock()
        conn.executemany.side_effect = RuntimeError("down")

        class FakePool:
            def acquire(self):
                return _AcquireCM(conn)

        class _AcquireCM:
            def __init__(self, c):
                self._c = c

            async def __aenter__(self):
                return self._c

            async def __aexit__(self, *args):
                return None

        syncer._pool = FakePool()
        monkeypatch.setattr(_asyncio, "sleep", AsyncMock())

        with pytest.raises(RuntimeError, match="down"):
            await syncer._insert_batch([_audit_entry()])
        assert conn.executemany.await_count == syncer._MAX_INSERT_ATTEMPTS


# ─────────────────────────────────────────────
# Sprint 1 Bug Fixes — 2026-04-18 audit
# ─────────────────────────────────────────────


class TestB2_CoerceUtc:
    """B2: Mixed naive/aware datetime comparison silently killed the lag gauge
    (TypeError was caught by bare `except` in _sync_file). _coerce_utc normalises
    to tz-aware UTC so comparison is always well-defined.
    """

    def test_coerce_naive_adds_utc_tzinfo(self):
        from datetime import datetime as _dt

        naive = _dt(2026, 4, 17, 12, 0, 0)
        assert naive.tzinfo is None
        result = audit_sync._coerce_utc(naive)
        assert result is not None
        assert result.tzinfo is not None
        assert result.utcoffset().total_seconds() == 0

    def test_coerce_aware_is_unchanged(self):
        from datetime import datetime as _dt, timezone as _tz

        aware = _dt(2026, 4, 17, 12, 0, 0, tzinfo=_tz.utc)
        assert audit_sync._coerce_utc(aware) is aware

    def test_coerce_none_returns_none(self):
        assert audit_sync._coerce_utc(None) is None

    @pytest.mark.asyncio
    async def test_lag_metric_survives_mixed_tz_ts_in_jsonl(self, tmp_path: Path):
        """Ingest both a naive-UTC and an aware-UTC timestamp in the same file.
        Pre-fix, comparison at line 426 raised TypeError, caught by the outer
        except at line 424, silently freezing _last_synced_event_ts. Post-fix,
        both are coerced and the newest one wins.
        """
        from datetime import date as _date

        syncer = audit_sync.create_syncer(**_factory_kwargs(audit_dir=tmp_path))
        assert syncer is not None
        syncer._ensure_pool = AsyncMock()
        syncer._insert_batch = AsyncMock()

        jsonl = tmp_path / f"audit-{_date.today()}.jsonl"
        _make_jsonl(
            jsonl,
            [
                _audit_entry(timestamp="2026-04-17T04:00:00"),  # naive UTC
                _audit_entry(timestamp="2026-04-17T05:00:00+00:00"),  # aware UTC
            ],
        )

        # Pre-fix this would succeed silently with _last_synced_event_ts=None.
        # Post-fix it updates to the newer aware ts.
        await syncer._tick()

        assert syncer._last_synced_event_ts is not None
        assert syncer._last_synced_event_ts.tzinfo is not None
        assert syncer._last_synced_event_ts.hour == 5, (
            "Newest ts (05:00) should win; pre-fix this was None because "
            "naive-vs-aware comparison raised TypeError and was swallowed."
        )


class TestB4_RowsTotalMetricAfterCursor:
    """B4: `audit_sync_rows_total{success}` previously incremented inside
    _insert_batch, before the cursor was persisted. A crash between executemany
    and _save_cursor re-ran the whole file on the next tick; asyncpg does not
    distinguish ON CONFLICT skips from real inserts, so the counter inflated
    on every replay. Fix: increment only after _save_cursor succeeds.
    """

    @pytest.mark.asyncio
    async def test_insert_batch_does_not_increment_rows_total_directly(
        self, tmp_path: Path, monkeypatch
    ):
        """_insert_batch must NOT touch rows_total{success} anymore."""
        syncer = audit_sync.create_syncer(**_factory_kwargs(audit_dir=tmp_path))
        assert syncer is not None

        # Capture any calls to the metric
        metric_calls = []

        class _FakeCounter:
            def __init__(self, label):
                self._label = label

            def inc(self, n=1):
                metric_calls.append((self._label, n))

        class _FakeLabels:
            def labels(self, *, status):
                return _FakeCounter(status)

        fake_metrics = MagicMock()
        fake_metrics.audit_sync_rows_total = _FakeLabels()
        fake_metrics.audit_sync_batches_total = _FakeLabels()
        monkeypatch.setattr(audit_sync, "_metrics", fake_metrics)

        # Mock pool + conn so executemany succeeds
        conn = AsyncMock()
        conn.executemany = AsyncMock(return_value=None)

        class _FakeAcquire:
            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *a):
                return None

        class _FakePool:
            def acquire(self_inner):
                return _FakeAcquire()

        syncer._pool = _FakePool()

        await syncer._insert_batch([_audit_entry()])

        # No success increment should happen inside _insert_batch
        success_calls = [c for c in metric_calls if c[0] == "success"]
        assert success_calls == [], (
            f"_insert_batch should no longer increment rows_total{{success}} "
            f"(moved to _sync_file post-_save_cursor); got {success_calls}"
        )

    @pytest.mark.asyncio
    async def test_sync_file_increments_rows_total_after_cursor_save(
        self, tmp_path: Path, monkeypatch
    ):
        """_sync_file must increment rows_total{success} AFTER _save_cursor."""
        from datetime import date as _date

        syncer = audit_sync.create_syncer(**_factory_kwargs(audit_dir=tmp_path))
        assert syncer is not None
        syncer._ensure_pool = AsyncMock()
        syncer._insert_batch = AsyncMock()

        increments: list[int] = []

        class _FakeCounter:
            def inc(self, n):
                increments.append(n)

        class _FakeLabels:
            def labels(self, *, status):
                return _FakeCounter() if status == "success" else MagicMock()

        fake_metrics = MagicMock()
        fake_metrics.audit_sync_rows_total = _FakeLabels()
        fake_metrics.audit_sync_malformed_lines_total = MagicMock()
        monkeypatch.setattr(audit_sync, "_metrics", fake_metrics)

        jsonl = tmp_path / f"audit-{_date.today()}.jsonl"
        _make_jsonl(jsonl, [_audit_entry(documento_id=f"D{i}") for i in range(3)])

        await syncer._sync_file(jsonl)

        # Cursor advanced
        assert audit_sync._load_cursor(jsonl) == jsonl.stat().st_size
        # And exactly one increment of 3 rows happened (after the cursor save)
        assert increments == [3], (
            f"Expected exactly one rows_total{{success}}.inc(3) call after "
            f"_save_cursor; got {increments}"
        )


class TestB5_PgVersionGuard:
    """B5: audit_entries UNIQUE NULLS NOT DISTINCT requires Postgres 15+; older
    servers silently ignore the clause and NULLs never match, producing
    duplicate rows for any event with NULL documento_id. Fail loud instead.
    """

    @pytest.mark.asyncio
    async def test_verify_pg_version_disables_syncer_on_pg14(self, tmp_path: Path):
        syncer = audit_sync.create_syncer(**_factory_kwargs(audit_dir=tmp_path))
        assert syncer is not None

        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=140000)  # PG 14

        class _FakeAcquire:
            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *a):
                return None

        pool_closed = False

        class _FakePool:
            def acquire(self_inner):
                return _FakeAcquire()

            async def close(self_inner):
                nonlocal pool_closed
                pool_closed = True

        syncer._pool = _FakePool()

        with pytest.raises(RuntimeError, match="Postgres >= 15"):
            await syncer._verify_pg_version()

        assert syncer._disabled is True
        assert syncer.shutdown.is_set()
        assert pool_closed
        assert syncer._pool is None

    @pytest.mark.asyncio
    async def test_verify_pg_version_passes_on_pg15(self, tmp_path: Path):
        syncer = audit_sync.create_syncer(**_factory_kwargs(audit_dir=tmp_path))
        assert syncer is not None

        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=150000)  # PG 15

        class _FakeAcquire:
            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *a):
                return None

        class _FakePool:
            def acquire(self_inner):
                return _FakeAcquire()

        original_pool = _FakePool()
        syncer._pool = original_pool

        await syncer._verify_pg_version()

        assert syncer._disabled is False
        assert not syncer.shutdown.is_set()
        assert syncer._pool is original_pool  # pool retained on success

    @pytest.mark.asyncio
    async def test_tick_early_returns_when_disabled(self, tmp_path: Path):
        syncer = audit_sync.create_syncer(**_factory_kwargs(audit_dir=tmp_path))
        assert syncer is not None
        syncer._disabled = True
        syncer._ensure_pool = AsyncMock()
        syncer._insert_batch = AsyncMock()

        from datetime import date as _date

        jsonl = tmp_path / f"audit-{_date.today()}.jsonl"
        _make_jsonl(jsonl, [_audit_entry()])

        await syncer._tick()

        # Must not reach pool creation or insert
        syncer._ensure_pool.assert_not_awaited()
        syncer._insert_batch.assert_not_awaited()
