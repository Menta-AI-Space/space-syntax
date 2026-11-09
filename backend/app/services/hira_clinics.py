"""HIRA (심평원) 병의원 진료과목 기반 반경 조회 — clinic 수직의 real competition을
specialty-aware하게 만들기 위한 데이터 소스.

hira_hospitals / hira_hospital_subjects (tools/ingest_hira_specialty.py로 적재)를
읽는다. 테이블이 아직 없거나(적재 전) DB 접속이 실패하면 HiraError를 던진다 — 호출부는
SbizError를 다루듯 이를 잡아 warning으로 강등하고 기존 동작으로 폴백해야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.spatial_query import get_connection


class HiraError(Exception):
    """hira_hospitals/hira_hospital_subjects 조회 실패 (테이블 미적재 등, enrichment-only)."""


@dataclass
class SubjectCount:
    subject_code: str
    subject_name: str
    specialist_count: int = 0


@dataclass
class ClinicRecord:
    ykiho: str
    name: str
    cl_code: str | None
    cl_name: str | None
    addr: str | None
    lon: float
    lat: float
    distance_m: float
    subjects: list[SubjectCount] = field(default_factory=list)
    total_doctors: int | None = None


_CLINICS_IN_RADIUS_SQL = """
SELECT h.ykiho, h.name, h.cl_code, h.cl_name, h.addr, h.lon, h.lat,
       ST_Distance(
           h.geom_5179,
           ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), 5179)
       ) AS distance_m,
       h.total_doctors
FROM hira_hospitals h
WHERE ST_DWithin(
        h.geom_5179,
        ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), 5179),
        %(radius_m)s
      )
      {cl_filter}
      {subject_filter}
ORDER BY distance_m
"""

_SUBJECTS_FOR_YKIHOS_SQL = """
SELECT ykiho, subject_code, subject_name, specialist_count
FROM hira_hospital_subjects
WHERE ykiho = ANY(%s)
"""


def _tables_exist(cur) -> bool:
    cur.execute(
        "SELECT to_regclass('public.hira_hospitals') IS NOT NULL "
        "AND to_regclass('public.hira_hospital_subjects') IS NOT NULL"
    )
    return bool(cur.fetchone()[0])


def fetch_clinics_in_radius(
    lat: float,
    lng: float,
    radius_m: int,
    *,
    subject_codes: list[str] | None = None,
    cl_codes: list[str] | None = None,
) -> list[ClinicRecord]:
    """반경 내 HIRA 등록 의료기관 (진료과목 포함).

    subject_codes가 주어지면 해당 진료과목을 하나라도 표방하는 기관만 반환한다
    (반환되는 subjects 리스트에는 해당 기관의 모든 진료과목이 담긴다 — 필터링된
    부분집합이 아니다). cl_codes는 종별코드(예: 의원=31) 필터.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if not _tables_exist(cur):
                    raise HiraError(
                        "hira_hospitals/hira_hospital_subjects 테이블이 없습니다. "
                        "python -m tools.ingest_hira_specialty 를 먼저 실행하세요."
                    )

                params: dict = {"lng": lng, "lat": lat, "radius_m": radius_m}
                cl_filter = ""
                if cl_codes:
                    cl_filter = "AND h.cl_code = ANY(%(cl_codes)s)"
                    params["cl_codes"] = cl_codes

                subject_filter = ""
                if subject_codes:
                    subject_filter = (
                        "AND EXISTS (SELECT 1 FROM hira_hospital_subjects s "
                        "WHERE s.ykiho = h.ykiho AND s.subject_code = ANY(%(subject_codes)s))"
                    )
                    params["subject_codes"] = subject_codes

                cur.execute(
                    _CLINICS_IN_RADIUS_SQL.format(
                        cl_filter=cl_filter, subject_filter=subject_filter
                    ),
                    params,
                )
                rows = cur.fetchall()

                clinics: dict[str, ClinicRecord] = {}
                order: list[str] = []
                for r in rows:
                    ykiho = r[0]
                    order.append(ykiho)
                    clinics[ykiho] = ClinicRecord(
                        ykiho=ykiho,
                        name=r[1],
                        cl_code=r[2],
                        cl_name=r[3],
                        addr=r[4],
                        lon=float(r[5]) if r[5] is not None else lng,
                        lat=float(r[6]) if r[6] is not None else lat,
                        distance_m=float(r[7]),
                        total_doctors=r[8],
                    )

                if clinics:
                    cur.execute(_SUBJECTS_FOR_YKIHOS_SQL, (list(clinics),))
                    for ykiho, subject_code, subject_name, specialist_count in cur.fetchall():
                        if ykiho in clinics:
                            clinics[ykiho].subjects.append(
                                SubjectCount(
                                    subject_code=subject_code,
                                    subject_name=subject_name or "",
                                    specialist_count=int(specialist_count or 0),
                                )
                            )
    except HiraError:
        raise
    except Exception as e:  # pragma: no cover - connection/driver errors
        raise HiraError(f"HIRA 조회 실패: {e}") from e

    return [clinics[y] for y in order]


