import type { IsochroneBand } from "@/lib/api";
import { ISO_FILL, ISO_STROKE } from "./dashboardUtils";

// Ported from frontend/js/dashboard.js `projectBandsToSvg` — an equirectangular
// projection of the isochrone rings onto a small SVG, used when the Kakao Maps
// SDK is unavailable (e.g. domain not yet registered for this environment).
export function IsochroneFallbackSvg({ bands, width = 200, height = 140 }: { bands: IsochroneBand[]; width?: number; height?: number }) {
  const rings = bands.map((b) => b.polygon?.coordinates?.[0]).filter((r): r is [number, number][] => Boolean(r) && r.length >= 3);
  if (!rings.length) return null;

  let minLng = Infinity;
  let maxLng = -Infinity;
  let minLat = Infinity;
  let maxLat = -Infinity;
  rings.forEach((ring) => {
    ring.forEach(([lng, lat]) => {
      minLng = Math.min(minLng, lng);
      maxLng = Math.max(maxLng, lng);
      minLat = Math.min(minLat, lat);
      maxLat = Math.max(maxLat, lat);
    });
  });
  const pad = 0.08;
  const spanLng = maxLng - minLng || 0.001;
  const spanLat = maxLat - minLat || 0.001;
  const cx = (minLng + maxLng) / 2;
  const cy = (minLat + maxLat) / 2;
  const scale = Math.min((width * (1 - pad * 2)) / spanLng, (height * (1 - pad * 2)) / spanLat);

  const toXY = ([lng, lat]: [number, number]) => {
    const x = width / 2 + (lng - cx) * scale;
    const y = height / 2 - (lat - cy) * scale;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  };

  const ordered = [...rings].reverse();

  return (
    <svg className="dash-isochrone-svg" viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      {ordered.map((ring, i) => {
        const idx = rings.length - 1 - i;
        return (
          <polygon
            key={idx}
            points={ring.map(toXY).join(" ")}
            fill={ISO_FILL[idx] || ISO_FILL[0]}
            stroke={ISO_STROKE[idx] || ISO_STROKE[0]}
            strokeWidth={1.5}
            opacity={0.85}
          />
        );
      })}
      <circle cx={width / 2} cy={height / 2} r={4} fill="#2563eb" stroke="#fff" strokeWidth={1.5} />
    </svg>
  );
}
