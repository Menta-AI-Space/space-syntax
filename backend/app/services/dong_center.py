"""행정동 이름 → 상권 중심 좌표.

자동완성(/api/geo/dong-search)과 에이전트 챗이 같은 좌표를 쓰도록 한 곳에 둔다.
둘이 다른 지점을 잡으면, 목록에서 '신촌동'을 고른 사용자와 "신촌에 카페 자리
점수 매겨줘"라고 물은 사용자가 서로 다른 답을 받는다.

좌표는 폴리곤 기하 중심이 아니라 **점포가 몰린 곳(상권 중심)**이다. 신촌동은
절반이 연세대 캠퍼스라 기하 중심이 캠퍼스 안에 찍히고, 그 지점은 반경 500m 안
점포가 189곳뿐이라 "카페 0곳 · 경쟁 매우 낮음"이라는 정반대 분석이 나왔다
(같은 신촌동이라도 상권 중심은 2,408곳 · 동일업종 78곳 · 경쟁 매우 높음).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.services.spatial_query import get_connection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DongCenter:
    name: str
    lat: float
    lng: float


# adm_nm은 '서울특별시 <구> <동>' 전체 경로로 저장돼 있어(연남 → 서울특별시
# 마포구 연남동) 접두가 아니라 부분 매칭한다. 점포가 없는 동(공원·산 등)은
# 기하 중심(ST_PointOnSurface)으로 폴백한다.
#
# 무게중심 계산은 반드시 LIMIT을 적용한 뒤에만 돈다 — '동'처럼 광범위한 질의가
# 수백 개 동을 집계하면 5초 룰을 깬다.
_DONG_SEARCH_SQL = """
WITH matched AS (
    SELECT b.adm_nm, b.geom
    FROM sgis_dong_boundary b
    WHERE b.adm_nm ILIKE %s
    ORDER BY char_length(b.adm_nm), b.adm_nm
    LIMIT %s
)
SELECT m.adm_nm,
       ST_Y(ST_Transform(ST_SetSRID(COALESCE(s.center, ST_PointOnSurface(m.geom)), 5179), 4326)) AS lat,
       ST_X(ST_Transform(ST_SetSRID(COALESCE(s.center, ST_PointOnSurface(m.geom)), 5179), 4326)) AS lng
