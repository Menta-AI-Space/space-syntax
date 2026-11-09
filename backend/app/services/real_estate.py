"""Real estate transaction data from data.go.kr (commercial + apartment + officetel).

Commercial service: RTMSDataSvcNrgTrade (상업업무용 부동산 매매 실거래가)
Apartment service: RTMSDataSvcAptTrade (아파트 매매 신고정보)
Officetel service: RTMSDataSvcOffiTrade (오피스텔 매매 실거래가)

All use REST GET with query params and return XML.
Update frequency: daily (일 1회)
"""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, TypeVar
from urllib.parse import unquote

import httpx

from app.config import settings

# --- Commercial (NrgTrade) ---
COMMERCIAL_SERVICE_BASE = "http://apis.data.go.kr/1613000/RTMSDataSvcNrgTrade"
COMMERCIAL_OPERATION = "getRTMSDataSvcNrgTrade"
COMMERCIAL_API_HTTP = f"{COMMERCIAL_SERVICE_BASE}/{COMMERCIAL_OPERATION}"
COMMERCIAL_API_HTTPS = (
    f"https://apis.data.go.kr/1613000/RTMSDataSvcNrgTrade/{COMMERCIAL_OPERATION}"
)

# --- Apartment (AptTrade) ---
APARTMENT_SERVICE_BASE = "http://apis.data.go.kr/1613000/RTMSDataSvcAptTrade"
APARTMENT_OPERATION = "getRTMSDataSvcAptTrade"
APARTMENT_API_HTTP = f"{APARTMENT_SERVICE_BASE}/{APARTMENT_OPERATION}"
APARTMENT_API_HTTPS = (
    f"https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/{APARTMENT_OPERATION}"
)

# --- Officetel (OffiTrade) ---
OFFICETEL_SERVICE_BASE = "http://apis.data.go.kr/1613000/RTMSDataSvcOffiTrade"
OFFICETEL_OPERATION = "getRTMSDataSvcOffiTrade"
OFFICETEL_API_HTTP = f"{OFFICETEL_SERVICE_BASE}/{OFFICETEL_OPERATION}"
OFFICETEL_API_HTTPS = (
    f"https://apis.data.go.kr/1613000/RTMSDataSvcOffiTrade/{OFFICETEL_OPERATION}"
)

# --- Officetel rent (OffiRent) — 전월세 실거래 ---
OFFICETEL_RENT_OPERATION = "getRTMSDataSvcOffiRent"
OFFICETEL_RENT_API_HTTP = (
    f"http://apis.data.go.kr/1613000/RTMSDataSvcOffiRent/{OFFICETEL_RENT_OPERATION}"
)
OFFICETEL_RENT_API_HTTPS = (
    f"https://apis.data.go.kr/1613000/RTMSDataSvcOffiRent/{OFFICETEL_RENT_OPERATION}"
)

# Backward-compatible aliases (commercial)
SERVICE_BASE = COMMERCIAL_SERVICE_BASE
SERVICE_OPERATION = COMMERCIAL_OPERATION
API_BASE_HTTP = COMMERCIAL_API_HTTP
API_BASE_HTTPS = COMMERCIAL_API_HTTPS
API_BASE = COMMERCIAL_API_HTTPS

DEFAULT_LAWD_CD = "11410"  # 서대문구
REQUEST_DELAY_SEC = 0.05  # ~20 req/s, under 30 TPS limit

# Sinchon commercial core — real trades use these umdNm values, not "신촌동" alone.
SINCHON_COMMERCIAL_UMD = frozenset({"대현동", "창천동", "신촌동"})


def is_sinchon_commercial_umd(umd_nm: str) -> bool:
    return umd_nm in SINCHON_COMMERCIAL_UMD


def _jibun_for_geocode(jibun: str) -> str | None:
    """Return a Kakao-friendly jibun suffix, or None when masked/empty."""
    j = jibun.strip()
    if not j or "*" in j:
        return None
    if not j.endswith("번지"):
        j = f"{j}번지"
    return j


def _format_seoul_address(sgg_nm: str, umd_nm: str, detail: str | None = None) -> str:
    parts = ["서울", sgg_nm, umd_nm]
    if detail:
        parts.append(detail)
    return " ".join(parts).strip()


