"""Seoul commercial district estimated sales benchmark (hybrid API + CSV)."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.seoul_commercial import SeoulCommercialError, _fetch_seoul_json

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
UPJONG_MAP_PATH = DATA_DIR / "seoul_service_upjong_map.json"
SNAPSHOT_PATH = DATA_DIR / "seoul_sales_snapshot.csv"
MAN_WON = 10_000

# Candidate Open API dataset IDs (Seoul golmok — may vary by quarter)
SALES_DATASETS = (
    "VwsmTrdarSelOslQq",
    "TbTrdarSelngOsl",
    "VwsmAdmdongSelOslQq",
    "TbAdmdongSelngOsl",
)


@dataclass
class SalesBenchmark:
    monthly_revenue_man: float | None
    per_store_monthly_man: float | None
    total_quarterly_sales_won: int | None
    store_count: int | None
    service_code: str
    service_name: str
    scope: str
    data_source: str
    warnings: list[str]


def _load_upjong_map() -> dict[str, Any]:
    with UPJONG_MAP_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def resolve_seoul_service_code(
    *,
    inds_scls_cd: str | None,
    inds_mcls_cd: str | None,
    business_label: str = "",
) -> tuple[str, str] | None:
    """업종 코드/라벨을 서울시 상권분석 서비스업종 코드로 매핑한다.

    매핑에 없는 업종(미용실·헬스장·오락·숙박·주유소·전기차충전·전문서비스 등)을
    다른 업종 매출로 조용히 대체하면 화면에는 그럴싸한 숫자가 뜨지만 실제로는
    전혀 다른 업종의 매출이다. 매칭되는 게 없으면 None을 반환해 호출부가
    "벤치마크 없음"으로 정직하게 표시하게 한다.
    """
    mapping = _load_upjong_map()
    if inds_scls_cd and inds_scls_cd in mapping:
        entry = mapping[inds_scls_cd]
        return entry["service_code"], entry["service_name"]
    if inds_mcls_cd and inds_mcls_cd in mapping:
        entry = mapping[inds_mcls_cd]
        return entry["service_code"], entry["service_name"]
    label = business_label.strip().lower()
    if label:
        for entry in mapping.values():
            keywords = entry.get("keywords") or []
            if any(kw in label for kw in keywords):
                return entry["service_code"], entry["service_name"]
    return None


def _normalize_dong_name(adm_name: str | None) -> str | None:
    if not adm_name:
        return None
    parts = adm_name.split()
    for p in reversed(parts):
        if p.endswith("동"):
            return p
    return parts[-1] if parts else None


def _match_snapshot_row(
    adm_name: str | None,
    service_code: str,
) -> dict[str, str] | None:
    if not SNAPSHOT_PATH.is_file():
        return None
    dong = _normalize_dong_name(adm_name)
    gu_hint = None
    if adm_name:
        for part in adm_name.split():
            if part.endswith("구"):
                gu_hint = part
                break
    best: dict[str, str] | None = None
    with SNAPSHOT_PATH.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("service_code") != service_code:
                continue
            row_adm = row.get("adm_name") or ""
            if dong and dong in row_adm:
                if gu_hint and gu_hint in row_adm:
                    return row
                best = row
            elif gu_hint and gu_hint in row_adm and best is None:
                best = row
    return best


def _parse_sales_amount(row: dict[str, Any]) -> int | None:
    for key in (
        "THSMON_SELNG_AMT",
        "TOT_SELNG_AMT",
        "SELNG_AMT",
        "SALES_AMT",
        "MON_SELNG_AMT",
        "THS_MON_SELNG_AMT",
    ):
        val = row.get(key)
        if val is not None and str(val).strip() not in ("", "0"):
            try:
                return int(float(str(val).replace(",", "")))
            except (TypeError, ValueError):
                continue
    return None


def _parse_store_count(row: dict[str, Any]) -> int | None:
    for key in ("STOR_CO", "TOT_STOR_CO", "STORE_CO", "점포수"):
        val = row.get(key)
        if val is not None and str(val).strip():
            try:
                return int(float(str(val).replace(",", "")))
            except (TypeError, ValueError):
                continue
    return None


def _parse_service_code(row: dict[str, Any]) -> str | None:
    for key in ("SVC_INDUTY_CD", "SVC_INDUTY_CODE", "INDUTY_CD", "MAIN_SVC_CD"):
        val = row.get(key)
        if val:
            return str(val).strip()
    return None


async def _fetch_sales_from_api(
    service_code: str,
    *,
    adm_name: str | None,
    dong_name: str | None,
) -> tuple[int | None, int | None, str]:
    if not settings.data_seoul_go_api_key.strip():
        raise SeoulCommercialError("DATA_SEOUL_GO_API_KEY is not configured")

    last_error = ""
    for dataset in SALES_DATASETS:
        try:
            data = await _fetch_seoul_json(dataset, 1, 1000)
        except SeoulCommercialError as exc:
            last_error = str(exc)
            continue
        except Exception as exc:
            last_error = str(exc)
            continue

        rows = data.get("rows") or []
        matched: list[dict[str, Any]] = []
        for row in rows:
            code = _parse_service_code(row)
            if code and code != service_code:
                continue
            row_adm = str(row.get("ADMDONG_NM") or row.get("ADMDNG_NM") or row.get("TRDAR_NM") or "")
            if dong_name and dong_name not in row_adm:
                if adm_name and not any(
                    part in row_adm for part in adm_name.split() if part.endswith(("동", "구"))
                ):
                    continue
            matched.append(row)

        if not matched:
            continue

        # Use latest quarter row
        latest = sorted(matched, key=lambda r: str(r.get("QUART") or r.get("CRTR_YM") or ""))[-1]
        sales = _parse_sales_amount(latest)
        stores = _parse_store_count(latest)
        if sales:
            return sales, stores, f"서울시 상권분석 Open API ({dataset})"

    raise SeoulCommercialError(last_error or "No matching sales rows from Seoul API")


def _benchmark_from_snapshot(
    row: dict[str, str],
    *,
    fallback_store_count: int | None,
    service_code: str,
    service_name: str,
) -> SalesBenchmark:
    warnings: list[str] = []
    quarterly = int(row.get("total_sales_won") or 0)
    stores = int(row.get("store_count") or 0) or (fallback_store_count or 0)
    if stores <= 0:
        stores = max(fallback_store_count or 1, 1)
        warnings.append("점포 수 미확인 — 반경 내 동일업종 수로 대체")
    monthly_total_man = (quarterly / 3) / MAN_WON if quarterly > 0 else None
    per_store = None
    if monthly_total_man and stores > 0:
        per_store = monthly_total_man / stores
    return SalesBenchmark(
        monthly_revenue_man=round(per_store, 1) if per_store else round(monthly_total_man or 0, 1),
        per_store_monthly_man=round(per_store, 1) if per_store else None,
        total_quarterly_sales_won=quarterly or None,
        store_count=stores,
        service_code=service_code,
        service_name=service_name,
        scope=row.get("adm_name") or "서울 행정동",
        data_source="서울 추정매출 CSV 스냅샷",
        warnings=warnings,
    )


def is_seoul_location(adm_name: str | None, lat: float, lng: float) -> bool:
    if adm_name and ("서울" in adm_name or adm_name.startswith("서울특별시")):
        return True
    # Rough bbox for Seoul
    return 37.42 <= lat <= 37.70 and 126.76 <= lng <= 127.18


async def fetch_sales_benchmark(
    lat: float,
    lng: float,
    *,
    adm_name: str | None,
    inds_scls_cd: str | None,
    inds_mcls_cd: str | None,
    business_label: str = "",
    fallback_store_count: int | None = None,
) -> SalesBenchmark:
    resolved = resolve_seoul_service_code(
        inds_scls_cd=inds_scls_cd,
        inds_mcls_cd=inds_mcls_cd,
        business_label=business_label,
    )
    if resolved is None:
        return SalesBenchmark(
            monthly_revenue_man=None,
            per_store_monthly_man=None,
            total_quarterly_sales_won=None,
            store_count=fallback_store_count,
            service_code="",
            service_name=business_label or "미분류",
            scope=adm_name or "서울",
            data_source="미제공",
            warnings=[
                "이 업종은 서울시 상권분석 추정매출 코드와 매핑되어 있지 않아 "
                "매출 벤치마크를 제공하지 않습니다 — 손익분기·입지 점수 기준으로만 판정합니다."
            ],
        )
    service_code, service_name = resolved
    warnings: list[str] = []

    if not is_seoul_location(adm_name, lat, lng):
        return SalesBenchmark(
            monthly_revenue_man=None,
            per_store_monthly_man=None,
            total_quarterly_sales_won=None,
            store_count=fallback_store_count,
            service_code=service_code,
            service_name=service_name,
            scope="서울 외 지역",
            data_source="미제공",
            warnings=[
                "서울 외 지역은 상권 추정매출 미제공 — 입지 점수·경쟁 밀도 기준으로 판정합니다."
            ],
        )

    dong = _normalize_dong_name(adm_name)

    # 1) Open API
    if settings.data_seoul_go_api_key.strip():
        try:
            quarterly_won, api_stores, source = await _fetch_sales_from_api(
                service_code, adm_name=adm_name, dong_name=dong
            )
            api_warnings = list(warnings)
            if api_stores:
                stores = api_stores
            else:
                stores = fallback_store_count or 1
                api_warnings.append("점포 수 미확인 — 반경 내 동일업종 수로 대체")
            monthly_total_man = (quarterly_won / 3) / MAN_WON
            per_store = monthly_total_man / stores if stores > 0 else monthly_total_man
            return SalesBenchmark(
                monthly_revenue_man=round(per_store, 1),
                per_store_monthly_man=round(per_store, 1),
                total_quarterly_sales_won=quarterly_won,
                store_count=stores,
                service_code=service_code,
                service_name=service_name,
                scope=adm_name or "서울",
                data_source=source,
                warnings=api_warnings,
            )
        except SeoulCommercialError as exc:
            warnings.append(f"서울 Open API: {exc}")

    # 2) CSV snapshot
    snap = _match_snapshot_row(adm_name, service_code)
    if snap:
        result = _benchmark_from_snapshot(
            snap,
            fallback_store_count=fallback_store_count,
            service_code=service_code,
            service_name=service_name,
        )
        result.warnings = warnings + result.warnings
        return result

    warnings.append("서울 추정매출 벤치마크 없음 — 손익분기·입지 점수만으로 판정")
    return SalesBenchmark(
        monthly_revenue_man=None,
        per_store_monthly_man=None,
        total_quarterly_sales_won=None,
        store_count=fallback_store_count,
        service_code=service_code,
        service_name=service_name,
        scope=adm_name or "서울",
        data_source="미제공",
        warnings=warnings,
    )
