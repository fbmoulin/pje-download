"""Unit tests for file_utils helpers.

These helpers replaced duplicated file-metadata expressions across the worker,
dashboard, and batch downloader. The tests below pin the defensive contracts for
byte summation and deduplication.
"""

from __future__ import annotations

from file_utils import merge_file_lists, total_bytes


class TestTotalBytes:
    def test_sums_normal_items(self):
        files = [
            {"tamanhoBytes": 100},
            {"tamanhoBytes": 250},
            {"tamanhoBytes": 50},
        ]
        assert total_bytes(files) == 400

    def test_missing_key_treated_as_zero(self):
        """Missing tamanhoBytes must not raise KeyError."""
        files = [{"nome": "a.pdf"}, {"tamanhoBytes": 200}]
        assert total_bytes(files) == 200

    def test_none_value_skipped(self):
        files = [{"tamanhoBytes": None}, {"tamanhoBytes": 300}]
        assert total_bytes(files) == 300

    def test_string_integer_coerced(self):
        """MNI SOAP responses can return tamanhoBytes as a string."""
        files = [{"tamanhoBytes": "1024"}, {"tamanhoBytes": 512}]
        assert total_bytes(files) == 1536

    def test_empty_string_skipped(self):
        files = [{"tamanhoBytes": ""}, {"tamanhoBytes": 100}]
        assert total_bytes(files) == 100

    def test_empty_list_returns_zero(self):
        assert total_bytes([]) == 0

    def test_all_missing_fields_returns_zero(self):
        files = [{"nome": "x.pdf"}, {"nome": "y.pdf"}]
        assert total_bytes(files) == 0


class TestMergeFileLists:
    def test_dedupe_by_checksum(self):
        """Items with the same checksum keep only the first occurrence."""
        a = [{"checksum": "abc", "nome": "doc.pdf", "fonte": "mni"}]
        b = [{"checksum": "abc", "nome": "doc.pdf", "fonte": "gdrive"}]
        result = merge_file_lists(a, b)
        assert len(result) == 1
        assert result[0]["fonte"] == "mni"

    def test_dedupe_by_composite_key_when_no_checksum(self):
        a = [{"nome": "doc.pdf", "tamanhoBytes": 100, "fonte": "mni"}]
        b = [{"nome": "doc.pdf", "tamanhoBytes": 100, "fonte": "mni"}]
        result = merge_file_lists(a, b)
        assert len(result) == 1

    def test_different_checksums_kept(self):
        a = [{"checksum": "aaa", "nome": "a.pdf"}]
        b = [{"checksum": "bbb", "nome": "b.pdf"}]
        result = merge_file_lists(a, b)
        assert len(result) == 2

    def test_multiple_groups_merged(self):
        g1 = [{"checksum": "x1", "nome": "one.pdf"}]
        g2 = [{"checksum": "x2", "nome": "two.pdf"}]
        g3 = [{"checksum": "x1", "nome": "one-dup.pdf"}]
        result = merge_file_lists(g1, g2, g3)
        assert len(result) == 2
        names = {r["nome"] for r in result}
        assert "one.pdf" in names
        assert "two.pdf" in names

    def test_empty_groups_returns_empty(self):
        assert merge_file_lists([], []) == []
