"""시장 신호 적재 도구의 «조용히 틀리는» 지점만 골라 검사한다.

⚠️ 적재 결과 수치는 테스트하지 않는다 (CLAUDE.md). 여기서 막으려는 것은 셋이다.

1. **PNU 조립** — 다섯 컬럼을 이어 붙여 19자리를 만드는데, 자릿수가 안 맞는 행을
   0으로 채워 맞추면 «다른 자치구의 필지»가 된다. 조용히 틀리고, 틀린 줄도 모른다.
2. **시점 누수** — 인허가 표에는 허가일·착공일·사용승인일이 함께 있다. 준공 사실을
   그 이전 연도 행에 붙이면 미래를 보는 것이고 백테스트가 전부 거짓이 된다.
   그래서 시점 절단 SQL이 «허가일만» 기준으로 자르는지 확인한다.
3. **출처 없는 계획** — planned_transit 는 사람이 채우는 표라, 출처·확인일이 빈
   행을 받아 주면 "우리가 지어낸 값"이 통제변수로 들어간다.
"""

from __future__ import annotations

from datetime import date

import pytest

from tools import compute_parcel_market_signals as sig
from tools.ingest_building_permits import make_pnu, parse_date
from tools.ingest_large_stores import is_closed
from tools.ingest_vacancy_rates import normalize_quarter, resolve_region
from tools.seed_planned_transit import read_rows as transit_rows


# ── 1. PNU 조립 ──────────────────────────────────────────────────────
def test_pnu_is_19_digits_and_zero_pads_only_bun_ji():
    # 강남구 역삼동 823-15 → 1168010100 + 1(대지) + 0823 + 0015
    assert make_pnu("11680", "10100", "1", "823", "15") == "1168010100" "1" "0823" "0015"


def test_pnu_defaults_ji_to_zero_when_missing():
    """부번이 없는 필지(예: 823번지)는 부번 0000 이다 — 빈칸이 아니다."""
    assert make_pnu("11680", "10100", "1", "823", "").endswith("08230000")


def test_pnu_keeps_mountain_flag():
    """산 2-1 과 2-1 은 «다른 필지»다. 대지구분을 뭉개면 안 된다."""
    land = make_pnu("11680", "10100", "1", "2", "1")
    mountain = make_pnu("11680", "10100", "2", "2", "1")
    assert land != mountain
    assert land[10] == "1" and mountain[10] == "2"


@pytest.mark.parametrize(
    "sigungu,bjdong,bun",
    [
        ("1168", "10100", "823"),      # 시군구 4자리 — 0 채우면 다른 구가 된다
        ("11680", "1010", "823"),      # 법정동 4자리
        ("11680", "10100", ""),        # 본번 없음
        ("11680", "10100", "12345"),   # 본번 5자리
    ],
)
def test_pnu_returns_none_instead_of_guessing(sigungu, bjdong, bun):
    """모양이 안 맞으면 «지어내지 말고» None 을 돌려줘야 한다."""
    assert make_pnu(sigungu, bjdong, "1", bun, "0") is None


# ── 2. 날짜 파싱 ─────────────────────────────────────────────────────
def test_parse_date_reads_yyyymmdd():
    assert parse_date("20240315") == date(2024, 3, 15)
    assert parse_date("2024-03-15") == date(2024, 3, 15)


@pytest.mark.parametrize("raw", ["", "0", "00000000", "20241332", "18500101", "20991231"])
def test_parse_date_rejects_garbage_and_far_future(raw):
    """원본에 2099 같은 값이 섞여 온다. 그대로 두면 시점 절단이 망가진다."""
    assert parse_date(raw) is None