def _sinchon_commercial_fallback(sgg_nm: str, umd_nm: str) -> str | None:
    if umd_nm in SINCHON_COMMERCIAL_UMD:
        return _format_seoul_address(sgg_nm, "신촌로")
    return None

PropertyType = Literal["commercial", "apartment", "officetel"]

T = TypeVar("T")


class RealEstateError(Exception):
    pass


class RealEstateConfigError(RealEstateError):
    pass


@dataclass
class CommercialTrade:
    deal_amount: int  # 만원 (10,000 KRW units)
    building_ar: float
    plottage_ar: float
    umd_nm: str
    jibun: str
    sgg_nm: str
    deal_year: str
    deal_month: str
    deal_day: str
    building_use: str
    floor: str
    build_year: str
    building_type: str = ""  # 일반(통건물) / 집합(구분소유 호실)
    land_use: str = ""  # 용도지역 (예: 일반상업, 제2종일반주거)
    share_dealing_type: str = ""  # 지분거래 구분 (지분거래 시 "지분")
    cdeal_type: str = ""  # 해제여부 ("O" = 해제된 계약)
    cdeal_day: str = ""  # 해제사유발생일
    dealing_gbn: str = ""  # 중개거래 / 직거래
    price_per_sqm: float | None = None

    @property
    def property_type(self) -> PropertyType:
        return "commercial"

    @property
    def is_cancelled(self) -> bool:
        return self.cdeal_type.strip().upper() == "O"

    @property
    def is_share_deal(self) -> bool:
        return "지분" in self.share_dealing_type

    @property
    def is_strata(self) -> bool:
        """집합건물(구분소유 호실) 거래 여부. False면 일반(통건물) 또는 미상."""
        return self.building_type.strip() == "집합"

    @property
    def deal_date(self) -> str:
        return f"{self.deal_year}-{self.deal_month.zfill(2)}-{self.deal_day.zfill(2)}"

    def geocode_query(self) -> str:
        """Korean address string for Kakao geocoding."""
        jibun = _jibun_for_geocode(self.jibun)
        return _format_seoul_address(self.sgg_nm, self.umd_nm, jibun)

    def geocode_fallback_queries(self) -> list[str]:
        seen: set[str] = set()
        queries: list[str] = []
        for q in (
            self.geocode_query(),
            _format_seoul_address(self.sgg_nm, self.umd_nm),
            _sinchon_commercial_fallback(self.sgg_nm, self.umd_nm),
        ):
            if q and q not in seen:
                seen.add(q)
                queries.append(q)
        return queries


@dataclass
class ApartmentTrade:
    deal_amount: int  # 만원 (10,000 KRW units)
    exclu_use_ar: float  # exclusive area m² — use for price/sqm
    apt_nm: str
    umd_nm: str
    jibun: str
    sgg_nm: str
    sgg_cd: str
    deal_year: str
    deal_month: str
    deal_day: str
    floor: str
    build_year: str
    dealing_gbn: str
    cdeal_type: str = ""  # 해제여부 ("O" = 해제된 계약)
    cdeal_day: str = ""  # 해제사유발생일
    price_per_sqm: float | None = None

    @property
    def property_type(self) -> PropertyType:
        return "apartment"

    @property
    def is_cancelled(self) -> bool:
        return self.cdeal_type.strip().upper() == "O"

    @property
    def deal_date(self) -> str:
        return f"{self.deal_year}-{self.deal_month.zfill(2)}-{self.deal_day.zfill(2)}"

    def geocode_query(self) -> str:
        """Try apartment name first; fall back to jibun address."""
        apt = self.apt_nm.strip()
        if apt:
            return _format_seoul_address(self.sgg_nm, self.umd_nm, apt)
        jibun = _jibun_for_geocode(self.jibun)
        return _format_seoul_address(self.sgg_nm, self.umd_nm, jibun)

    def geocode_fallback_queries(self) -> list[str]:
        seen: set[str] = set()
        queries: list[str] = []
        for q in (
            self.geocode_query(),
            _format_seoul_address(self.sgg_nm, self.umd_nm),
            _sinchon_commercial_fallback(self.sgg_nm, self.umd_nm),
        ):
            if q and q not in seen:
                seen.add(q)
                queries.append(q)
        return queries


