import { useEffect, useRef, useState } from "react";
import type { AnalyzeResponse, SimulateResponse } from "@/lib/api";
import { loadKakaoMaps } from "@/lib/kakao";
import { METRIC_PALETTE, MAP_OVERLAY_COLORS, metricColor, rampColors, valueExtent, propKey, geoJsonToKakaoPath } from "@/lib/mapUtils";
import {
  REACH_MODE_LABELS,
  formatDistance,
  reachByMode,
  useReachState,
  type ModeReach,
  type ReachMode,
} from "@/lib/reachCircle";
import { clearMetricLayer, publishMetricLayer, useMetricLayer } from "@/components/map/legendStore";

type KakaoMapInstance = {
  setCenter: (latlng: unknown) => void;
  setBounds: (bounds: { isEmpty: () => boolean }) => void;
  relayout?: () => void;
};

type KakaoMarker = { setMap: (map: KakaoMapInstance | null) => void };
type KakaoPolyline = {
  setMap: (map: KakaoMapInstance | null) => void;
  setOptions?: (options: Record<string, unknown>) => void;
};
type ManagedRoadPolyline = {
  polyline: KakaoPolyline;
  osmId: number;
  baseColor: string;
  clickHandler?: () => void;
};
type KakaoOverlay = { setMap: (map: KakaoMapInstance | null) => void };

function clearRoadPolylines(entries: ManagedRoadPolyline[]) {
  entries.forEach(({ polyline, clickHandler }) => {
    if (clickHandler) window.kakao.maps.event.removeListener(polyline, "click", clickHandler);
    polyline.setMap(null);
  });
}

const DEFAULT_CENTER = { lat: 37.5781, lng: 126.9351 };

/** Ported from `renderSegments`'s polyline click handler in frontend/js/map.js
 * (which shows this info via a blocking `alert(...)`). Here it is surfaced
 * through the `onRoadClick` callback instead so the caller can render it
 * without blocking the page — same fields, non-blocking presentation. */
export interface RoadClickInfo {
  osmId: number;
  highway: string;
  lengthM: number;
  metric: string;
  metricLabel: string;
  metricValue: number | string;
}

// Ported from `CANDIDATE_MARKER_COLORS` in frontend/js/map.js.
const CANDIDATE_MARKER_COLORS: Record<string, string> = {
  a: "#2563eb",
  b: "#059669",
  c: "#d97706",
  d: "#7c3aed",
  e: "#db2777",
};

export interface WatchlistMapMarker {
  id: string;
  lat: number;
  lng: number;
  address: string;
}

export interface CandidateMapMarker {
  id: string;
  lat: number;
  lng: number;
  address: string;
  label: string; // "A".."E" or "★"
  dimmed?: boolean;
}

