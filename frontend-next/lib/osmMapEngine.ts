"use client";

/**
 * 한국 밖 도면용 바탕 지도 — OpenStreetMap 타일 위에 Leaflet으로 그린다 (2026-11-09).
 *
 * 카카오 지도는 한국 밖 타일이 없어 두바이·미국 샘플이 흰 바탕 위에 길·블록 그림만 떴다.
 * 그리는 코드(PlannedSitePanel.draw)는 카카오 API 모양 그대로 두고, 여기서 **카카오 `maps`
 * 객체와 같은 모양**(Map·LatLng·LatLngBounds·Polyline·Polygon·CustomOverlay·event)을 Leaflet
 * 위에 흉내 낸다. 그래서 화면은 도면이 어디 있느냐에 따라 엔진만 바꿔 끼운다.
 *
 * Leaflet은 window를 만지므로 동적 import — 정적 내보내기(빌드 때 서버 렌더)에서 안 깨지게.
 * 타일 출처는 OSM 표준 타일(이용 정책상 가벼운 시연용), 저작권 표시는 Leaflet 기본 컨트롤이 붙인다.
 */

import type * as L from "leaflet";

export type LatLngLike = { lat: number; lng: number };
export type MapHandle = {
  setBounds: (bounds: unknown) => void;
  relayout: () => void;
  /** 엔진을 바꿔 끼울 때 Leaflet 자원을 놓아준다. 카카오에는 없다. */
  destroy?: () => void;
};
export type ShapeHandle = {
  setMap: (map: unknown) => void;
  setOptions: (options: { strokeWeight?: number; strokeColor?: string }) => void;
};
type StrokeOptions = { strokeWeight?: number; strokeColor?: string; strokeOpacity?: number; zIndex?: number };
type PolylineOptions = StrokeOptions & { path: LatLngLike[] };
type PolygonOptions = StrokeOptions & { path: LatLngLike[]; fillColor?: string; fillOpacity?: number };
type OverlayOptions = { position: LatLngLike; content: string; yAnchor?: number; zIndex?: number };

/** 카카오 `window.kakao.maps`와 그리기 코드가 쓰는 부분만 같은 모양. */
export interface MapsApi {
  Map: new (el: HTMLElement, opts: { center: LatLngLike; level: number }) => MapHandle;
  LatLng: new (lat: number, lng: number) => LatLngLike;
  LatLngBounds: new (sw: LatLngLike, ne: LatLngLike) => unknown;
  Polyline: new (opts: PolylineOptions) => ShapeHandle;
  Polygon: new (opts: PolygonOptions) => ShapeHandle;
  CustomOverlay: new (opts: OverlayOptions) => ShapeHandle;
  event: { addListener: (target: unknown, type: string, fn: () => void) => void };
}

const KOREA = { minLng: 124.0, minLat: 33.0, maxLng: 132.0, maxLat: 39.5 };

/** 한국 안이면 카카오, 밖이면 OSM — 백엔드 `planned_site.metric_epsg_for`와 같은 상자. */
export function inKorea(lat: number, lng: number): boolean {
  return KOREA.minLng <= lng && lng <= KOREA.maxLng && KOREA.minLat <= lat && lat <= KOREA.maxLat;
}

/** 카카오 level(1이 가장 가까움) ≈ 웹 메르카토르 zoom. 처음 중심을 놓을 때만 쓴다. */
function zoomFromLevel(level: number): number {
  return Math.max(3, Math.min(19, 20 - level));
}

let apiPromise: Promise<MapsApi> | null = null;

// 타일 실패 알림 — 몇 장 연속 실패하면 화면이 「바탕이 비었다」고 말할 수 있게.
const tileErrorListeners = new Set<() => void>();
let tileErrorCount = 0;
export function subscribeOsmTileError(fn: () => void): () => void {
  tileErrorListeners.add(fn);
  return () => {
    tileErrorListeners.delete(fn);
  };
}
function noteTileError() {
  tileErrorCount += 1;
  if (tileErrorCount === 3) for (const fn of tileErrorListeners) fn();
}

export function loadOsmMaps(): Promise<MapsApi> {
  if (!apiPromise) apiPromise = import("leaflet").then((mod) => buildApi(mod.default ?? (mod as unknown as typeof L)));
  return apiPromise;
}