@dataclass
class OfficetelTrade:
    deal_amount: int  # 만원 (10,000 KRW units)
    exclu_use_ar: float  # exclusive area m² — use for price/sqm
    offi_nm: str
    umd_nm: str
    jibun: str
    sgg_nm: str
    sgg_cd: str
    deal_year: str
    deal_month: str
    deal_day: str
    floor: str
    build_year: str
    dealing_gbn: str
    cdeal_type: str = ""  # 해제여부 ("O" = 해제된 계약)
    cdeal_day: str = ""  # 해제사유발생일
    price_per_sqm: float | None = None

    @property
    def property_type(self) -> PropertyType:
        return "officetel"

    @property
    def is_cancelled(self) -> bool:
        return self.cdeal_type.strip().upper() == "O"

    @property
    def deal_date(self) -> str:
        return f"{self.deal_year}-{self.deal_month.zfill(2)}-{self.deal_day.zfill(2)}"

    def geocode_query(self) -> str:
        """Try officetel name first; fall back to jibun address."""
        offi = self.offi_nm.strip()
        if offi:
            return _format_seoul_address(self.sgg_nm, self.umd_nm, offi)
        jibun = _jibun_for_geocode(self.jibun)
        return _format_seoul_address(self.sgg_nm, self.umd_nm, jibun)

    def geocode_fallback_queries(self) -> list[str]:
        seen: set[str] = set()
        queries: list[str] = []
        for q in (
            self.geocode_query(),
            _format_seoul_address(self.sgg_nm, self.umd_nm),
            _sinchon_commercial_fallback(self.sgg_nm, self.umd_nm),
        ):
            if q and q not in seen:
                seen.add(q)
                queries.append(q)
        return queries


@dataclass
class OfficetelRent:
    """오피스텔 전월세 실거래 (OffiRent). deposit·monthly_rent 단위: 만원.

    monthly_rent == 0 이면 전세, > 0 이면 월세.
    """

    deposit: int  # 보증금 (만원)
    monthly_rent: int  # 월세 (만원); 0 = 전세
    exclu_use_ar: float  # 전용면적 m²
    offi_nm: str
    umd_nm: str
    jibun: str
    sgg_nm: str
    sgg_cd: str
    deal_year: str
    deal_month: str
    deal_day: str
    floor: str
    build_year: str
    contract_type: str = ""  # 계약구분: 신규 / 갱신
    contract_term: str = ""  # 계약기간 (예: 202605~202805)
    use_rr_right: str = ""  # 계약갱신요구권 사용 여부 ("사용")
    pre_deposit: int = 0  # 종전계약 보증금 (갱신 시)
    pre_monthly_rent: int = 0  # 종전계약 월세 (갱신 시)
    monthly_rent_per_sqm: float | None = None  # 월세 KRW/m² (월세 계약만)

    @property
    def property_type(self) -> str:
        return "officetel_rent"

    @property
    def is_jeonse(self) -> bool:
        return self.monthly_rent == 0

    @property
    def is_renewal(self) -> bool:
        return "갱신" in self.contract_type

    @property
    def deal_date(self) -> str:
        return f"{self.deal_year}-{self.deal_month.zfill(2)}-{self.deal_day.zfill(2)}"

    def geocode_query(self) -> str:
        offi = self.offi_nm.strip()
        if offi:
            return _format_seoul_address(self.sgg_nm, self.umd_nm, offi)
        jibun = _jibun_for_geocode(self.jibun)
        return _format_seoul_address(self.sgg_nm, self.umd_nm, jibun)

    def geocode_fallback_queries(self) -> list[str]:
        seen: set[str] = set()
        queries: list[str] = []
        for q in (
            self.geocode_query(),
            _format_seoul_address(self.sgg_nm, self.umd_nm),
            _sinchon_commercial_fallback(self.sgg_nm, self.umd_nm),
        ):
            if q and q not in seen:
                seen.add(q)
                queries.append(q)
        return queries


