"""B1 — a batch must not silently drop numbers the user typed.

Before: invalid lines were dropped with only a log (carrying the raw values), the
201 reply did not mention them, duplicates became ``total 3 / entries 2 / pending 1``
and the UI filtered ``l.length > 10`` and cleared the textarea.

Now: the server accepts the valid ones, returns ``invalidos`` (each truncated to 64
chars, at most 100 items, plus ``invalidos_total``) and ``duplicados`` (count removed,
first occurrence kept, order preserved); the UI keeps only the rejected lines.

CNJ-shaped values are built at runtime (repo rule: no formatted CNJ literals).
"""

from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import structlog
from aiohttp.test_utils import TestClient, TestServer

from dashboard_api import APP_CTX_KEY, BatchJob, create_app


def cnj(seq: int) -> str:
    """A well-formed CNJ number, assembled at runtime."""
    return "{:07d}-{:02d}.{}.{}.{:02d}.{:04d}".format(seq, 1, 2024, 8, 8, 1)


def _install_fake_submit(app) -> list[list[str]]:
    """Replace submit_batch so no real batch task is started; record the lists."""
    seen: list[list[str]] = []

    async def fake_submit(ps, include_anexos=True, gdrive_map=None, **kwargs):
        seen.append(list(ps))
        return BatchJob(
            id="fake",
            processos=ps,
            status="queued",
            created_at=datetime.datetime.now().isoformat(),
        )

    app[APP_CTX_KEY].state.submit_batch = fake_submit
    return seen


async def _post(tmp_path, payload):
    async with TestClient(TestServer(create_app(tmp_path))) as client:
        seen = _install_fake_submit(client.app)
        resp = await client.post("/api/download", json=payload)
        return resp.status, await resp.json(), seen


@pytest.mark.asyncio
async def test_invalid_lines_are_returned_and_valid_ones_accepted(tmp_path):
    ok1, ok2 = cnj(1), cnj(2)
    status, body, seen = await _post(tmp_path, {"processos": [ok1, "abc", ok2, "1234"]})
    assert status == 201
    assert seen == [[ok1, ok2]]
    assert body["processos"] == 2
    assert body["invalidos"] == ["abc", "1234"]
    assert body["duplicados"] == 0
    # fields that old clients read are unchanged
    assert body["batch_id"] == "fake" and body["status"] == "queued"


@pytest.mark.asyncio
async def test_clean_batch_reports_empty_invalidos_and_zero_duplicados(tmp_path):
    status, body, _ = await _post(tmp_path, {"processos": [cnj(1)]})
    assert status == 201
    assert body["invalidos"] == []
    assert body["duplicados"] == 0


@pytest.mark.asyncio
async def test_duplicates_removed_keeping_first_occurrence_order(tmp_path):
    a, b, c = cnj(1), cnj(2), cnj(3)
    status, body, seen = await _post(tmp_path, {"processos": [b, a, b, c, a, b]})
    assert status == 201
    assert seen == [[b, a, c]]
    assert body["processos"] == 3
    assert body["duplicados"] == 3


@pytest.mark.asyncio
async def test_duplicates_ignore_surrounding_whitespace_and_quotes(tmp_path):
    a = cnj(1)
    status, body, seen = await _post(
        tmp_path, {"processos": [a, f" {a} ", f'"{a}"', f"'{a}'"]}
    )
    assert status == 201
    assert seen == [[a]]
    assert body["duplicados"] == 3


@pytest.mark.asyncio
async def test_invalidos_truncated_to_64_chars_and_capped_at_100(tmp_path):
    long_line = "x" * 200
    junk = [f"lixo{i}" for i in range(150)]
    status, body, _ = await _post(tmp_path, {"processos": [cnj(1), long_line, *junk]})
    assert status == 201
    assert len(body["invalidos"]) == 100
    assert body["invalidos"][0] == "x" * 64
    assert all(len(item) <= 64 for item in body["invalidos"])
    assert body["invalidos_total"] == 151


@pytest.mark.asyncio
async def test_all_invalid_keeps_400_and_includes_invalidos(tmp_path):
    status, body, seen = await _post(
        tmp_path, {"processos": ["abc", "y" * 90, cnj(1)[:-1]]}
    )
    assert status == 400
    assert seen == []
    assert "error" in body
    assert body["invalidos"] == ["abc", "y" * 64, cnj(1)[:-1]]
    assert body["duplicados"] == 0


@pytest.mark.asyncio
async def test_all_invalid_400_also_truncates_and_caps(tmp_path):
    status, body, _ = await _post(
        tmp_path, {"processos": ["z" * 100] + [f"k{i}" for i in range(150)]}
    )
    assert status == 400
    assert len(body["invalidos"]) == 100
    assert body["invalidos"][0] == "z" * 64


