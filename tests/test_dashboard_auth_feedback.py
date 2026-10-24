"""The dashboard must say "API key needed" on a 401, not "offline".

In production every /api/* route needs X-API-Key (api_key_middleware), while the page
itself, /healthz and /static are public. With the field empty the page loaded but every
call answered 401, and the UI read that as "OFFLINE" / "Serviço indisponível" and showed
"Opcional" in the key field. These tests run the real functions of static/js/app.js
under node (like tests/test_dashboard_js_escaping.py); they are skipped without node.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
APP_JS = ROOT / "static" / "js" / "app.js"
DASHBOARD_HTML = ROOT / "dashboard.html"
needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


def _function_source(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    if src[max(0, start - 6) : start] == "async ":
        start -= 6
    # The body opens after the parameter list's ")": a default like `opts = {}` has its
    # own braces and must not be taken for the body.
    body_open = start + re.search(r"\)\s*\{", src[start:]).end() - 1
    depth = 0
    for i in range(body_open, len(src)):
        depth += src[i] == "{"
        depth -= src[i] == "}"
        if depth == 0:
            return src[start : i + 1]
    raise AssertionError(f"unterminated function {name}")


def _run_js(expr: str, *functions: str, prelude: str = ""):
    """Evaluate ``expr`` (may be a Promise) after the real functions; return its JSON."""
    src = APP_JS.read_text(encoding="utf-8")
    body = "\n".join(_function_source(src, f) for f in functions)
    script = (
        f"{prelude}\n{body}\n"
        "(async () => {\n"
        "  try {\n"
        f"    const r = await ({expr});\n"
        "    console.log(JSON.stringify({ok: r}));\n"
        "  } catch (e) {\n"
        "    console.log(JSON.stringify({thrown: String(e && e.message), status: e && e.status}));\n"
        "  }\n"
        "})();"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=20, check=True
    )
    return json.loads(out.stdout)


def _fake_fetch(status: int, body: dict) -> str:
    return (
        "const API = '';\n"
        "function getApiKey() { return ''; }\n"
        "globalThis.fetch = async () => ({\n"
        f"  ok: {str(200 <= status < 300).lower()}, status: {status},\n"
        f"  json: async () => ({json.dumps(body)}),\n"
        "});\n"
    )


@needs_node
def test_apifetch_error_carries_the_http_status():
    prelude = _fake_fetch(401, {"error": "Unauthorized"})
    got = _run_js("apiFetch('/api/status')", "apiFetch", prelude=prelude)
    assert got == {"thrown": "Unauthorized", "status": 401}


@needs_node
def test_apifetch_still_returns_json_on_success():
    prelude = _fake_fetch(200, {"current_status": "idle"})
    got = _run_js("apiFetch('/api/status')", "apiFetch", prelude=prelude)
    assert got == {"ok": {"current_status": "idle"}}


@needs_node
def test_a_401_is_reported_as_missing_api_key_not_as_offline():
    got = _run_js("statusFeedbackFor({status: 401})", "statusFeedbackFor")["ok"]
    assert got["label"] == "Chave de API"
    assert "Chave de API" in got["sessionText"]
    assert "indispon" not in got["sessionText"]


@pytest.mark.parametrize("err", ["{}", "{status: 500}", "null", "new Error('x')"])
@needs_node
def test_any_other_failure_keeps_the_offline_wording(err):
    got = _run_js(f"statusFeedbackFor({err})", "statusFeedbackFor")["ok"]
    assert got == {"label": "Offline", "sessionText": "⚠ Serviço indisponível"}


def test_key_field_no_longer_says_optional():
    html = DASHBOARD_HTML.read_text(encoding="utf-8")
    assert 'placeholder="Opcional"' not in html
    assert "dashboard-api-key" in html


_KEY_PRELUDE = (
    "const API_KEY_STORAGE_KEY = 'k';\n"
    "const _store = {};\n"
    "globalThis.localStorage = {\n"
    "  getItem: (k) => (k in _store ? _store[k] : null),\n"
    "  setItem: (k, v) => { _store[k] = String(v); },\n"
    "  removeItem: (k) => { delete _store[k]; },\n"
    "};\n"
    "let calls = 0;\n"
    "const onChanged = () => { calls += 1; };\n"
)


def _key_calls(sequence: str) -> int:
    """Apply the given JS statements, return how many times onChanged ran."""
    return _run_js(
        f"(() => {{ {sequence}; return calls; }})()",
        "getApiKey",
        "setApiKey",
        "applyApiKey",
        prelude=_KEY_PRELUDE,
    )["ok"]


@needs_node
def test_saving_a_new_key_reloads_the_status_once():
    # `change` and `blur` both fire for one edit: the second finds the same key.
    assert (
        _key_calls("applyApiKey(' abc ', onChanged); applyApiKey('abc', onChanged)")
        == 1
    )


@needs_node
def test_an_untouched_or_empty_field_reloads_nothing():
    assert _key_calls("applyApiKey('', onChanged)") == 0


@needs_node
def test_replacing_and_clearing_the_key_each_reload_the_status():
    seq = (
        "applyApiKey('abc', onChanged); applyApiKey('xyz', onChanged); "
        "applyApiKey('', onChanged)"
    )
    assert _key_calls(seq) == 3


def test_init_wires_the_key_field_to_applyapikey_and_refreshstatus():
    src = APP_JS.read_text(encoding="utf-8")
    init = _function_source(src, "init")
    assert "applyApiKey(e.target.value, refreshStatus)" in init
    assert "async function refreshStatus(" in src