function escapeMarkerHtml(text: string): string {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

interface KakaoMapProps {
  metric: string;
  mode?: string;
  analysisData?: AnalyzeResponse | null;
  simulationData?: SimulateResponse | null;
  selectedClosedOsmIds?: number[];
  onMapClick?: (lat: number, lng: number) => void;
  onRoadClick?: (info: RoadClickInfo) => void;
  showRoadLayer?: boolean;
  watchlistMarkers?: WatchlistMapMarker[];
  candidateMarkers?: CandidateMapMarker[];
  focusBoundsTargets?: { lat: number; lng: number }[] | null;
}

export function KakaoMap({
  metric,
  mode = "walk",
  analysisData,
  simulationData,
  selectedClosedOsmIds = [],
  onMapClick,
  onRoadClick,
  showRoadLayer = true,
  watchlistMarkers = [],
  candidateMarkers = [],
  focusBoundsTargets = null,
}: KakaoMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<KakaoMapInstance | null>(null);
  const polylinesRef = useRef<ManagedRoadPolyline[]>([]);
  const simulationPolylinesRef = useRef<KakaoPolyline[]>([]);
  const circleRef = useRef<{ setMap: (map: KakaoMapInstance | null) => void } | null>(null);
  // 이동수단별 도달권 원 + 이름표. 슬라이더 드래그마다 새로 만들지 않고
  // 이동수단을 키로 들고 있다가 반경·위치만 갱신한다(드래그가 매끄럽도록).
  const reachRingsRef = useRef<
    Map<
      ReachMode,
      {
        circle: {
          setMap: (map: KakaoMapInstance | null) => void;
          setRadius: (r: number) => void;
          setPosition: (latlng: unknown) => void;
        };
        label: { setMap: (map: KakaoMapInstance | null) => void; setPosition: (latlng: unknown) => void };
        labelEl: HTMLElement;
      }
    >
  >(new Map());
  // 위치·이동수단이 바뀔 때만 지도를 다시 맞춘다 — 슬라이더 드래그(분값)마다
  // 지도가 확대/축소되며 튀지 않도록. 마지막으로 맞춘 컨텍스트 키를 기억한다.
  const reachFitKeyRef = useRef<string | null>(null);
  const reach = useReachState();
  const markerRef = useRef<KakaoMarker | null>(null);
  const watchlistOverlaysRef = useRef<KakaoOverlay[]>([]);
  const candidateOverlaysRef = useRef<KakaoOverlay[]>([]);
  const infoWindowRef = useRef<{ setContent: (html: string) => void; setPosition: (pos: unknown) => void; open: (map: KakaoMapInstance) => void } | null>(null);
  const onMapClickRef = useRef(onMapClick);
  const onRoadClickRef = useRef(onRoadClick);
  const mapClickCleanupRef = useRef<(() => void) | null>(null);
  const [ready, setReady] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mapAttempt, setMapAttempt] = useState(0);

  onMapClickRef.current = onMapClick;
  onRoadClickRef.current = onRoadClick;

  useEffect(() => {
    let cancelled = false;
    const container = containerRef.current;
    if (!container) return;
    // 정리 시점에 ref.current를 다시 읽지 않도록 지금 잡아 둔다. 이 Map은
    // 재할당하지 않고 내용만 바꾸므로 같은 객체를 계속 가리킨다.
    const reachRings = reachRingsRef.current;

    loadKakaoMaps()
      .then(() => {
        if (cancelled || !containerRef.current) return;

        const map = new window.kakao.maps.Map(containerRef.current, {
          center: new window.kakao.maps.LatLng(DEFAULT_CENTER.lat, DEFAULT_CENTER.lng),
          level: 5,
        });
        mapRef.current = map;

        const mapClickHandler = (mouseEvent: { latLng: { getLat: () => number; getLng: () => number } }) => {
          const lat = mouseEvent.latLng.getLat();
          const lng = mouseEvent.latLng.getLng();
          if (markerRef.current) markerRef.current.setMap(null);
          markerRef.current = new window.kakao.maps.Marker({
            position: mouseEvent.latLng,
            map,
          });
          onMapClickRef.current?.(lat, lng);
        };
        window.kakao.maps.event.addListener(map, "click", mapClickHandler);
        mapClickCleanupRef.current = () => window.kakao.maps.event.removeListener(map, "click", mapClickHandler);

        requestAnimationFrame(() => {
          mapRef.current?.relayout?.();
          setReady(true);
        });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // 개발용 문장(「NEXT_PUBLIC_KAKAO_JS_KEY와 localhost:3000 도메인 설정을 확인하세요」)이
        // 첫 화면 지도 자리에 그대로 떴고 다시 부를 버튼도 없어 새로고침밖에 없었다 — 지도 탭
        // (StreetGradeMap)과 같은 사용자 문장 + 「다시 불러오기」 (2026-11-07 UX 자동 개선).
        console.error(err);
        setLoadError("지도를 불러오지 못했습니다 — 인터넷 연결을 확인하고 다시 불러와 주세요.");
      });

    return () => {
      cancelled = true;
      mapClickCleanupRef.current?.();
      mapClickCleanupRef.current = null;
      clearRoadPolylines(polylinesRef.current);
      polylinesRef.current = [];
      simulationPolylinesRef.current.forEach((p) => p.setMap(null));
      simulationPolylinesRef.current = [];
      circleRef.current?.setMap(null);
      circleRef.current = null;
      reachRings.forEach(({ circle, label }) => {
        circle.setMap(null);
        label.setMap(null);
      });
      reachRings.clear();
      reachFitKeyRef.current = null;
      clearMetricLayer();
      markerRef.current?.setMap(null);
      markerRef.current = null;
      watchlistOverlaysRef.current.forEach((o) => o.setMap(null));
      watchlistOverlaysRef.current = [];
      candidateOverlaysRef.current.forEach((o) => o.setMap(null));
      candidateOverlaysRef.current = [];
      mapRef.current = null;
    };
  }, [mapAttempt]);

  // 도달권 원 — 이동수단(도보·자전거·오토바이·차량)을 **한 번에** 그린다.
  // 사용자 지적: "도보랑 자전거랑 차랑 오토바이가 다르잖아. 그것도 같이 잘
  // 표시해줘야 하는 거고. 지금은 도보 몇 미터밖에 안 되니까." — 도보 원 하나만
  // 보여 주면 차량 상권을 판단할 수 없다.
  //
  // 실측(백엔드 도로망)인 모드는 실선 + 옅은 채움, 나머지(우회계수 근사)는
  // 점선 + 채움 없음으로 구분한다. 색은 전부 같은 초록 계열이라 '초록 = 도달권'
  // 이라는 뜻이 흐려지지 않고, 어느 원이 어느 이동수단인지는 원 위 이름표와
  // 범례가 말해 준다.
  useEffect(() => {
    if (!ready || !mapRef.current || !reach) {
      reachRingsRef.current.forEach(({ circle, label }) => {
        circle.setMap(null);
        label.setMap(null);
      });
      reachRingsRef.current.clear();
      reachFitKeyRef.current = null;
      return;
    }

    const center = new window.kakao.maps.LatLng(reach.lat, reach.lng);
    const rings = reachByMode(reach);
    const live = new Set(rings.map((r) => r.mode));

    // 슬라이더를 1분씩 드래그할 때마다 원을 지웠다 새로 만들면 드래그가 끊긴다.
    // 이동수단별로 원·이름표를 하나씩 들고 있다가 반경/위치만 갱신한다.
    reachRingsRef.current.forEach((entry, mode) => {
      if (!live.has(mode)) {
        entry.circle.setMap(null);
        entry.label.setMap(null);
        reachRingsRef.current.delete(mode);
      }
    });

    rings.forEach(({ mode: ringMode, radiusM, measured }) => {
      // 이름표는 원 꼭대기에 — 색만으로는 어느 원이 어느 이동수단인지 모른다.
      const labelPos = new window.kakao.maps.LatLng(reach.lat + radiusM / 111_320, reach.lng);
      const text = `${REACH_MODE_LABELS[ringMode] || ringMode} ${formatDistance(radiusM)}`;
      const existing = reachRingsRef.current.get(ringMode);

      if (existing) {
        existing.circle.setPosition(center);
        existing.circle.setRadius(radiusM);
        existing.label.setPosition(labelPos);
        existing.labelEl.textContent = text;
        existing.labelEl.className = `reach-ring-label${measured ? " reach-ring-label--measured" : ""}`;
        return;
      }

      const circle = new window.kakao.maps.Circle({
        center,
        radius: radiusM,
        strokeWeight: measured ? 3 : 2,
        strokeColor: MAP_OVERLAY_COLORS.reach,
        strokeOpacity: measured ? 0.95 : 0.55,
        strokeStyle: measured ? "solid" : "shortdash",
        fillColor: MAP_OVERLAY_COLORS.reach,
        fillOpacity: measured ? 0.1 : 0,
        clickable: false,
      });
      circle.setMap(mapRef.current);

      const labelEl = document.createElement("span");
      labelEl.className = `reach-ring-label${measured ? " reach-ring-label--measured" : ""}`;
      labelEl.textContent = text;
      const label = new window.kakao.maps.CustomOverlay({
        position: labelPos,
        map: mapRef.current,
        content: labelEl,
        yAnchor: 0.5,
        xAnchor: 0.5,
        zIndex: 5,
      });
      reachRingsRef.current.set(ringMode, { circle, label, labelEl });
    });

    // 위치·이동수단이 바뀐 첫 렌더에만 지도를 맞춘다. 분값 드래그만으로는
    // 다시 맞추지 않아 화면이 튀지 않는다. 가장 바깥 원(차량)이 들어오게 맞춘다.
    const fitKey = `${reach.lat.toFixed(6)},${reach.lng.toFixed(6)},${reach.mode}`;
    if (reachFitKeyRef.current !== fitKey && window.kakao.maps.LatLngBounds) {
      reachFitKeyRef.current = fitKey;
      const atMax = reachByMode({ ...reach, minute: reach.maxMinute });
      const maxRadius = atMax.length ? atMax[atMax.length - 1].radiusM : 0;
      if (Number.isFinite(maxRadius) && maxRadius > 0) {
        const dLat = maxRadius / 111_320;
        const dLng = maxRadius / (111_320 * Math.cos((reach.lat * Math.PI) / 180));
        const bounds = new window.kakao.maps.LatLngBounds();
        bounds.extend(new window.kakao.maps.LatLng(reach.lat - dLat, reach.lng - dLng));
        bounds.extend(new window.kakao.maps.LatLng(reach.lat + dLat, reach.lng + dLng));
        mapRef.current.setBounds(bounds);
      }
    }
  }, [ready, reach]);

  useEffect(() => {
    if (!ready || !mapRef.current || !analysisData) return;

    clearRoadPolylines(polylinesRef.current);
    polylinesRef.current = [];
    if (circleRef.current) {
      circleRef.current.setMap(null);
      circleRef.current = null;
    }

    const center = new window.kakao.maps.LatLng(analysisData.center.lat, analysisData.center.lng);
    mapRef.current.setCenter(center);

    // 분석 반경은 지표가 아니라 '어디까지 분석했는지'를 나타내는 보조선이다.
    // 예전에는 파란색(#2563eb)에 옅은 채움이었는데, 히트램프의 가장 낮은
    // 구간도 파란색이라 같은 지도에서 파란색이 두 가지를 뜻했다("파란색 기준이
    // 뭔지 모르겠다"). 램프에 없는 중립 회색 점선으로 빼고 채움을 없앤다 —
    // 채움이 있으면 그 아래 도로 색까지 함께 물들어 지표 색을 흐린다.
    circleRef.current = new window.kakao.maps.Circle({
      center,
      radius: analysisData.radius_m,
      strokeWeight: 2,
      strokeColor: MAP_OVERLAY_COLORS.analysisRadius,
      strokeOpacity: 0.7,
      strokeStyle: "dashed",
      fillOpacity: 0,
      clickable: false,
    });
    circleRef.current?.setMap(mapRef.current);

    const modeFlag = mode === "walk" ? "walkable" : "drivable";
    type SegmentFeature = AnalyzeResponse["segments"]["features"][number];
    const features = (analysisData.segments.features || []).filter(
      (f: SegmentFeature) => f.properties?.[modeFlag]
    );
    if (features.length === 0) {
      clearMetricLayer();
      return;
    }

    const values = features.map((f: SegmentFeature) => {
      const key = propKey(f.properties, metric, mode);
      return (f.properties?.[key] as number) ?? 0;
    });
    const { min, max } = valueExtent(values);
    // 범례가 색을 설명하려면 지도가 **실제로 쓴** 값 범위를 알아야 한다.
    publishMetricLayer({ metric, mode, min, max, segmentCount: features.length });
    const bounds = new window.kakao.maps.LatLngBounds();

    features.forEach((feature: SegmentFeature) => {
      const coords = feature.geometry?.coordinates as [number, number][] | undefined;
      if (!coords) return;
      const path = geoJsonToKakaoPath(coords);
      path.forEach((p) => bounds.extend(p));
      const key = propKey(feature.properties, metric, mode);
      const value = (feature.properties?.[key] as number) ?? 0;
      const osmId = Number(feature.properties?.osm_id);
      const color = metricColor(value, min, max, metric);
      const polyline = new window.kakao.maps.Polyline({
        path,
        strokeWeight: 4,
        strokeColor: color,
        strokeOpacity: 0.92,
        strokeStyle: "solid",
        clickable: Number.isFinite(osmId),
      });
      // Ported from `renderSegments`'s polyline click handler in
      // frontend/js/map.js: clicking an analyzed road segment shows its
      // info (OSM ID / type / length / current metric value) — it does not
      // toggle anything on this map (road-closure selection lives on the
      // 신설도로 시뮬레이션 tab's own map instead).
      const clickHandler = Number.isFinite(osmId)
        ? () => {
            const props = feature.properties || {};
            const metricLabel = METRIC_PALETTE[metric]?.name || metric;
            onRoadClickRef.current?.({
              osmId,
              highway: String(props.highway ?? "—"),
              lengthM: Number(props.length_m ?? 0),
              metric,
              metricLabel,
              metricValue: (props[key] as number | undefined) ?? "n/a",
            });
          }
        : undefined;
      if (clickHandler) window.kakao.maps.event.addListener(polyline, "click", clickHandler);
      polyline.setMap(mapRef.current);
      polylinesRef.current.push({ polyline, osmId, baseColor: color, clickHandler });
    });

    if (!bounds.isEmpty()) {
      mapRef.current.setBounds(bounds);
    }

    if (markerRef.current) markerRef.current.setMap(null);
    markerRef.current = new window.kakao.maps.Marker({ position: center, map: mapRef.current });

    requestAnimationFrame(() => mapRef.current?.relayout?.());
  }, [ready, analysisData, metric, mode]);

  useEffect(() => {
    const selected = new Set(selectedClosedOsmIds);
    polylinesRef.current.forEach(({ polyline, osmId, baseColor }) => {
      const closed = selected.has(osmId);
      polyline.setOptions?.({
        strokeWeight: closed ? 7 : 4,
        strokeColor: closed ? "#111827" : baseColor,
        strokeStyle: closed ? "shortdash" : "solid",
      });
    });
  }, [ready, analysisData, metric, mode, selectedClosedOsmIds]);

  useEffect(() => {
    simulationPolylinesRef.current.forEach((p) => p.setMap(null));
    simulationPolylinesRef.current = [];
    if (!ready || !mapRef.current || !simulationData) return;

    simulationData.segments.features.forEach((feature) => {
      const coords = feature.geometry?.coordinates as [number, number][] | undefined;
      if (!coords?.length) return;
      const props = feature.properties || {};
      const isVirtual = props.virtual === true;
      const isClosed = props.closed === true;
      const delta = typeof props.integration_delta_pct === "number" ? props.integration_delta_pct : 0;
      const color = isClosed ? "#111827" : isVirtual ? "#7c3aed" : delta >= 2 ? "#16a34a" : delta <= -2 ? "#dc2626" : "#94a3b8";
      const polyline = new window.kakao.maps.Polyline({
        path: geoJsonToKakaoPath(coords),
        strokeWeight: isVirtual || isClosed ? 7 : Math.abs(delta) >= 2 ? 5 : 3,
        strokeColor: color,
        strokeOpacity: Math.abs(delta) >= 2 || isVirtual || isClosed ? 0.95 : 0.45,
        strokeStyle: isVirtual || isClosed ? "shortdash" : "solid",
        clickable: false,
        zIndex: isVirtual || isClosed ? 6 : 4,
      });
      polyline.setMap(mapRef.current);
      simulationPolylinesRef.current.push(polyline);
    });
  }, [ready, simulationData]);

  useEffect(() => {
    if (!ready) return;
    polylinesRef.current.forEach(({ polyline }) => polyline.setMap(showRoadLayer ? mapRef.current : null));
  }, [showRoadLayer, ready, analysisData, metric, mode]);

  function openMarkerInfo(address: string, position: unknown, title = "관심 목록") {
    if (!mapRef.current) return;
    if (!infoWindowRef.current) {
      infoWindowRef.current = new window.kakao.maps.InfoWindow({ removable: true });
    }
    const infoWindow = infoWindowRef.current;
    if (!infoWindow) return;
    infoWindow.setContent(
      `<div class="watchlist-marker-info"><strong>${escapeMarkerHtml(title)}</strong><p>${escapeMarkerHtml(address)}</p></div>`
    );
    infoWindow.setPosition(position);
    infoWindow.open(mapRef.current);
  }

  // Ported from `updateWatchlistMarkers` in frontend/js/map.js: plain star
  // markers for every saved 입지 분석 watchlist item, shown while the 입지
  // 분석 tab is active (the caller passes an empty array otherwise).
  useEffect(() => {
    watchlistOverlaysRef.current.forEach((o) => o.setMap(null));
    watchlistOverlaysRef.current = [];
    if (!ready || !mapRef.current) return;
    watchlistMarkers.forEach((item) => {
      const pos = new window.kakao.maps.LatLng(item.lat, item.lng);
      const wrap = document.createElement("div");
      wrap.innerHTML = `<div class="watchlist-map-marker" role="button" tabindex="0" aria-label="관심 목록 위치"><span class="watchlist-map-marker__icon" aria-hidden="true">★</span></div>`;
      const el = wrap.firstElementChild as HTMLElement;
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        openMarkerInfo(item.address, pos);
      });
      const overlay = new window.kakao.maps.CustomOverlay({
        position: pos,
        map: mapRef.current,
        content: el,
        yAnchor: 1,
        xAnchor: 0.5,
        zIndex: 4,
      });
      watchlistOverlaysRef.current.push(overlay);
    });
  }, [ready, watchlistMarkers]);

  // Ported from `updateCandidateMarkers` in frontend/js/map.js /
  // `syncCandidateMarkers` in frontend/js/candidate_compare.js: selected
  // compare candidates get colored A-E badges, everything else on the
  // watchlist gets a dimmed star (the caller passes an empty array outside
  // the 관심목록 · 후보지 비교 tab).
  useEffect(() => {
    candidateOverlaysRef.current.forEach((o) => o.setMap(null));
    candidateOverlaysRef.current = [];
    if (!ready || !mapRef.current) return;
    candidateMarkers.forEach((item) => {
      const isLetter = /^[A-E]$/i.test(item.label);
      const isWatchlist = item.label === "★";
      const color = isLetter ? CANDIDATE_MARKER_COLORS[item.label.toLowerCase()] || CANDIDATE_MARKER_COLORS.a : "#f59e0b";
      const dimClass = item.dimmed ? " candidate-map-marker--dim" : "";
      const watchlistClass = isWatchlist ? " candidate-map-marker--watchlist" : "";
      const inner = isWatchlist ? `<span class="candidate-map-marker__star" aria-hidden="true">★</span>` : `<span>${item.label}</span>`;
      // AI 상담이 꽂은 별(id "agent-pin")은 관심 목록에 넣은 게 아니다 — 지도에서 누르거나
      // 상담이 분석한 자리다. 「관심 목록」이라 쓰면 저장된 줄 안다(2026-11-06 라이브 확인).
      const title = item.id === "agent-pin" ? "고른 자리" : isWatchlist ? "관심 목록" : `후보 ${item.label}`;
      const pos = new window.kakao.maps.LatLng(item.lat, item.lng);
      const wrap = document.createElement("div");
      wrap.innerHTML = `<div class="candidate-map-marker${dimClass}${watchlistClass}" style="--marker-color:${color}" title="${title}" role="button" tabindex="0">${inner}</div>`;
      const el = wrap.firstElementChild as HTMLElement;
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        openMarkerInfo(item.address, pos, title);
      });
      const overlay = new window.kakao.maps.CustomOverlay({
        position: pos,
        map: mapRef.current,
        content: el,
        yAnchor: 1,
        xAnchor: 0.5,
        zIndex: item.dimmed ? 2 : 3,
      });
      candidateOverlaysRef.current.push(overlay);
    });
  }, [ready, candidateMarkers]);

  // Ported from `focusCandidateBounds` in frontend/js/map.js, called from
  // `renderCompareResults` in frontend/js/candidate_compare.js whenever the
  // compare results table renders.
  const focusBoundsKey = (focusBoundsTargets || []).map((p) => `${p.lat.toFixed(6)},${p.lng.toFixed(6)}`).join("|");
  useEffect(() => {
    if (!ready || !mapRef.current || !focusBoundsTargets?.length) return;
    const bounds = new window.kakao.maps.LatLngBounds();
    focusBoundsTargets.forEach((p) => bounds.extend(new window.kakao.maps.LatLng(p.lat, p.lng)));
    if (!bounds.isEmpty()) mapRef.current.setBounds(bounds);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, focusBoundsKey]);

  useEffect(() => {
    if (!ready || !mapRef.current) return;

    const container = containerRef.current;
    if (!container || typeof ResizeObserver === "undefined") {
      requestAnimationFrame(() => mapRef.current?.relayout?.());
      return;
    }

    const observer = new ResizeObserver(() => {
      mapRef.current?.relayout?.();
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [ready]);

  if (loadError) {
    return (
      <div className="map-error" role="alert">
        <div className="map-error__body">
          <p>{loadError}</p>
          <button
            type="button"
            className="map-error__retry"
            onClick={() => {
              setLoadError(null);
              setReady(false);
              setMapAttempt((n) => n + 1);
            }}
          >
            다시 불러오기
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="kakao-map"
      role="application"
      aria-label="분석 지도"
      aria-busy={!ready}
    />
  );
}

/** 지표 값 축약 표기 — 큰 수는 자릿수를 줄여 범례가 넘치지 않게. */
function formatMetricValue(v: number): string {
  if (!Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(v / 1_000).toFixed(1)}k`;
  if (abs >= 10) return v.toFixed(0);
  if (abs >= 1) return v.toFixed(1);
  return v.toFixed(2);
}

/**
 * 지도 범례.
 *
 * 사용자 지적: "지도 위의 색상이 뭘 의미하는지 정확하게 표시해줘야겠다. 지금
 * 그냥 파란색으로 되어 있는 게 있는데 그 파란색이 기준이 뭔지를 모르겠다."
 *
 * 예전 범례는 색 띠와 '낮음/높음'만 있었다. **무엇이** 낮고 높은지(어떤 지표인지),
 * 값이 얼마인지, 그리고 지도에 함께 그려진 원들(분석 반경·도달권)이 무엇인지는
 * 하나도 없었다. 그래서 범례를 '지도에 그려진 모든 색과 선'의 목록으로 바꾼다:
 *   1) 색 띠 — 지금 보고 있는 지표 이름 + 양 끝 실제 값
 *   2) 원·선 — 분석 반경(회색 점선), 도달권(초록, 이동수단별)
 * 값 범위는 지도가 실제로 쓴 값을 그대로 받아 적는다(legendStore) — 범례가
 * 따로 계산하면 지도와 어긋날 수 있다.
 */
export function MapLegend({
  metric,
  simulationActive = false,
  simple = false,
}: {
  metric: string;
  simulationActive?: boolean;
  /** 첫 화면용 — 지표 이름·양 끝 숫자·도달권 줄을 빼고 색과 번호가 무슨 뜻인지만. */
  simple?: boolean;
}) {
  const layer = useMetricLayer();
  const reach = useReachState();

  // 2026-10-26: 첫 화면(길 순위)에서 범례가 「0.00 / 1.4」, 「도달권 5분 · 도보 380m」, 실선·점선
  // 설명을 냈는데 그 화면은 도달권 원을 그리지 않는다 — 검색한 사람에게 뜻 없는 글이었다(소유자 지적).
  if (simple && !simulationActive) {
    return (
      <div className="map-legend map-legend--full map-legend--simple">
        <span className="map-legend-title">길 색 = 사람이 지나가는 정도</span>
        <div className="map-legend-scale">
          <span className="map-legend-end">적음</span>
          <div
            className="map-legend-bar"
            style={{ background: `linear-gradient(to right, ${rampColors(layer?.metric ?? metric).join(", ")})` }}
          />
          <span className="map-legend-end">많음</span>
        </div>
        <p className="map-legend-note">붉을수록 오가는 사람이 많은 길 · 큰 주황 번호가 목 좋은 길 순위예요 (작은 노란 번호는 역 출구)</p>
      </div>
    );
  }

  if (simulationActive) {
    return (
      <div className="map-legend map-legend--delta">
        <span className="map-legend-title">신설도로 시뮬레이션</span>
        <span><i style={{ background: MAP_OVERLAY_COLORS.simGain }} />통행 늘어난 길</span>
        <span><i style={{ background: MAP_OVERLAY_COLORS.simLoss }} />통행 줄어든 길</span>
        <span><i style={{ background: MAP_OVERLAY_COLORS.simClosed }} />폐쇄</span>
        <span><i style={{ background: MAP_OVERLAY_COLORS.simVirtual }} />신설</span>
      </div>
    );
  }

  // 범례가 설명하는 건 '지금 화면의 지표'다. 지도가 아직 아무것도 안 그렸으면
  // 요청된 지표 이름만 쓰고 값 범위는 비운다 (없는 숫자를 지어내지 않는다).
  const shownMetric = layer?.metric ?? metric;
  const metricName = METRIC_PALETTE[shownMetric]?.name || shownMetric;
  const ordered = rampColors(shownMetric);
  const invert = METRIC_PALETTE[shownMetric]?.invert === true;
  const lowLabel = invert ? "많이 들어감" : "낮음";
  const highLabel = invert ? "덜 들어감" : "높음";
  const rings: ModeReach[] = reach ? reachByMode(reach) : [];
  const anyApproximate = rings.some((r) => !r.measured);

  return (
    <div className="map-legend map-legend--full">
      <div className="map-legend-row">
        <span className="map-legend-title">길 색 · {metricName}</span>
        <div className="map-legend-scale">
          <span className="map-legend-end">
            {lowLabel}
            {layer ? <em>{formatMetricValue(layer.min)}</em> : null}
          </span>
          <div
            className="map-legend-bar"
            style={{ background: `linear-gradient(to right, ${ordered.join(", ")})` }}
          />
          <span className="map-legend-end">
            {highLabel}
            {layer ? <em>{formatMetricValue(layer.max)}</em> : null}
          </span>
        </div>
      </div>

      <div className="map-legend-row map-legend-row--shapes">
        <span className="map-legend-shape">
          <i className="map-legend-ring map-legend-ring--dashed" style={{ borderColor: MAP_OVERLAY_COLORS.analysisRadius }} />
          분석 반경
        </span>
        {rings.length > 0 && (
          <span className="map-legend-shape">
            <i className="map-legend-ring" style={{ borderColor: MAP_OVERLAY_COLORS.reach }} />
            도달권 {reach?.minute}분
          </span>
        )}
        {rings.map((r) => (
          <span key={r.mode} className={`map-legend-reach${r.measured ? " map-legend-reach--measured" : ""}`}>
            {REACH_MODE_LABELS[r.mode]} {formatDistance(r.radiusM)}
          </span>
        ))}
      </div>

      {anyApproximate && (
        <p className="map-legend-note">
          실선은 도로망으로 잰 도달권, 점선은 같은 동네의 우회 정도를 적용한 근사입니다.
          다른 이동수단으로 정확히 재려면 아래 도달권 분석에서 이동수단을 바꾸세요.
        </p>
      )}
    </div>
  );
}
