"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api, type RadiusM, type SimulateResponse, type VirtualRoadRequest } from "@/lib/api";
import { reverseGeocodeLatLng, searchKoreanAddresses, type AddressSearchItem } from "@/lib/address";
import { HEAT_STOPS } from "@/lib/mapUtils";
import { DELTA_MIN_PCT, DELTA_STRONG_PCT, SIM_COLORS, deltaTone } from "@/lib/simDelta";
import { SimMap } from "@/components/map/SimMap";

interface SimulationPanelProps {
  center?: { lat: number; lng: number } | null;
  radius: RadiusM;
  closedOsmIds: number[];
  result: SimulateResponse | null;
  onClosedOsmIdsChange: (ids: number[]) => void;
  onResult: (result: SimulateResponse | null) => void;
}

const MAX_CLOSED_ROADS = 20;

// Ported from `simHeatGradient` in frontend/js/simulation.js.
function simHeatGradient(): string {
  return `linear-gradient(to right, ${HEAT_STOPS.map(([r, g, b]) => `rgb(${r}, ${g}, ${b})`).join(", ")})`;
}

const HIGHWAY_LABELS: Record<string, string> = {
  footway: "보행로",
  path: "보행로",
  pedestrian: "보행자거리",
  steps: "계단",
  residential: "주거 도로",
  service: "이면 도로",
  living_street: "생활 도로",
  primary: "주요 도로",
  secondary: "간선 도로",
  tertiary: "보조 간선",
  unclassified: "일반 도로",
  cycleway: "자전거 도로",
};

// 카카오 길찾기식 경로 입력: [출발, …경유, 도착] 슬롯을 순서대로 채우면 그
// 순서가 그대로 가상 도로의 폴리라인 좌표가 된다.
type Slot = { id: number; text: string; point: { lat: number; lng: number } | null };

function distanceM(a: { lat: number; lng: number }, b: { lat: number; lng: number }): number {
  const mPerDegLat = 111_320;
  const mPerDegLng = 111_320 * Math.cos((a.lat * Math.PI) / 180);
  return Math.hypot((a.lat - b.lat) * mPerDegLat, (a.lng - b.lng) * mPerDegLng);
}

function centroidOfPoints(points: { lat: number; lng: number }[]): { lat: number; lng: number } | null {
  if (!points.length) return null;
  let latSum = 0;
  let lngSum = 0;
  for (const p of points) {
    latSum += p.lat;
    lngSum += p.lng;
  }
  return { lat: latSum / points.length, lng: lngSum / points.length };
}

function fmtPct(value?: number | null) {
  if (value === null || value === undefined) return "—";
  // One decimal below 10% so small-but-real changes don't collapse to "+0%".
  const rounded = Math.abs(value) < 10 ? value.toFixed(1) : String(Math.round(value));
  return `${value >= 0 ? "+" : ""}${rounded}%`;
}

// 지도 색과 같은 기준으로 칠한다(lib/simDelta). 예전에는 0%도 값 없음도
// 초록(positive)이라, 지도가 회색으로 "변화 없음"이라 한 칸의 숫자가 옆에서는
// 좋아졌다고 말했다.
const TONE_CLASS: Record<string, string> = { up: "positive", down: "negative", flat: "neutral" };

function MetricCard({ label, metric }: { label: string; metric: SimulateResponse["integration"] }) {
  return (
    <div className="sim-next-metric">
      <span>{label}</span>
      <strong>{metric.display_before.toFixed(1)} → {metric.display_after.toFixed(1)}</strong>
      <em className={TONE_CLASS[deltaTone(metric.change_pct)]}>{fmtPct(metric.change_pct)}</em>
    </div>
  );
}

type SimView = "delta" | "before" | "after";

