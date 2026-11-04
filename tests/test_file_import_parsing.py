"""File import (`handleFileUpload`) must not drop or truncate numbers silently.

Before: the header-less CSV path took the first CNJ-shaped prefix of a line, so a
number with 5 digits in the last block was imported truncated to 4 (a different,
valid-looking process), and every path filtered ``length > 10`` without a word.

Now `parseImportedFile` keeps everything except blanks/comments in the textarea
(the server validates and reports `invalidos`), refuses to cut a longer digit run,
and reports how many lines it skipped. Runs the real function under node.
CNJ-shaped values are built at runtime (repo rule: no formatted CNJ literals).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).parent.parent / "static" / "js" / "app.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


def cnj(seq: int, last: str = "0001") -> str:
    return "{:07d}-{:02d}.{}.{}.{:02d}.{}".format(seq, 1, 2024, 8, 8, last)


def _function_source(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    depth = 0
    for i in range(src.index("{", start), len(src)):
        depth += src[i] == "{"
        depth -= src[i] == "}"
        if depth == 0:
            return src[start : i + 1]
    raise AssertionError(f"unterminated function {name}")


def parse(name: str, text: str) -> dict:
    src = APP_JS.read_text(encoding="utf-8")
    body = _function_source(src, "parseImportedFile")
    script = (
        f"{body}\nconsole.log(JSON.stringify("
        f"parseImportedFile({json.dumps(name)}, {json.dumps(text)})));"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=20, check=True
    )
    return json.loads(out.stdout)


def test_csv_without_header_does_not_truncate_a_five_digit_last_block():
    bad = cnj(1, "00011")
    r = parse("x.csv", f"coluna\n{bad}\n{cnj(2)}\n")
    assert cnj(1) not in r["numeros"]
    assert bad in r["numeros"] or r["skipped"] >= 1
    assert cnj(2) in r["numeros"]


def test_txt_keeps_short_lines_for_the_server_to_reject():
    r = parse("x.txt", f"{cnj(1)}\n12345\n# comentario\n\n")
    assert r["numeros"] == [cnj(1), "12345"]
    assert r["skipped"] == 0


def test_csv_header_column_keeps_short_cells():
    r = parse("x.csv", f"numero;obs\n{cnj(1)};a\n123;b\n")
    assert r["numeros"] == [cnj(1), "123"]


def test_csv_without_header_counts_unmatched_lines():
    r = parse("x.csv", f"lixo\n{cnj(1)}\nsem numero\n")
    assert r["numeros"] == [cnj(1)]
    assert r["skipped"] == 1


def test_json_list():
    r = parse("x.json", json.dumps([cnj(1), {"numero": "99"}, ""]))
    assert r["numeros"] == [cnj(1), "99"]
