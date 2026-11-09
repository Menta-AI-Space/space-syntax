"""Tests for the clinic-vertical HIRA wiring in app.services.real_competition:
- falls back to SBIZ-based direct bucket when HIRA tables are absent
- replaces the direct bucket with HIRA clinics (weighted by specialty relation)
  when subject_codes are resolved and HIRA data is available
"""

from __future__ import annotations

from app.services.hira_clinics import ClinicRecord, HiraError, SubjectCount
from app.services.real_competition import compute_real_competition
from app.services.sbiz_stores import StoreRecord


def _store(name: str, scls_nm: str = "내과/소아과 의원") -> StoreRecord:
    return StoreRecord(
        bizes_id=name,
        bizes_nm=name,
        inds_lcls_cd="Q1",
        inds_lcls_nm="보건업",
        inds_mcls_cd="Q102",
        inds_mcls_nm="의원",
        inds_scls_cd="Q10202",
        inds_scls_nm=scls_nm,
        lon=126.9372,
        lat=37.5560,
        bld_mng_no="",
        flr_no="1",
        rdnm_adr="서울 어딘가",
    )


def test_clinic_falls_back_to_sbiz_when_hira_tables_absent(monkeypatch):
    def _raise(*args, **kwargs):
        raise HiraError("hira_hospitals/hira_hospital_subjects 테이블이 없습니다.")

    monkeypatch.setattr(
        "app.services.hira_clinics.fetch_clinics_in_radius", _raise
    )

    stores = [_store("마포정형외과의원")]
    result = compute_real_competition(
        lat=37.5559,
        lng=126.9370,
        stores=stores,
        vertical="clinic",
        radius_m=500,
        subject_codes=["04", "05"],
    )

    direct = next(b for b in result.buckets if b.key == "direct")
    # HIRA lookup failed -> fell back to the existing SBIZ keyword-bucket path,
    # which still classifies "의원" stores into direct via keywords/mcls_codes.
    assert direct.count >= 1
    assert direct.nearest[0].name == "마포정형외과의원"
    assert any("HIRA" in w for w in result.warnings)


def test_clinic_uses_hira_direct_entries_when_available(monkeypatch):
    clinic = ClinicRecord(
        ykiho="Y1",
        name="신촌정형외과의원",
        cl_code="31",
        cl_name="의원",
        addr="서울 서대문구",
        lon=126.9371,
        lat=37.5560,
        distance_m=50.0,
        subjects=[SubjectCount(subject_code="05", subject_name="정형외과", specialist_count=2)],
    )
    rehab_clinic = ClinicRecord(
        ykiho="Y2",
        name="재활사랑의원",
        cl_code="31",
        cl_name="의원",
        addr="서울 서대문구",
        lon=126.9375,
        lat=37.5562,
        distance_m=120.0,
        subjects=[SubjectCount(subject_code="21", subject_name="재활의학과", specialist_count=1)],
    )
    unrelated_clinic = ClinicRecord(
        ykiho="Y3",
        name="눈사랑안과의원",
        cl_code="31",
        cl_name="의원",
        addr="서울 서대문구",
        lon=126.9373,
        lat=37.5561,
        distance_m=80.0,
        subjects=[SubjectCount(subject_code="12", subject_name="안과", specialist_count=1)],
    )

    def _fake_fetch(lat, lng, radius_m, *, subject_codes=None, cl_codes=None):
        return [clinic, rehab_clinic, unrelated_clinic]

    monkeypatch.setattr(
        "app.services.hira_clinics.fetch_clinics_in_radius", _fake_fetch
    )

    # No SBIZ stores at all — proves the direct bucket is entirely HIRA-sourced.
    result = compute_real_competition(
        lat=37.5559,
        lng=126.9370,
        stores=[],
        vertical="clinic",
        radius_m=500,
        subject_codes=["05"],  # 정형외과 선택
    )

    direct = next(b for b in result.buckets if b.key == "direct")
    names = {n.name for n in direct.nearest}
    assert "신촌정형외과의원" in names  # direct subject match (weight 1.0)
    assert "재활사랑의원" in names  # adjacent subject (weight 0.7)
    assert "눈사랑안과의원" not in names  # unrelated specialty excluded
    assert not result.warnings

    ortho = next(n for n in direct.nearest if n.name == "신촌정형외과의원")
    rehab = next(n for n in direct.nearest if n.name == "재활사랑의원")
    assert ortho.weight > rehab.weight  # direct (1.0x) outweighs adjacent (0.7x)
    assert ortho.subjects == ["정형외과"]
    assert ortho.specialist_count == 2


def test_clinic_without_subject_codes_uses_existing_sbiz_path(monkeypatch):
    """When no subject is selected (subject_codes=None), behavior is unchanged —
    HIRA is never even queried."""
    called = []

    def _fake_fetch(*args, **kwargs):
        called.append(1)
        return []

    monkeypatch.setattr(
        "app.services.hira_clinics.fetch_clinics_in_radius", _fake_fetch
    )

    stores = [_store("마포내과의원", scls_nm="내과/소아과 의원")]
    result = compute_real_competition(
        lat=37.5559,
        lng=126.9370,
        stores=stores,
        vertical="clinic",
        radius_m=500,
        subject_codes=None,
    )

    direct = next(b for b in result.buckets if b.key == "direct")
    assert direct.count >= 1
    assert not called  # HIRA path never triggered
