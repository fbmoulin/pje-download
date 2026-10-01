"""static/js/app.js builds HTML strings out of server data (PJe error text, batch
ids, statuses). Those strings land in *attributes* as well as in text nodes, so
the escaper must handle quotes.

The old `esc()` went through `textContent` -> `innerHTML`, which escapes `& < >`
but NOT `"` or `'`. `title="${esc(detail)}"` was therefore injectable with
`" onmouseover="...`, and `onclick="viewBatch('${esc(id)}')"` with `')...//`.

There is no JS test runner in this repo; these tests run the real functions from
app.js under node (skipped when node is absent) against a stub that mimics the
browser's innerHTML serialiser, so they fail on the old implementation.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).parent.parent / "static" / "js" / "app.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

# What a browser does for `span.textContent = s; span.innerHTML`: escapes & < >
# (and nbsp) only. Quotes are NOT escaped in text-node serialisation.
_DOM_STUB = """
const document = { createElement() {
  let text = '';
  return {
    set textContent(v) { text = String(v); },
    get innerHTML() {
      return text.replace(/&/g, '&amp;').replace(/</g, '&lt;')
                 .replace(/>/g, '&gt;').replace(/\\u00a0/g, '&nbsp;');
    },
  };
} };
"""


def _function_source(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    depth = 0
    for i in range(src.index("{", start), len(src)):
        depth += src[i] == "{"
        depth -= src[i] == "}"
        if depth == 0:
            return src[start : i + 1]
    raise AssertionError(f"unterminated function {name}")


def _run(expr: str, *functions: str):
    src = APP_JS.read_text(encoding="utf-8")
    body = "\n".join(_function_source(src, f) for f in functions)
    script = f"{_DOM_STUB}\n{body}\nconsole.log(JSON.stringify({expr}));"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=20, check=True
    )
    return json.loads(out.stdout)


HOSTILE = [
    '" onmouseover="alert(1)" x="',
    "'); alert(1); //",
    "<img src=x onerror=alert(1)>",
    "a&b",
]


@pytest.mark.parametrize("payload", HOSTILE)
def test_esc_leaves_no_raw_html_metacharacters(payload):
    escaped = _run(f"esc({json.dumps(payload)})", "esc")
    for ch in ('"', "'", "<", ">"):
        assert ch not in escaped, f"{ch!r} survived in {escaped!r}"
    assert not re.search(r"&(?!amp;|lt;|gt;|quot;|#39;)", escaped)


def test_esc_roundtrips_plain_text_and_falsy_values():
    assert _run('esc("Processo 5000001-00.2024")', "esc") == "Processo 5000001-00.2024"
    assert _run("esc('')", "esc") == ""
    assert _run("esc(null)", "esc") == ""
    assert _run("esc(undefined)", "esc") == ""


@pytest.mark.parametrize("payload", HOSTILE)
def test_status_tag_escapes_the_status(payload):
    html = _run(f"statusTag({json.dumps(payload)})", "esc", "statusTag")
    # The only quotes allowed are the template's own around class=; the payload's
    # must be neutralised, so there is exactly one pair and no extra tag.
    assert html.count('"') == 2
    assert html.count("<") == 2 and html.count(">") == 2  # <span ...>...</span> only


def test_no_inline_event_handler_interpolates_data():
    """`onclick="fn('${x}')"` runs `x` as JS after the HTML parser has decoded
    entities, so escaping cannot make it safe. Use data-* + a delegated listener."""
    src = APP_JS.read_text(encoding="utf-8")
    offenders = re.findall(r"""\bon[a-z]+\s*=\s*(?:"[^"]*\$\{|'[^']*\$\{)""", src)
    assert offenders == []
