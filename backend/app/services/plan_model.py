"""계획도면 블록 → 「가게가 들어설 가능성」 점수 (2026-10-27).

지어진 신도시 10곳(5,941블록)에서 「블록 요인 → 실제 가게 수」를 배운 나무 모델(`app/data/plan_model.json`,
`tools/validation/train_plan_model.py`가 만든다)을 도면 블록에 그대로 적용한다. 운영 서버엔 sklearn이 없어 나무를
순수 파이썬으로 읽는다.

블록 요인은 학습과 같은 정의로 잰다 — 다르면 모델이 엉뚱한 숫자를 읽는다:
  블록       길 가운데 선(도면 길 + 주변 기존 길)으로 둘러싸인 면, 800~300,000㎡
  hw_max     둘레 30m 안 길의 가장 높은 등급 — 기존 길은 OSM 분류, 도면 길은 폭으로 환산
  res_block  블록 안에 A(공동주택)용지 또는 기존 아파트 건물
  res_area   블록 중심 400·800m 안 다른 아파트 블록 넓이 합
  syntax     블록 둘레 길의 통합·선택 최댓값을 블록끼리 줄 세운 백분위 평균
  station_m  가장 가까운 역까지 직선거리(3km에서 자름)
채점(도시 하나 빼고 배우기): 「가게 10곳 이상 블록」을 맞게 고르는 비율 0.837, 상가 후보만 0.823.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from shapely import STRtree
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

MODEL_PATH = Path(__file__).resolve().parents[1] / "data" / "plan_model.json"
MIN_BLOCK_M2 = 800.0
MAX_BLOCK_M2 = 300_000.0
SIDE_M = 30.0

# 학습(cityseer_town.py HW_RANK)과 같은 등급표
HW_RANK = {
    "trunk": 6, "primary": 6, "secondary": 5, "tertiary": 4, "unclassified": 3, "residential": 3,
    "living_street": 2, "pedestrian": 2, "service": 1, "footway": 1, "path": 1, "cycleway": 1, "steps": 0,
}


def width_rank(width_m: float | None) -> int:
    """도면 길 폭 → OSM 등급. 대로(25m~)·중로(12~25m)·소로(~12m) 구분을 OSM 분류에 맞춘 것 — 실측 대조 전 가정."""
    if width_m is None:
        return 3
    if width_m >= 30:
        return 6
    if width_m >= 20:
        return 5
    if width_m >= 12:
        return 4
    if width_m >= 8:
        return 3
    return 2


@dataclass
class PlanLine:
    geom: LineString  # EPSG:5179
    integration: float
    choice: float
    rank: int


@dataclass
class BlockScore:
    block_area_m2: float
    score: float  # 모델 출력 log1p(가게 수)
    expected_stores: float
    commercial_share: float  # 지어진 신도시에서 이 점수대 블록 중 「가게 10곳 이상」이었던 비율
    top_pct: int  # 지어진 신도시 블록 가운데 이 점수가 상위 몇 %인가 (10% 단위)
    features: dict[str, float]


@lru_cache(maxsize=1)
def load_model() -> dict:
    return json.loads(MODEL_PATH.read_text(encoding="utf-8"))


def _predict(model: dict, row: list[float]) -> float:
    total = model["baseline"]
    for t in model["trees"]:
        i = 0
        while not t["leaf"][i]:
            x = row[t["feature"][i]]
            go_left = t["missing_left"][i] if x is None or x != x else x <= t["threshold"][i]
            i = t["left"][i] if go_left else t["right"][i]
        total += t["value"][i]
    return total


def _decile(model: dict, value: float) -> int:
    """0(가장 낮은 10%) ~ 9(가장 높은 10%)."""
    cuts = model["deciles"]
    for k in range(len(cuts) - 1):
        if value <= cuts[k + 1]:
            return k
    return len(cuts) - 2


def _percentile(values: list[float]) -> list[float]:
    """같은 값은 평균 순위 — pandas rank(pct=True)와 같은 셈."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = rank / len(values)
        i = j + 1
    return out


def build_blocks(lines: list[PlanLine]) -> list[Polygon]:
    faces = polygonize(unary_union([ln.geom for ln in lines]))
    return [f for f in faces if MIN_BLOCK_M2 <= f.area <= MAX_BLOCK_M2]


