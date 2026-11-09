"""Tests for app.services.hira_clinics — HIRA specialty-aware clinic radius queries."""

from __future__ import annotations

import psycopg2
import pytest

from app.config import settings
from app.services.hira_clinics import HiraError, fetch_clinics_in_radius, subjects_summary_in_radius


def _db_available() -> bool:
    try:
        psycopg2.connect(settings.database_url, connect_timeout=2).close()
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db_available(), reason="local postgres not running")


def test_fetch_clinics_raises_hira_error_when_connection_fails(monkeypatch):
    """Graceful failure: connection errors surface as HiraError, mirroring SbizError,
    so real_competition.py can catch it and fall back to SBIZ-based competition."""
    import app.services.hira_clinics as hira_clinics

    def _boom():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(hira_clinics, "get_connection", _boom)

    with pytest.raises(HiraError):
        fetch_clinics_in_radius(37.5559, 126.9370, 500)


def test_subjects_summary_raises_hira_error_when_connection_fails(monkeypatch):
    import app.services.hira_clinics as hira_clinics

    def _boom():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(hira_clinics, "get_connection", _boom)

    with pytest.raises(HiraError):
        subjects_summary_in_radius(37.5559, 126.9370, 500)


@requires_db
def test_fetch_clinics_in_radius_sinchon_returns_clinics_with_subjects():
    clinics = fetch_clinics_in_radius(37.5559, 126.9370, 500)
    assert len(clinics) > 0
    for c in clinics[:5]:
        assert c.distance_m <= 500
        assert c.ykiho


@requires_db
def test_fetch_clinics_in_radius_filters_by_subject_code():
    ortho_only = fetch_clinics_in_radius(37.5559, 126.9370, 1000, subject_codes=["05"])
    for c in ortho_only:
        codes = {s.subject_code for s in c.subjects}
        assert "05" in codes


@requires_db
def test_subjects_summary_in_radius_sinchon():
    summary = subjects_summary_in_radius(37.5559, 126.9370, 1000)
    assert len(summary) > 0
    for s in summary:
        assert s.clinic_count > 0
        assert s.specialist_total >= 0