// Ported from `simLegendDeltaHtml`/`simLegendIntegrationHtml` in
// frontend/js/simulation.js — textual legend for the SimMap overlay colors.
function SimLegend({ result, view }: { result: SimulateResponse | null; view: SimView }) {
  // 결과가 나오기 전에는 범례를 띄우지 않는다. 설명할 색이 아직 없고,
  // 왼쪽 단계 패널이 이미 다음 행동을 안내하고 있어 지도만 가린다.
  if (!result) return null;
  if (view === "delta") {
    return (
      <div className="sim-legend-panel">
        <p className="sim-legend-heading">접근성 변화</p>
        <ul className="sim-legend-items">
          <li className="sim-legend-item"><span className="sim-swatch" style={{ background: SIM_COLORS.improvedStrong }} /><span>크게 개선 (+{DELTA_STRONG_PCT}% 이상)</span></li>
          <li className="sim-legend-item"><span className="sim-swatch" style={{ background: SIM_COLORS.improved }} /><span>개선 (+{DELTA_MIN_PCT}% 이상)</span></li>
          <li className="sim-legend-item"><span className="sim-swatch" style={{ background: SIM_COLORS.unchanged }} /><span>변화 없음</span></li>
          <li className="sim-legend-item"><span className="sim-swatch" style={{ background: SIM_COLORS.worsened }} /><span>악화 (−{DELTA_MIN_PCT}% 이하)</span></li>
          <li className="sim-legend-item"><span className="sim-swatch sim-swatch--dash" style={{ color: SIM_COLORS.virtual }} /><span>신설 도로</span></li>
        </ul>
      </div>
    );
  }
  const phaseLabel = view === "before" ? "신설 전" : "신설 후";
  return (
    <div className="sim-legend-panel">
      <p className="sim-legend-heading">접근성 — {phaseLabel}</p>
      <div className="sim-legend-scale">
        <span className="sim-legend-scale-label">낮음</span>
        <div className="sim-legend-scale-bar" style={{ background: simHeatGradient() }} />
        <span className="sim-legend-scale-label" style={{ textAlign: "right" }}>높음</span>
      </div>
      {view === "after" && <p className="sim-legend-note">신설 도로는 보라색 점선으로 표시됩니다.</p>}
    </div>
  );
}

