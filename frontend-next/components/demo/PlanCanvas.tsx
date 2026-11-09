"use client";

import { useMemo } from "react";
import type { Opening, Space, SpaceKind, Wall } from "@/lib/demoFloorplan";
import { norm, type VgaCell, type VgaResult } from "@/lib/floorplanVga";
import type { TermKey } from "@/lib/floorplanTerms";

/**
 * 평면도 한 장과, 그 위에 얹히는 계산 결과.
 *
 * 규칙은 하나다 — **탭이 바뀌어도 도면은 그대로다.** 벽·개구부·방은 언제나 같은
 * 자리에 같은 모양으로 있고, 바뀌는 것은 그 위에 칠하는 값뿐이다. 탭마다 도면을
 * 다시 그리면 보는 사람은 매번 "이게 아까 그 건물이 맞나"부터 확인해야 한다.
 *
 * 좌표계는 고정이다(1000×700). 부모가 어떤 크기든 `meet`으로 맞춰 넣으므로 도면이
 * 잘리지 않고, 화면 크기에 따라 배율을 직접 계산하는 코드를 두지 않는다 — SVG가
 * 이미 한다.
 */

export const VIEW_W = 1000;
export const VIEW_H = 700;

/** 무엇을 칠할지. `null`이면 도면만 — 용어를 설명하는 동안은 값을 칠하지 않는다. */
export type Paint = TermKey | null;

/**
 * 히트맵 색 띠. 옅은 곳에서 진한 곳으로 한 방향이다.
 *
 * 무지개(파랑→빨강)를 쓰지 않는다. 무지개는 중간값이 양 끝보다 밝아서 순서가
 * 눈에 안 잡히고, 빨강이 "위험"으로 읽혀 **높은 값이 나쁜 값처럼** 보인다. 이
 * 지표들은 높은 쪽이 좋은 것도 나쁜 것도 아니다.
 *
 * 색 문자열을 CSS 변수로 두지 않는 이유: 값에 따라 두 색 사이를 섞어야 하고,
 * `color-mix()`를 1,750개 rect에 걸면 브라우저가 느려진다.
 */
const RAMP = ["#eff6ff", "#bfdbfe", "#7dabf8", "#3b7dd8", "#1d4ed8"] as const;

function rampColor(t: number): string {
  const x = Math.min(0.999, Math.max(0, t)) * (RAMP.length - 1);
  const i = Math.floor(x);
  return mix(RAMP[i], RAMP[i + 1], x - i);
}

function mix(a: string, b: string, t: number): string {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  const out = pa.map((v, i) => Math.round(v + (pb[i] - v) * t));
  return `rgb(${out[0]},${out[1]},${out[2]})`;
}

/** 방 용도별 옅은 바탕색. 값을 칠하지 않을 때만 보인다. */
const SPACE_FILL: Record<SpaceKind, string> = {
  use: "var(--accent-background-light-blue)",
  circulation: "var(--accent-background-lime)",
  shared: "var(--purple-90)",
  facility: "var(--fill-normal)",
  leftover: "var(--orange-90)",
  unassigned: "var(--fill-alternative)",
};

function cellValue(c: VgaCell, paint: Exclude<Paint, null>): number {
  if (paint === "connectivity") return c.connectivity;
  if (paint === "mean_depth") return c.meanDepth;
  return c.integration;
}

function range(v: VgaResult, paint: Exclude<Paint, null>): [number, number] {
  if (paint === "connectivity") return [v.connectivityMin, v.connectivityMax];
  if (paint === "mean_depth") return [v.depthMin, v.depthMax];
  return [v.integrationMin, v.integrationMax];
}

function centroid(ring: { x: number; y: number }[]): { x: number; y: number } {
  let a = 0;
  let cx = 0;
  let cy = 0;
  for (let i = 0; i < ring.length; i += 1) {
    const p = ring[i];
    const q = ring[(i + 1) % ring.length];
    const cross = p.x * q.y - q.x * p.y;
    a += cross;
    cx += (p.x + q.x) * cross;
    cy += (p.y + q.y) * cross;
  }
  if (a === 0) return ring[0] ?? { x: 0, y: 0 };
  // ㄱ자 방에서는 외곽 사각형의 중심이 벽 밖으로 나간다 — 무게중심을 쓴다.
  return { x: cx / (3 * a), y: cy / (3 * a) };
}

