"""Turn a floor-plan VGA result into a ranked list of problems.

The spec (docs/SPEC_FLOORPLAN_TABS_UI.md) says the screen must answer "what is
wrong with this plan", not "what is the Integration". Numbers and heatmaps stay
available, but the default view is this list: each item names a place on the
plan, says why it is a problem, and carries a severity so the user knows what
to look at first.

Everything here is derived from the grid metrics we already compute — no new
data, no semantic elements (rooms, stairs, entrances). When those arrive the
rules can get sharper; until then the wording stays at the level the evidence
supports ("외진 구역", not "화장실 앞 복도").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from app.services.floorplan_vga import GridCell, VGAResult

# 구역 무게중심에서 이 칸 수 안에 실명이 있으면 그 이름으로 부른다.
LABEL_REACH_CELLS = 8.0

# 문제 목록에 올릴 사각지대: 목 하위 이 비율 안에 드는 칸을 모은다.
DEAD_ZONE_QUANTILE = 0.10
# 그 칸들이 붙어 있는 덩어리가 이 칸 수 이상일 때만 "구역"으로 친다 —
# 한두 칸짜리 구석은 격자 잡음이지 설계 문제가 아니다.
MIN_ZONE_CELLS = 6
# 통행이 이 비율 안의 칸에 몰려 있으면 병목 후보.
CHOKE_QUANTILE = 0.05
# 시야가 막힌 구역: 보이는 칸 수 하위 비율.
BLIND_QUANTILE = 0.10

Severity = str  # "high" | "medium" | "low"


@dataclass
class Issue:
    severity: Severity
    title: str
    detail: str
    # 도면 위 마커 위치 (캔버스 좌표) — 문제가 한 지점이 아니라 구역이면 무게중심.
    x: float
    y: float
    x_m: float | None = None
    y_m: float | None = None
    # 구역 문제일 때 크기. 축척이 있으면 ㎡, 없으면 칸 수만.
    cell_count: int = 0
    area_m2: float | None = None
    # 근거 — 화면 툴팁·LLM 입력용 짧은 문구.
    evidence: str = ""
    # 구역을 이루는 격자 칸 (row, col). 화면이 그 부분을 잘라 그림으로 보여 준다 —
    # 「남서쪽 매장 68 부근」이라는 글만으로는 어디인지 모른다(2026-10-24 사용자 지적).
    cells: list[tuple[int, int]] = field(default_factory=list)
    # 규칙 요약문이 카드 번호로 가리킬 때 쓰는 표지 — "best_zone"(가장 목이 좋은 구역)·
    # "dead_zone"(외진 구역). 응답에도 실어 화면이 「발견된 문제」 개수에서 좋은 구역을 뺀다
    # (2026-11-07 UX 자동 개선).
    kind: str = ""


# 「가장 목이 좋은 곳」으로 묶을 칸 — 목 상위 몇 %.
BEST_ZONE_QUANTILE = 0.10


def _grid_of(result: VGAResult, key) -> np.ndarray:
    grid = np.full((result.rows, result.cols), np.nan)
    for cell in result.cells:
        grid[cell.row, cell.col] = key(cell)
    return grid


def _zones(mask: np.ndarray, min_cells: int) -> list[np.ndarray]:
    """Connected blobs of ``mask`` (4-neighbour), largest first, ≥ min_cells."""
    labels, count = ndimage.label(mask)
    zones = []
    for label in range(1, count + 1):
        coords = np.argwhere(labels == label)
        if len(coords) >= min_cells:
            zones.append(coords)
    zones.sort(key=len, reverse=True)
    return zones


def _place(result: VGAResult, coords: np.ndarray) -> tuple[float, float, float | None, float | None]:
    r, c = coords.mean(axis=0)
    x = (float(c) + 0.5) * result.cell_size
    y = (float(r) + 0.5) * result.cell_size
    m = result.cell_size_m / result.cell_size if result.cell_size_m else None
    return x, y, (x * m if m else None), (y * m if m else None)


def _side(result: VGAResult, x: float, y: float) -> str:
    """Rough compass label so the sentence reads "북동쪽 구역" not "(1483, 760)"."""
    w, h = result.cols * result.cell_size, result.rows * result.cell_size
    ns = "북" if y < h / 3 else ("남" if y > 2 * h / 3 else "")
    ew = "서" if x < w / 3 else ("동" if x > 2 * w / 3 else "")
    label = ns + ew
    return f"{label}쪽" if label else "가운데"


_NUMBERED_ROOM = re.compile(r"^\s*(매장|호실|점포|상가|사무실|룸|방)\s*0*(\d+)\s*$")


def _room(text: str) -> str:
    """도면 이름을 말로 푼다 — 「매장 07」 → 「7번 매장」. 따옴표로 도면 글자를 그대로 옮기면
    「'매장 68'이 뭐냐」는 물음이 나왔다(2026-10-27 사용자 지적). 번호 꼴이 아니면 이름 그대로."""
    m = _NUMBERED_ROOM.match(text)
    return f"{int(m.group(2))}번 {m.group(1)}" if m else text.strip()


def _near_label(result: VGAResult, labels: list[tuple[str, float, float]], x: float, y: float) -> str:
    """"31번 매장 근처 " if a room name sits close to (x, y), else ""."""
    reach = LABEL_REACH_CELLS * result.cell_size
    best = None
    for text, lx, ly in labels:
        d = ((lx - x) ** 2 + (ly - y) ** 2) ** 0.5
        if d <= reach and (best is None or d < best[0]):
            best = (d, text)
    return f"{_room(best[1])} 근처 " if best else ""


def _zone_names(
    result: VGAResult, labels: list[tuple[str, float, float]], x: float, y: float, coords: np.ndarray
) -> str:
    """구역 안(없으면 가까이)에 적힌 실명 — 없으면 빈 문자열. ``_zone_name`` 설명 참고."""
    inside = {(int(r), int(c)) for r, c in coords}
    cs = result.cell_size
    integration = {(cell.row, cell.col): cell.integration for cell in result.cells}
    named: dict[tuple[int, int], list[str]] = {}
    for text, lx, ly in labels:
        key = (int(ly // cs), int(lx // cs))
        if key in inside:
            named.setdefault(key, []).append(text)
    if named:
        peak = max(named, key=lambda k: integration.get(k, float("-inf")))
        return "·".join(_room(t) for t in named[peak])
    return _near_label(result, labels, x, y).replace(" 근처", "").strip()


def _zone_name(
    result: VGAResult, labels: list[tuple[str, float, float]], x: float, y: float, coords: np.ndarray
) -> str:
    """구역 이름. **구역 안에 적힌** 실명을 먼저 쓴다 — 통로 구역의 무게중심에서 가장
    가까운 이름은 옆 매장인 경우가 많다(「'매장 15' 일대」, 실제로는 「메인 몰」 통로).
    안에 적힌 게 없으면 가까운 실명, 그것도 없으면 방위."""
    # 안에 적힌 이름 중 **목이 가장 높은 칸에 적힌 것** — 매장 안보다 통로·광장 이름이 뽑힌다.
    return _zone_names(result, labels, x, y, coords) or _side(result, x, y)


def _zone_label(
    result: VGAResult, labels: list[tuple[str, float, float]], x: float, y: float, coords: np.ndarray
) -> str:
    """제목에 붙는 「68번 매장 근처 」 — 구역 **안에** 적힌 이름을 먼저 쓴다(``_zone_name``과 같은 규칙).
    무게중심에서 가까운 이름만 보면 막다른 날개 통로가 「남동쪽 약 1031㎡」처럼 이름 없이 나왔다
    (2026-10-25). 이름이 없으면 빈 문자열 — 방위는 제목 앞에 따로 붙는다."""
    name = _zone_names(result, labels, x, y, coords)
    return f"{name} 근처 " if name else ""


def _area(result: VGAResult, n_cells: int) -> float | None:
    return n_cells * result.cell_size_m ** 2 if result.cell_size_m else None


def _area_text(area_m2: float | None, n_cells: int) -> str:
    return f"약 {area_m2:,.0f}㎡" if area_m2 else f"{n_cells}칸"


def _zone_mean(grid: np.ndarray, coords: np.ndarray) -> float:
    return float(np.nanmean(grid[coords[:, 0], coords[:, 1]]))


def _depth_sentence(result: VGAResult, mean_depth: np.ndarray, coords: np.ndarray) -> str:
    """외진 구역마다 다른 첫 문장 — 같은 종류 카드가 글자 하나 다르지 않게 겹쳤다(2026-10-25
    사용자 지적: 「1과 2가 동일한 답」). 그 구역의 실제 깊이를 건물 평균과 견준다."""
    zone = _zone_mean(mean_depth, coords)
    return (
        f"이 구역까지 오려면 건물 어디서 출발해도 평균 {zone:.1f}번 꺾어야 합니다"
        f"(건물 평균 {result.mean_depth_mean:.1f}번)."
    )


def _view_sentence(result: VGAResult, connectivity: np.ndarray, coords: np.ndarray) -> str:
    """시야가 막힌 구역마다 다른 첫 문장 — 거기 서면 보이는 넓이를 건물 평균과 견준다."""
    zone = _zone_mean(connectivity, coords)
    mean = result.connectivity_mean
    share = 100 * zone / mean if mean > 0 else 0.0
    if result.cell_size_m:
        cell_m2 = result.cell_size_m ** 2
        return (
            f"이 구역 안에서 둘러보면 바닥 약 {zone * cell_m2:,.0f}㎡가 보입니다 — "
            f"건물 평균({mean * cell_m2:,.0f}㎡)의 {share:.0f}%뿐입니다."
        )
    return f"이 구역 안에서 둘러보면 바닥 {zone:.0f}칸이 보입니다 — 건물 평균({mean:.0f}칸)의 {share:.0f}%뿐입니다."


def detect_issues(
    result: VGAResult, labels: list[tuple[str, float, float]] | None = None
) -> list[Issue]:
    """``labels`` are (text, x, y) room names in canvas coordinates; with them
    a zone is named after the nearest room instead of just its compass side."""
    labels = labels or []
    issues: list[Issue] = []
    if not result.cells:
        return issues

    # 1. 도면 일부가 분석에서 빠짐 — 벽에 막힌 문. 결과 전체를 흔드는 문제라 항상 첫째.
    if result.coverage_pct < 70.0 and result.excluded_cells:
        coords = np.array([[c.row, c.col] for c in result.excluded_cells])
        x, y, xm, ym = _place(result, coords)
        issues.append(
            Issue(
                severity="high",
                title="도면의 일부가 벽에 막혀 분석에서 빠졌습니다",
                detail=(
                    f"열린 공간의 {100 - result.coverage_pct:.0f}%가 나머지와 이어지지 않습니다. "
                    "문이 벽으로 그려져 있을 가능성이 높습니다 — 빗금 친 경계에서 문 자리를 찍고 다시 분석하세요."
                ),
                x=x, y=y, x_m=xm, y_m=ym,
                cell_count=len(coords),
                area_m2=_area(result, len(coords)),
                cells=[(int(r), int(c)) for r, c in coords],
                evidence=f"반영률 {result.coverage_pct:.0f}%",
                kind="excluded",
            )
        )

    integration = _grid_of(result, lambda c: c.integration)
    choice = _grid_of(result, lambda c: c.choice)
    connectivity = _grid_of(result, lambda c: c.connectivity)
    mean_depth = _grid_of(result, lambda c: c.mean_depth)
    valid = ~np.isnan(integration)
    total_cells = int(valid.sum())

    # 2. 사각지대 — 목 하위 10% 칸이 뭉쳐 있는 구역.
    dead_cut = np.nanquantile(integration, DEAD_ZONE_QUANTILE)
    for coords in _zones(valid & (integration <= dead_cut), MIN_ZONE_CELLS)[:3]:
        x, y, xm, ym = _place(result, coords)
        share = len(coords) / total_cells
        area = _area(result, len(coords))
        issues.append(
            Issue(
                severity="high" if share >= 0.04 else "medium",
                title=(
                    f"{_side(result, x, y)} {_zone_label(result, labels, x, y, coords)}외진 구역 — "
                    f"찾아가기 먼 바닥 {_area_text(area, len(coords))}"
                ),
                detail=(
                    f"{_depth_sentence(result, mean_depth, coords)} 꺾는 횟수가 많을수록 손님 발길이 덜 닿습니다. "
                    "통로를 잇거나, 창고·사무처럼 손님이 안 와도 되는 용도로 돌리는 편이 낫습니다."
                ),
                x=x, y=y, x_m=xm, y_m=ym,
                cell_count=len(coords),
                area_m2=area,
                cells=[(int(r), int(c)) for r, c in coords],
                evidence=f"건물에서 찾아가기 가장 어려운 {DEAD_ZONE_QUANTILE * 100:.0f}% 바닥이 모인 곳 (실내의 {share * 100:.0f}%)",
                kind="dead_zone",
            )
        )

    # 3. 병목 — 통행이 좁은 곳 한 군데에 몰림.
    choke_cut = np.nanquantile(choice, 1 - CHOKE_QUANTILE)
    choke_zones = _zones(valid & (choice >= choke_cut), 3)
    if choke_zones:
        top = choke_zones[0]
        top_share = len(top) / max(int((valid & (choice >= choke_cut)).sum()), 1)
        if top_share >= 0.5:
            x, y, xm, ym = _place(result, top)
            issues.append(
                Issue(
                    severity="medium",
                    title=f"{_side(result, x, y)} {_near_label(result, labels, x, y)}한 곳으로 동선이 몰립니다",
                    detail=(
                        "건물 안을 오가는 길이 이 지점을 거의 다 거쳐 갑니다. 붐빌 때 막히기 쉽고, "
                        "반대로 눈에 띄어야 하는 매장·안내물에는 가장 좋은 자리입니다."
                    ),
                    x=x, y=y, x_m=xm, y_m=ym,
                    cell_count=len(top),
                    area_m2=_area(result, len(top)),
                    cells=[(int(r), int(c)) for r, c in top],
                    evidence=f"지나가는 빈도 상위 {CHOKE_QUANTILE * 100:.0f}% 칸의 {top_share * 100:.0f}%가 한 덩어리",
                )
            )

    # 4. 시야가 막힌 구역 — 보이는 칸이 적은 곳이 뭉쳐 있음. 외진 구역과 겹치면 생략.
    blind_cut = np.nanquantile(connectivity, BLIND_QUANTILE)
    dead_marks = [(i.x, i.y) for i in issues if "외진" in i.title]
    for coords in _zones(valid & (connectivity <= blind_cut), MIN_ZONE_CELLS)[:2]:
        x, y, xm, ym = _place(result, coords)
        if any(abs(x - dx) < 6 * result.cell_size and abs(y - dy) < 6 * result.cell_size for dx, dy in dead_marks):
            continue
        area = _area(result, len(coords))
        issues.append(
            Issue(
                severity="medium",
                title=(
                    f"{_side(result, x, y)} {_zone_label(result, labels, x, y, coords)}시야가 막힌 구역 — "
                    f"벽에 가려 잘 안 보이는 바닥 {_area_text(area, len(coords))}"
                ),
                detail=(
                    f"{_view_sentence(result, connectivity, coords)} 그래서 길을 잃기 쉽고 매장이 있어도 눈에 안 띕니다 — "
                    "벽을 트거나 안내 표지가 필요합니다."
                ),
                x=x, y=y, x_m=xm, y_m=ym,
                cell_count=len(coords),
                area_m2=area,
                cells=[(int(r), int(c)) for r, c in coords],
                evidence=f"건물에서 둘러봐도 가장 적게 보이는 {BLIND_QUANTILE * 100:.0f}% 바닥이 모인 곳",
            )
        )

    # 5. 참고 — 가장 목이 좋은 「구역」. 예전엔 여기서 「모이는 자리(목 1위 칸)와 지나가는
    # 자리(지나가는 빈도 1위 칸)가 다르다」고 말했다. 그런데 지나가는 빈도 1위 칸은 두 통로가
    # 직각으로 만나는 **모서리 한 칸**에 뾰족하게 튄다 — 가시성 그래프에서 두 통로 사이 최단
    # 경로가 모두 그 한 칸을 지나기 때문이다(2026-10-24 실측: 대형 복합몰 샘플에서 1위가 막다른
    # 푸드코트 골목 모서리, 그 칸의 목은 파란색). 정밀 계산과 표본 계산이 같은 자리를 골라 표본
    # 오차도 아니다. 사용자가 "파란 곳이 어떻게 가장 많이 지나가는 자리냐"고 짚었다.
    # 그래서 한 칸짜리 주장을 내리고, 목 상위 10% 칸이 이어진 **구역**을 그림과 함께 보여 준다.
    best_cut = np.nanquantile(integration, 1 - BEST_ZONE_QUANTILE)
    best_zones = _zones(valid & (integration >= best_cut), MIN_ZONE_CELLS)
    if best_zones:
        top = best_zones[0]
        x, y, xm, ym = _place(result, top)
        area = _area(result, len(top))
        issues.append(
            Issue(
                severity="low",
                title=(
                    f"{_zone_name(result, labels, x, y, top)} 일대가 가장 목이 좋은 곳입니다 — "
                    f"손님이 가장 쉽게 닿는 바닥 {_area_text(area, len(top))}"
                ),
                detail=(
                    "건물 어디서 출발해도 닿기 쉬워 사람이 자연히 모이는 구역입니다. 머물게 하는 매장"
                    "(카페·휴게)이나 손님을 끌어야 하는 핵심 매장이 맞습니다."
                ),
                x=x, y=y, x_m=xm, y_m=ym,
                cell_count=len(top),
                area_m2=area,
                cells=[(int(r), int(c)) for r, c in top],
                evidence=f"건물에서 찾아가기 가장 쉬운 {BEST_ZONE_QUANTILE * 100:.0f}% 바닥이 이어진 가장 큰 구역",
                kind="best_zone",
            )
        )

    # 6. 참고 — 목이 고른가 치우쳤는가.
    spread = result.integration_max / result.integration_mean if result.integration_mean > 0 else 0.0
    if spread >= 2.5:
        b = result.best_spots[0] if result.best_spots else None
        issues.append(
            Issue(
                severity="low",
                title="좋은 자리와 나쁜 자리의 차이가 큽니다",
                detail="목이 몇 곳에 치우쳐 있어 매장 위치에 따라 유불리가 크게 갈립니다. 임대료 차등의 근거가 됩니다.",
                x=b.x if b else 0.0, y=b.y if b else 0.0,
                x_m=b.x_m if b else None, y_m=b.y_m if b else None,
                evidence=f"최고/평균 목 {spread:.1f}배",
            )
        )

    order = {"high": 0, "medium": 1, "low": 2}
    issues.sort(key=lambda i: order[i.severity])
    # 7. 로봇 통로(2026-11-09, 물류센터 샘플) — 축척을 알 때만. 랙 사이 통로가 로봇 폭보다 좁은 구역과
    # 로봇이 들어갔다 되돌아 나와야 하는 막다른 끝. 좁은 구역은 바닥의 10% 넘게 있을 때만 카드로 —
    # 쇼핑몰처럼 통로가 다 넓은 도면에 벽 틈 몇 칸으로 「로봇」 카드가 뜨면 엉뚱하다.
    if result.robot_passable_pct is not None and result.aisle_width_grid is not None and result.analyzed_mask is not None:
        from app.services.floorplan_vga import ROBOT_AISLE_MIN_M

        narrow = result.analyzed_mask & (result.aisle_width_grid > 0) & (result.aisle_width_grid < ROBOT_AISLE_MIN_M)
        total = int(result.analyzed_mask.sum())
        if total and narrow.sum() / total >= 0.10:
            zones = sorted(_zones(narrow, min_cells=8), key=len, reverse=True)[:2]
            for coords in zones:
                x, y, xm, ym = _place(result, coords)
                issues.append(
                    Issue(
                        severity="medium",
                        title=(
                            f"{_side(result, x, y)} {_zone_label(result, labels, x, y, coords)}통로가 로봇 폭보다 좁습니다 — "
                            f"폭 {ROBOT_AISLE_MIN_M:.1f}m 미만 바닥 {_area_text(_area(result, len(coords)), len(coords))}"
                        ),
                        detail=(
                            "물류 로봇(폭 약 1.0m, 양쪽 여유 0.3m)이 못 들어가는 통로입니다. 랙 간격을 "
                            f"{ROBOT_AISLE_MIN_M:.1f}m 이상으로 벌리거나 사람 전용 통로로 두세요. 랙을 옮기고 다시 분석하면 바로 다시 잽니다."
                        ),
                        x=x, y=y, x_m=xm, y_m=ym,
                        cell_count=len(coords),
                        area_m2=_area(result, len(coords)),
                        cells=[(int(r), int(c)) for r, c in coords],
                        evidence=f"로봇이 지나갈 수 있는 바닥 {result.robot_passable_pct:.0f}%",
                        kind="robot_narrow",
                    )
                )
        # 막다른 끝 카드는 좁은 통로 카드가 있는 도면(랙이 있는 물류 도면)에만 — 쇼핑몰에서는 매장 입구가
        # 「끝」으로 잡혀 손님 흐름 얘기에 로봇 카드가 끼어든다.
        has_narrow = any(i.kind == "robot_narrow" for i in issues)
        if result.robot_dead_ends and has_narrow:
            first = result.robot_dead_ends[0]
            n = len(result.robot_dead_ends)
            issues.append(
                Issue(
                    severity="low",
                    title=f"로봇 통로의 막다른 끝 {n}곳 — 들어갔다 되돌아 나와야 합니다",
                    detail=(
                        "로봇이 통로 끝에서 돌아 나와야 해 교행이 막히고 시간이 듭니다. 끝을 옆 통로와 터 주거나 "
                        "일방통행으로 정하세요. 가장 좁은 끝이 "
                        f"{_side(result, first.x, first.y)}에 있습니다."
                    ),
                    x=first.x, y=first.y, x_m=first.x_m, y_m=first.y_m,
                    cell_count=len(result.robot_dead_end_cells),
                    area_m2=_area(result, len(result.robot_dead_end_cells)),
                    cells=list(result.robot_dead_end_cells),
                    evidence=f"막다른 끝 {n}곳 · 로봇 기준 폭 {ROBOT_AISLE_MIN_M:.1f}m",
                    kind="robot_dead_end",
                )
            )

    return issues