@dataclass
class SubjectSummary:
    subject_code: str
    subject_name: str
    clinic_count: int
    specialist_total: int


_SUBJECT_SUMMARY_SQL = """
SELECT s.subject_code, MAX(s.subject_name) AS subject_name,
       COUNT(DISTINCT h.ykiho) AS clinic_count,
       COALESCE(SUM(s.specialist_count), 0) AS specialist_total
FROM hira_hospitals h
JOIN hira_hospital_subjects s ON s.ykiho = h.ykiho
WHERE ST_DWithin(
        h.geom_5179,
        ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), 5179),
        %(radius_m)s
      )
GROUP BY s.subject_code
ORDER BY clinic_count DESC
"""


def subjects_summary_in_radius(lat: float, lng: float, radius_m: int) -> list[SubjectSummary]:
    """반경 내 진료과목별 공급 요약 (기관 수 + 전문의 수 합) — '이 상권의 과목별 공급' 뷰용."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if not _tables_exist(cur):
                    raise HiraError(
                        "hira_hospitals/hira_hospital_subjects 테이블이 없습니다. "
                        "python -m tools.ingest_hira_specialty 를 먼저 실행하세요."
                    )
                cur.execute(_SUBJECT_SUMMARY_SQL, {"lng": lng, "lat": lat, "radius_m": radius_m})
                rows = cur.fetchall()
    except HiraError:
        raise
    except Exception as e:  # pragma: no cover
        raise HiraError(f"HIRA 조회 실패: {e}") from e

    return [
        SubjectSummary(
            subject_code=r[0],
            subject_name=r[1] or "",
            clinic_count=int(r[2]),
            specialist_total=int(r[3]),
        )
        for r in rows
    ]


@dataclass
class SubjectInfo:
    subject_code: str
    subject_name: str
    institution_count: int = 0


_ALL_SUBJECTS_SQL = """
SELECT subject_code, MAX(subject_name) AS subject_name, COUNT(DISTINCT ykiho) AS institution_count
FROM hira_hospital_subjects
GROUP BY subject_code
ORDER BY subject_code
"""


def list_all_subjects() -> list[SubjectInfo]:
    """전국 진료과목 목록(코드+명칭+전국 기관 수) — 진료과목 선택 UI(picker)용.

    subjects_summary_in_radius와 달리 반경 제약이 없다 (선택 가능한 전체 과목
    목록을 보여줘야 하므로).
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if not _tables_exist(cur):
                    raise HiraError(
                        "hira_hospitals/hira_hospital_subjects 테이블이 없습니다. "
                        "python -m tools.ingest_hira_specialty 를 먼저 실행하세요."
                    )
                cur.execute(_ALL_SUBJECTS_SQL)
                rows = cur.fetchall()
    except HiraError:
        raise
    except Exception as e:  # pragma: no cover
        raise HiraError(f"HIRA 조회 실패: {e}") from e

    return [
        SubjectInfo(subject_code=r[0], subject_name=r[1] or "", institution_count=int(r[2]))
        for r in rows
    ]