TradeRecord = CommercialTrade | ApartmentTrade | OfficetelTrade


@dataclass
class TradeFetchResult:
    items: list[TradeRecord] = field(default_factory=list)
    total_count: int = 0
    page_no: int = 1
    num_of_rows: int = 100


@dataclass
class CommercialFetchResult:
    """Typed result for commercial-only callers (tests, backward compat)."""

    items: list[CommercialTrade] = field(default_factory=list)
    total_count: int = 0
    page_no: int = 1
    num_of_rows: int = 100


@dataclass
class ApartmentFetchResult:
    items: list[ApartmentTrade] = field(default_factory=list)
    total_count: int = 0
    page_no: int = 1
    num_of_rows: int = 100


@dataclass
class OfficetelFetchResult:
    items: list[OfficetelTrade] = field(default_factory=list)
    total_count: int = 0
    page_no: int = 1
    num_of_rows: int = 100


@dataclass
class OfficetelRentFetchResult:
    items: list[OfficetelRent] = field(default_factory=list)
    total_count: int = 0
    page_no: int = 1
    num_of_rows: int = 100


def _text(elem: ET.Element | None, tag: str) -> str:
    if elem is None:
        return ""
    child = elem.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _parse_float(value: str) -> float:
    if not value:
        return 0.0
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return 0.0


def _parse_int(value: str) -> int:
    if not value:
        return 0
    try:
        return int(value.replace(",", ""))
    except ValueError:
        return 0


def compute_price_per_sqm(deal_amount: int, area_sqm: float) -> float | None:
    """dealAmount is in 만원; return KRW per m²."""
    if area_sqm <= 0 or deal_amount <= 0:
        return None
    return round(deal_amount * 10_000 / area_sqm, 2)


