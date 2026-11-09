"""개별공시지가 API 확인용 프로브 — 적재 전에 "이 API가 쓸 만한가"만 본다. DB 불필요.

``docs/LAND_VALUE_MODEL_RESEARCH.md`` 1.3이 이 API를 **미확인 투성이**로 남겼다:
행 수, 필드 목록(PNU 유무), 좌표계, 시간범위("-"로 표기됨), 갱신주기 전부 모른다.
그런데 이 셋이 이후 계획을 전부 바꾼다:

- **PNU가 없으면** ``parcel_centroids``·``land_prices``에 붙일 키가 없어 필지 단위가
  성립하지 않는다 (실거래가 종속변수가 못 됐던 것과 같은 이유).
- **전 필지가 아니라 표준지만 주면** 서울 87만 중 2만이라 동×용도지역 계층이
  30을 못 채우고, 동 고정효과에 해당하는 통제가 무너진다.
- **과거 연도를 안 주면** 필지 고정효과 패널(연구 문서 2.7)이 불가능하고 횡단면만
  남는다.

그래서 적재 코드를 쓰기 전에 이걸 먼저 돌린다. 판정만 하고 아무것도 저장하지 않는다.

**엔드포인트를 하드코딩하지 않는다.** data.go.kr 15029071은 API 유형이 ``LINK``라
data.go.kr이 중계하지 않고 원 제공처(브이월드)로 넘긴다 — 즉 실제 URL과 인증키가
data.go.kr 것이 아닐 수 있다. 실제 URL·요청변수는 활용신청 후 데이터셋 페이지의
**"상세기능정보"**에 나오므로, 그걸 ``--url``로 넘긴다.

키는 ``.env``에서 읽는다 (``DATA_GO_API_KEY`` 또는 ``VWORLD_API_KEY``). 명령줄에
키를 적지 말 것 — 셸 기록에 남는다.

Run:
  # data.go.kr 계열 (serviceKey)
  cd backend && .venv/Scripts/python -m tools.probe_land_price_api \\
      --url "http://apis.data.go.kr/1611000/nsdi/IndvdLandPriceService/attr/getIndvdLandPriceAttr" \\
      --key-source data_go --key-param serviceKey --pnu 1111010100100010000

  # V-World NED 계열 (key)
  cd backend && .venv/Scripts/python -m tools.probe_land_price_api \\
      --url "https://api.vworld.kr/ned/data/getIndvdLandPriceAttr" \\
      --key-source vworld --key-param key --pnu 1111010100100010000

  # 과거 연도를 주는지 확인
  ... --years 2026 2020 2010 2000
"""

from __future__ import annotations

import argparse
import json
import sys
from urllib.parse import unquote
from xml.etree import ElementTree as ET

import httpx

from app.config import settings

# 응답에서 이 이름들이 보이면 PNU로 인정한다. 출처마다 표기가 다르다.
PNU_FIELD_HINTS = ("pnu", "ldCode", "landCode", "토지대장번호", "고유번호")
PRICE_FIELD_HINTS = ("pblntfPclnd", "publicLandPrice", "공시지가", "개별공시지가")
YEAR_FIELD_HINTS = ("stdrYear", "baseYear", "기준연도", "년도")
TIMEOUT_S = 30.0


def resolve_key(source: str) -> str:
    """.env에서 키를 읽는다. unquote는 data.go.kr의 Encoding 키를 그대로 받기 위함."""
    raw = {
        "data_go": settings.data_go_api_key,
        "vworld": settings.vworld_api_key,
    }[source].strip()
    if not raw:
        var = "DATA_GO_API_KEY" if source == "data_go" else "VWORLD_API_KEY"
        raise SystemExit(
            f"{var}가 .env에 없다. 저장소 루트에서 `copy .env.example .env` 후 채울 것."
        )
    return unquote(raw)


def parse_any(text: str) -> tuple[str, object]:
    """JSON이든 XML이든 파이썬 자료구조로. (형식, 값)"""
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return "json", json.loads(text)
    return "xml", ET.fromstring(text)


def walk_keys(node: object, out: set[str], depth: int = 0) -> None:
    """중첩 구조에서 나타나는 모든 필드명을 모은다 (구조를 모르니 전수로 본다)."""
    if depth > 8:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            out.add(k)
            walk_keys(v, out, depth + 1)
    elif isinstance(node, list):
        for v in node[:5]:
            walk_keys(v, out, depth + 1)
    elif isinstance(node, ET.Element):
        for child in node:
            out.add(child.tag)
            walk_keys(child, out, depth + 1)


def find_hint(fields: set[str], hints: tuple[str, ...]) -> str | None:
    lowered = {f.lower(): f for f in fields}
    for hint in hints:
        for low, original in lowered.items():
            if hint.lower() in low:
                return original
    return None