function buildApi(leaflet: typeof L): MapsApi {
  // zIndex마다 pane 하나 — 카카오는 zIndex로 위아래를 정하고(길 5·6이 땅 2·3 위), Leaflet SVG는
  // 더한 순서라 길이 땅 밑에 깔린다. pane의 z-index로 같은 순서를 만든다.
  const paneFor = (map: L.Map, z: number | undefined): string | undefined => {
    if (z == null) return undefined;
    const name = `plan-z${z}`;
    if (!map.getPane(name)) {
      const pane = map.createPane(name);
      pane.style.zIndex = String(400 + z);
    }
    return name;
  };

  class MapImpl implements MapHandle {
    readonly map: L.Map;
    constructor(el: HTMLElement, opts: { center: LatLngLike; level: number }) {
      this.map = leaflet.map(el, { zoomControl: true, attributionControl: true });
      tileErrorCount = 0;
      leaflet
        .tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 19,
          attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        })
        .on("tileerror", noteTileError)
        .addTo(this.map);
      this.map.setView([opts.center.lat, opts.center.lng], zoomFromLevel(opts.level));
    }
    setBounds(bounds: unknown) {
      this.map.fitBounds(bounds as L.LatLngBounds, { padding: [12, 12] });
    }
    relayout() {
      this.map.invalidateSize();
    }
    destroy() {
      this.map.remove();
    }
  }

  const toLatLng = (p: LatLngLike): L.LatLngExpression => [p.lat, p.lng];

  class LayerShape implements ShapeHandle {
    protected layer: L.Path | L.Marker;
    private z: number | undefined;
    constructor(layer: L.Path | L.Marker, z: number | undefined) {
      this.layer = layer;
      this.z = z;
    }
    setMap(map: unknown) {
      if (!map) {
        this.layer.remove();
        return;
      }
      const target = (map as MapImpl).map;
      if (this.layer instanceof leaflet.Path) {
        const pane = paneFor(target, this.z);
        if (pane) (this.layer.options as L.PathOptions).pane = pane;
      }
      this.layer.addTo(target);
    }
    setOptions(options: { strokeWeight?: number; strokeColor?: string }) {
      if (this.layer instanceof leaflet.Path) {
        this.layer.setStyle({ weight: options.strokeWeight, color: options.strokeColor });
      }
    }
    on(type: string, fn: () => void) {
      this.layer.on(type, fn);
    }
  }

  class PolylineImpl extends LayerShape {
    constructor(opts: PolylineOptions) {
      super(
        leaflet.polyline(opts.path.map(toLatLng), {
          weight: opts.strokeWeight ?? 3,
          color: opts.strokeColor ?? "#000",
          opacity: opts.strokeOpacity ?? 1,
          lineCap: "round",
          lineJoin: "round",
        }),
        opts.zIndex,
      );
    }
  }

  class PolygonImpl extends LayerShape {
    constructor(opts: PolygonOptions) {
      super(
        leaflet.polygon(opts.path.map(toLatLng), {
          weight: opts.strokeWeight ?? 1,
          color: opts.strokeColor ?? "#fff",
          opacity: opts.strokeOpacity ?? 1,
          fillColor: opts.fillColor ?? "#999",
          fillOpacity: opts.fillOpacity ?? 0.3,
        }),
        opts.zIndex,
      );
    }
  }

  class OverlayImpl extends LayerShape {
    constructor(opts: OverlayOptions) {
      // 카카오 yAnchor(0.5 = 가운데, 1.2 = 점 위로 띄움)를 transform으로 — 글자는 안 가리고 눌리지 않는다.
      const y = opts.yAnchor ?? 0.5;
      const html = `<div style="transform:translate(-50%,-${(y * 100).toFixed(0)}%);white-space:nowrap">${opts.content}</div>`;
      super(
        leaflet.marker(toLatLng(opts.position), {
          icon: leaflet.divIcon({ html, className: "plan-osm-overlay", iconSize: undefined, iconAnchor: [0, 0] }),
          interactive: false,
          keyboard: false,
          zIndexOffset: (opts.zIndex ?? 0) * 10,
        }),
        undefined,
      );
    }
  }

  return {
    Map: MapImpl,
    LatLng: class {
      lat: number;
      lng: number;
      constructor(lat: number, lng: number) {
        this.lat = lat;
        this.lng = lng;
      }
    },
    LatLngBounds: class {
      constructor(sw: LatLngLike, ne: LatLngLike) {
        return leaflet.latLngBounds([sw.lat, sw.lng], [ne.lat, ne.lng]) as unknown as object;
      }
    } as unknown as MapsApi["LatLngBounds"],
    Polyline: PolylineImpl,
    Polygon: PolygonImpl,
    CustomOverlay: OverlayImpl,
    event: {
      addListener: (target, type, fn) => {
        (target as LayerShape).on(type, fn);
      },
    },
  };
}
