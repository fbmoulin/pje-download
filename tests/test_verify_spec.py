#!/usr/bin/env python3
"""
Tests for the canonical spec verifier (verify_spec.py)
"""

import pytest
from pathlib import Path
from tools.verify_spec import validate_spec


def test_validate_spec_passes_on_good_spec():
    """A well-formed SDD spec should pass all checks."""
    spec = Path("docs/specs/sdd-pje-download.md")
    result = validate_spec(spec)
    assert result.passed >= 9, f"Too many failures: {result.failures}"  # spec has 11 checks
    assert len(result.failures) == 0


def test_validate_spec_detects_missing_file():
    """Non-existent spec should return failure."""
    spec = Path("docs/specs/does-not-exist.md")
    result = validate_spec(spec)
    assert result.passed == 0
    assert len(result.failures) > 0


def test_validate_spec_structure():
    """Result object must have the expected fields."""
    spec = Path("docs/specs/sdd-pje-download.md")
    result = validate_spec(spec)
    assert hasattr(result, "path")
    assert hasattr(result, "passed")
    assert hasattr(result, "total")
    assert hasattr(result, "failures")