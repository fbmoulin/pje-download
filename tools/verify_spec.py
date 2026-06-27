#!/usr/bin/env python3
"""
Canonical verification command for Markdown specification documents.

Usage (direct):
    python tools/verify_spec.py docs/specs/*.md

Usage (as module from repo root):
    PYTHONPATH=. python -m tools.verify_spec docs/specs/*.md

Exit codes:
    0 = All specs passed validation
    1 = One or more specs failed validation
    2 = Usage / argument error

This tool replaces ad-hoc verification scripts and provides a stable,
canonical command for CI and local pre-commit checks on specification files.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

REQUIRED_SECTIONS = [
    "USER VALIDATION GATE",
    "writing-plans",
    "subagent-driven-development",
    "plan-quality-gate",
]

STRUCTURAL_CHECKS = [
    ("Has minimum length (>2000 chars)", lambda c: len(c) > 2000),
    ("Contains USER VALIDATION GATE section", lambda c: "USER VALIDATION GATE" in c),
    ("Mentions writing-plans skill", lambda c: "writing-plans" in c),
    ("Mentions subagent-driven-development skill", lambda c: "subagent-driven-development" in c),
    ("Mentions plan-quality-gate skill", lambda c: "plan-quality-gate" in c),
    ("Contains at least one table", lambda c: "|" in c and "---" in c),
    ("Contains References or Referências section", lambda c: "Referências" in c or "References" in c or "references/" in c),
]


@dataclass
class SpecResult:
    path: Path
    passed: int
    total: int
    failures: List[str]


def validate_spec(path: Path) -> SpecResult:
    if not path.exists():
        return SpecResult(path, 0, 1, [f"File does not exist: {path}"])

    content = path.read_text(encoding="utf-8")
    failures: List[str] = []
    passed = 0

    for desc, check in STRUCTURAL_CHECKS:
        if check(content):
            passed += 1
        else:
            failures.append(desc)

    return SpecResult(path, passed, len(STRUCTURAL_CHECKS), failures)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Canonical verifier for Markdown specification documents (SDD specs)."
    )
    parser.add_argument(
        "specs",
        nargs="+",
        type=Path,
        help="One or more Markdown specification files to validate",
    )
    args = parser.parse_args()

    results: List[SpecResult] = []
    total_passed = 0
    total_failed = 0

    print("=== Canonical Spec Verification ===")

    for spec_path in args.specs:
        result = validate_spec(spec_path)
        results.append(result)

        status = "✅ PASS" if not result.failures else "❌ FAIL"
        print(f"\n{status} {result.path}")
        print(f"   {result.passed}/{result.total} checks passed")

        for failure in result.failures:
            print(f"   - {failure}")

        if result.failures:
            total_failed += 1
        else:
            total_passed += 1

    print("\n=== Summary ===")
    print(f"Specs passed: {total_passed}")
    print(f"Specs failed: {total_failed}")

    if total_failed > 0:
        print("\nVERDICT: FAILED — one or more specs did not meet structural requirements")
        return 1

    print("\nVERDICT: PASSED — all specs meet structural requirements")
    return 0


if __name__ == "__main__":
    sys.exit(main())