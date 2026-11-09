"""Tests for the SGIS grid CSV parsing helpers used by scripts/import_sgis_grid.py."""

from __future__ import annotations

import csv

from scripts.import_sgis_grid import _iter_stat_rows, _parse_stat_value


def _write_csv(path, rows, *, header: bool) -> None:
    with path.open("w", encoding="cp949", newline="") as f:
        writer = csv.writer(f)
        if header:
            writer.writerow(["기준연도", "격자코드", "통계항목", "통계값"])
        for row in rows:
            writer.writerow(row)


class TestParseStatValue:
    def test_blank_and_na_become_zero(self):
        assert _parse_stat_value("") == 0
        assert _parse_stat_value("N/A") == 0

    def test_numeric_string_parses(self):
        assert _parse_stat_value("42") == 42
        assert _parse_stat_value("42.0") == 42


class TestIterStatRows:
    def test_headered_csv(self, tmp_path):
        path = tmp_path / "with_header.csv"
        _write_csv(
            path,
            [("2024", "zzzz0001", "to_in_001", 100)],
            header=True,
        )
        rows = list(_iter_stat_rows(path))
        assert rows == [("zzzz0001", "to_in_001", 100)]

    def test_headerless_census_csv(self, tmp_path):
        """Census portal exports often omit the header row entirely."""
        path = tmp_path / "headerless.csv"
        _write_csv(
            path,
            [("2024", "zzzz0001", "to_in_001", 100)],
            header=False,
        )
        rows = list(_iter_stat_rows(path))
        assert rows == [("zzzz0001", "to_in_001", 100)]