# ── 3. 시점 절단 — 여기가 새면 전부 거짓이 된다 ──────────────────────
def test_time_cut_uses_permit_date_only():
    """절단 조건이 «허가일»에만 걸려 있어야 한다.

    착공일·사용승인일로 자르면 그 시점엔 아직 일어나지 않은 사실을 과거 행에
    붙이게 된다. 원시 표에는 세 날짜가 다 있으므로 실수하기 쉽다.
    """
    sql = sig.UPSERT
    assert "b.permit_date <  %(cut)s" in sql or "b.permit_date < %(cut)s" in sql
    # 절단 대상으로 착공·준공일을 쓰지 않는다.
    assert "start_date <" not in sql
    assert "approval_date <" not in sql


def test_closed_stores_are_excluded_at_the_cut():
    """컷 시점에 이미 폐업한 점포는 «지금 있는 것»으로 세면 안 된다."""
    assert "s.close_date IS NULL OR s.close_date >= %(cut)s" in sig.UPSERT


def test_transit_cut_uses_announcement_not_expected_open():
    """개통 «예정»일은 자주 바뀐다 — 시장이 안 날짜는 고시일이다."""
    assert "t.announced_on < %(cut)s" in sig.UPSERT
    assert "open_expected" not in sig.UPSERT


def test_parse_years_accepts_single_and_range():
    assert sig.parse_years("2025") == [2025]
    assert sig.parse_years("2018-2020") == [2018, 2019, 2020]


# ── 4. 대규모점포 폐업 판정 ──────────────────────────────────────────
@pytest.mark.parametrize("status", ["폐업", "직권말소", "영업정지(취소)", "휴업"])
def test_is_closed_catches_every_wording(status):
    assert is_closed(status) is True


def test_is_closed_leaves_operating_stores_alone():
    assert is_closed("영업/정상") is False
    assert is_closed("") is False


# ── 5. 공실률 — 지역·분기 정규화 ─────────────────────────────────────
@pytest.mark.parametrize(
    "header,expected",
    [("2025년 1분기", "2025Q1"), ("2025 1/4", "2025Q1"), ("2024Q3", "2024Q3")],
)
def test_normalize_quarter(header, expected):
    assert normalize_quarter(header) == expected


def test_sido_and_trdar_are_distinguished():
    """"서울"(시도)과 "서울 강남대로"(상권)는 «다른 단위»다.

    섞으면 상권 값을 시도 값으로 읽어 필지 판정이 통째로 틀어진다.
    """
    assert resolve_region("서울") == ("11", "sido")
    code, level = resolve_region("서울 강남대로")
    assert level == "trdar" and code.startswith("11:")


# ── 6. 계획 노선 — 출처 없는 행은 받지 않는다 ────────────────────────
HEADER = ("line_nm,station_nm,stage,announced_on,open_expected,"
          "lng,lat,src_url,verified_on,note\n")


def _write(tmp_path, body):
    p = tmp_path / "transit.csv"
    p.write_text(HEADER + body, encoding="utf-8")
    return p


def test_transit_row_without_source_is_rejected(tmp_path):
    path = _write(tmp_path, "위례신사선,신사역,예타,2025-04-30,,127.02,37.51,,2026-08-26,\n")
    with pytest.raises(SystemExit, match="src_url"):
        list(transit_rows(path))


def test_transit_row_with_swapped_coordinates_is_rejected(tmp_path):
    """경도·위도를 바꿔 넣는 실수는 흔하고, 넣으면 조용히 엉뚱한 곳이 된다."""
    path = _write(
        tmp_path,
        "위례신사선,신사역,예타,2025-04-30,,37.51,127.02,https://x.go.kr/1,2026-08-26,\n")
    with pytest.raises(SystemExit, match="한국 밖"):
        list(transit_rows(path))


def test_transit_valid_row_is_accepted(tmp_path):
    path = _write(
        tmp_path,
        "위례신사선,신사역,예타,2025-04-30,,127.02,37.51,https://x.go.kr/1,2026-08-26,메모\n")
    rows = list(transit_rows(path))
    assert len(rows) == 1
    assert rows[0][1] == "위례신사선"
    assert rows[0][4] == date(2025, 4, 30)      # announced_on
