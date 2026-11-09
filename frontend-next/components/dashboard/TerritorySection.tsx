"use client";

import { useEffect, useRef, useState } from "react";
import type { TerritoryAnalysisResponse, TerritoryPatch } from "@/lib/api";
import { loadKakaoMaps, isKakaoMapsReady } from "@/lib/kakao";
import { ISO_MODE_LABELS, TERRITORY_NEW_ENTRANT_COLOR, TERRITORY_ZONE_COLORS, fmtTerritoryArea, territoryBrandColor } from "./dashboardUtils";

type TerritoryView = "before" | "after" | "zones";
const VIEWS: { key: TerritoryView; label: string }[] = [
  { key: "before", label: "Before · 기존 권역" },
  { key: "after", label: "After · 진입 후" },
  { key: "zones", label: "변화 구역" },
];

// Mounts the Kakao map and draws territory patches/zones for the active view,
// mirroring `mountDashTerritoryMap`/`drawTerritoryPatchesOnMap`/`drawTerritoryZonesOnMap`
// in frontend/js/dashboard.js. Falls back to an empty canvas (matching Vanilla's
// `container.innerHTML = ""` fallback) when the Kakao Maps SDK cannot load.
function TerritoryMap({ territory, lat, lng, view }: { territory: TerritoryAnalysisResponse; lat: number | null; lng: number | null; view: TerritoryView }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [kakaoReady, setKakaoReady] = useState(isKakaoMapsReady());
  const mapRef = useRef<{ setBounds: (b: unknown) => void; relayout?: () => void } | null>(null);
  const overlaysRef = useRef<{ setMap: (m: unknown) => void }[]>([]);

  useEffect(() => {
    let cancelled = false;
    loadKakaoMaps().then(() => { if (!cancelled) setKakaoReady(true); }).catch(() => { if (!cancelled) setKakaoReady(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!kakaoReady || !containerRef.current) return;
    const kakao = window.kakao;
    overlaysRef.current.forEach((o) => o.setMap(null));
    overlaysRef.current = [];
    containerRef.current.innerHTML = "";

    const fallback = territory.before_territories[0] || territory.after_territories[0];
    const center = new kakao.maps.LatLng(lat ?? fallback?.lat ?? 37.5665, lng ?? fallback?.lng ?? 126.978);
    const map = new kakao.maps.Map(containerRef.current, { center, level: 5, draggable: true, scrollwheel: false });
    mapRef.current = map;

    const bounds = new kakao.maps.LatLngBounds();
    let hasBounds = false;
    const extendFromList = (list: TerritoryPatch[]) => {
      list.forEach((item) => {
        const ring = item.polygon?.coordinates?.[0];
        if (ring && ring.length >= 3) {
          ring.forEach(([lng2, lat2]) => { bounds.extend(new kakao.maps.LatLng(lat2, lng2)); hasBounds = true; });
        }
        if (item.lat != null && item.lng != null) { bounds.extend(new kakao.maps.LatLng(item.lat, item.lng)); hasBounds = true; }
      });
    };

    if (view === "zones") {
      territory.zones.forEach((zone) => {
        const ring = zone.polygon?.coordinates?.[0];
        if (!ring || ring.length < 3) return;
        const colors = TERRITORY_ZONE_COLORS[zone.zone_type] || { stroke: "#64748b", fill: "#e2e8f0" };
        const path = ring.map(([lng2, lat2]) => new kakao.maps.LatLng(lat2, lng2));
        path.forEach((p) => { bounds.extend(p); hasBounds = true; });
        const poly = new kakao.maps.Polygon({ path, strokeWeight: 2, strokeColor: colors.stroke, strokeOpacity: 0.9, fillColor: colors.fill, fillOpacity: 0.45, clickable: false, zIndex: 5 });
        poly.setMap(map);
        overlaysRef.current.push(poly);
      });
    } else {
      const patches = view === "before" ? territory.before_territories : territory.after_territories;
      extendFromList(patches);
      const sorted = [...patches].sort((a, b) => (a.is_new_entrant ? 1 : 0) - (b.is_new_entrant ? 1 : 0));
      sorted.forEach((t) => {
        const ring = t.polygon?.coordinates?.[0];
        if (!ring || ring.length < 3) return;
        const path = ring.map(([lng2, lat2]) => new kakao.maps.LatLng(lat2, lng2));
        const isNew = Boolean(t.is_new_entrant);
        const color = isNew ? TERRITORY_NEW_ENTRANT_COLOR : territoryBrandColor(t.brand);
        const poly = new kakao.maps.Polygon({ path, strokeWeight: isNew ? 4 : 2, strokeColor: color, strokeOpacity: isNew ? 1 : 0.85, fillColor: color, fillOpacity: isNew ? 0.3 : 0.16, zIndex: isNew ? 10 : 3, clickable: false });
        poly.setMap(map);
        overlaysRef.current.push(poly);
        if (t.lat != null && t.lng != null) {
          const label = new kakao.maps.CustomOverlay({
            position: new kakao.maps.LatLng(t.lat, t.lng),
            content: `<div class="dash-territory-label${isNew ? " dash-territory-label--new" : ""}" style="border-color:${color};${isNew ? "" : `color:${color};`}">${t.brand}${isNew ? " · 신규 진입" : ""}</div>`,
            yAnchor: 1.3,
            zIndex: isNew ? 11 : 4,
          });
          label.setMap(map);
          overlaysRef.current.push(label);
        }
      });
    }

    if (hasBounds) map.setBounds(bounds);
    requestAnimationFrame(() => {
      mapRef.current?.relayout?.();
      if (hasBounds) mapRef.current?.setBounds(bounds);
    });

    return () => {
      overlaysRef.current.forEach((o) => o.setMap(null));
      overlaysRef.current = [];
    };
  }, [kakaoReady, territory, lat, lng, view]);

  if (!kakaoReady) {
    return <div id="dash-territory-map" className="dash-territory-map-canvas" role="img" aria-label="권역 변화 지도" />;
  }
  return <div ref={containerRef} id="dash-territory-map" className="dash-territory-map-canvas" role="img" aria-label="권역 변화 지도" />;
}

/**
 * 지도에 실제로 칠해진 색만 설명한다.
 *
 * 예전에는 어떤 보기에서도 "흡수·중복·사각지대" 세 색을 고정으로 적었다.
 * 그런데 그 색은 「변화 구역」 보기에서만 그려지고, Before/After 보기의
 * 지도는 브랜드마다 다른 색으로 칠한다 — 범례에 없는 색이 지도에 있고,
 * 지도에 없는 색이 범례에 있었다.
 */
function TerritoryLegend({ territory, view }: { territory: TerritoryAnalysisResponse; view: TerritoryView }) {
  if (view === "zones") {
    const shown = new Set(territory.zones.map((z) => z.zone_type));
    const items: { key: string; label: string; cls: string }[] = [
      { key: "absorbed", label: "흡수", cls: "dash-territory-swatch--absorbed" },
      { key: "overlap", label: "중복", cls: "dash-territory-swatch--overlap" },
      { key: "gap", label: "사각지대", cls: "dash-territory-swatch--gap" },
    ].filter((i) => shown.has(i.key));
    return (
      <p className="dash-territory-legend">
        {items.length ? items.map((i) => (
          <span className="dash-territory-legend-item" key={i.key}>
            <span className={`dash-territory-swatch ${i.cls}`} />{i.label}
          </span>
        )) : <span className="demand-meta">이 조건에서는 바뀌는 구역이 없습니다.</span>}
      </p>
    );
  }

  // Before/After — 브랜드마다 색이 다르므로 어떤 색이 어느 브랜드인지 적는다.
  const patches = view === "before" ? territory.before_territories : territory.after_territories;
  const seen = new Map<string, { color: string; isNew: boolean }>();
  patches.forEach((t) => {
    const brand = t.brand || "기타";
    if (seen.has(brand)) return;
    seen.set(brand, {
      color: t.is_new_entrant ? TERRITORY_NEW_ENTRANT_COLOR : territoryBrandColor(t.brand),
      isNew: Boolean(t.is_new_entrant),
    });
  });
  if (!seen.size) return <p className="dash-territory-legend"><span className="demand-meta">표시할 권역이 없습니다.</span></p>;
  return (
    <p className="dash-territory-legend">
      {[...seen.entries()].map(([brand, meta]) => (
        <span className="dash-territory-legend-item" key={brand}>
          <span className="dash-territory-swatch" style={{ background: meta.color, borderColor: meta.color }} />
          {brand}{meta.isNew ? " · 신규 진입" : ""}
        </span>
      ))}
    </p>
  );
}

// Ported from `renderTerritorySection` in frontend/js/dashboard.js. As in the
// Vanilla source, the text lists below the map show only `before_territories`
// and `zones` — `after_territories` is only consumed by the map view toggle.
export function TerritorySection({ territory, lat, lng }: { territory: TerritoryAnalysisResponse | null; lat: number | null; lng: number | null }) {
  const [view, setView] = useState<TerritoryView>("zones");
  if (!territory) return null;

  const modeLabel = ISO_MODE_LABELS[territory.delivery_mode] || territory.delivery_mode;

  return (
    <section className="dash-card dash-card--territory" data-dash="territory">
      <header className="dash-card-header">
        <span className="dash-card-icon" aria-hidden="true">🗺</span>
        <h3>권역 잠식 · Before/After</h3>
        <span className="dash-card-badge">{modeLabel} {territory.delivery_minutes}분</span>
      </header>
      {territory.narrative.map((n, i) => <p key={i} className="dash-strategy">{n}</p>)}
      <div className="dash-territory-map-wrap">
        <div className="dash-territory-toggle" role="group" aria-label="권역 변화 보기">
          {VIEWS.map((v) => (
            <button key={v.key} type="button" className={`dash-territory-toggle-btn${v.key === view ? " dash-territory-toggle-btn--active" : ""}`} onClick={() => setView(v.key)}>
              {v.label}
            </button>
          ))}
        </div>
        <TerritoryMap territory={territory} lat={lat} lng={lng} view={view} />
        <TerritoryLegend territory={territory} view={view} />
      </div>
      <div className="territory-grid">
        <div>
          <h4 className="dash-subtitle">Before — 기존 경쟁 브랜드 권역</h4>
          <ul className="territory-brand-list">
            {territory.before_territories.length
              ? territory.before_territories.map((t, i) => <li key={i}><strong>{t.brand}</strong> <span className="demand-meta">{t.store_name} · {fmtTerritoryArea(t.area_m2)}</span></li>)
              : <li className="demand-meta">동일업종 경쟁 없음</li>}
          </ul>
        </div>
        <div>
          <h4 className="dash-subtitle">After — 신규 진입 시 변화</h4>
          <ul className="territory-zone-list">
            {territory.zones.length
              ? territory.zones.map((z, i) => (
                  <li key={i} className={`territory-zone territory-zone--${z.zone_type}`}>
                    <strong>{z.label}</strong><span>{fmtTerritoryArea(z.area_m2)}</span>
                    <p className="demand-meta">{z.note}</p>
                  </li>
                ))
              : <li className="demand-meta">권역 변화 없음</li>}
          </ul>
        </div>
      </div>
      <h4 className="dash-subtitle">권역 수요 특성</h4>
      <ul className="territory-demand-list">
        {territory.demand.demand_notes.length ? territory.demand.demand_notes.map((n, i) => <li key={i}>{n}</li>) : <li className="demand-meta">수요 데이터 없음</li>}
      </ul>
      <h4 className="dash-subtitle">브랜드 적합성</h4>
      <p className="dash-strategy">{territory.brand_fit_headline}</p>
      <ul className="territory-fit-list">
        {territory.brand_fit_bullets.map((b, i) => <li key={i}>{b}</li>)}
      </ul>
      {(territory.warnings || []).map((w, i) => <p key={i} className="demand-meta dash-iso-warn">{w}</p>)}
      <p className="dash-footnote">※ 모델 적합도는 도보 {territory.model_fit_minutes}분, 권역 잠식은 {modeLabel} {territory.delivery_minutes}분 도로망 도달권 기준입니다. 지도에 경쟁 점포·권역이 표시됩니다.</p>
    </section>
  );
}
