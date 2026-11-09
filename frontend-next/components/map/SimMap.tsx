"use client";

// Ported from frontend/js/simulation.js's `#sim-map` handling (`initSimMap`,
// `simDrawBaselineContext`, `simRenderDraft`, `simDrawResultOverlays`,
// `simUpdateCenterMarker`). This is a dedicated Kakao map instance for the
// 신설도로 시뮬레이션 tab, independent of the 입지 분석 tab's KakaoMap — same
// separation Vanilla keeps (`#map` vs `#sim-map`).

import { useEffect, useRef, useState } from "react";
import { SIM_COLORS, simDeltaColor } from "@/lib/simDelta";
import type { RadiusM, SimulateResponse } from "@/lib/api";
import { loadKakaoMaps } from "@/lib/kakao";
import { geoJsonToKakaoPath, metricColor, valueExtent } from "@/lib/mapUtils";

type SimView = "delta" | "before" | "after";

const DEFAULT_CENTER = { lat: 37.5781, lng: 126.9351 };

interface SimContext {
  center: { lat: number; lng: number };
  radiusM: RadiusM;
  segments: { type: string; features: Array<{ geometry?: { coordinates?: unknown }; properties?: Record<string, unknown> }> };
}

interface SimMapProps {
  context: SimContext | null;
  drawing: boolean;
  draftPoints: { lat: number; lng: number }[];
  onAddWaypoint: (lat: number, lng: number) => void;
  onReposition: (lat: number, lng: number) => void;
  closedOsmIds: number[];
  onToggleClosedRoad: (osmId: number) => void;
  result: SimulateResponse | null;
  view: SimView;
}