def score_lots(
    lot_polygons: list[Polygon],
    lot_is_housing: list[bool],
    lines: list[PlanLine],
    apartment_points: list[Point],
    station_points: list[Point] | None,
) -> list[BlockScore | None]:
    """획지마다 그 획지가 든 블록의 점수. 블록을 못 찾으면(도면 가장자리) 획지 자체를 블록으로 본다.

    ``station_points``가 None이면 역을 **모르는** 것(조회 실패) — 역 없이 배운 모델을 쓴다. 빈 목록은 「3km 안에
    역이 없다」는 사실이라 역 모델에 3km로 넣는다. 둘을 섞으면 조회가 한 번 실패할 때 모든 블록이 「역에서 멀다」로
    떨어진다(2026-10-27 로컬 실측: F10 13곳 중 12등)."""
    model_all = load_model()
    model = model_all["models"]["with_station" if station_points is not None else "no_station"]
    cap = float(model_all.get("station_cap_m", 3000.0))
    blocks = build_blocks(lines)
    tree = STRtree(blocks) if blocks else None

    # 획지 → 블록
    lot_block: list[int] = []
    for poly in lot_polygons:
        p = poly.representative_point()
        idx = None
        if tree is not None:
            for k in tree.query(p):
                if blocks[k].contains(p):
                    idx = int(k)
                    break
        if idx is None:
            blocks.append(poly)
            idx = len(blocks) - 1
        lot_block.append(idx)

    # 아파트 블록: A용지가 든 블록 + 기존 아파트 건물이 든 블록
    res = [False] * len(blocks)
    for poly, housing, b in zip(lot_polygons, lot_is_housing, lot_block):
        if housing:
            res[b] = True
    if apartment_points:
        btree = STRtree(blocks)
        for pt in apartment_points:
            for k in btree.query(pt):
                if blocks[k].contains(pt):
                    res[int(k)] = True
    centres = [b.centroid for b in blocks]
    areas = [b.area for b in blocks]

    # 둘레 30m 안 길: 통합·선택 최댓값, 가장 높은 등급
    ltree = STRtree([ln.geom for ln in lines])
    hmax, cmax, hw = [], [], []
    for b in blocks:
        ring = b.boundary.buffer(SIDE_M)
        near = [lines[int(k)] for k in ltree.query(ring) if lines[int(k)].geom.intersects(ring)]
        hmax.append(max((ln.integration for ln in near), default=0.0))
        cmax.append(max((ln.choice for ln in near), default=0.0))
        hw.append(max((ln.rank for ln in near), default=2))
    syntax = [(a + c) / 2 for a, c in zip(_percentile(hmax), _percentile(cmax))]

    rows = []
    for i, b in enumerate(blocks):
        def res_area(r: float) -> float:
            return sum(areas[j] for j in range(len(blocks)) if j != i and res[j] and centres[j].distance(centres[i]) <= r)

        station = min((centres[i].distance(s) for s in station_points or []), default=cap)
        rows.append({
            "area": areas[i],
            "compact": 4 * math.pi * areas[i] / (b.length ** 2) if b.length else 0.0,
            "hw_max": float(hw[i]),
            "res_block": 1.0 if res[i] else 0.0,
            "res_area_400": res_area(400),
            "res_area_800": res_area(800),
            "syntax": syntax[i],
            "station_m": min(station, cap),
            "station_known": 0.0 if station_points is None else 1.0,  # 화면 문구용 — 모델 요인 아님
        })

    out: list[BlockScore | None] = []
    cache: dict[int, BlockScore] = {}
    for b in lot_block:
        if b not in cache:
            feats = rows[b]
            value = _predict(model, [feats[f] for f in model["features"]])
            cache[b] = BlockScore(
                block_area_m2=round(areas[b], 1),
                score=round(value, 4),
                expected_stores=round(math.expm1(value), 1),
                commercial_share=model["commercial_share_by_decile"][_decile(model, value)],
                top_pct=(10 - _decile(model, value)) * 10,
                features={k: round(v, 3) for k, v in feats.items()},
            )
        out.append(cache[b])
    return out
