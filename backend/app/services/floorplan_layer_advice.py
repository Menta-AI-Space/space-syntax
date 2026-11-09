"""어느 레이어가 벽인지 **프로그램이 찾아 추천한다**.

레이어 목록을 내주고 사람이 고르게 한 것(2026-09-30)은 "못 읽습니다"보다는 낫지만,
여전히 손님에게 숙제를 시킨다. 실제 사무소 도면 3장을 받아 보니 더 나빴다 —
벽이 **한 레이어에 다 있지 않다.** 실측(2026-10-01):

- `1층평면도.dwg`: `WA1`에 세대 내부 벽만 있고 **외벽이 없다.** 외벽은 `fin`에
  조경선과 **섞여** 들어 있다.
- `PLAN-1ST FLOOR.dwg`: 벽이 `A-WL-FUL`·`A-WL-GLS`·`A-WL-PTL`에 나뉘어 있고,
  게다가 **한 장에 평면도 여러 개 + 배치도 + 표제란**이 나란히 놓여 있다
  (도면 범위 1,529m × 368m).

그래서 이름만으로는 끝나지 않는다. 여기서는 **후보 레이어를 하나씩 넣어 보고
건물이 닫히는지로 점수를 매긴다.** 사람은 추천을 확인만 하면 된다.

**두 번 틀린 자리다.** 처음엔 점수를 「선이 차지한 경계 상자 대비 실내 비율」로
재서, 대지경계선(`A-SP-LOT`)과 캐드 보조 레이어(`DefPoints`)를 벽으로 집었다 —
건물이 아니라 **땅을 감싸 놓고** 점수가 올랐기 때문이다. 다음엔 "경계 상자를
넓히는 후보는 거절"로 막았더니 이번엔 외벽이 든 `fin`까지 같이 거절했다(조경선이
한 레이어에 섞여 있어서 상자가 넓어진다).

지금 쓰는 방법은 **분모를 먼저 고정하는 것**이다:

1. 이름으로 찾은 벽에서 **가장 큰 덩어리 하나**를 골라 그 범위를 「건물 영역」으로 잡는다.
   한 장에 평면도가 여러 개여도 하나만 본다. 분모가 종이 전체가 되지 않는다.
2. 후보 레이어는 **그 영역 안에 걸치는 선만** 쓴다. `fin`의 외벽은 들어오고
   조경선은 잘려 나간다. 레이어를 통째로 받거나 버리지 않는다.
3. 점수는 **실내로 잡힌 면적(㎡)**이다. 「경계 상자 대비 비율」로 쓰지 않는다 —
   ㄱ자 건물은 상자를 다 채우지 못해서 제대로 고른 조합도 50%대가 나오고, 그러면
   "닫혔다/아니다"를 가를 선을 그을 수 없다. 면적은 그 자체로 읽힌다: 외벽이
   새면 바깥 공기가 들어와 실내가 0에 가깝고, 막히면 실제 바닥면적이 나온다.
   단 **합계가 아니라 가장 큰 덩어리 하나**를 센다 — 합계로 재면 포장·조경
   해치선처럼 빽빽한 레이어가 선 사이에 갇힌 자잘한 칸으로 점수를 벌어 벽으로
   뽑힌다(`1층평면도.dwg`의 `fin`이 2,705㎡를 그렇게 벌었다. 그려 보니 외벽이
   아니라 해치 뭉치였다 — 2026-10-01). 진짜 외벽은 **큰 덩어리 하나**를 만든다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

logger = logging.getLogger(__name__)

# 「건물 영역」을 찾을 때 쓰는 격자의 칸 크기(m)와 긴 변 칸수 상한.
# **칸 크기를 도면 크기에 비례해 두면 안 된다** — 1,500m 종이에서 칸이 17m가 되어
# 평면도 여러 개가 한 덩어리로 뭉쳤다(2026-10-01).
CORE_CELL_M = 1.0
CORE_MAX_SIDE = 2_000
# 이 거리(m)보다 가까운 벽은 같은 건물로 본다. 문 구멍(1~3m)과 맞붙은 세대는
# 이어지고, 한 장에 나란히 놓인 **다른 평면도**는 끊긴다.
# ⚠️ 「가장 큰 연결 덩어리」를 틈 없이 세면 `WA1`처럼 **세대 내부 벽만 든 레이어**에서
# 세대 한 칸(17×10m)이 뽑혀 평면도 전체를 놓친다. 그래서 틈을 실치수로 잇는다.
CORE_LINK_M = 6.0
# 건물 영역을 사방으로 이만큼 넓힌다. 경계가 벽을 자르면 바깥에서 물이 안 들어온다.
CORE_MARGIN = 0.05
# 점수를 낼 때 쓰는 격자. 분석용(2,200칸)보다 성글어도 "닫혔나"는 판별된다 —
# 후보 조합마다 다시 그리므로 싸야 한다.
ADVICE_CELLS = 4_000
# 한 번에 시험할 후보 레이어 수 상한. 실무 도면은 레이어가 40~80개라 전부
# 돌리면 느리다. 건물 영역 안의 선 길이가 긴 것부터 본다.
MAX_CANDIDATES = 14
# 실내 면적을 이 비율만큼 못 늘리는 후보는 넣지 않는다 (잡음이 섞이는 것을 막는다).
MIN_GAIN_RATIO = 0.05

Segment = tuple[float, float, float, float]
Box = tuple[float, float, float, float]  # (x0, y0, x1, y1)


@dataclass
class LayerAdvice:
    """추천 결과. 화면은 이걸 체크박스 기본값으로 쓰면 된다."""

    wall_layers: list[str]
    # 벽이 감싼 실내 면적(㎡) — 추천 전/후.
    indoor_before: float
    indoor_after: float
    added: list[str] = field(default_factory=list)
    reason: str = ""
    # 건물 영역. 호출하는 쪽이 분석 범위를 좁히는 데 그대로 쓸 수 있다.
    core_box: Box | None = None
    # 한 장에 그려져 있던 도면 수. 2 이상이면 그중 하나만 분석한 것이다.
    drawing_count: int = 1


def _grid_of(box: Box, cells: int) -> tuple[int, int, float]:
    width = max(box[2] - box[0], 1e-9)
    height = max(box[3] - box[1], 1e-9)
    cell = ((width * height) / max(cells, 1)) ** 0.5
    return max(8, int(width / cell)), max(8, int(height / cell)), cell


def _grid_fixed_cell(box: Box) -> tuple[int, int, float]:
    """실치수로 칸을 잡는다(m). 종이가 아주 크면 칸을 키워 비용을 묶는다."""
    width = max(box[2] - box[0], 1e-9)
    height = max(box[3] - box[1], 1e-9)
    cell = max(CORE_CELL_M, max(width, height) / CORE_MAX_SIDE)
    return max(8, int(width / cell)), max(8, int(height / cell)), cell


def _rasterize(segments, box: Box, cols: int, rows: int, cell: float) -> np.ndarray:
    blocked = np.zeros((rows, cols), dtype=bool)
    for x1, y1, x2, y2 in segments:
        c1, r1 = (x1 - box[0]) / cell, (y1 - box[1]) / cell
        c2, r2 = (x2 - box[0]) / cell, (y2 - box[1]) / cell
        steps = int(max(abs(c2 - c1), abs(r2 - r1))) * 2 + 2
        prev = None
        for t in np.linspace(0.0, 1.0, steps):
            c = int(round(c1 + (c2 - c1) * t))
            r = int(round(r1 + (r2 - r1) * t))
            if not (0 <= c < cols and 0 <= r < rows):
                prev = None
                continue
            # 비스듬한 벽의 모서리 틈을 막는다 (floorplan_vga._draw_wall과 같은 이유).
            if prev is not None and prev[0] != r and prev[1] != c:
                blocked[prev[0], c] = True
            blocked[r, c] = True
            prev = (r, c)
    return blocked


def _core_box(segments, extent: Box, anchors=None) -> tuple[Box, int] | None:
    """선이 모인 덩어리 중 **벽이 가장 많이 든 하나**의 범위.

    한 장에 평면도가 여럿일 때 하나만 고른다. 덩어리를 **가장 큰 것**으로 고르면
    벽이 든 도면이 아니라 **옆 도면**을 집는다(`1층평면도(1).dwg`: 1,401×2,129m
    종이에서 벽이 없는 쪽을 골라 실내 26㎡를 보고했다 — 2026-10-01). 그래서
    덩어리는 선 전체로 찾되, **고르는 기준은 이름으로 찾은 벽이 얼마나 들었나**다.
    """
    if not segments:
        return None
    cols, rows, cell = _grid_fixed_cell(extent)
    blocked = _rasterize(segments, extent, cols, rows, cell)
    if not blocked.any():
        return None
    # 문·창 구멍 때문에 한 건물이 여러 조각으로 보인다 — 부풀려 잇는다.
    link = max(1, int(round(CORE_LINK_M / cell / 2)))
    linked = ndimage.binary_dilation(blocked, iterations=link)
    labeled, count = ndimage.label(linked, structure=np.ones((3, 3), dtype=int))
    if count == 0:
        return None
    if anchors:
        # 이름으로 찾은 벽이 가장 많이 떨어지는 덩어리를 고른다.
        marks = _rasterize(anchors, extent, cols, rows, cell)
        scores = ndimage.sum(marks, labeled, index=range(1, count + 1))
    else:
        # 벽을 못 찾았으면 선이 가장 빽빽한 덩어리로 대신한다.
        scores = ndimage.sum(blocked, labeled, index=range(1, count + 1))
    best = int(np.argmax(scores)) + 1
    # 「도면 한 장」으로 셀 만한 덩어리만 센다 — 표제란·방위표 같은 부스러기는 뺀다.
    bulk = ndimage.sum(blocked, labeled, index=range(1, count + 1))
    rows_i, cols_i = np.nonzero(labeled == best)
    # 고른 도면 테두리 **안에** 든 덩어리는 다른 도면이 아니라 그 건물의 섬 블록이다
    # (통로가 연결 거리보다 넓으면 가운데 매장 블록이 떨어져 보인다). 세지 않는다 —
    # 안 그러면 몰 하나에 「한 장에 도면이 3개」라고 말한다(2026-10-22, 시연용 몰).
    objects = ndimage.find_objects(labeled)
    r0, r1, c0, c1 = rows_i.min(), rows_i.max(), cols_i.min(), cols_i.max()
    drawings = 0
    for i, (size, sl) in enumerate(zip(bulk, objects), start=1):
        if size < max(bulk.max() * 0.1, 20) or sl is None:
            continue
        inside = (
            i != best
            and sl[0].start >= r0 and sl[0].stop - 1 <= r1
            and sl[1].start >= c0 and sl[1].stop - 1 <= c1
        )
        if not inside:
            drawings += 1
    x0 = extent[0] + cols_i.min() * cell
    x1 = extent[0] + (cols_i.max() + 1) * cell
    y0 = extent[1] + rows_i.min() * cell
    y1 = extent[1] + (rows_i.max() + 1) * cell
    mx = (x1 - x0) * CORE_MARGIN
    my = (y1 - y0) * CORE_MARGIN
    return (x0 - mx, y0 - my, x1 + mx, y1 + my), drawings


def _clip(segments, box: Box) -> list[Segment]:
    """건물 영역에 걸치는 선만 남긴다. 레이어를 통째로 받거나 버리지 않는 것이 요점이다."""
    out: list[Segment] = []
    for s in segments:
        if (
            max(s[0], s[2]) >= box[0] and min(s[0], s[2]) <= box[2]
            and max(s[1], s[3]) >= box[1] and min(s[1], s[3]) <= box[3]
        ):
            out.append(s)
    return out


def _indoor_area(segments, box: Box) -> float:
    """벽이 감싼 **가장 큰 실내 덩어리의 면적(㎡)**. 외벽이 새면 0에 가깝다."""
    if not segments:
        return 0.0
    cols, rows, cell = _grid_of(box, ADVICE_CELLS)
    blocked = _rasterize(segments, box, cols, rows, cell)
    if not blocked.any():
        return 0.0
    # 바깥에서 흘려 넣어 실내를 가린다.
    padded = np.pad(blocked, 1, constant_values=False)
    labeled, _ = ndimage.label(~padded, structure=np.ones((3, 3), dtype=int))
    exterior = (labeled == labeled[0, 0])[1:-1, 1:-1]
    indoor = ~blocked & ~exterior
    if not indoor.any():
        return 0.0
    # 합계가 아니라 최대 덩어리. 해치선이 만든 자잘한 칸은 여기서 떨어져 나간다.
    chunks, count = ndimage.label(indoor, structure=np.ones((3, 3), dtype=int))
    if count == 0:
        return 0.0
    biggest = ndimage.sum(indoor, chunks, index=range(1, count + 1)).max()
    return float(biggest * cell * cell)


def suggest_wall_layers(
    lines_by_layer: dict[str, list[Segment]],
    named_walls: list[str],
    width: float,
    height: float,
    skip: set[str] | None = None,
) -> LayerAdvice:
    """이름으로 찾은 벽에 레이어를 더해 보며 건물이 닫히는 조합을 찾는다.

    ``lines_by_layer``는 {레이어: [(x1,y1,x2,y2), ...]}이고 좌표는 원점 기준이다.
    ``skip``에는 문·치수처럼 벽이 될 수 없는 레이어를 넣는다.
    """
    skip = skip or set()
    extent: Box = (0.0, 0.0, max(width, 1e-9), max(height, 1e-9))
    chosen = [n for n in named_walls if lines_by_layer.get(n)]

    # 영역을 찾는 씨앗은 **벽이 아니라 잡음을 뺀 선 전체**다. 벽 레이어로 찾으면
    # `WA1`처럼 세대 내부 벽만 든 레이어에서 **세대 한 칸**이 뽑혀 평면도 전체를
    # 놓친다(2026-10-01). 도면 한 장 안은 설비·가구까지 빽빽하고 도면 사이는
    # 비어 있으므로, 선 전체로 보면 「그려진 도면 한 개」가 그대로 덩어리가 된다.
    seed = [
        seg for n, segs in lines_by_layer.items()
        if n not in skip for seg in segs
    ]
    anchors = [seg for n in chosen for seg in lines_by_layer[n]]
    found = _core_box(seed, extent, anchors=anchors)
    core, drawings = found if found else (extent, 1)

    inside: dict[str, list[Segment]] = {}
    for name, segs in lines_by_layer.items():
        clipped = _clip(segs, core)
        if clipped:
            inside[name] = clipped

    current = [seg for n in chosen for seg in inside.get(n, [])]
    before = _indoor_area(current, core)
    core_area = max((core[2] - core[0]) * (core[3] - core[1]), 1e-9)

    candidates = sorted(
        (n for n in inside if n not in chosen and n not in skip),
        key=lambda n: -sum(
            ((s[2] - s[0]) ** 2 + (s[3] - s[1]) ** 2) ** 0.5 for s in inside[n]
        ),
    )[:MAX_CANDIDATES]

    added: list[str] = []
    score = before
    # 탐욕적으로 하나씩. 조합 전수는 2^14라 못 돌리고, 벽은 서로 보완하므로
    # 한 번에 가장 많이 올리는 것을 고르는 것으로 충분하다.
    while candidates:
        best_name, best_score = None, score
        for name in candidates:
            trial = _indoor_area(current + inside[name], core)
            if trial > best_score + core_area * MIN_GAIN_RATIO:
                best_name, best_score = name, trial
        if best_name is None:
            break
        current = current + inside[best_name]
        chosen.append(best_name)
        added.append(best_name)
        candidates.remove(best_name)
        score = best_score

    pct = score / core_area * 100.0
    if not chosen:
        reason = "벽으로 쓸 레이어를 찾지 못했습니다. 아래 목록에서 직접 골라 주세요."
    elif added:
        reason = (
            f"이름으로 찾은 벽만으로는 이어진 실내가 {before:,.0f}㎡밖에 잡히지 않아 "
            f"{' · '.join(added)}을(를) 더해 {score:,.0f}㎡까지 넓혔습니다."
        )
    elif score <= core_area * 0.1:
        reason = (
            f"벽으로 감싸인 실내가 {score:,.0f}㎡뿐입니다 — 외벽이 빠졌거나 다른 "
            "레이어에 있을 수 있습니다. 아래에서 확인해 주세요."
        )
    else:
        reason = f"이름으로 찾은 벽만으로 실내 {score:,.0f}㎡가 잡힙니다."
    logger.info("벽 레이어 추천: %s (실내 %.0f㎡, 영역의 %.0f%%)", chosen, score, pct)

    return LayerAdvice(
        wall_layers=chosen,
        indoor_before=round(before, 1),
        indoor_after=round(score, 1),
        added=added,
        reason=reason,
        core_box=core,
        drawing_count=drawings,
    )
