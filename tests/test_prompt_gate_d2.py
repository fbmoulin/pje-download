"""D2 evidence contracts. Only synthetic local files, never real transcripts."""

import json

import pytest

from tests.test_prompt_gate_hook import VAGO
from tests.test_prompt_gate_telemetry import _cli, _hook, _id_do_aviso

TS = "2026-11-14T10:00:00Z"
RULE = "possui_dod"
OTHER = "alvo_ou_sintoma"


def event(i=1, rules=None):
    return {"id": f"{i:08x}", "ts": TS, "regras": rules or [RULE], "decisao": "aviso"}


def label(i=1, rule=RULE, value="tp", version=2):
    r = {"id": f"{i:08x}", "ts": TS, "rotulo": value}
    if version is not None:
        r.update(schema_version=version, regra=rule)
    return r


def files(tmp_path, events, labels=None):
    state = tmp_path / "state"
    base = state / "pje-prompt-gate"
    base.mkdir(parents=True)
    for name, rows in (("telemetria", events), ("rotulos", labels)):
        if rows is not None:
            (base / f"{name}.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
            )
    return state, base


def counts(output, rule=RULE):
    row = next(s for s in output.splitlines() if s.startswith(rule + " "))
    return row.split()[1:]


def test_opposite_labels_are_independent_and_v2(tmp_path):
    state, base = files(tmp_path, [event(rules=[RULE, OTHER])])
    for rule, value in ((RULE, "tp"), (OTHER, "fp")):
        p = _cli(state, "--label", "00000001", rule, value)
        assert p.returncode == 0, p.stderr
    p = _cli(state, "--report")
    assert p.returncode == 0, p.stderr
    assert counts(p.stdout)[:4] == ["1", "1", "0", "1"]
    assert counts(p.stdout, OTHER)[:4] == ["1", "1", "1", "0"]
    assert all(
        r["schema_version"] == 2
        for r in map(json.loads, (base / "rotulos.jsonl").read_text().splitlines())
    )


@pytest.mark.parametrize("rules,success", [([RULE], True), ([RULE, OTHER], False)])
def test_short_label_only_for_single_rule(tmp_path, rules, success):
    state, base = files(tmp_path, [event(rules=rules)])
    p = _cli(state, "--label", "00000001", "tp")
    assert (p.returncode == 0) is success
    assert (base / "rotulos.jsonl").exists() is success
    if success:
        assert json.loads((base / "rotulos.jsonl").read_text())["regra"] == RULE


def test_legacy_ambiguous_is_pending_until_specific_review(tmp_path):
    state, base = files(tmp_path, [event(rules=[RULE, OTHER])], [label(version=None)])
    p = _cli(state, "--report")
    assert p.returncode == 0
    assert counts(p.stdout)[:4] == ["1", "0", "0", "0"]
    assert "ambiguos" in p.stdout
    old = (base / "rotulos.jsonl").read_bytes()
    assert _cli(state, "--label", "00000001", RULE, "fp").returncode == 0
    assert (base / "rotulos.jsonl").read_bytes().startswith(old)
    p = _cli(state, "--report")
    assert counts(p.stdout)[:4] == ["1", "1", "1", "0"]
    assert counts(p.stdout, OTHER)[:4] == ["1", "0", "0", "0"]


def test_specific_overrides_legacy_and_last_specific_wins(tmp_path):
    state, _ = files(
        tmp_path,
        [event()],
        [label(value="tp"), label(value="fp"), label(version=None), label(value="fp")],
    )
    p = _cli(state, "--report")
    assert p.returncode == 0
    assert counts(p.stdout)[:4] == ["1", "1", "1", "0"]


def test_single_rule_legacy_is_usable(tmp_path):
    state, _ = files(tmp_path, [event()], [label(version=None)])
    assert counts(_cli(state, "--report").stdout)[:4] == ["1", "1", "0", "1"]


@pytest.mark.parametrize(
    "args",
    [
        ("00000002", RULE, "tp"),
        ("00000001", OTHER, "tp"),
        ("00000001", "unknown", "tp"),
        ("00000001", RULE, "maybe"),
    ],
)
def test_invalid_label_never_writes(tmp_path, args):
    state, base = files(tmp_path, [event()])
    assert _cli(state, "--label", *args).returncode == 1
    assert not (base / "rotulos.jsonl").exists()


@pytest.mark.parametrize("rows", [[], [event()]])
def test_empty_sample_and_missing_labels_are_distinct(tmp_path, rows):
    state, base = files(tmp_path, rows)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in base.iterdir()}
    p = _cli(state, "--report")
    assert p.returncode == 0
    assert ("AMOSTRA_VAZIA" in p.stdout) is (not rows)
    assert "rotulos: ausente" in p.stdout
    assert str(base.resolve()) in p.stdout and "gerado_em:" in p.stdout
    assert {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in base.iterdir()
    } == before
    if rows:
        assert TS.replace("Z", "+00:00") in p.stdout


