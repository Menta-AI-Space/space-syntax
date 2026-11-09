"""Tests for the 진료과목(clinic specialty) picker backend additions:
- GET /api/clinic-subjects (nationwide HIRA subject list, degrades on HiraError)
- explicit `subject_codes` override on /api/real-competition and
  /api/lease-feasibility winning over the Q-code-derived
  `resolve_subject_codes(inds_scls_cd)` fallback.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.models.schemas import LeaseFeasibilityRequest, RealCompetitionRequest
from app.routers import lease_feasibility as lease_feasibility_router
from app.routers import real_competition as real_competition_router
from app.services.hira_clinics import HiraError, SubjectInfo
from app.services.sbiz_stores import StoreRecord


class ProbeComplete(Exception):
    """Raised by a stubbed compute_real_competition to short-circuit the router
    once the interesting kwargs have been captured, without needing to mock
    the rest of the request pipeline (isochrones, synergy, model fit, ...)."""


def _clinic_upjong() -> SimpleNamespace:
    return SimpleNamespace(
        archetype="medical",
        inds_lcls_cd="Q1",
        inds_mcls_cd="Q102",
        inds_scls_cd="Q10202",
        label="외과 의원",
        breadcrumb="보건업 > 의원 > 외과 의원",
        business_type="custom",
        match_method="scls",
    )


def _store(name: str = "매장1") -> StoreRecord:
    return StoreRecord(
        bizes_id=name,
        bizes_nm=name,
        inds_lcls_cd="Q1",
        inds_lcls_nm="보건업",
        inds_mcls_cd="Q102",
        inds_mcls_nm="의원",
        inds_scls_cd="Q10202",
        inds_scls_nm="외과 의원",
        lon=126.9372,
        lat=37.5560,
        bld_mng_no="",
        flr_no="1",
        rdnm_adr="서울 어딘가",
    )


# --- GET /api/clinic-subjects -------------------------------------------------


def test_clinic_subjects_endpoint_returns_ordered_list(monkeypatch):
    monkeypatch.setattr(
        real_competition_router,
        "list_all_subjects",
        lambda: [
            SubjectInfo(subject_code="01", subject_name="내과", institution_count=100),
            SubjectInfo(subject_code="05", subject_name="정형외과", institution_count=50),
        ],
    )
    resp = asyncio.run(real_competition_router.clinic_subjects())
    assert [s.subject_code for s in resp.subjects] == ["01", "05"]
    assert resp.subjects[0].subject_name == "내과"
    assert resp.subjects[0].institution_count == 100
    assert resp.warnings == []


def test_clinic_subjects_endpoint_degrades_to_empty_list_on_hira_error(monkeypatch):
    def _raise():
        raise HiraError("hira_hospitals/hira_hospital_subjects 테이블이 없습니다.")

    monkeypatch.setattr(real_competition_router, "list_all_subjects", _raise)
    resp = asyncio.run(real_competition_router.clinic_subjects())
    assert resp.subjects == []
    assert resp.warnings and "HIRA" in resp.warnings[0] or "테이블" in resp.warnings[0]


# --- /api/real-competition: subject_codes override ---------------------------


def _patch_real_competition_pipeline(monkeypatch, *, captured: dict):
    async def fake_resolve_point(request):
        return 37.5559, 126.9370

    async def fake_resolve_upjong(request):
        return _clinic_upjong()

    async def fake_load_context(lat, lng, radius_m, upjong):
        return [object()], object(), [], upjong, None, []

    def fake_compute_isochrones(**kwargs):
        return SimpleNamespace(warnings=[], bands=[])

    def fake_compute_real_competition(**kwargs):
        captured["subject_codes"] = kwargs.get("subject_codes")
        raise ProbeComplete

    monkeypatch.setattr(real_competition_router, "_resolve_point", fake_resolve_point)
    monkeypatch.setattr(real_competition_router, "_resolve_upjong", fake_resolve_upjong)
    monkeypatch.setattr(real_competition_router, "resolve_vertical", lambda **kwargs: "clinic")
    monkeypatch.setattr(
        real_competition_router, "resolve_subject_codes", lambda inds_scls_cd: ["04", "05"]
    )
    monkeypatch.setattr(real_competition_router, "_load_context", fake_load_context)
    monkeypatch.setattr(
        real_competition_router, "competition_fetch_radius_m", lambda *a, **k: 500
    )
    monkeypatch.setattr(real_competition_router, "compute_isochrones", fake_compute_isochrones)
    monkeypatch.setattr(
        real_competition_router, "compute_real_competition", fake_compute_real_competition
    )


def test_real_competition_subject_override_wins_over_qcode_derivation(monkeypatch):
    captured: dict = {}
    _patch_real_competition_pipeline(monkeypatch, captured=captured)

    request = RealCompetitionRequest(
        lat=37.5559, lng=126.9370, radius_m=500, subject_codes=["99"]
    )
    with pytest.raises(ProbeComplete):
        asyncio.run(real_competition_router.real_competition(request))

    # Explicit override (["99"]) wins even though resolve_subject_codes (mocked
    # to simulate the Q10202 -> ["04","05"] Q-code derivation) would return a
    # different list.
    assert captured["subject_codes"] == ["99"]


def test_real_competition_falls_back_to_qcode_derivation_when_no_override(monkeypatch):
    captured: dict = {}
    _patch_real_competition_pipeline(monkeypatch, captured=captured)

    request = RealCompetitionRequest(lat=37.5559, lng=126.9370, radius_m=500)
    with pytest.raises(ProbeComplete):
        asyncio.run(real_competition_router.real_competition(request))

    assert captured["subject_codes"] == ["04", "05"]


# --- /api/lease-feasibility: subject_codes override ---------------------------


def _patch_lease_feasibility_pipeline(monkeypatch, *, captured: dict):
    from app.services.hira_clinics import HiraError as _HiraError  # noqa: F401
    from app.services.sgis import SgisError

    async def fake_resolve_point(request):
        return 37.5559, 126.9370

    async def fake_resolve_upjong(request):
        return _clinic_upjong()

    async def fake_site_score(score_req):
        return SimpleNamespace(
            competition=None,
            total_score=70.0,
            grade="B",
            demand=None,
            segment=SimpleNamespace(
                integration_top_pct=0, choice_top_pct=0, connectivity_top_pct=0
            ),
            transit=None,
        )

    async def fake_cannibalization(dash_req):
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="no roads")

    async def fake_fetch_demand_profile(lat, lng):
        raise SgisError("no demand profile")

    async def fake_fetch_sales_benchmark(*args, **kwargs):
        return SimpleNamespace(
            warnings=[], monthly_revenue_man=None, per_store_monthly_man=None,
            scope="", data_source="",
        )

    async def fake_fetch_stores_in_radius(lat, lng, radius_m):
        return [_store()]

    def fake_compute_real_competition(**kwargs):
        captured["subject_codes"] = kwargs.get("subject_codes")
        raise ProbeComplete

    monkeypatch.setattr(lease_feasibility_router, "_resolve_point", fake_resolve_point)
    monkeypatch.setattr(lease_feasibility_router, "_resolve_upjong", fake_resolve_upjong)
    monkeypatch.setattr(lease_feasibility_router, "site_score", fake_site_score)
    monkeypatch.setattr(lease_feasibility_router, "cannibalization", fake_cannibalization)
    monkeypatch.setattr(
        lease_feasibility_router, "fetch_demand_profile", fake_fetch_demand_profile
    )
    monkeypatch.setattr(
        lease_feasibility_router, "fetch_sales_benchmark", fake_fetch_sales_benchmark
    )
    monkeypatch.setattr(lease_feasibility_router, "resolve_vertical", lambda **kwargs: "clinic")
    monkeypatch.setattr(
        lease_feasibility_router, "fetch_stores_in_radius", fake_fetch_stores_in_radius
    )
    monkeypatch.setattr(
        lease_feasibility_router, "resolve_subject_codes", lambda inds_scls_cd: ["04", "05"]
    )
    monkeypatch.setattr(
        lease_feasibility_router, "compute_real_competition", fake_compute_real_competition
    )


def test_lease_feasibility_subject_override_wins_over_qcode_derivation(monkeypatch):
    captured: dict = {}
    _patch_lease_feasibility_pipeline(monkeypatch, captured=captured)

    request = LeaseFeasibilityRequest(
        lat=37.5559, lng=126.9370, radius_m=500, monthly_rent_man=300, subject_codes=["99"]
    )
    with pytest.raises(ProbeComplete):
        asyncio.run(lease_feasibility_router.lease_feasibility(request))

    assert captured["subject_codes"] == ["99"]


def test_lease_feasibility_falls_back_to_qcode_derivation_when_no_override(monkeypatch):
    captured: dict = {}
    _patch_lease_feasibility_pipeline(monkeypatch, captured=captured)

    request = LeaseFeasibilityRequest(
        lat=37.5559, lng=126.9370, radius_m=500, monthly_rent_man=300
    )
    with pytest.raises(ProbeComplete):
        asyncio.run(lease_feasibility_router.lease_feasibility(request))

    assert captured["subject_codes"] == ["04", "05"]
