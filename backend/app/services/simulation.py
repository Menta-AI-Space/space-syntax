"""Before/after road-network simulation (도로망 변화 시뮬레이션).

Virtual roads are spliced into the analyzed network and requested OSM ways can
be removed. The before run uses original segment indices; the after run may be
compacted by closures, so ``after_index_by_original`` preserves correspondence
when Integration and Choice deltas are assembled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.config import settings
from app.services.depthmapx_engine import compute_depthmapx_metrics
from app.services.graph_builder import _approx_dist_m, build_graph
from app.services.space_syntax import WALK, compute_metrics
from app.services.spatial_query import RoadSegment

# Virtual segments get negative osm_ids so no real OSM way can collide.
VIRTUAL_OSM_ID_BASE = -1

# Below this share of change a segment counts as "unchanged" for display.
CHANGED_THRESHOLD_PCT = 2.0

# Segments within this distance of a drawn/closed road form the "local impact"
# summary — the whole-radius mean dilutes a local intervention to ~0%.
LOCAL_IMPACT_RADIUS_M = 150.0

ROAD_TYPE_HIGHWAY = {
    "footway": "footway",
    "residential": "residential",
}


class SimulationError(Exception):
    pass


@dataclass
class VirtualRoad:
    coordinates: list[list[float]]  # [[lng, lat], ...]
    road_type: str = "footway"


@dataclass
class SegmentDelta:
    index: int
    osm_id: int
    highway: str
    coordinates: list[list[float]]
    integration_before: float
    integration_after: float
    choice_before: float
    choice_after: float
    integration_delta_pct: float | None
    choice_delta_pct: float | None
    changed: bool
    closed: bool = False
    # True when graph noding merged this segment away in exactly one of the
    # two runs, so before/after are not comparable for it.
    no_data: bool = False


@dataclass
class MetricSummary:
    display_before: float
    display_after: float
    change_pct: float | None


@dataclass
class NewRoadMetric:
    """After-run space syntax values of one drawn virtual road."""

    osm_id: int
    road_type: str
    length_m: float
    integration: float
    choice: float
    integration_top_pct: float | None


@dataclass
class SimulationResult:
    integration: MetricSummary
    choice: MetricSummary
    segments: list[SegmentDelta]
    virtual_segments: list[RoadSegment]
    improved_count: int
    harmed_count: int
    local_integration: MetricSummary | None = None
    local_choice: MetricSummary | None = None
    local_segment_count: int = 0
    new_roads: list[NewRoadMetric] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _approx_length_m(coordinates: list[list[float]]) -> float:
    total = 0.0
    for (lng1, lat1), (lng2, lat2) in zip(coordinates, coordinates[1:]):
        mlat = math.radians((lat1 + lat2) / 2)
        dx = (lng2 - lng1) * math.cos(mlat) * 111_320
        dy = (lat2 - lat1) * 111_320
        total += math.hypot(dx, dy)
    return total


def build_virtual_segments(roads: list[VirtualRoad]) -> list[RoadSegment]:
    """Turn user-drawn polylines into RoadSegment objects.

    Footways are walk-only; anything else joins both subnetworks. All
    virtual roads are at grade (layer 0) so graph_builder's geometric
    noding snaps them onto crossing existing roads — that snap is exactly
    what makes a drawn road change the network.
    """
    segments: list[RoadSegment] = []
    for i, road in enumerate(roads):
        coords = [c for c in road.coordinates if len(c) == 2]
        if len(coords) < 2:
            raise SimulationError(f"가상 도로 {i + 1}: 좌표가 2개 이상 필요합니다")
        length = _approx_length_m(coords)
        if length < 5:
            raise SimulationError(f"가상 도로 {i + 1}: 5m 이상이어야 합니다")
        if length > 2000:
            raise SimulationError(f"가상 도로 {i + 1}: 2km를 넘을 수 없습니다")
        highway = ROAD_TYPE_HIGHWAY.get(road.road_type, "footway")
        segments.append(
            RoadSegment(
                osm_id=VIRTUAL_OSM_ID_BASE - i,
                highway=highway,
                length_m=length,
                coordinates=coords,
                walkable=True,
                drivable=highway != "footway",
            )
        )
    return segments


def _walk_metrics(
    segments: list[RoadSegment],
    warnings: list[str],
    engine: str | None = None,
) -> tuple[dict[str, dict[int, float]], set[int]]:
    """Walk metrics from one explicit engine so before/after stay comparable.

    Returns ``(metrics, covered)``. ``covered`` holds the segment indices that
    survived graph noding; a 0.0 metric for an index outside it is a
    placeholder for "not measured", not a real value.
    """
    G, edge_map = build_graph(segments)
    covered = set(edge_map.keys())
    use_depthmapx = engine == "depthmapx" or (
        engine is None and settings.depthmapx_enabled
    )
    if use_depthmapx:
        depthmapx_metrics, depthmapx_error = compute_depthmapx_metrics(
            G, segments, modes=(WALK,)
        )
        if depthmapx_metrics is not None:
            return depthmapx_metrics[WALK], covered
        if engine == "depthmapx" or settings.depthmapx_strict:
            raise SimulationError(
                "변경 전후를 동일한 DepthmapX 엔진으로 계산할 수 없습니다: "
                f"{depthmapx_error or 'unknown error'}"
            )
        if depthmapx_error:
            warnings.append(
                f"DepthmapX 미사용 (Python 엔진으로 대체): {depthmapx_error}"
            )
    metrics, metric_warnings = compute_metrics(G, segments, modes=(WALK,))
    warnings.extend(metric_warnings)
    return metrics[WALK], covered


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    position = (len(sorted_values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    return sorted_values[lower] + (
        sorted_values[upper] - sorted_values[lower]
    ) * (position - lower)


def _display_summary(
    before: list[float], after: list[float]
) -> MetricSummary:
    """0–100 display values on a scale shared by both runs.

    Both runs are normalized against the same 98th-percentile ceiling so
    the before/after numbers stay comparable (normalizing each run
    against itself would erase the very change we're measuring).
    """
    combined = sorted(v for v in before + after if v > 0)
    ceiling = _percentile(combined, 0.98) if combined else 1.0
    if ceiling <= 0:
        ceiling = 1.0

    def display(values: list[float]) -> float:
        if not values:
            return 0.0
        mean = sum(values) / len(values)
        return round(min(mean / ceiling, 1.0) * 100, 1)

    d_before = display(before)
    d_after = display(after)
    change = None
    mean_before = sum(before) / len(before) if before else 0.0
    mean_after = sum(after) / len(after) if after else 0.0
    if mean_before > 1e-9:
        change = round((mean_after - mean_before) / mean_before * 100, 1)
    return MetricSummary(
        display_before=d_before, display_after=d_after, change_pct=change
    )


def _delta_pct(before: float, after: float) -> float | None:
    if before <= 1e-9:
        return None
    return round((after - before) / before * 100, 1)


def _local_summary(
    deltas: list["SegmentDelta"],
    virtual_segments: list[RoadSegment],
) -> tuple[MetricSummary | None, MetricSummary | None, int]:
    """Before/after summary over segments near the drawn/closed roads.

    The whole-radius mean dilutes a local intervention to ~0%, so this is the
    number that actually answers "how did the change affect its surroundings".
    """
    anchors = [tuple(c) for seg in virtual_segments for c in seg.coordinates]
    anchors += [tuple(c) for d in deltas if d.closed for c in d.coordinates]
    if not anchors:
        return None, None, 0

    int_before: list[float] = []
    int_after: list[float] = []
    cho_before: list[float] = []
    cho_after: list[float] = []
    for d in deltas:
        if d.no_data:
            continue
        dist = min(
            _approx_dist_m(tuple(coord), anchor)
            for coord in d.coordinates
            for anchor in anchors
        )
        if dist > LOCAL_IMPACT_RADIUS_M:
            continue
        int_before.append(d.integration_before)
        int_after.append(d.integration_after)
        cho_before.append(d.choice_before)
        cho_after.append(d.choice_after)

    if not int_before:
        return None, None, 0
    return (
        _display_summary(int_before, int_after),
        _display_summary(cho_before, cho_after),
        len(int_before),
    )


def _new_road_metrics(
    virtual_segments: list[RoadSegment],
    after_all: dict[str, dict[int, float]],
    after_covered: set[int],
    virtual_base: int,
    warnings: list[str],
) -> list[NewRoadMetric]:
    """After-run integration/choice of each drawn road, plus its network rank."""
    reference = [
        v
        for idx, v in after_all["integration"].items()
        if idx in after_covered and v > 0
    ]
    metrics: list[NewRoadMetric] = []
    for j, seg in enumerate(virtual_segments):
        after_idx = virtual_base + j
        if after_idx not in after_covered:
            warnings.append(
                f"가상 도로 {j + 1}번이 기존 도로와 합쳐져 자체 지표를 계산하지 못했습니다."
            )
            continue
        integration = after_all["integration"].get(after_idx, 0.0)
        choice = after_all["choice"].get(after_idx, 0.0)
        if integration <= 0:
            # Present in the graph but outside the main walkable component:
            # the drawn line neither crosses nor comes close enough to a road.
            warnings.append(
                # 숫자 뒤 조사는 숫자에 따라 달라져('1이', '2가') 자동으로 고를 수
                # 없다. '번'을 붙이면 언제나 '이'라 어색한 '이(가)'를 없앨 수 있다.
                f"가상 도로 {j + 1}번이 기존 도로망과 연결되지 않았습니다. "
                "기존 도로와 교차하거나 도로 위에서 시작/끝나도록 그려주세요."
            )
        top_pct = None
        if reference and integration > 0:
            higher = sum(1 for v in reference if v > integration)
            top_pct = round(100.0 * (higher + 1) / len(reference), 1)
        metrics.append(
            NewRoadMetric(
                osm_id=seg.osm_id,
                road_type=seg.highway,
                length_m=seg.length_m,
                integration=integration,
                choice=choice,
                integration_top_pct=top_pct,
            )
        )
    return metrics


def run_simulation(
    segments: list[RoadSegment],
    roads: list[VirtualRoad] | None = None,
    closed_osm_ids: list[int] | None = None,
    before_metrics: dict[str, dict[int, float]] | None = None,
    before_engine: str | None = None,
) -> SimulationResult:
    """Compute walk-mode metrics before/after adding roads and closing roads."""
    roads = roads or []
    requested_closed = set(closed_osm_ids or [])
    if not roads and not requested_closed:
        raise SimulationError("가상 도로 또는 폐쇄 도로가 없습니다")

    known_osm_ids = {segment.osm_id for segment in segments}
    closed = requested_closed & known_osm_ids
    missing_closed = requested_closed - known_osm_ids
    if requested_closed and not closed and not roads:
        raise SimulationError("폐쇄할 도로를 현재 분석 범위에서 찾을 수 없습니다")

    virtual_segments = build_virtual_segments(roads)
    warnings: list[str] = []
    if missing_closed:
        warnings.append(
            f"현재 분석 범위에서 찾지 못한 폐쇄 도로 ID {len(missing_closed)}개는 제외했습니다"
        )

    engine = before_engine
    # Cached baselines carry no graph-coverage info; None means "assume covered".
    before_covered: set[int] | None = None
    if before_metrics is not None:
        before = before_metrics
    else:
        engine = "depthmapx" if settings.depthmapx_enabled else "python"
        try:
            before, before_covered = _walk_metrics(segments, warnings, engine)
        except SimulationError:
            if engine != "depthmapx":
                raise
            engine = "python"
            warnings.append(
                "DepthmapX를 변경 전후에 동일하게 적용할 수 없어 Python 엔진으로 재계산했습니다."
            )
            before, before_covered = _walk_metrics(segments, warnings, engine)
    after_existing_segments: list[RoadSegment] = []
    after_index_by_original: dict[int, int] = {}
    for original_idx, seg in enumerate(segments):
        if seg.osm_id in closed:
            continue
        after_index_by_original[original_idx] = len(after_existing_segments)
        after_existing_segments.append(seg)

    if not after_existing_segments and not virtual_segments:
        raise SimulationError("폐쇄 후 분석 가능한 도로망이 없습니다")

    after_all, after_covered = _walk_metrics(
        after_existing_segments + virtual_segments,
        warnings,
        engine,
    )

    walkable_idx = [i for i, s in enumerate(segments) if s.walkable]

    deltas: list[SegmentDelta] = []
    improved = 0
    harmed = 0
    for i in walkable_idx:
        seg = segments[i]
        is_closed = seg.osm_id in closed
        after_idx = after_index_by_original.get(i)
        ib = before["integration"].get(i, 0.0)
        cb = before["choice"].get(i, 0.0)
        ia = after_all["integration"].get(after_idx, 0.0) if after_idx is not None else 0.0
        ca = after_all["choice"].get(after_idx, 0.0) if after_idx is not None else 0.0

        # Graph noding can merge a short segment into a junction so it vanishes
        # from exactly one run's graph (e.g. a virtual road splitting it into
        # sub-snap-grid pieces). Its 0.0 there means "not measured", not a real
        # collapse to zero — carry the measured side over instead of reporting
        # a spurious ±100% delta.
        no_data = False
        if not is_closed:
            if after_idx is not None and after_idx not in after_covered:
                ia, ca = ib, cb
                no_data = True
            elif before_covered is not None and i not in before_covered:
                ib, cb = ia, ca
                no_data = True

        if no_data:
            i_pct = None
            c_pct = None
            changed = False
        else:
            i_pct = _delta_pct(ib, ia)
            c_pct = _delta_pct(cb, ca)
            changed = (i_pct is not None and abs(i_pct) >= CHANGED_THRESHOLD_PCT) or (
                c_pct is not None and abs(c_pct) >= CHANGED_THRESHOLD_PCT
            )
            if i_pct is not None and i_pct >= CHANGED_THRESHOLD_PCT:
                improved += 1
            if i_pct is not None and i_pct <= -CHANGED_THRESHOLD_PCT:
                harmed += 1
        deltas.append(
            SegmentDelta(
                index=i,
                osm_id=seg.osm_id,
                highway=seg.highway,
                coordinates=seg.coordinates,
                integration_before=ib,
                integration_after=ia,
                choice_before=cb,
                choice_after=ca,
                integration_delta_pct=i_pct,
                choice_delta_pct=c_pct,
                changed=changed,
                closed=is_closed,
                no_data=no_data,
            )
        )

    int_before = [d.integration_before for d in deltas]
    int_after = [d.integration_after for d in deltas]
    cho_before = [d.choice_before for d in deltas]
    cho_after = [d.choice_after for d in deltas]

    local_integration, local_choice, local_count = _local_summary(
        deltas, virtual_segments
    )
    new_roads = _new_road_metrics(
        virtual_segments,
        after_all,
        after_covered,
        virtual_base=len(after_existing_segments),
        warnings=warnings,
    )

    return SimulationResult(
        integration=_display_summary(int_before, int_after),
        choice=_display_summary(cho_before, cho_after),
        segments=deltas,
        virtual_segments=virtual_segments,
        improved_count=improved,
        harmed_count=harmed,
        local_integration=local_integration,
        local_choice=local_choice,
        local_segment_count=local_count,
        new_roads=new_roads,
        warnings=warnings,
    )


HIGHWAY_LABELS = {
    "footway": "보행로",
    "residential": "주거지 도로",
    "service": "이면도로",
    "primary": "주간선도로",
    "secondary": "간선도로",
    "tertiary": "보조간선도로",
    "unclassified": "일반도로",
    "pedestrian": "보행자 전용",
    "path": "샛길",
    "steps": "계단",
    "living_street": "생활도로",
}


def impact_candidate_ids(result: SimulationResult, limit: int = 200) -> list[int]:
    """Unique OSM IDs with material integration change, strongest first."""
    candidates = [
        delta
        for delta in result.segments
        if delta.integration_delta_pct is not None
        and abs(delta.integration_delta_pct) >= CHANGED_THRESHOLD_PCT
    ]
    candidates.sort(
        key=lambda delta: abs(delta.integration_after - delta.integration_before),
        reverse=True,
    )
    return list(dict.fromkeys(delta.osm_id for delta in candidates))[:limit]


def _top_changed(
    result: SimulationResult,
    names: dict[int, str] | None,
    limit: int,
    *,
    improved: bool,
) -> list[dict]:
    names = names or {}
    threshold = CHANGED_THRESHOLD_PCT if improved else -CHANGED_THRESHOLD_PCT
    candidates = [
        delta
        for delta in result.segments
        if delta.integration_delta_pct is not None
        and (
            delta.integration_delta_pct >= threshold
            if improved
            else delta.integration_delta_pct <= threshold
        )
    ]
    candidates.sort(
        key=lambda delta: delta.integration_after - delta.integration_before,
        reverse=improved,
    )

    rows: list[dict] = []
    seen_labels: set[str] = set()
    for delta in candidates:
        name = names.get(delta.osm_id, "").strip()
        label = name or f"{HIGHWAY_LABELS.get(delta.highway, delta.highway)} (#{delta.osm_id})"
        if label in seen_labels:
            continue
        seen_labels.add(label)
        rows.append(
            {
                "label": label,
                "osm_id": delta.osm_id,
                "integration_delta_pct": delta.integration_delta_pct,
                "choice_delta_pct": delta.choice_delta_pct,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def top_improved(
    result: SimulationResult,
    names: dict[int, str] | None = None,
    limit: int = 5,
) -> list[dict]:
    """Most-improved existing segments, ranked by absolute integration gain."""
    return _top_changed(result, names, limit, improved=True)


def top_worsened(
    result: SimulationResult,
    names: dict[int, str] | None = None,
    limit: int = 5,
) -> list[dict]:
    """Most-worsened existing segments, ranked by absolute integration loss."""
    return _top_changed(result, names, limit, improved=False)


def rule_based_simulation_explanation(
    result: SimulationResult, top: list[dict]
) -> str:
    i_chg = result.integration.change_pct
    c_chg = result.choice.change_pct
    has_added = bool(result.virtual_segments)
    has_closed = any(delta.closed for delta in result.segments)
    if has_added and has_closed:
        action_label = "신규 연결·도로 폐쇄"
    elif has_added:
        action_label = "신규 연결"
    elif has_closed:
        action_label = "도로 폐쇄"
    else:
        action_label = "도로망 변경"

    local = result.local_integration
    has_local = local is not None and result.local_segment_count > 0
    meaningful_change = (
        abs(i_chg or 0) >= 1
        or abs(c_chg or 0) >= 1
        or (has_local and abs(local.change_pct or 0) >= 1)
        or result.improved_count
        or result.harmed_count
    )
    if not meaningful_change:
        return (
            f"{action_label} 시나리오는 주변 접근성에 큰 변화를 주지 않습니다. "
            "기존 경로와 겹치거나 새로운 지름길·단절을 만들지 못하는 위치일 수 있습니다."
        )

    sentences: list[str] = []
    if has_local:
        sentences.append(
            f"{action_label} 시나리오를 적용하면 변경 지점 주변 {LOCAL_IMPACT_RADIUS_M:.0f}m "
            f"도로 {result.local_segment_count}곳의 평균 접근성(Integration)은 "
            f"{local.display_before:.1f} → {local.display_after:.1f}"
            + (f"({local.change_pct:+.1f}%)" if local.change_pct is not None else "")
            + "로 변합니다."
        )
    else:
        sentences.append(f"{action_label} 시나리오의 영향입니다.")
    global_summary = result.integration
    if i_chg is not None:
        suffix = "입니다." if abs(i_chg) >= 1 else "로 거의 유지됩니다."
        sentences.append(
            f"반경 전체 평균 접근성은 {global_summary.display_before:.1f} → "
            f"{global_summary.display_after:.1f}({i_chg:+.1f}%){suffix}"
        )
    if c_chg is not None and abs(c_chg) >= 1:
        sentences.append(f"통과 동선 잠재력(Choice)은 평균 {c_chg:+.1f}% 변합니다.")

    counts = []
    if result.improved_count:
        counts.append(f"수혜 도로 {result.improved_count}곳")
    if result.harmed_count:
        counts.append(f"피해 도로 {result.harmed_count}곳")
    if counts:
        sentences.append("·".join(counts) + "이 확인됩니다.")

    for road in result.new_roads:
        if road.integration_top_pct is not None:
            sentences.append(
                f"신설 도로 자체의 접근성은 변경 후 도로망에서 상위 "
                f"{road.integration_top_pct:.0f}% 수준입니다."
            )
            break

    if top:
        best = top[0]
        text = f"가장 크게 좋아지는 곳은 {best['label']}"
        if best.get("integration_delta_pct") is not None:
            text += f"(접근성 {best['integration_delta_pct']:+.0f}%)"
        sentences.append(text + "입니다.")
    sentences.append("이는 보행 이동 잠재력의 구조적 변화이며, 실제 유동인구 증감을 보장하지는 않습니다.")
    return " ".join(sentences)