// 신설도로 시뮬레이션 패널. 경로 입력은 카카오 길찾기처럼 출발·경유·도착
// 슬롯으로 받고(주소 검색 또는 지도 클릭), 곡선 도로는 경유지를 여러 개
// 찍어 폴리라인으로 근사한다. 기존 도로 폐쇄는 별도의 「지도에서 선택」
// 모드에서 회색 도로를 클릭해 고른다. 기준 도로망(context)은 분석 위치나
// 첫 경로 지점에서 자동으로 불러오므로 별도의 위치 설정 단계가 없다.
export function SimulationPanel({ center, radius, closedOsmIds, result, onClosedOsmIdsChange, onResult }: SimulationPanelProps) {
  const [slots, setSlots] = useState<Slot[]>([
    { id: 0, text: "", point: null },
    { id: 1, text: "", point: null },
  ]);
  const nextSlotIdRef = useRef(2);
  const slotsRef = useRef(slots);
  slotsRef.current = slots;
  const [suggest, setSuggest] = useState<{ slotId: number; items: AddressSearchItem[] } | null>(null);
  const suggestRequestRef = useRef(0);
  const [roadType, setRoadType] = useState<"footway" | "residential">("footway");
  const [closureMode, setClosureMode] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [view, setView] = useState<SimView>("delta");
  const [context, setContext] = useState<{ center: { lat: number; lng: number }; radiusM: RadiusM; segments: { type: string; features: Array<{ geometry?: { coordinates?: unknown }; properties?: Record<string, unknown> }> }; segmentCount: number } | null>(null);
  const [contextLoading, setContextLoading] = useState(false);
  // 왼쪽 패널 하나가 입력과 결과를 오간다: 결과가 도착하면 결과 뷰,
  // 「조건 수정」을 누르면 입력 뷰(결과·지도 오버레이는 유지).
  const [editing, setEditing] = useState(false);
  const requestVersionRef = useRef(0);

  useEffect(() => {
    if (result) setEditing(false);
  }, [result]);

  const routePoints = useMemo(() => slots.filter((s) => s.point).map((s) => s.point!), [slots]);
  const routeKey = routePoints.map((p) => `${p.lat},${p.lng}`).join("|");

  function invalidateResult() {
    requestVersionRef.current += 1;
    setLoading(false);
    onResult(null);
  }

  // 입력(경로·폐쇄 목록·유형)이 바뀌면 이전 결과를 무효화한다. 분석 위치·
  // 반경 변경은 AppShell이 simulationResult를 직접 비우므로 여기서 context
  // 변경까지 무효화하면 안 된다 — runSimulation이 경로 중심으로 context를
  // 다시 불러올 때 방금 받은 결과까지 버리게 된다.
  useEffect(() => {
    requestVersionRef.current += 1;
    setLoading(false);
    onResult(null);
  }, [closedOsmIds, routeKey, roadType, onResult]);

  // 분석 탭에서 위치가 이미 정해져 있으면 그 주변 도로망을 조용히 불러온다
  // (이전의 「현재 분석 위치 사용」 버튼을 자동화한 것).
  useEffect(() => {
    if (context || contextLoading || !center) return;
    void fetchContext({ lat: center.lat, lng: center.lng, radius_m: radius });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [center?.lat, center?.lng]);

  async function fetchContext(payload: { lat: number; lng: number; radius_m: RadiusM }) {
    setContextLoading(true);
    try {
      const data = await api.simulationContext(payload);
      setContext({ center: data.center, radiusM: data.radius_m as RadiusM, segments: data.segments, segmentCount: data.segment_count });
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : "주변 도로망을 불러오지 못했습니다.");
      return null;
    } finally {
      setContextLoading(false);
    }
  }

  // 경로 지점이 현재 도로망 밖이면(또는 아직 없으면) 그 지점 중심으로 다시
  // 불러온다 — 위치 설정 단계를 없앤 대신 여기서 자동으로 따라간다.
  function ensureContextFor(lat: number, lng: number) {
    if (contextLoading) return;
    if (context && distanceM(context.center, { lat, lng }) <= context.radiusM) return;
    void fetchContext({ lat, lng, radius_m: context?.radiusM ?? radius });
  }

  function setSlotPoint(id: number, lat: number, lng: number, label?: string) {
    setSlots((cur) => cur.map((s) => (s.id === id ? { ...s, point: { lat, lng }, text: label ?? `${lat.toFixed(5)}, ${lng.toFixed(5)}` } : s)));
    setSuggest(null);
    setError("");
    if (!label) {
      reverseGeocodeLatLng(lat, lng)
        .then((addr) => {
          if (!addr) return;
          setSlots((cur) => cur.map((s) => (s.id === id && s.point?.lat === lat && s.point?.lng === lng ? { ...s, text: addr } : s)));
        })
        .catch(() => {});
    }
    ensureContextFor(lat, lng);
  }

  // 지도 클릭: 빈 슬롯을 위에서부터 채우고, 다 찼으면 도착 앞에 경유지로
  // 추가한다 — 클릭만으로 곡선 경로를 이어 그릴 수 있다.
  function handleMapPoint(lat: number, lng: number) {
    const empty = slotsRef.current.find((s) => !s.point);
    if (empty) {
      setSlotPoint(empty.id, lat, lng);
      return;
    }
    const id = nextSlotIdRef.current++;
    setSlots((cur) => [...cur.slice(0, -1), { id, text: "", point: null }, cur[cur.length - 1]]);
    setSlotPoint(id, lat, lng);
  }

  function addWaypointRow() {
    const id = nextSlotIdRef.current++;
    setSlots((cur) => [...cur.slice(0, -1), { id, text: "", point: null }, cur[cur.length - 1]]);
  }

  // 경유 행은 삭제, 출발·도착 행은 비우기만 한다(행 자체는 항상 남는다).
  function clearOrRemoveSlot(id: number) {
    setSlots((cur) => {
      const idx = cur.findIndex((s) => s.id === id);
      if (idx > 0 && idx < cur.length - 1) return cur.filter((s) => s.id !== id);
      return cur.map((s) => (s.id === id ? { ...s, text: "", point: null } : s));
    });
    setSuggest((cur) => (cur?.slotId === id ? null : cur));
  }

  async function handleSlotTextChange(id: number, value: string) {
    setSlots((cur) => cur.map((s) => (s.id === id ? { ...s, text: value, point: null } : s)));
    const requestId = ++suggestRequestRef.current;
    const q = value.trim();
    if (q.length < 2) {
      setSuggest(null);
      return;
    }
    try {
      const items = await searchKoreanAddresses(q, 6);
      if (requestId === suggestRequestRef.current) setSuggest(items.length ? { slotId: id, items } : null);
    } catch {
      if (requestId === suggestRequestRef.current) setSuggest(null);
    }
  }

  function handleToggleClosedRoad(osmId: number) {
    if (closedOsmIds.includes(osmId)) {
      onClosedOsmIdsChange(closedOsmIds.filter((id) => id !== osmId));
      return;
    }
    if (closedOsmIds.length >= MAX_CLOSED_ROADS) {
      setError(`폐쇄 도로는 최대 ${MAX_CLOSED_ROADS}개까지 선택할 수 있습니다.`);
      return;
    }
    setError("");
    onClosedOsmIdsChange([...closedOsmIds, osmId]);
  }

  function closedRoadLabel(osmId: number): string {
    const feature = context?.segments.features.find((f) => Number(f.properties?.osm_id) === osmId);
    if (!feature?.properties) return `#${osmId}`;
    const type = HIGHWAY_LABELS[String(feature.properties.highway)] ?? "도로";
    const length = Number(feature.properties.length_m);
    return Number.isFinite(length) ? `${type} ${Math.round(length)}m` : type;
  }

  function handleClearAll() {
    setSlots([
      { id: nextSlotIdRef.current++, text: "", point: null },
      { id: nextSlotIdRef.current++, text: "", point: null },
    ]);
    setSuggest(null);
    setClosureMode(false);
    onClosedOsmIdsChange([]);
    setError("");
    invalidateResult();
  }

  async function runSimulation() {
    const points = routePoints;
    if (points.length === 1) {
      setError("도착점을 지정하세요.");
      return;
    }
    const roads: VirtualRoadRequest[] = points.length >= 2
      ? [{ road_type: roadType, coordinates: points.map((p) => [p.lng, p.lat] as [number, number]) }]
      : [];
    if (roads.length === 0 && closedOsmIds.length === 0) {
      invalidateResult();
      setError("신설 도로를 그리거나 폐쇄할 도로를 선택하세요.");
      return;
    }

    let effectiveCenter = context?.center ?? null;
    let radiusForRequest: RadiusM = context?.radiusM ?? radius;
    if (points.length >= 2) {
      // 시뮬레이션은 항상 경로 중심에서 돈다 — 컨텍스트가 분석 위치나
      // 마지막 클릭 지점에 중심을 두고 있으면 출발지 쪽이 반경 밖으로
      // 벗어나 그 주변 도로가 계산·색칠에서 통째로 빠진다.
      const centroid = centroidOfPoints(points)!;
      const span = Math.max(...points.map((p) => distanceM(centroid, p)));
      const desiredRadius: RadiusM = span > radiusForRequest * 0.8 ? 1000 : radiusForRequest;
      if (!effectiveCenter || desiredRadius !== radiusForRequest || distanceM(effectiveCenter, centroid) > 50) {
        const data = await fetchContext({ lat: centroid.lat, lng: centroid.lng, radius_m: desiredRadius });
        if (!data) return;
        effectiveCenter = data.center;
        radiusForRequest = data.radius_m as RadiusM;
      }
    } else if (!effectiveCenter) {
      setError("먼저 출발·도착점을 지정하세요.");
      return;
    }

    const requestVersion = ++requestVersionRef.current;
    setLoading(true);
    setError("");
    try {
      const data = await api.simulate({
        lat: effectiveCenter.lat,
        lng: effectiveCenter.lng,
        radius_m: radiusForRequest,
        roads,
        closed_osm_ids: closedOsmIds,
      });
      if (requestVersion === requestVersionRef.current) {
        onResult(data);
        setView("delta");
      }
    } catch (err) {
      if (requestVersion === requestVersionRef.current) {
        onResult(null);
        setError(err instanceof Error ? err.message : "시뮬레이션 요청에 실패했습니다.");
      }
    } finally {
      if (requestVersion === requestVersionRef.current) setLoading(false);
    }
  }

  const showResults = Boolean(result) && !editing;

  return (
    <div className="sim-fullbleed">
      {/* 왼쪽 패널 하나 — 입력과 결과를 전환 (오른쪽 패널 없음). */}
      <aside className="app-sidebar app-sidebar--floating sim-controls">
      <div className="sim-next-head">
        <div>
          <span className="status-pill">simulation</span>
          <h3>도로 변화 전후 비교</h3>
        </div>
      </div>

      <div className={showResults ? "hidden" : ""}>
      {/* 1단계 — 신설 도로 경로 (출발 → 경유 → 도착). */}
      <section className="sim-step">
        <p className="sim-step-title">
          <span className={`sim-step-num${routePoints.length >= 2 ? " sim-step-num--done" : ""}`}>1</span>
          신설 도로 그리기
        </p>
        <div className="sim-route">
          {slots.map((s, i) => {
            const role = i === 0 ? "start" : i === slots.length - 1 ? "end" : "via";
            return (
              <div className="sim-route-row" key={s.id}>
                <span className={`sim-route-badge sim-route-badge--${role}`}>
                  {role === "start" ? "출발" : role === "end" ? "도착" : "경유"}
                </span>
                <div className="address-autocomplete-wrap">
                  <input
                    className="text-input"
                    value={s.text}
                    aria-label={role === "start" ? "출발점" : role === "end" ? "도착점" : `경유지 ${i}`}
                    placeholder="주소 검색 또는 지도 클릭"
                    onChange={(e) => void handleSlotTextChange(s.id, e.target.value)}
                  />
                  {suggest?.slotId === s.id && (
                    <ul className="address-autocomplete-results">
                      {suggest.items.map((item) => (
                        <li key={`${item.lat}-${item.lng}-${item.address}`}>
                          <button type="button" className="address-autocomplete-item" onClick={() => setSlotPoint(s.id, item.lat, item.lng, item.address)}>
                            {item.source === "place" ? "가게" : "주소"} · {item.address}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                {(role === "via" || s.point || s.text) && (
                  <button type="button" className="sim-waypoint-remove" aria-label="지점 삭제" onClick={() => clearOrRemoveSlot(s.id)}>✕</button>
                )}
              </div>
            );
          })}
        </div>
        <div className="sim-draw-controls">
          <button type="button" className="sim-btn" onClick={addWaypointRow}>+ 경유지</button>
          <label className="sim-road-type-label">
            유형
            <select className="text-input sim-road-type-select" value={roadType} onChange={(e) => setRoadType(e.target.value as "footway" | "residential")}>
              <option value="footway">보행로</option>
              <option value="residential">차량 도로</option>
            </select>
          </label>
          <button type="button" className="sim-btn" onClick={handleClearAll}>초기화</button>
        </div>
        <p className="sim-location-hint">지도를 클릭하면 빈 칸부터 순서대로 찍힙니다. 곡선 도로는 경유지를 여러 개 추가하세요.</p>
      </section>

      {/* 2단계 — 기존 도로 폐쇄 (선택 사항). */}
      <section className="sim-step">
        <p className="sim-step-title">
          <span className={`sim-step-num${closedOsmIds.length > 0 ? " sim-step-num--done" : ""}`}>2</span>
          도로 폐쇄 <span className="sim-optional">(선택)</span>
        </p>
        <button
          type="button"
          className={`sim-btn${closureMode ? " sim-btn--active" : ""}`}
          onClick={() => setClosureMode((v) => !v)}
        >
          {closureMode ? "선택 끝내기" : "지도에서 폐쇄할 도로 선택"}
        </button>
        {closureMode && (
          <p className="sim-location-hint">
            {context ? "회색 도로를 클릭해 선택·해제하세요." : "먼저 출발점을 지정하면 주변 도로가 표시됩니다."}
          </p>
        )}
        {closedOsmIds.length > 0 && (
          <ul className="sim-closed-list">
            {closedOsmIds.map((id) => (
              <li className="sim-closed-chip" key={id}>
                <span>{closedRoadLabel(id)}</span>
                <button type="button" aria-label="폐쇄 해제" onClick={() => handleToggleClosedRoad(id)}>✕</button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* 3단계 — 실행 · 전후 비교. */}
      <section className="sim-step">
        <p className="sim-step-title">
          <span className={`sim-step-num${result ? " sim-step-num--done" : ""}`}>3</span>
          전후 비교
        </p>
        <button
          className="primary-button primary-button--compact sim-run-button"
          type="button"
          onClick={() => { void runSimulation(); }}
          disabled={loading}
        >
          {loading ? "계산 중..." : result ? "다시 실행" : "시뮬레이션 실행"}
        </button>
        {loading && (
          <p className="status status-banner status-banner--loading sim-run-status">접근성을 다시 계산하는 중…</p>
        )}

        {error && <p className="status-banner status-banner--error">{error}</p>}
      </section>
      </div>

      {/* 결과 뷰 — 같은 왼쪽 패널에서 입력을 대신해 표시된다. */}
      <div className={showResults ? "panel-results" : "hidden"}>
        <div className="panel-results-bar">
          <button type="button" className="sim-btn" onClick={() => setEditing(true)}>← 조건 수정</button>
          <button type="button" className="sim-btn" disabled={loading} onClick={() => { void runSimulation(); }}>
            {loading ? "계산 중..." : "다시 실행"}
          </button>
        </div>
        <div className="sim-view-toggles" role="group" aria-label="시뮬레이션 보기">
          {(["delta", "before", "after"] as SimView[]).map((v) => (
            <button
              key={v}
              type="button"
              className={`sim-btn${view === v ? " sim-btn--active" : ""}`}
              onClick={() => setView(v)}
            >
              {v === "delta" ? "변화" : v === "before" ? "신설 전" : "신설 후"}
            </button>
          ))}
        </div>
        {error && <p className="status-banner status-banner--error">{error}</p>}
        {result && (
          <div className="sim-next-result">
            {result.warnings && result.warnings.length > 0 && (
              <div className="warnings-banner">
                {result.warnings.map((w, i) => <div key={i}>{w}</div>)}
              </div>
            )}
            {result.local_integration && (
              <div className="sim-next-impact">
                <h4>변경 지점 주변 {result.local_radius_m ?? 150}m — 도로 {(result.local_segment_count ?? 0).toLocaleString()}개</h4>
                <div className="sim-next-metrics">
                  <MetricCard label="찾아오기 쉬운 정도 (주변)" metric={result.local_integration} />
                  {result.local_choice && <MetricCard label="자주 지나는 길 (주변)" metric={result.local_choice} />}
                </div>
              </div>
            )}
            <div className="sim-next-impact">
              <h4>반경 전체 평균</h4>
              <div className="sim-next-metrics">
                <MetricCard label="찾아오기 쉬운 정도 (전체)" metric={result.integration} />
                <MetricCard label="자주 지나는 길 (전체)" metric={result.choice} />
              </div>
            </div>
            {(result.new_roads?.length ?? 0) > 0 && (
              <div className="sim-next-impact">
                <h4>신설 도로 자체 지표</h4>
                <ul>
                  {result.new_roads!.map((road, i) => (
                    <li key={road.osm_id}>
                      <span>가상 도로 {i + 1} · {road.road_type === "footway" ? "보행로" : "차량 도로"} {Math.round(road.length_m)}m</span>
                      <strong>{road.integration_top_pct != null ? `접근성 상위 ${Math.round(road.integration_top_pct)}%` : "—"}</strong>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <p className="site-panel-meta">
              수혜 도로 {result.improved_count.toLocaleString()}개 · 피해 도로 {result.harmed_count.toLocaleString()}개
            </p>
            {result.explanation?.text && <p>{result.explanation.text}</p>}
            <ImpactList title="수혜 도로" items={result.top_improved} empty="눈에 띄게 좋아지는 도로가 없습니다." />
            <ImpactList title="피해 도로" items={result.top_worsened} empty="눈에 띄게 나빠지는 도로가 없습니다." />
          </div>
        )}
      </div>
      </aside>

      {/* 지도를 전체 배경으로 꽉 채운다 (풀블리드) — 입지 분석과 동일.
          closureMode가 아닐 때는 지도 클릭이 경로 지점을 찍고(기존 도로는
          클릭 불가), closureMode에서는 회색 도로 클릭이 폐쇄를 토글한다. */}
      <div className="map-stage sim-map-stage">
        <SimMap
          context={context}
          drawing={!closureMode}
          draftPoints={routePoints}
          onAddWaypoint={handleMapPoint}
          onReposition={() => {}}
          closedOsmIds={closedOsmIds}
          onToggleClosedRoad={handleToggleClosedRoad}
          result={result}
          view={view}
        />
        <div className="map-overlay-bottom">
          <SimLegend result={result} view={view} />
        </div>
      </div>

    </div>
  );
}

function ImpactList({ title, items, empty }: { title: string; items: SimulateResponse["top_improved"]; empty: string }) {
  return (
    <div className="sim-next-impact">
      <h4>{title}</h4>
      {items.length === 0 ? <p className="site-panel-meta">{empty}</p> : (
        <ul>
          {items.map((item) => (
            <li key={`${title}-${item.osm_id}`}>
              <span>{item.label}</span>
              <strong className={TONE_CLASS[deltaTone(item.integration_delta_pct)]}>{fmtPct(item.integration_delta_pct)}</strong>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