export default function PlanCanvas({
  walls,
  openings,
  spaces,
  vga,
  paint,
  showExtremes = false,
  onSpace,
}: {
  walls: Wall[];
  openings: Opening[];
  spaces: Space[];
  /** 계산이 끝나기 전에는 `null`이다. 그때는 도면만 그린다. */
  vga: VgaResult | null;
  paint: Paint;
  /** 가장 높은 자리와 가장 낮은 자리에 표를 세운다. */
  showExtremes?: boolean;
  onSpace?: (s: Space) => void;
}) {
  const extremes = useMemo(() => {
    if (!vga || !paint || !vga.cells.length) return null;
    let hi = vga.cells[0];
    let lo = vga.cells[0];
    for (const c of vga.cells) {
      if (cellValue(c, paint) > cellValue(hi, paint)) hi = c;
      if (cellValue(c, paint) < cellValue(lo, paint)) lo = c;
    }
    return { hi, lo };
  }, [vga, paint]);

  const [lo, hi] = vga && paint ? range(vga, paint) : [0, 1];

  return (
    <svg
      className="pc"
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={
        paint ? "평면도 위에 계산한 값을 칠한 그림" : "평면도 — 벽·문·방 이름만 표시"
      }
    >
      {/* 방 바탕 — 값을 칠할 때는 히트맵이 덮으므로 옅게만 둔다. */}
      <g className="pc-spaces">
        {spaces.map((s) => (
          <polygon
            key={s.id}
            className={onSpace ? "pc-space pc-space--click" : "pc-space"}
            points={s.ring.map((p) => `${p.x},${p.y}`).join(" ")}
            fill={SPACE_FILL[s.kind]}
            fillOpacity={paint ? 0.35 : 1}
            onClick={onSpace ? () => onSpace(s) : undefined}
          >
            <title>{`${s.name} · ${s.areaM2}㎡`}</title>
          </polygon>
        ))}
      </g>

      {/* 계산 결과 — 격자 한 칸이 rect 하나다. */}
      {vga && paint && (
        <g className="pc-heat">
          {vga.cells.map((c) => (
            <rect
              key={`${c.col}-${c.row}`}
              x={c.col * vga.cell}
              y={c.row * vga.cell}
              width={vga.cell}
              height={vga.cell}
              fill={rampColor(norm(cellValue(c, paint), lo, hi))}
              fillOpacity={0.82}
            />
          ))}
        </g>
      )}

      {/* 벽은 언제나 값 위에 온다 — 값이 벽을 덮으면 방 경계를 못 읽는다. */}
      <g className="pc-walls">
        {walls.map((w, i) => (
          <line
            key={i}
            x1={w.a.x}
            y1={w.a.y}
            x2={w.b.x}
            y2={w.b.y}
            stroke="var(--label-strong)"
            strokeWidth={7}
            strokeLinecap="square"
          />
        ))}
      </g>

      <g className="pc-openings">
        {openings.map((o, i) => {
          const main = o.kind === "main" || o.kind === "exit";
          return (
            <line
              key={i}
              x1={o.a.x}
              y1={o.a.y}
              x2={o.b.x}
              y2={o.b.y}
              stroke={
                o.kind === "window"
                  ? "var(--blue-50)"
                  : main
                    ? "var(--lime-37)"
                    : "var(--surface-1)"
              }
              strokeWidth={main ? 9 : o.kind === "window" ? 5 : 8}
              strokeLinecap="butt"
            />
          );
        })}
      </g>

      {/* 방 이름 — 방이 글자보다 클 때만. 작은 방에 글자를 밀어 넣으면 벽을 넘는다. */}
      <g className="pc-labels">
        {spaces.map((s) => {
          const xs = s.ring.map((p) => p.x);
          const ys = s.ring.map((p) => p.y);
          const w = Math.max(...xs) - Math.min(...xs);
          const h = Math.max(...ys) - Math.min(...ys);
          if (w < s.name.length * 9 || h < 26) return null;
          const c = centroid(s.ring);
          return (
            <text
              key={s.id}
              className="pc-label"
              x={c.x}
              y={c.y}
              textAnchor="middle"
              dominantBaseline="middle"
              fontSize={13}
              fill="var(--label-neutral)"
            >
              {s.name}
            </text>
          );
        })}
      </g>

      {/* 가장 높은 자리와 가장 낮은 자리. 값 하나만 보여 주면 "그래서 어디?"가 남는다. */}
      {showExtremes && extremes && (
        <g className="pc-marks">
          {(
            [
              [extremes.hi, "가장 높음", "var(--blue-45)"],
              [extremes.lo, "가장 낮음", "var(--label-alternative)"],
            ] as const
          ).map(([c, label, color]) => (
            <g key={label}>
              <circle cx={c.x} cy={c.y} r={17} fill="none" stroke={color} strokeWidth={3} />
              <circle cx={c.x} cy={c.y} r={4} fill={color} />
              <rect
                x={c.x - 34}
                y={c.y - 44}
                width={68}
                height={20}
                rx={10}
                fill={color}
              />
              <text
                className="pc-label"
                x={c.x}
                y={c.y - 34}
                textAnchor="middle"
                dominantBaseline="middle"
                fontSize={12}
                fill="var(--static-white)"
              >
                {label}
              </text>
            </g>
          ))}
        </g>
      )}
    </svg>
  );
}

/** 히트맵 색 띠를 패널에서 같이 보여 준다 — 색만 있고 범례가 없으면 못 읽는다. */
export function RampLegend({ lowLabel, highLabel }: { lowLabel: string; highLabel: string }) {
  return (
    <div className="pc-legend">
      <span>{lowLabel}</span>
      <i
        aria-hidden="true"
        style={{ background: `linear-gradient(90deg, ${RAMP.join(", ")})` }}
      />
      <span>{highLabel}</span>
    </div>
  );
}