@pytest.mark.asyncio
async def test_logs_carry_counts_never_the_raw_values(tmp_path):
    secret = "NOME-DA-PARTE-SECRETA"
    a = cnj(1)
    with structlog.testing.capture_logs() as logs:
        status, _, _ = await _post(tmp_path, {"processos": [a, a, secret]})
    assert status == 201
    dumped = json.dumps(logs, default=str)
    assert secret not in dumped
    assert a not in dumped
    events = [e for e in logs if e.get("event") == "dashboard.invalid_processos"]
    assert events and events[0]["count"] == 1


@pytest.mark.asyncio
async def test_resend_of_same_batch_after_total_rejection_is_stateless(tmp_path):
    """Rejected-only input never creates a batch, so retrying is safe."""
    async with TestClient(TestServer(create_app(tmp_path))) as client:
        seen = _install_fake_submit(client.app)
        r1 = await client.post("/api/download", json={"processos": ["abc"]})
        r2 = await client.post("/api/download", json={"processos": ["abc", cnj(1)]})
        assert r1.status == 400 and r2.status == 201
        assert seen == [[cnj(1)]]


# ── UI (static/js/app.js), run under node like test_dashboard_js_escaping ──

APP_JS = Path(__file__).parent.parent / "static" / "js" / "app.js"
needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


def _function_source(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    depth = 0
    for i in range(src.index("{", start), len(src)):
        depth += src[i] == "{"
        depth -= src[i] == "}"
        if depth == 0:
            return src[start : i + 1]
    raise AssertionError(f"unterminated function {name}")


def _run_js(expr: str, *functions: str):
    src = APP_JS.read_text(encoding="utf-8")
    body = "\n".join(_function_source(src, f) for f in functions)
    out = subprocess.run(
        ["node", "-e", f"{body}\nconsole.log(JSON.stringify({expr}));"],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    return json.loads(out.stdout)


@needs_node
def test_js_parse_lines_sends_every_nonblank_line_even_short_ones():
    # old code filtered l.length > 10 and silently dropped "1234"
    text = "  abc \n\n1234\n" + cnj(1) + "\n   \n"
    assert _run_js(f"parseProcessoLines({json.dumps(text)})", "parseProcessoLines") == [
        "abc",
        "1234",
        cnj(1),
    ]


@needs_node
def test_js_textarea_keeps_only_rejected_lines():
    a = cnj(1)
    text = f"abc\n{a}\n1234\n\n  '{a}'\n"
    expr = f"remainingAfterSubmit({json.dumps(text)}, {json.dumps(['abc', '1234'])}, 2)"
    assert (
        _run_js(
            expr, "parseProcessoLines", "normalizeProcessoLine", "remainingAfterSubmit"
        )
        == "abc\n1234"
    )


@needs_node
def test_js_textarea_cleared_when_nothing_rejected():
    text = cnj(1) + "\n" + cnj(2)
    expr = f"remainingAfterSubmit({json.dumps(text)}, [], 0)"
    assert (
        _run_js(
            expr, "parseProcessoLines", "normalizeProcessoLine", "remainingAfterSubmit"
        )
        == ""
    )


@needs_node
def test_js_rejected_lines_longer_than_64_match_by_truncated_form():
    long_line = "w" * 90
    text = f"{long_line}\n{cnj(1)}"
    expr = f"remainingAfterSubmit({json.dumps(text)}, {json.dumps(['w' * 64])}, 1)"
    assert (
        _run_js(
            expr, "parseProcessoLines", "normalizeProcessoLine", "remainingAfterSubmit"
        )
        == long_line
    )


@needs_node
def test_js_when_server_list_was_truncated_the_whole_text_is_kept():
    # invalidos_total (150) > listed (2): can't tell which lines were rejected,
    # so nothing the user typed may be thrown away.
    text = "aaa\nbbb\nccc"
    expr = f"remainingAfterSubmit({json.dumps(text)}, ['aaa', 'bbb'], 150)"
    assert (
        _run_js(
            expr, "parseProcessoLines", "normalizeProcessoLine", "remainingAfterSubmit"
        )
        == text
    )


@needs_node
def test_js_submit_no_longer_filters_by_length_or_clears_blindly():
    src = APP_JS.read_text(encoding="utf-8")
    submit = _function_source(src, "submitDownload")
    assert not re.search(r"length > 10\b(?!0)", submit)
    assert "textarea.value = ''" not in submit
    assert "remainingAfterSubmit" in submit