FROM matched m
LEFT JOIN LATERAL (
    SELECT ST_Centroid(ST_Collect(st.geom_5179)) AS center
    FROM sbiz_stores st
    WHERE ST_Intersects(st.geom_5179, m.geom)
) s ON TRUE
ORDER BY char_length(m.adm_nm), m.adm_nm
"""

# 어간 폴백용. ILIKE '%서초%동'으로 찾으면 '서울특별시 서초구 **내곡동**'까지
# 걸린다 — '서초'가 구 이름에 있고 이름이 '동'으로 끝나기 때문이다. 그래서
# 어간은 **마지막 낱말(동 이름)**에만 걸리도록 정규식으로 묶는다.
# gu 인자에 '%'를 주면 구를 한정하지 않는다.
_DONG_SEARCH_RE_SQL = _DONG_SEARCH_SQL.replace(
    "WHERE b.adm_nm ILIKE %s",
    "WHERE b.adm_nm LIKE %s AND b.adm_nm ~ %s",
)


# 행정동 경계·점포 무게중심은 적재 주기가 연 단위라 요청 사이에 변하지 않는다.
# 매번 PostGIS 집계를 돌리면 에이전트 챗 한 번에 ~1초가 붙어 5초 예산을 깎으므로
# 결과가 있는 질의만 프로세스 안에 담아 둔다(빈 결과는 담지 않는다 — DB가
# 잠시 죽었을 때의 빈 목록이 굳어 버리면 복구되지 않는다).
_MEMO: dict[tuple[str, int], list[DongCenter]] = {}


def _legal_dong_fallback(cur, q: str, limit: int) -> list:
    """법정동 이름만 아는 질의를 같은 구의 행정동으로 잇는다.

    '여의도동'은 법정동이고 경계 테이블에 있는 행정동은 '여의동'이라, 끝의 '동'을
    떼는 폴백으로도 안 잡힌다(어간 자체가 다르다). legal_dong_codes로 그 법정동이
    속한 구를 알아낸 뒤, 같은 구 안에서 어간이 가장 길게 겹치는 행정동을 찾는다
    ('영등포구' + '여의도' → 없음 → '여의' → 여의동).

    구를 한정하므로 다른 구의 비슷한 이름이 딸려올 일은 없다. 앞의 두 조회가
    모두 빈손일 때만 도는 마지막 폴백이라 평소 응답 시간에는 영향이 없다.
    """
    cur.execute(
        "SELECT addr_name FROM legal_dong_codes WHERE lowest_name = %s LIMIT 1", (q,)
    )
    row = cur.fetchone()
    if not row or not row[0]:
        return []
    parts = str(row[0]).split()
    if len(parts) < 3:
        return []
    gu = parts[1]
    stem = q[:-1] if q.endswith("동") else q
    for size in range(len(stem), 1, -1):
        cur.execute(
            _DONG_SEARCH_RE_SQL, (f"%{gu}%", f"(^| ){stem[:size]}[^ ]*동$", limit)
        )
        rows = cur.fetchall()
        if rows:
            return rows
    return []


def find_dong_centers(query: str, limit: int = 6) -> list[DongCenter]:
    """이름이 query를 포함하는 행정동의 상권 중심. 조회 실패는 빈 목록으로 강등."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    cached = _MEMO.get((q, limit))
    if cached is not None:
        return cached
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_DONG_SEARCH_SQL, (f"%{q}%", limit))
                rows = list(cur.fetchall())
                # 사람들은 법정동 이름으로 친다("역삼동")는데 경계 테이블에 있는 건
                # 행정동 이름("역삼1동", "역삼2동")이라 한 건도 안 잡혔다. 역삼·성수·
                # 여의도·목·화곡·상계처럼 번호가 붙는 동이 서울에 많아 검색이 통째로
                # 비는 경우가 잦았다.
                #
                # 첫 조회가 성공해도 이 폴백은 함께 돌린다 — '삼성동'은 관악구에
                # 그대로 있고 강남구에는 삼성1·2동으로 있어서, 멈춰 버리면 정작
                # 사람들이 찾는 강남 쪽이 빠진다.
                if q.endswith("동") and len(q) >= 2:
                    # 어간은 마지막 낱말(동 이름)에만 걸어야 한다 — '%서초%동'으로
                    # 찾으면 '서초구 내곡동'까지 딸려온다.
                    cur.execute(
                        _DONG_SEARCH_RE_SQL,
                        ("%", f"(^| ){q[:-1]}[^ ]*동$", limit),
                    )
                    seen = {r[0] for r in rows}
                    rows += [r for r in cur.fetchall() if r[0] not in seen]
                    rows = rows[:limit]
                if not rows:
                    rows = _legal_dong_fallback(cur, q, limit)
    except Exception as exc:  # 조회가 죽어도 호출측(자동완성·챗)은 계속 동작해야 한다
        logger.warning("find_dong_centers failed for q=%s: %s", q, exc)
        return []

    out: list[DongCenter] = []
    for adm_nm, lat, lng in rows:
        if adm_nm is None or lat is None or lng is None:
            continue
        # '서울특별시 마포구 연남동' → '마포구 연남동' (구를 남겨 동명 중복을 구분)
        parts = str(adm_nm).split(" ", 1)
        name = parts[1] if len(parts) == 2 else str(adm_nm)
        out.append(DongCenter(name=name, lat=float(lat), lng=float(lng)))
    if out:
        _MEMO[(q, limit)] = out
    return out


