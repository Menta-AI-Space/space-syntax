"""Unit tests for demand profile parsing and benchmarks (no live SGIS)."""

from app.services.demand_benchmark import top_percentile_rank, youth_pct_from_age_bands
from app.services.sgis import AgeBand, _parse_age_bands


class TestParseAgeBands:
    def test_pplsummary_row(self):
        row = {
            "teenage_less_than_cnt": "500",
            "teenage_less_than_per": "8.5",
            "teenage_cnt": "800",
            "teenage_per": "12.0",
            "twenty_cnt": "1200",
            "twenty_per": "34.0",
            "thirty_cnt": "600",
            "thirty_per": "17.0",
        }
        bands = _parse_age_bands(row, 3500)
        labels = [b.band for b in bands]
        assert "20대" in labels
        twenty = next(b for b in bands if b.band == "20대")
        assert twenty.count == 1200
        assert twenty.pct == 34.0


class TestBenchmark:
    def test_top_percentile_rank(self):
        dist = [1000.0, 5000.0, 10000.0, 20000.0]
        assert top_percentile_rank(20000.0, dist) == 25
        assert top_percentile_rank(1000.0, dist) == 100

    def test_youth_pct(self):
        bands = [
            AgeBand("10대", 100, 10.0),
            AgeBand("20대", 340, 34.0),
        ]
        assert youth_pct_from_age_bands(bands) == 44.0