def probe_once(url: str, params: dict, label: str) -> tuple[bool, set[str], str]:
    """한 번 호출하고 (성공 여부, 필드 집합, 요약 한 줄)."""
    try:
        resp = httpx.get(url, params=params, timeout=TIMEOUT_S)
    except httpx.HTTPError as exc:
        return False, set(), f"{label}: 요청 실패 — {type(exc).__name__}: {exc}"

    if resp.status_code != 200:
        return False, set(), f"{label}: HTTP {resp.status_code} — {resp.text[:200]}"
    if not resp.content:
        # V-World 다운로드가 인증 없이 호출되면 Content-Length 0으로 온다 —
        # 같은 증상이면 키가 안 먹은 것이다.
        return False, set(), f"{label}: 빈 응답 (인증 실패 의심)"

    try:
        kind, parsed = parse_any(resp.text)
    except Exception as exc:
        return False, set(), f"{label}: 파싱 실패 {type(exc).__name__} — {resp.text[:200]}"

    fields: set[str] = set()
    walk_keys(parsed, fields)

    # 흔한 실패: 키 미승인/오타면 200에 에러 본문이 온다
    lowered_body = resp.text[:600].lower()
    for marker in ("service key", "servicekey", "인증", "unauthor", "error", "등록되지"):
        if marker in lowered_body and len(fields) < 8:
            return False, fields, f"{label}: 인증/승인 오류로 보임 — {resp.text[:200]}"

    # totalCount=0은 "그 연도에 자료가 없다"이지 성공이 아니다. 이걸 성공으로 세면
    # 연도 하한을 잘못 읽는다(2008이 응답한 것처럼 보였다, 2026-08-21).
    if '"totalCount" : "0"' in resp.text or "<totalCount>0</totalCount>" in resp.text:
        return False, fields, f"{label}: 자료 없음 (totalCount=0)"

    return True, fields, f"{label}: {kind}, 필드 {len(fields)}개, {len(resp.content):,} bytes"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True,
                        help="활용신청 후 데이터셋 페이지 '상세기능정보'의 엔드포인트")
    parser.add_argument("--key-source", choices=("data_go", "vworld"), default="data_go")
    parser.add_argument("--key-param", default="serviceKey",
                        help="키를 실을 요청변수 이름 (data.go.kr=serviceKey, V-World=key)")
    parser.add_argument("--pnu", default=None, help="한 필지를 찍어볼 PNU 19자리")
    parser.add_argument("--years", nargs="*", default=["2026", "2020", "2010"],
                        help="과거 연도를 주는지 확인할 기준연도들")
    parser.add_argument("--extra", nargs="*", default=[],
                        help="추가 요청변수 key=value (상세기능정보에 맞춰)")
    args = parser.parse_args()

    key = resolve_key(args.key_source)
    base = {args.key_param: key, "format": "json", "numOfRows": "10", "pageNo": "1"}
    # V-World는 domain이 없으면 INCORRECT_KEY로 거부한다 — 키가 틀린 게 아니라
    # 발급 때 등록한 서비스 URL을 함께 보내야 한다. 서버측 호출이라 안 보내도 될
    # 줄 알았는데 아니었다(2026-08-21 실측).
    if args.key_source == "vworld" and settings.vworld_domain.strip():
        base.setdefault("domain", settings.vworld_domain.strip())
    for kv in args.extra:
        k, _, v = kv.partition("=")
        base[k] = v

    print(f"엔드포인트: {args.url}")
    print(f"키 출처: {args.key_source} (.env), 요청변수: {args.key_param}\n")

    all_fields: set[str] = set()
    ok_years: list[str] = []

    # 1) 연도별로 한 번씩 — 시간범위 "-" 표기가 실제로 무슨 뜻인지 확인한다
    for year in args.years:
        params = dict(base)
        params["stdrYear"] = year
        if args.pnu:
            params["pnu"] = args.pnu
        ok, fields, msg = probe_once(args.url, params, f"기준연도 {year}")
        print("  " + msg)
        if ok:
            ok_years.append(year)
            all_fields |= fields

    if not all_fields:
        print("\n판정: 응답을 얻지 못했다. URL·요청변수·승인 상태를 확인할 것.")
        return 1

    print(f"\n관측된 필드 {len(all_fields)}개:")
    for f in sorted(all_fields)[:60]:
        print(f"  {f}")

    # 2) 세 가지 결정적 질문에 답한다
    pnu_field = find_hint(all_fields, PNU_FIELD_HINTS)
    price_field = find_hint(all_fields, PRICE_FIELD_HINTS)
    year_field = find_hint(all_fields, YEAR_FIELD_HINTS)

    print("\n── 판정 ──")
    print(f"  PNU 필드      : {pnu_field or '❌ 없음 → 필지 단위 불가'}")
    print(f"  공시지가 필드 : {price_field or '❌ 없음'}")
    print(f"  기준연도 필드 : {year_field or '❌ 없음'}")
    print(f"  응답한 연도   : {', '.join(ok_years) if ok_years else '없음'}")
    if len(ok_years) > 1:
        print("  → 과거 연도를 준다. 필지 고정효과 패널이 가능하다.")
    elif ok_years:
        print("  → 한 연도만 응답했다. 횡단면만 가능할 수 있다 — 다른 연도로 더 확인할 것.")

    print(
        "\n※ 전 필지인지 표준지만인지는 이 프로브로 못 가린다. 표준지 여부 필드가"
        "\n  있는지 위 목록에서 확인하고, 없으면 한 동의 필지 수를 세어 지적도"
        "\n  필지 수와 비교할 것 (표준지는 필지의 2% 남짓이다)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