@pytest.mark.parametrize(
    "n,fp,eligible", [(59, 0, False), (60, 2, True), (60, 3, False)]
)
def test_thresholds(tmp_path, n, fp, eligible):
    state, _ = files(
        tmp_path,
        [event(i) for i in range(n)],
        [label(i, value="fp" if i < fp else "tp") for i in range(n)],
    )
    p = _cli(state, "--report")
    assert p.returncode == 0
    assert counts(p.stdout)[-1] == ("elegivel" if eligible else "insuficiente")
    assert "nao promove" in p.stdout


@pytest.mark.parametrize(
    "events,labels",
    [
        ([event(), event()], []),
        ([event()], [label(i=2)]),
        ([event()], [label(rule=OTHER)]),
        ([event()], [label(version=99)]),
        ([event()], [dict(label(), rotulo="unknown")]),
        ([dict(event(), ts="bad")], []),
        ([dict(event(), regras=["unknown"])], []),
        ([dict(event(), prompt="PRIVATE_SENTINEL")], []),
        ([dict(event(), latencia_ms=10**400)], []),
        ([dict(event(), latencia_ms=float("inf"))], []),
        ([dict(event(), latencia_ms=True)], []),
        ([dict(event(), regras=[{}])], []),
        ([event()], [dict(label(), schema_version=True)]),
    ],
)
def test_invalid_evidence_never_reports_eligibility_or_accepts_label(
    tmp_path, events, labels
):
    state, base = files(tmp_path, events, labels)
    before = (base / "rotulos.jsonl").read_bytes()
    p = _cli(state, "--report")
    assert p.returncode == 1
    assert "EVIDENCIA_INCOMPLETA" in p.stderr
    assert "elegivel" not in p.stdout and "PRIVATE_SENTINEL" not in p.stderr
    assert "Traceback" not in p.stderr
    assert _cli(state, "--label", "00000001", RULE, "tp").returncode == 1
    assert (base / "rotulos.jsonl").read_bytes() == before


@pytest.mark.parametrize(
    "name,bad",
    [
        ("telemetria", b'{"secret":"PRIVATE_SENTINEL"'),
        ("rotulos", b"not-json PRIVATE_SENTINEL\n"),
        ("telemetria", b"\xff\n"),
        ("rotulos", b'{"id":"a","id":"b"}\n'),
    ],
)
def test_corrupt_bytes_are_not_echoed_or_ignored(tmp_path, name, bad):
    state, base = files(tmp_path, [event()], [])
    (base / f"{name}.jsonl").write_bytes(bad)
    p = _cli(state, "--report")
    assert p.returncode == 1
    assert "EVIDENCIA_INCOMPLETA" in p.stderr
    assert f"{name}.jsonl" in p.stderr and "linha 1" in p.stderr
    assert "PRIVATE_SENTINEL" not in p.stdout + p.stderr


def test_unreadable_source_reports_without_raw_exception(tmp_path, monkeypatch, capsys):
    from tools import prompt_gate as gate

    state, base = files(tmp_path, [event()])
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    original = type(base).read_text

    def fail(path, *args, **kwargs):
        if path.name == "telemetria.jsonl":
            raise PermissionError("PRIVATE_SENTINEL")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(base), "read_text", fail)
    assert gate.main(["--report"]) == 1
    out = capsys.readouterr()
    assert "EVIDENCIA_INCOMPLETA" in out.err and "PRIVATE_SENTINEL" not in out.err


@pytest.mark.parametrize("sep", ["\u2028", "\u2029", "\x85"])
def test_unicode_line_separator_in_a_field_is_not_a_record_break(tmp_path, sep):
    """The hook writes these raw (ensure_ascii=False) inside session_id."""
    state = tmp_path / "state"
    event_id = _id_do_aviso(_hook(VAGO, state, sessao=f"a{sep}b"))
    raw = (state / "pje-prompt-gate" / "telemetria.jsonl").read_bytes()
    assert sep.encode() in raw and raw.count(b"\n") == 1
    p = _cli(state, "--report")
    assert p.returncode == 0, p.stderr
    assert _cli(state, "--label", event_id, RULE, "tp").returncode == 0


@pytest.mark.parametrize("position", ["middle", "end"])
def test_blank_line_is_still_incomplete_evidence(tmp_path, position):
    state, base = files(tmp_path, [event(1), event(2)])
    arq = base / "telemetria.jsonl"
    first, second = arq.read_text("utf-8").splitlines(keepends=True)
    blank = first + "\n" + second if position == "middle" else first + second + "\n"
    arq.write_text(blank, encoding="utf-8")
    p = _cli(state, "--report")
    assert p.returncode == 1 and "EVIDENCIA_INCOMPLETA" in p.stderr


def test_label_refusal_does_not_need_sys_exception(tmp_path, monkeypatch, capsys):
    """sys.exception() is 3.11+; macOS ships 3.9 as /usr/bin/python3."""
    import sys

    from tools import prompt_gate as gate

    state, base = files(tmp_path, [event()])
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.delattr(sys, "exception", raising=False)
    assert gate.main(["--label", "00000002", RULE, "tp"]) == 1
    assert capsys.readouterr().err.strip() == "aviso de qualidade nao encontrado"
    assert not (base / "rotulos.jsonl").exists()
