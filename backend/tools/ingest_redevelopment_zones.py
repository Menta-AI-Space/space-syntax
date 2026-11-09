r"""정비구역 SHP(UQ181) → ``redevelopment_zones`` 적재.

원천: 서울 열린데이터광장 "서울시 의제처리구역 위치정보"(OA-20957), 레이어
``UPIS_C_UQ181``. 도시·주거환경정비법·도시재정비촉진특별법에 따른 구역 18종.

**재건축·재개발은 땅값에 큰 영향을 준다.** 그래서 이 적재의 핵심은 폴리곤이
아니라 **고시일**이다 — 시점이 없으면 과거 연도 행에 붙이는 순간 미래를 보게
되어 방법론 A·B(미래 예측·뜰 자리)에 쓸 수 없다. 그게 UQ151 도시계획시설(도로)이
막힌 이유였다.

⚠️ **고시일은 별도 컬럼이 아니다.** ``NTFC_SN``(고시번호)에 인코딩돼 있다::

    11000NTC201706088160
    └구┘  └─┘└──날짜──┘└번호┘

2026-08-24 실측(``202602`` 배포본, 3,209건): **79.8%(2,560건)** 에서 날짜를
뽑았고 **1990~2026년 37개 연도**에 걸쳐 있다. 즉 **최신 파일 하나로 과거
전체가 복원된다** — 과거 배포본을 모으거나 웹스크래이핑할 필요가 없다.

⚠️ **``SIGNGU_SE``를 자치구 판정에 쓰지 말 것.** 3,209건 중 2,131건(66%)이
``11000``(서울시 광역)이다. 자치구는 필지와의 **공간조인으로** 판정한다.
그래서 이 도구는 지역 필터를 두지 않고 **서울 전체를 넣는다** (3,209건 ·
폴리곤 6.6MB라 가볍고, 정비구역 효과를 재려면 한 구의 사례 수로는 부족하다).

⚠️ **좌표계는 5186이 아니라 EPSG:5174다.** ``.prj``이 이렇게 적혀 있다::

    Korean_1985_Modified_Korea_Central_Belt
    SPHEROID Bessel_1841 · False_Northing 500000 · Central_Meridian 127.0028902777778

"중부원점"이라는 말만 보고 5186으로 넣었더니 필지와 **Y가 정확히 100,000m
어긋나 공간조인이 0건**이 됐다(2026-08-24). 5186은 ``y_0=600000`` + GRS80이고,
5174가 ``y_0=500000`` + Bessel + ``lon_0=127.0028902777778``로 .prj과 일치한다.
필지 쪽과 맞추려면 여기서 5179로 옮긴다.

⚠️ 좌표계가 틀리면 **오류 없이 0건**이 된다. 적재 후 반드시 공간조인 건수를
확인할 것 — ``ST_Extent``로 필지와 범위를 비교하면 바로 보인다.

⚠️⚠️ **``zone_class``가 NULL인 행을 "정비구역"으로 쓰지 말 것.** 이게 이 표의
가장 큰 함정이다. 3,209건 중 1,052건이 ``SCLAS_CL``이 비어 있고, 그 평균 면적이
**272,180㎡**로 실제 정비구역(3천~8만㎡)의 3~90배다. 정체는 1940~90년대
**토지구획정리사업**이다(``영동2토지구획정리사업`` 1985년 1,300ha,
``시흥토지구획정리사업`` 1980년 563ha, ``영등포토지구획정리사업`` 1946년).
서울 도시화 시절 사업이라 구 하나를 통째로 덮는다.

실측(2026-08-24, 금천구): 필지 15,940건이 구역에 걸리는데 그중 **15,697건(98%)
이 이 NULL 구역**이다. 그대로 쓰면 금천구 거의 모든 필지가 1로 찍혀 **변별력이
0**이 된다. ``zone_class IS NOT NULL``로 걸러야 1,966건(12%)이 남고 그게 실제
재개발·재건축이다:

    UQ1211/1212  재정비촉진        UQ1221/1222  재개발(주택정비/도시정비형)
    UQ1231/1232  재건축            UQ1811~1814  가로주택·자율주택 등 소규모

Run:
  cd backend && .venv/Scripts/python -m tools.ingest_redevelopment_zones \
      "C:/Users/cmdys/Downloads/UQ181_의제처리구역_202602.zip"

  # 넣기 전에 확인만 (DB 불필요)
  cd backend && .venv/Scripts/python -m tools.ingest_redevelopment_zones \
      "C:/Users/cmdys/Downloads/UQ181_*.zip" --inspect
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import date

# Windows 콘솔 기본 코드페이지에서 한글 로그가 UnicodeEncodeError로 죽는다.
# 적재를 다 해 놓고 마지막 print에서 터지면 성공/실패를 구분할 수 없다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import psycopg2
import psycopg2.extras
from shapely.geometry import mapping, shape
from shapely.geometry.multipolygon import MultiPolygon

from app.config import settings
from tools.ingest_cadastral import _shp_readers_from_zip

# 원본 좌표계. .prj 실측(2026-08-24): Bessel_1841 · y_0=500000 ·
# lon_0=127.0028902777778 → **5174**. 5186(y_0=600000, GRS80)로 넣으면
# Y가 100,000m 밀려 공간조인이 조용히 0건이 된다.
DEFAULT_SRID = 5174

_UPSERT_SQL = """
INSERT INTO redevelopment_zones (
  zone_key, zone_name, zone_class, zone_class_l, notice_no, notice_date,
  consent_no, sgg_code, drawing_date, area_sqm, src, geom_5179, ingested_at
) VALUES %s
ON CONFLICT (zone_key) DO UPDATE SET
  zone_name    = EXCLUDED.zone_name,
  zone_class   = EXCLUDED.zone_class,
  zone_class_l = EXCLUDED.zone_class_l,
  notice_date  = EXCLUDED.notice_date,
  consent_no   = EXCLUDED.consent_no,
  sgg_code     = EXCLUDED.sgg_code,
  drawing_date = EXCLUDED.drawing_date,
  area_sqm     = EXCLUDED.area_sqm,
  src          = EXCLUDED.src,
  geom_5179    = EXCLUDED.geom_5179,
  ingested_at  = NOW()