export function SimMap({ context, drawing, draftPoints, onAddWaypoint, onReposition, closedOsmIds, onToggleClosedRoad, result, view }: SimMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<{ setCenter: (latlng: unknown) => void; setBounds: (b: unknown) => void; relayout?: () => void } | null>(null);
  const centerMarkerRef = useRef<{ setMap: (m: unknown) => void } | null>(null);
  const roadOverlaysRef = useRef<{ setMap: (m: unknown) => void }[]>([]);
  const draftOverlaysRef = useRef<{ setMap: (m: unknown) => void }[]>([]);
  const drawingRef = useRef(drawing);
  const onAddWaypointRef = useRef(onAddWaypoint);
  const onRepositionRef = useRef(onReposition);
  const [ready, setReady] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  drawingRef.current = drawing;
  onAddWaypointRef.current = onAddWaypoint;
  onRepositionRef.current = onReposition;

  useEffect(() => {
    let cancelled = false;
    if (!containerRef.current) return;
    loadKakaoMaps()
      .then(() => {
        if (cancelled || !containerRef.current) return;
        const map = new window.kakao.maps.Map(containerRef.current, {
          center: new window.kakao.maps.LatLng(DEFAULT_CENTER.lat, DEFAULT_CENTER.lng),
          level: 5,
        });
        mapRef.current = map;
        // Ported from `simulationHandleMapClick`: while drawing, every map
        // click adds a waypoint (baseline roads are made non-clickable — see
        // the roads effect below — so clicks reach the map even over a
        // road); while not drawing, a click repositions the simulation
        // context (clicks that land on a clickable baseline road are instead
        // captured by that road's own click handler and toggle closure).
        window.kakao.maps.event.addListener(map, "click", (mouseEvent: { latLng: { getLat: () => number; getLng: () => number } }) => {
          const lat = mouseEvent.latLng.getLat();
          const lng = mouseEvent.latLng.getLng();
          if (drawingRef.current) onAddWaypointRef.current(lat, lng);
          else onRepositionRef.current(lat, lng);
        });
        requestAnimationFrame(() => {
          mapRef.current?.relayout?.();
          setReady(true);
        });
      })
      .catch((err: Error) => {
        if (!cancelled) setLoadError(err.message);
      });
    return () => {
      cancelled = true;
      roadOverlaysRef.current.forEach((o) => o.setMap(null));
      roadOverlaysRef.current = [];
      draftOverlaysRef.current.forEach((o) => o.setMap(null));
      draftOverlaysRef.current = [];
      centerMarkerRef.current?.setMap(null);
      centerMarkerRef.current = null;
      mapRef.current = null;
    };
  }, []);

  // Ported from `simCenterMap`/`simUpdateCenterMarker`: recenter the map on
  // the simulation context's location. Kept as its own effect (deps only on
  // the center coordinates, not `result`/`view`/`drawing`/`closedOsmIds` like
  // the roads effect below) so toggling drawing mode or the view switcher
  // doesn't jerk the map back to center on every redraw.
  useEffect(() => {
    if (!ready || !mapRef.current || !context?.center) return;
    mapRef.current.setCenter(new window.kakao.maps.LatLng(context.center.lat, context.center.lng));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, context?.center.lat, context?.center.lng]);

  // Ported from `simDrawBaselineContext` (no result yet: gray existing
  // roads + purple dashed radius circle, closed candidates highlighted like
  // the 입지 분석 map does) and `simDrawResultOverlays` (result present:
  // color-coded roads per `view`, virtual roads overlaid except in
  // "before"). Both draw into the same overlay set because Vanilla replaces
  // one with the other rather than layering them.
  useEffect(() => {
    roadOverlaysRef.current.forEach((o) => o.setMap(null));
    roadOverlaysRef.current = [];
    centerMarkerRef.current?.setMap(null);
    centerMarkerRef.current = null;
    if (!ready || !mapRef.current || !context?.center) return;
    const map = mapRef.current;

    const radiusCircle = new window.kakao.maps.Circle({
      center: new window.kakao.maps.LatLng(context.center.lat, context.center.lng),
      radius: context.radiusM,
      strokeWeight: 2,
      strokeColor: "#6d28d9",
      strokeOpacity: result ? 0.6 : 0.75,
      strokeStyle: "dashed",
      fillColor: "#6d28d9",
      fillOpacity: result ? 0.04 : 0.06,
      clickable: false,
      zIndex: 1,
    });
    radiusCircle.setMap(map);
    roadOverlaysRef.current.push(radiusCircle);

    centerMarkerRef.current = new window.kakao.maps.Marker({
      position: new window.kakao.maps.LatLng(context.center.lat, context.center.lng),
      map,
      zIndex: 4,
    });

    if (!result) {
      const closed = new Set(closedOsmIds);
      const features = (context.segments?.features || []).filter((f) => f.properties?.walkable);
      features.forEach((f) => {
        const coords = f.geometry?.coordinates as [number, number][] | undefined;
        if (!coords?.length) return;
        const osmId = Number(f.properties?.osm_id);
        const isClosed = Number.isFinite(osmId) && closed.has(osmId);
        const line = new window.kakao.maps.Polyline({
          path: geoJsonToKakaoPath(coords),
          strokeWeight: isClosed ? 6 : 3,
          strokeColor: isClosed ? SIM_COLORS.closed : "#cbd5e1",
          strokeOpacity: isClosed ? 0.95 : 0.75,
          strokeStyle: isClosed ? "shortdash" : "solid",
          clickable: !drawing && Number.isFinite(osmId),
          zIndex: isClosed ? 3 : 1,
        });
        if (!drawing && Number.isFinite(osmId)) {
          window.kakao.maps.event.addListener(line, "click", () => onToggleClosedRoad(osmId));
        }
        line.setMap(map);
        roadOverlaysRef.current.push(line);
      });
      return;
    }

    const features = result.segments.features || [];
    const existing = features.filter((f) => !f.properties?.virtual);
    const virtual = features.filter((f) => f.properties?.virtual);

    let colorFor: (p: Record<string, unknown>) => string;
    if (view === "delta") {
      colorFor = (p) => simDeltaColor(p.integration_delta_pct as number | null | undefined);
    } else {
      const key = view === "before" ? "integration_before" : "integration_after";
      const values = existing
        .flatMap((f) => [f.properties?.integration_before, f.properties?.integration_after])
        .filter((v): v is number => typeof v === "number");
      const { min, max } = valueExtent(values);
      colorFor = (p) => metricColor((p[key] as number) ?? 0, min, max, "integration");
    }

    existing.forEach((f) => {
      const coords = f.geometry?.coordinates as [number, number][] | undefined;
      if (!coords?.length) return;
      const p = f.properties || {};
      const line = new window.kakao.maps.Polyline({
        path: geoJsonToKakaoPath(coords),
        strokeWeight: view === "delta" && p.changed ? 5 : 3,
        strokeColor: colorFor(p),
        strokeOpacity: view === "delta" && !p.changed ? 0.5 : 0.92,
        strokeStyle: "solid",
        clickable: false,
        zIndex: 2,
      });
      line.setMap(map);
      roadOverlaysRef.current.push(line);
    });

    if (view !== "before") {
      virtual.forEach((f) => {
        const coords = f.geometry?.coordinates as [number, number][] | undefined;
        if (!coords?.length) return;
        const line = new window.kakao.maps.Polyline({
          path: geoJsonToKakaoPath(coords),
          strokeWeight: 6,
          strokeColor: SIM_COLORS.virtual,
          strokeOpacity: 0.95,
          strokeStyle: "shortdash",
          clickable: false,
          zIndex: 5,
        });
        line.setMap(map);
        roadOverlaysRef.current.push(line);
      });
    }
  }, [ready, context, result, view, closedOsmIds, drawing, onToggleClosedRoad]);

  // Ported from `simRenderDraft`: the purple dashed draft polyline + vertex
  // dots for waypoints placed so far while drawing.
  useEffect(() => {
    draftOverlaysRef.current.forEach((o) => o.setMap(null));
    draftOverlaysRef.current = [];
    if (!ready || !mapRef.current) return;
    const map = mapRef.current;
    const path = draftPoints.map((p) => new window.kakao.maps.LatLng(p.lat, p.lng));
    if (path.length >= 2) {
      const line = new window.kakao.maps.Polyline({
        path,
        strokeWeight: 5,
        strokeColor: SIM_COLORS.draft,
        strokeOpacity: 0.9,
        strokeStyle: "shortdash",
        zIndex: 5,
        clickable: false,
      });
      line.setMap(map);
      draftOverlaysRef.current.push(line);
    }
    // 출발은 파랑, 도착은 빨강, 경유는 보라 — 패널의 출발·경유·도착 배지와
    // 같은 색으로 지도 위 점을 구분한다.
    path.forEach((p, i) => {
      const dotColor = i === 0 ? "#2563eb" : i === path.length - 1 && path.length >= 2 ? "#dc2626" : SIM_COLORS.draft;
      const dot = new window.kakao.maps.CustomOverlay({
        position: p,
        map,
        content: `<div class="sim-vertex" style="--dot-color:${dotColor}"></div>`,
        yAnchor: 0.5,
        xAnchor: 0.5,
        zIndex: 6,
      });
      draftOverlaysRef.current.push(dot);
    });
  }, [ready, draftPoints]);

  useEffect(() => {
    if (!ready || !mapRef.current) return;
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === "undefined") {
      requestAnimationFrame(() => mapRef.current?.relayout?.());
      return;
    }
    const observer = new ResizeObserver(() => mapRef.current?.relayout?.());
    observer.observe(container);
    return () => observer.disconnect();
  }, [ready]);

  if (loadError) {
    return (
      <div className="map-error">
        <p>{loadError}</p>
      </div>
    );
  }

  return <div ref={containerRef} className="kakao-map sim-map" role="application" aria-label="시뮬레이션 지도" aria-busy={!ready} />;
}