def _parse_xml_envelope(xml_text: str) -> tuple[ET.Element | None, int, int, int]:
    """Validate header and return body element with pagination fields."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise RealEstateError(f"Invalid XML response: {e}") from e

    header = root.find("header")
    result_code = _text(header, "resultCode")
    result_msg = _text(header, "resultMsg")

    if result_code and result_code not in ("00", "000"):
        raise RealEstateError(f"API error {result_code}: {result_msg or 'unknown'}")

    body = root.find("body")
    if body is None:
        return None, 0, 1, 100

    total_count = _parse_int(_text(body, "totalCount"))
    page_no = _parse_int(_text(body, "pageNo")) or 1
    num_of_rows = _parse_int(_text(body, "numOfRows")) or 100
    return body, total_count, page_no, num_of_rows


def _parse_commercial_item(item_elem: ET.Element) -> CommercialTrade:
    deal_amount = _parse_int(_text(item_elem, "dealAmount"))
    building_ar = _parse_float(_text(item_elem, "buildingAr"))
    return CommercialTrade(
        deal_amount=deal_amount,
        building_ar=building_ar,
        plottage_ar=_parse_float(_text(item_elem, "plottageAr")),
        umd_nm=_text(item_elem, "umdNm"),
        jibun=_text(item_elem, "jibun"),
        sgg_nm=_text(item_elem, "sggNm"),
        deal_year=_text(item_elem, "dealYear"),
        deal_month=_text(item_elem, "dealMonth"),
        deal_day=_text(item_elem, "dealDay"),
        building_use=_text(item_elem, "buildingUse"),
        floor=_text(item_elem, "floor"),
        build_year=_text(item_elem, "buildYear"),
        building_type=_text(item_elem, "buildingType"),
        land_use=_text(item_elem, "landUse"),
        share_dealing_type=_text(item_elem, "shareDealingType"),
        cdeal_type=_text(item_elem, "cdealType"),
        cdeal_day=_text(item_elem, "cdealDay"),
        dealing_gbn=_text(item_elem, "dealingGbn"),
        price_per_sqm=compute_price_per_sqm(deal_amount, building_ar),
    )


def _parse_apartment_item(item_elem: ET.Element) -> ApartmentTrade:
    deal_amount = _parse_int(_text(item_elem, "dealAmount"))
    exclu_use_ar = _parse_float(_text(item_elem, "excluUseAr"))
    return ApartmentTrade(
        deal_amount=deal_amount,
        exclu_use_ar=exclu_use_ar,
        apt_nm=_text(item_elem, "aptNm"),
        umd_nm=_text(item_elem, "umdNm"),
        jibun=_text(item_elem, "jibun"),
        sgg_nm=_text(item_elem, "sggNm"),
        sgg_cd=_text(item_elem, "sggCd"),
        deal_year=_text(item_elem, "dealYear"),
        deal_month=_text(item_elem, "dealMonth"),
        deal_day=_text(item_elem, "dealDay"),
        floor=_text(item_elem, "floor"),
        build_year=_text(item_elem, "buildYear"),
        dealing_gbn=_text(item_elem, "dealingGbn"),
        cdeal_type=_text(item_elem, "cdealType"),
        cdeal_day=_text(item_elem, "cdealDay"),
        price_per_sqm=compute_price_per_sqm(deal_amount, exclu_use_ar),
    )


def _parse_officetel_item(item_elem: ET.Element) -> OfficetelTrade:
    deal_amount = _parse_int(_text(item_elem, "dealAmount"))
    exclu_use_ar = _parse_float(_text(item_elem, "excluUseAr"))
    return OfficetelTrade(
        deal_amount=deal_amount,
        exclu_use_ar=exclu_use_ar,
        offi_nm=_text(item_elem, "offiNm"),
        umd_nm=_text(item_elem, "umdNm"),
        jibun=_text(item_elem, "jibun"),
        sgg_nm=_text(item_elem, "sggNm"),
        sgg_cd=_text(item_elem, "sggCd"),
        deal_year=_text(item_elem, "dealYear"),
        deal_month=_text(item_elem, "dealMonth"),
        deal_day=_text(item_elem, "dealDay"),
        floor=_text(item_elem, "floor"),
        build_year=_text(item_elem, "buildYear"),
        dealing_gbn=_text(item_elem, "dealingGbn"),
        cdeal_type=_text(item_elem, "cdealType"),
        cdeal_day=_text(item_elem, "cdealDay"),
        price_per_sqm=compute_price_per_sqm(deal_amount, exclu_use_ar),
    )


def compute_monthly_rent_per_sqm(monthly_rent: int, area_sqm: float) -> float | None:
    """monthlyRent(만원) → 월세 KRW/m². 전세(0)나 면적 결측이면 None."""
    if area_sqm <= 0 or monthly_rent <= 0:
        return None
    return round(monthly_rent * 10_000 / area_sqm, 2)


def _parse_officetel_rent_item(item_elem: ET.Element) -> OfficetelRent:
    monthly_rent = _parse_int(_text(item_elem, "monthlyRent"))
    exclu_use_ar = _parse_float(_text(item_elem, "excluUseAr"))
    return OfficetelRent(
        deposit=_parse_int(_text(item_elem, "deposit")),
        monthly_rent=monthly_rent,
        exclu_use_ar=exclu_use_ar,
        offi_nm=_text(item_elem, "offiNm"),
        umd_nm=_text(item_elem, "umdNm"),
        jibun=_text(item_elem, "jibun"),
        sgg_nm=_text(item_elem, "sggNm"),
        sgg_cd=_text(item_elem, "sggCd"),
        deal_year=_text(item_elem, "dealYear"),
        deal_month=_text(item_elem, "dealMonth"),
        deal_day=_text(item_elem, "dealDay"),
        floor=_text(item_elem, "floor"),
        build_year=_text(item_elem, "buildYear"),
        contract_type=_text(item_elem, "contractType"),
        contract_term=_text(item_elem, "contractTerm"),
        use_rr_right=_text(item_elem, "useRRRight"),
        pre_deposit=_parse_int(_text(item_elem, "preDeposit")),
        pre_monthly_rent=_parse_int(_text(item_elem, "preMonthlyRent")),
        monthly_rent_per_sqm=compute_monthly_rent_per_sqm(monthly_rent, exclu_use_ar),
    )


def parse_trades_xml(xml_text: str) -> CommercialFetchResult:
    """Parse commercial (NrgTrade) XML response."""
    body, total_count, page_no, num_of_rows = _parse_xml_envelope(xml_text)

    items: list[CommercialTrade] = []
    if body is not None:
        items_elem = body.find("items")
        if items_elem is not None:
            for item_elem in items_elem.findall("item"):
                items.append(_parse_commercial_item(item_elem))

    return CommercialFetchResult(
        items=items,
        total_count=total_count,
        page_no=page_no,
        num_of_rows=num_of_rows,
    )


def parse_apartment_trades_xml(xml_text: str) -> ApartmentFetchResult:
    """Parse apartment (AptTrade) XML response."""
    body, total_count, page_no, num_of_rows = _parse_xml_envelope(xml_text)

    items: list[ApartmentTrade] = []
    if body is not None:
        items_elem = body.find("items")
        if items_elem is not None:
            for item_elem in items_elem.findall("item"):
                items.append(_parse_apartment_item(item_elem))

    return ApartmentFetchResult(
        items=items,
        total_count=total_count,
        page_no=page_no,
        num_of_rows=num_of_rows,
    )


def parse_officetel_trades_xml(xml_text: str) -> OfficetelFetchResult:
    """Parse officetel (OffiTrade) XML response."""
    body, total_count, page_no, num_of_rows = _parse_xml_envelope(xml_text)

    items: list[OfficetelTrade] = []
    if body is not None:
        items_elem = body.find("items")
        if items_elem is not None:
            for item_elem in items_elem.findall("item"):
                items.append(_parse_officetel_item(item_elem))

    return OfficetelFetchResult(
        items=items,
        total_count=total_count,
        page_no=page_no,
        num_of_rows=num_of_rows,
    )


def parse_officetel_rents_xml(xml_text: str) -> OfficetelRentFetchResult:
    """Parse officetel rent (OffiRent) XML response."""
    body, total_count, page_no, num_of_rows = _parse_xml_envelope(xml_text)

    items: list[OfficetelRent] = []
    if body is not None:
        items_elem = body.find("items")
        if items_elem is not None:
            for item_elem in items_elem.findall("item"):
                items.append(_parse_officetel_rent_item(item_elem))

    return OfficetelRentFetchResult(
        items=items,
        total_count=total_count,
        page_no=page_no,
        num_of_rows=num_of_rows,
    )


def _service_key() -> str:
    key = settings.data_go_api_key.strip()
    if not key:
        raise RealEstateConfigError(
            "DATA_GO_API_KEY is not configured. Add it to .env (see .env.example)."
        )
    return unquote(key)


async def _fetch_trades_page(
    api_url_https: str,
    api_url_http: str,
    parse_fn,
    *,
    lawd_cd: str,
    deal_ymd: str,
    page_no: int = 1,
    num_of_rows: int = 100,
    service_label: str,
) -> (
    CommercialFetchResult
    | ApartmentFetchResult
    | OfficetelFetchResult
    | OfficetelRentFetchResult
):
    """Shared REST GET fetch for data.go.kr trade APIs."""
    params = {
        "serviceKey": _service_key(),
        "LAWD_CD": lawd_cd,
        "DEAL_YMD": deal_ymd,
        "pageNo": page_no,
        "numOfRows": num_of_rows,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(api_url_https, params=params)
            if response.status_code >= 500:
                response = await client.get(api_url_http, params=params)
    except httpx.HTTPError as exc:
        detail = str(exc).strip() or type(exc).__name__
        raise RealEstateError(
            f"RTMS network request failed for {service_label}: {detail}"
        ) from exc

    if response.status_code == 403:
        raise RealEstateError(
            f"data.go.kr returned 403 Forbidden. Verify DATA_GO_API_KEY is valid and "
            f"your portal account is subscribed to {service_label}."
        )
    if response.status_code != 200:
        raise RealEstateError(f"HTTP {response.status_code}: {response.text[:200]}")

    content_type = response.headers.get("content-type", "")
    if "xml" not in content_type and response.text.lstrip().startswith("{"):
        raise RealEstateError("Expected XML response from data.go.kr REST API")

    return parse_fn(response.text)


async def fetch_commercial_trades(
    lawd_cd: str,
    deal_ymd: str,
    page_no: int = 1,
    num_of_rows: int = 100,
) -> CommercialFetchResult:
    """Fetch one page of commercial trades via REST GET."""
    return await _fetch_trades_page(
        COMMERCIAL_API_HTTPS,
        COMMERCIAL_API_HTTP,
        parse_trades_xml,
        lawd_cd=lawd_cd,
        deal_ymd=deal_ymd,
        page_no=page_no,
        num_of_rows=num_of_rows,
        service_label="RTMSDataSvcNrgTrade (상업업무용 부동산 매매 실거래가)",
    )


async def fetch_apartment_trades(
    lawd_cd: str,
    deal_ymd: str,
    page_no: int = 1,
    num_of_rows: int = 100,
) -> ApartmentFetchResult:
    """Fetch one page of apartment trades via REST GET."""
    return await _fetch_trades_page(
        APARTMENT_API_HTTPS,
        APARTMENT_API_HTTP,
        parse_apartment_trades_xml,
        lawd_cd=lawd_cd,
        deal_ymd=deal_ymd,
        page_no=page_no,
        num_of_rows=num_of_rows,
        service_label="RTMSDataSvcAptTrade (아파트 매매 신고정보)",
    )


async def fetch_officetel_trades(
    lawd_cd: str,
    deal_ymd: str,
    page_no: int = 1,
    num_of_rows: int = 100,
) -> OfficetelFetchResult:
    """Fetch one page of officetel trades via REST GET."""
    return await _fetch_trades_page(
        OFFICETEL_API_HTTPS,
        OFFICETEL_API_HTTP,
        parse_officetel_trades_xml,
        lawd_cd=lawd_cd,
        deal_ymd=deal_ymd,
        page_no=page_no,
        num_of_rows=num_of_rows,
        service_label="RTMSDataSvcOffiTrade (오피스텔 매매 실거래가)",
    )


async def fetch_officetel_rents(
    lawd_cd: str,
    deal_ymd: str,
    page_no: int = 1,
    num_of_rows: int = 100,
) -> OfficetelRentFetchResult:
    """Fetch one page of officetel rent (전월세) records via REST GET."""
    return await _fetch_trades_page(
        OFFICETEL_RENT_API_HTTPS,
        OFFICETEL_RENT_API_HTTP,
        parse_officetel_rents_xml,
        lawd_cd=lawd_cd,
        deal_ymd=deal_ymd,
        page_no=page_no,
        num_of_rows=num_of_rows,
        service_label="RTMSDataSvcOffiRent (오피스텔 전월세 실거래가)",
    )


async def _fetch_all_pages_for_month(
    fetch_page_fn,
    lawd_cd: str,
    deal_ymd: str,
    num_of_rows: int = 100,
) -> list:
    all_items: list = []
    page = 1

    while True:
        result = await fetch_page_fn(
            lawd_cd, deal_ymd, page_no=page, num_of_rows=num_of_rows
        )
        all_items.extend(result.items)

        if not result.items or page * num_of_rows >= result.total_count:
            break

        page += 1
        await asyncio.sleep(REQUEST_DELAY_SEC)

    return all_items


async def fetch_trades_for_month(
    lawd_cd: str,
    deal_ymd: str,
    num_of_rows: int = 100,
) -> list[CommercialTrade]:
    """Fetch all commercial pages for a single YYYYMM."""
    return await _fetch_all_pages_for_month(
        fetch_commercial_trades, lawd_cd, deal_ymd, num_of_rows
    )


async def fetch_apartment_trades_for_month(
    lawd_cd: str,
    deal_ymd: str,
    num_of_rows: int = 100,
) -> list[ApartmentTrade]:
    """Fetch all apartment pages for a single YYYYMM."""
    return await _fetch_all_pages_for_month(
        fetch_apartment_trades, lawd_cd, deal_ymd, num_of_rows
    )


async def fetch_officetel_trades_for_month(
    lawd_cd: str,
    deal_ymd: str,
    num_of_rows: int = 100,
) -> list[OfficetelTrade]:
    """Fetch all officetel pages for a single YYYYMM."""
    return await _fetch_all_pages_for_month(
        fetch_officetel_trades, lawd_cd, deal_ymd, num_of_rows
    )


async def fetch_officetel_rents_for_month(
    lawd_cd: str,
    deal_ymd: str,
    num_of_rows: int = 100,
) -> list[OfficetelRent]:
    """Fetch all officetel rent (전월세) pages for a single YYYYMM."""
    return await _fetch_all_pages_for_month(
        fetch_officetel_rents, lawd_cd, deal_ymd, num_of_rows
    )


def _recent_deal_months(months_back: int) -> list[str]:
    """Return YYYYMM strings for the last N months (inclusive of current month)."""
    today = date.today()
    y, m = today.year, today.month
    months: list[str] = []
    for _ in range(months_back):
        months.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return months


async def fetch_recent_trades(
    lawd_cd: str = DEFAULT_LAWD_CD,
    months_back: int = 12,
    num_of_rows: int = 100,
) -> list[CommercialTrade]:
    """Fetch commercial trades for the last N months (all months in parallel)."""
    if months_back < 1:
        raise RealEstateError("months_back must be at least 1")

    by_month = await asyncio.gather(
        *(
            fetch_trades_for_month(lawd_cd, deal_ymd, num_of_rows)
            for deal_ymd in _recent_deal_months(months_back)
        )
    )
    return [trade for month_trades in by_month for trade in month_trades]


async def fetch_recent_apartment_trades(
    lawd_cd: str = DEFAULT_LAWD_CD,
    months_back: int = 12,
    num_of_rows: int = 100,
) -> list[ApartmentTrade]:
    """Fetch apartment trades for the last N months (all months in parallel)."""
    if months_back < 1:
        raise RealEstateError("months_back must be at least 1")

    by_month = await asyncio.gather(
        *(
            fetch_apartment_trades_for_month(lawd_cd, deal_ymd, num_of_rows)
            for deal_ymd in _recent_deal_months(months_back)
        )
    )
    return [trade for month_trades in by_month for trade in month_trades]


async def fetch_recent_officetel_trades(
    lawd_cd: str = DEFAULT_LAWD_CD,
    months_back: int = 12,
    num_of_rows: int = 100,
) -> list[OfficetelTrade]:
    """Fetch officetel trades for the last N months (all months in parallel)."""
    if months_back < 1:
        raise RealEstateError("months_back must be at least 1")

    by_month = await asyncio.gather(
        *(
            fetch_officetel_trades_for_month(lawd_cd, deal_ymd, num_of_rows)
            for deal_ymd in _recent_deal_months(months_back)
        )
    )
    return [trade for month_trades in by_month for trade in month_trades]


async def fetch_trades_by_property_type(
    property_type: PropertyType | Literal["all"],
    lawd_cd: str,
    *,
    deal_ymd: str | None = None,
    months_back: int = 12,
    num_of_rows: int = 100,
) -> list[TradeRecord]:
    """Fetch trades for one or all property types."""

    async def _fetch_one(pt: PropertyType) -> list[TradeRecord]:
        if deal_ymd:
            if pt == "commercial":
                return await fetch_trades_for_month(lawd_cd, deal_ymd, num_of_rows)
            if pt == "apartment":
                return await fetch_apartment_trades_for_month(
                    lawd_cd, deal_ymd, num_of_rows
                )
            return await fetch_officetel_trades_for_month(
                lawd_cd, deal_ymd, num_of_rows
            )

        if pt == "commercial":
            return await fetch_recent_trades(
                lawd_cd=lawd_cd, months_back=months_back, num_of_rows=num_of_rows
            )
        if pt == "apartment":
            return await fetch_recent_apartment_trades(
                lawd_cd=lawd_cd, months_back=months_back, num_of_rows=num_of_rows
            )
        return await fetch_recent_officetel_trades(
            lawd_cd=lawd_cd, months_back=months_back, num_of_rows=num_of_rows
        )

    if property_type == "all":
        commercial, apartment, officetel = await asyncio.gather(
            _fetch_one("commercial"),
            _fetch_one("apartment"),
            _fetch_one("officetel"),
        )
        return list(commercial) + list(apartment) + list(officetel)

    return await _fetch_one(property_type)