"""
_VALUES_TEMPLATE = (
    "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
    " ST_Multi(ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), %s), 5179)), NOW())"
)

BATCH_SIZE = 500

# 고시번호에서 날짜를 뽑는다. 앞 5자리는 고시기관(자치구 또는 11000=서울시),
# 가운데 3글자는 문서종류(NTC=고시, AGZ=의제), 그 뒤 8자리가 YYYYMMDD.
_NOTICE_RE = re.compile(r"^(\d{5})(NTC|AGZ)(\d{8})")
# 하한을 광복 직후로 잡는다. 실측(2026-08-24)에서 1953년 "관철(중앙1지구)
# 토지구획정리사업", 1964년 "용두토지구획정리사업"이 나왔고 실제 서울 도시개발
# 역사와 맞다 — 파싱 오류가 아니다. 1990년 하한으로 자르면 514건을 버린다.
# 상한은 미래 날짜(오타)를 걸러내되 예정 고시는 남기도록 넉넉히 둔다.
_YEAR_MIN, _YEAR_MAX = 1945, 2100


def parse_notice_date(notice_no: str | None) -> date | None:
    """``NTFC_SN`` → 고시일. 형식이 다르거나 날짜가 비현실적이면 None.

    ⚠️ None을 "고시일 없음"으로 정직하게 남긴다. 추측해서 채우면 방법론 A·B가
    조용히 미래를 보게 된다.
    """
    m = _NOTICE_RE.match((notice_no or "").strip())
    if not m:
        return None
    raw = m.group(3)
    try:
        year, month, day = int(raw[:4]), int(raw[4:6]), int(raw[6:8])
        if not _YEAR_MIN <= year <= _YEAR_MAX:
            return None
        return date(year, month, day)
    except ValueError:
        return None


def parse_dbf_date(raw: str | None) -> date | None:
    """DBF의 D형 필드(``CREATE_DAT``) → date. 공백·0·형식오류는 None."""
    s = (raw or "").strip()
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def zone_key(notice_no: str | None, name: str | None, seq: int) -> str:
    """안정적인 PK. 원본에 PK가 없어 고시번호+도면명으로 만든다.

    같은 고시로 여러 도면이 오는 경우가 있어 이름까지 넣고, 그래도 겹치면
    파일 안 순번으로 가른다 — 겹친 행을 조용히 덮으면 구역이 사라진다.
    """
    base = f"{(notice_no or '').strip()}|{(name or '').strip()}|{seq}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:24]


def _as_multipolygon(geom) -> MultiPolygon | None:
    """폴리곤만 통과. 선·점은 구역 경계가 아니라 부속 요소다."""
    if geom.is_empty:
        return None
    if geom.geom_type == "Polygon":
        return MultiPolygon([geom])
    if geom.geom_type == "MultiPolygon":
        return geom
    return None


def _field_index(reader) -> dict[str, int]:
    """DBF 필드명 → 인덱스. fields[0]은 DeletionFlag 메타라 제외한다."""
    return {f[0].upper(): i for i, f in enumerate(reader.fields[1:])}


def rows_from_reader(reader, src: str) -> tuple[list[tuple], Counter]:
    """SHP 리더 → upsert 튜플들. 좌표 변환은 SQL(5186→5179)에 맡긴다."""
    idx = _field_index(reader)

    def get(rec, name: str) -> str | None:
        i = idx.get(name)
        if i is None or i >= len(rec):
            return None
        value = rec[i]
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    out: list[tuple] = []
    stats: Counter = Counter()
    for seq, sr in enumerate(reader.iterShapeRecords()):
        stats["records"] += 1
        geom = _as_multipolygon(shape(sr.shape.__geo_interface__))
        if geom is None:
            stats["bad_geom"] += 1
            continue
        notice_no = get(sr.record, "NTFC_SN")
        notice_date = parse_notice_date(notice_no)
        if notice_date is None:
            stats["no_notice_date"] += 1
        else:
            stats[f"year_{notice_date.year}"] += 1
        area = get(sr.record, "DGM_AR")
        out.append((
            zone_key(notice_no, get(sr.record, "DGM_NM"), seq),
            get(sr.record, "DGM_NM") or "(이름없음)",
            get(sr.record, "SCLAS_CL"),
            get(sr.record, "LCLAS_CL"),
            notice_no,
            notice_date,
            get(sr.record, "WTNNC_SN"),
            get(sr.record, "SIGNGU_SE"),
            parse_dbf_date(get(sr.record, "CREATE_DAT")),
            float(area) if area else None,
            src,
            json.dumps(mapping(geom)),
        ))
    return out, stats


def _src_label(path: str) -> str:
    """배포본을 구분할 수 있게 파일명에서 라벨을 만든다."""
    stem = path.replace("\\", "/").rsplit("/", 1)[-1]
    if stem.lower().endswith(".zip"):
        stem = stem[:-4]
    return f"uq181_{stem}"[:64]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+", help="UQ181 zip 경로 또는 glob")
    ap.add_argument("--srid", type=int, default=DEFAULT_SRID,
                    help=f"원본 좌표계 (기본 {DEFAULT_SRID}: .prj 실측값. "
                         "5186으로 넣으면 Y가 10만m 밀려 조인이 0건이 된다)")
    ap.add_argument("--inspect", action="store_true",
                    help="고시일 추출률·연도분포만 보고 저장하지 않는다 (DB 불필요)")
    args = ap.parse_args()

    paths: list[str] = []
    for pattern in args.files:
        hit = sorted(glob.glob(pattern))
        if not hit:
            print(f"WARN: {pattern} 에 해당하는 파일이 없다")
        paths.extend(hit)
    if not paths:
        raise SystemExit("읽을 zip이 없다")

    conn = None if args.inspect else psycopg2.connect(settings.database_url)
    total = 0
    try:
        for path in paths:
            src = _src_label(path)
            for name, reader in _shp_readers_from_zip(path):
                rows, stats = rows_from_reader(reader, src)
                dated = len(rows) - stats["no_notice_date"]
                pct = 100.0 * dated / len(rows) if rows else 0.0
                print(f"{name}: {len(rows)}건 · 고시일 {dated}건({pct:.1f}%) "
                      f"· 기하이상 {stats['bad_geom']}")
                years = sorted(
                    (int(k[5:]), v) for k, v in stats.items() if k.startswith("year_")
                )
                if years:
                    print(f"  고시연도 {years[0][0]}~{years[-1][0]} "
                          f"({len(years)}개 연도)")
                if conn is not None and rows:
                    with conn.cursor() as cur:
                        for i in range(0, len(rows), BATCH_SIZE):
                            chunk = rows[i:i + BATCH_SIZE]
                            psycopg2.extras.execute_values(
                                cur, _UPSERT_SQL,
                                [r + (args.srid,) for r in chunk],
                                template=_VALUES_TEMPLATE, page_size=BATCH_SIZE,
                            )
                    conn.commit()
                total += len(rows)
    finally:
        if conn is not None:
            conn.close()

    print(f"완료 — {total}건" + (" (inspect, 저장 안 함)" if args.inspect else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