def _neighborhood_key(name: str) -> tuple[str, str]:
    """'용산구 이태원1동' → ('용산구', '이태원'). 번호만 다른 행정동을 한 동네로 묶는다."""
    gu, _, dong = name.partition(" ")
    base = dong[:-1] if dong.endswith("동") else dong
    # '이태원1' → '이태원', '성수1가1' → '성수'
    return gu, re.sub(r"\d+(가\d*)?$", "", base)


def resolve_unique_dong(query: str) -> DongCenter | None:
    """'신촌'처럼 동네 이름만 온 질의를 상권 중심 한 점으로 바꾼다.

    숫자가 있으면(예: '연남동 250-10') 이미 특정 지번을 가리키는 주소이므로
    건드리지 않는다. 같은 이름의 동이 **여러 구**에 있으면 어느 쪽인지 알 수 없어
    기존 지오코딩에 맡긴다.

    다만 같은 구 안에서 번호로만 갈린 행정동(이태원1·2동, 역삼1·2동)은 사람들이
    '이태원'이라 부르는 한 동네다. 이걸 '모호함'으로 보고 넘겨 버렸더니 '이태원
    노래방'이 상권 밖 지점으로 잡혀 오락 점포가 1곳으로 나왔다 — 이태원역 반경
    500m에는 9곳이 있다. 그래서 이런 경우는 묶어서 상권 중심 하나를 만든다.
    """
    q = (query or "").strip()
    if not q or any(ch.isdigit() for ch in q):
        return None
    hits = find_dong_centers(q, limit=6)
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]

    keys = {_neighborhood_key(h.name) for h in hits}
    if len(keys) != 1:
        return None  # 여러 구에 흩어져 있으면 어느 쪽인지 알 수 없다
    gu, stem = next(iter(keys))
    # 묶인 동들의 점포 무게중심을 다시 구한다 — 개별 중심의 산술평균은 점포가
    # 많은 쪽으로 쏠리지 않아 상권 중심에서 벗어난다.
    merged = _merged_center(gu, stem)
    if merged is None:
        return None
    # 묶은 이름은 사람들이 실제로 부르는 대로 — '양천구 목'이 아니라 '양천구 목동'.
    # 번호를 뗀 어간에 '동'을 붙이면 대개 법정동 이름과 같아진다.
    return DongCenter(name=f"{gu} {stem}동", lat=merged[0], lng=merged[1])


_MERGED_CENTER_SQL = """
WITH matched AS (
    SELECT b.geom FROM sgis_dong_boundary b
    WHERE b.adm_nm LIKE %s AND b.adm_nm ~ %s
), area AS (
    SELECT ST_Union(geom) AS geom FROM matched
)
SELECT ST_Y(ST_Transform(ST_SetSRID(c.center, 5179), 4326)),
       ST_X(ST_Transform(ST_SetSRID(c.center, 5179), 4326))
FROM (
    SELECT COALESCE(
        (SELECT ST_Centroid(ST_Collect(st.geom_5179)) FROM sbiz_stores st
         WHERE ST_Intersects(st.geom_5179, (SELECT geom FROM area))),
        (SELECT ST_PointOnSurface(geom) FROM area)
    ) AS center
) c
"""


def _merged_center(gu: str, stem: str) -> tuple[float, float] | None:
    """번호로 갈린 행정동들을 하나로 보고 점포 무게중심을 구한다."""
    cached = _MERGED_MEMO.get((gu, stem))
    if cached is not None:
        return cached
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    _MERGED_CENTER_SQL, (f"%{gu}%", f"(^| ){stem}[^ ]*동$")
                )
                row = cur.fetchone()
    except Exception as exc:
        logger.warning("_merged_center failed for %s %s: %s", gu, stem, exc)
        return None
    if not row or row[0] is None or row[1] is None:
        return None
    out = (float(row[0]), float(row[1]))
    _MERGED_MEMO[(gu, stem)] = out
    return out


_MERGED_MEMO: dict[tuple[str, str], tuple[float, float]] = {}
