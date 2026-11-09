"use client";

// React 19 타입에서는 JSX가 전역 네임스페이스가 아니다 — 반환 타입에 JSX.Element를
// 쓰려면 react에서 직접 가져와야 한다.
import type { JSX } from "react";
import {
  severityColor,
  type ActorId,
  type FloorplanDemo,
  type Improvement,
  type Issue,
  type Opening,
  type Pt,
  type RecogIssue,
  type Route,
  type Space,
  type SpaceKind,
  type Wall,
} from "@/lib/demoFloorplan";

/**
 * ⚠️ **아직 화면에 붙어 있지 않다.** `/demo`의 평면 공간구조 분석은 `PlanCanvas`
 * (공간구조 히트맵)를 쓴다. 이 파일은 `docs/SPEC_SPACE_ANALYSIS_MODULES.md` §4.3의 업무 탭
 * (발견된 문제 · 사용자 동선 · 길 찾기·가시성 · 공간 관계 · 설계 개선)에서 쓸
 * 오버레이들이고, 그 화면을 지을 때 붙는다. 지금 붙이면 계산이 없는 값을 그리게 된다.
 *
 * 도면 분석 데모의 도면 화면.
 *
 * 이 화면의 규칙은 하나다 — **도면은 탭이 바뀌어도 그대로다.** 벽·개구부·공간은
 * 언제나 같은 자리에 같은 모양으로 있고, 탭이 바꾸는 것은 그 위에 얹히는 표시뿐이다.
 * 탭마다 도면을 다시 그리면 보는 사람은 매번 "이게 아까 그 건물이 맞나"부터 다시
 * 확인해야 한다. 그래서 공통 레이어(공간 → 벽 → 개구부 → 실명)를 고정으로 깔고,
 * 모드별 오버레이를 그 위에 덧그린다.
 */

/* ------------------------------------------------------------------ *
 * 좌표계
 * ------------------------------------------------------------------ */

/**
 * 좌표계는 고정이다. 데이터의 모든 Pt는 이 viewBox 안의 값이고, 부모가 어떤
 * 크기든 meet으로 맞춰 넣으므로 도면이 잘리지 않는다. 화면 크기에 따라 스케일을
 * 직접 계산하는 코드를 두지 않는 이유가 이것이다 — SVG가 이미 한다.
 */
const VIEW_W = 1000;
const VIEW_H = 700;

export type CanvasMode = "recog" | "issues" | "routes" | "area" | "safety" | "improve" | "ab";

/** 모드마다 화면이 답하는 질문이 다르므로 aria-label도 달라야 한다. */
const MODE_ARIA: Record<CanvasMode, string> = {
  recog: "도면 인식 결과 평면도 — 인식하지 못한 지점에 번호를 표시했습니다",
  issues: "문제 진단 평면도 — 발견한 문제 위치를 심각도 색으로 표시했습니다",
  routes: "동선 분석 평면도 — 사용자 유형별 이동 경로와 겹치는 구간을 표시했습니다",
  area: "면적 배분 평면도 — 공간을 용도별 색으로 칠했습니다",
  safety: "안전·접근성 평면도 — 대피 방향과 확인이 필요한 지점을 표시했습니다",
  improve: "개선안 평면도 — 옮겨야 할 위치와 변경 범위를 표시했습니다",
  ab: "개선 전후 비교 평면도 — 두 안의 동선을 겹쳐 표시했습니다",
};

/**
 * FloorplanDemo는 "위 전부를 담는 컨테이너"라는 계약만 있고 필드 이름은 아직
 * 확정 전이다. 그래서 컨테이너를 이 리더 하나로만 읽는다. 필드 이름이 달라지면
 * 고칠 자리도 여기 한 곳이다. (타입을 다시 정의하는 게 아니다 — Wall·Space 같은
 * 원소 타입은 전부 lib에서 그대로 가져다 쓴다.)
 */
type PlanParts = {
  walls?: Wall[];
  openings?: Opening[];
  spaces?: Space[];
  issues?: Issue[];
  routes?: Route[];
  improvements?: Improvement[];
  recogIssues?: RecogIssue[];
};

function parts(plan: FloorplanDemo): PlanParts {
  return (plan ?? {}) as unknown as PlanParts;
}

function list<T>(v: T[] | undefined): T[] {
  return Array.isArray(v) ? v : [];
}

/* ------------------------------------------------------------------ *
 * 색
 * ------------------------------------------------------------------ */

/** 공간 용도별 색. 잔여공간만 색이 아니라 빗금 패턴이다 — 눈에 걸려야 하는 값이다. */
const SPACE_FILL: Record<SpaceKind, string> = {
  use: "var(--blue-90)",
  circulation: "var(--lime-90)",
  shared: "var(--purple-90)",
  facility: "var(--orange-90)",
  leftover: "url(#fpc-hatch-leftover)",
  unassigned: "var(--fill-alternative)",
};

/** 사용자 유형별 경로 색. 점선이냐 실선이냐는 데이터(Route.style)가 정한다. */
const ACTOR_COLOR: Record<ActorId, string> = {
  visitor: "var(--blue-50)",
  staff: "var(--lime-37)",
  manager: "var(--purple-40)",
  logistics: "var(--orange-39)",
  mobility: "var(--cool-neutral-25)",
};

const WALL_COLOR = "var(--label-strong)";

const OPENING_COLOR: Record<Opening["kind"], string> = {
  door: "var(--lime-37)",
  window: "var(--blue-50)",
  main: "var(--red-50)",
  exit: "var(--orange-39)",
};

/** 주출입구와 비상구는 더 굵다 — 대피 이야기를 할 때 제일 먼저 찾는 선이다. */
const OPENING_WIDTH: Record<Opening["kind"], number> = {
  door: 5,
  window: 4,
  main: 9,
  exit: 9,
};

/* ------------------------------------------------------------------ *
 * 기하 도우미
 * ------------------------------------------------------------------ */

function pts(path: Pt[]): string {
  return path.map((p) => `${p.x},${p.y}`).join(" ");
}

function bbox(ring: Pt[]) {
  const xs = ring.map((p) => p.x);
  const ys = ring.map((p) => p.y);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const y0 = Math.min(...ys);
  const y1 = Math.max(...ys);
  return { x0, y0, x1, y1, w: x1 - x0, h: y1 - y0, cx: (x0 + x1) / 2, cy: (y0 + y1) / 2 };
}

/** 폴리곤 무게중심. ㄱ자 방은 bbox 중심이 벽 밖으로 나가서 실명이 엉뚱한 데 찍힌다. */
function centroid(ring: Pt[]): Pt {
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
  if (Math.abs(a) < 1e-6) {
    const b = bbox(ring);
    return { x: b.cx, y: b.cy };
  }
  a *= 0.5;
  return { x: cx / (6 * a), y: cy / (6 * a) };
}

function segments(path: Pt[]): Array<[Pt, Pt]> {
  const out: Array<[Pt, Pt]> = [];
  for (let i = 0; i + 1 < path.length; i += 1) out.push([path[i], path[i + 1]]);
  return out;
}

function angleDeg(a: Pt, b: Pt): number {
  return (Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI;
}

function mid(a: Pt, b: Pt): Pt {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

function dist(a: Pt, b: Pt): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/**
 * 두 경로가 같은 복도를 쓰는 구간을 찾는다.
 *
 * 좌표가 딱 맞아떨어지길 기대하면 안 된다 — 방문자와 물류가 같은 복도를 걸어도
 * 폴리라인 점은 다르게 찍힌다. 그래서 "중점이 가깝고 방향이 나란하면 같은 구간"으로
 * 본다. 24는 viewBox 1000 기준 복도 한 폭쯤이고, 반대 방향으로 지나가는 것도
 * 겹침이므로 방향은 부호를 무시하고 나란함만 본다.
 */
function overlapWith(a: Pt[], b: Pt[]): Array<[Pt, Pt]> {
  const bSegs = segments(b);
  return segments(a).filter(([p, q]) => {
    const m = mid(p, q);
    const len = dist(p, q) || 1;
    const ux = (q.x - p.x) / len;
    const uy = (q.y - p.y) / len;
    return bSegs.some(([r, s]) => {
      if (dist(m, mid(r, s)) > 24) return false;
      const l2 = dist(r, s) || 1;
      const vx = (s.x - r.x) / l2;
      const vy = (s.y - r.y) / l2;
      return Math.abs(ux * vy - uy * vx) < 0.25;
    });
  });
}

/** 폴리곤 안에 점이 있나 — 막다른 통로 판정에만 쓰는 파생 계산이다. */
function inRing(ring: Pt[], p: Pt): boolean {
  let hit = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const a = ring[i];
    const b = ring[j];
    if (a.y > p.y !== b.y > p.y && p.x < ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x) hit = !hit;
  }
  return hit;
}

/* ------------------------------------------------------------------ *
 * 조각
 * ------------------------------------------------------------------ */

/**
 * 화살촉을 <marker>가 아니라 도형으로 직접 그린다. marker는 색을 선에서 물려받지
 * 못해서(context-stroke 지원이 고르지 않다) 경로 색마다 마커를 복제해야 하는데,
 * 그러면 defs가 경로 수만큼 늘어난다. 회전 변환 한 줄이 더 싸다.
 */
function Arrow({
  at,
  deg,
  color,
  size = 9,
  opacity = 1,
}: {
  at: Pt;
  deg: number;
  color: string;
  size?: number;
  opacity?: number;
}) {
  return (
    <path
      className="fpc-arrow"
      d={`M ${-size} ${-size * 0.62} L ${size * 0.9} 0 L ${-size} ${size * 0.62} Z`}
      fill={color}
      opacity={opacity}
      transform={`translate(${at.x} ${at.y}) rotate(${deg})`}
    />
  );
}

/** 번호 배지. 문제·인식오류·병목이 전부 같은 모양을 쓴다 — 탭이 달라도 같은 언어다. */
function Badge({
  at,
  no,
  color,
  r = 13,
  dim = false,
  title,
  onClick,
}: {
  at: Pt;
  no: number;
  color: string;
  r?: number;
  dim?: boolean;
  title?: string;
  onClick?: () => void;
}) {
  return (
    <g
      className={onClick ? "fpc-badge fpc-badge--clickable" : "fpc-badge"}
      opacity={dim ? 0.28 : 1}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      aria-label={onClick ? `${no}번 표시` : undefined}
    >
      <circle cx={at.x} cy={at.y} r={r} fill={color} stroke="var(--static-white)" strokeWidth={2} />
      <text
        className="fpc-badge-text"
        x={at.x}
        y={at.y}
        textAnchor="middle"
        dominantBaseline="central"
        fontSize={Math.round(r * 0.95)}
        fontWeight={700}
        fill="var(--static-white)"
      >
        {no}
      </text>
      {title ? <title>{title}</title> : null}
    </g>
  );
}

/** 경로 한 줄 — 폴리라인 + 꺾이는 자리의 진행 방향 화살표. */
function RouteLine({ route, muted = false }: { route: Route; muted?: boolean }) {
  const color = muted ? "var(--chart-secondary)" : ACTOR_COLOR[route.actor] ?? ACTOR_COLOR.visitor;
  const path = list(route.path);

  // 화살표는 꺾이는 자리에만 놓는다. 모든 점에 놓으면 직선 구간이 화살표 띠가 되어
  // 경로 자체가 안 보인다. 25도는 사람이 "방향이 바뀌었다"고 느끼는 정도다.
  const turns: Array<{ at: Pt; deg: number }> = [];
  for (let i = 1; i + 1 < path.length; i += 1) {
    const inDeg = angleDeg(path[i - 1], path[i]);
    const outDeg = angleDeg(path[i], path[i + 1]);
    let delta = Math.abs(outDeg - inDeg);
    if (delta > 180) delta = 360 - delta;
    if (delta > 25) turns.push({ at: path[i], deg: outDeg });
  }
  // 곧게 뻗은 경로도 어느 쪽으로 가는지는 말해 줘야 한다.
  if (!turns.length && path.length >= 2) {
    const a = path[path.length - 2];
    const b = path[path.length - 1];
    turns.push({ at: mid(a, b), deg: angleDeg(a, b) });
  }

  return (
    <g className="fpc-route" data-actor={route.actor}>
      <polyline
        points={pts(path)}
        fill="none"
        stroke={color}
        strokeWidth={muted ? 4 : 5}
        strokeOpacity={muted ? 0.7 : 1}
        strokeLinejoin="round"
        strokeLinecap="round"
        strokeDasharray={route.style === "dash" ? "14 9" : undefined}
      >
        <title>{route.label}</title>
      </polyline>
      {turns.map((t, i) => (
        <Arrow key={`tn-${i}`} at={t.at} deg={t.deg} color={color} size={8} opacity={muted ? 0.7 : 1} />
      ))}
    </g>
  );
}

/* ------------------------------------------------------------------ *
 * 본체
 * ------------------------------------------------------------------ */

export default function FloorplanCanvas({
  plan,
  mode,
  actors,
  focusIssue,
  improvementId,
  safetyView = "evacuation",
  abView = "a",
  onSpace,
  onIssue,
}: {
  plan: FloorplanDemo;
  mode: CanvasMode;
  /** routes 모드에서 그릴 사용자 유형들. 비면 방문자만. */
  actors?: ActorId[];
  /** 강조할 문제 번호. 있으면 그 번호만 크게, 나머지는 옅게. */
  focusIssue?: number | null;
  /** improve 모드에서 그릴 개선안 id. */
  improvementId?: string | null;
  /** safety 모드의 내부 메뉴. */
  safetyView?: "evacuation" | "accessibility";
  /** ab 모드에서 어느 안을 그릴지. */
  abView?: "a" | "b" | "diff";
  /** 공간을 누르면 알려 준다. area 모드에서만 커서가 바뀐다. */
  onSpace?: (space: Space) => void;
  onIssue?: (no: number) => void;
}): JSX.Element {
  const p = parts(plan);
  const spaces = list(p.spaces);
  const walls = list(p.walls);
  const openings = list(p.openings);
  const issues = list(p.issues);
  const routes = list(p.routes);

  const areaMode = mode === "area";

  /* ---- 공통 레이어: 어떤 탭에서도 이 네 겹은 같다 ------------------ */

  const spaceLayer = spaces.map((s) => (
    <polygon
      key={`sp-${s.id}`}
      className={areaMode ? "fpc-space fpc-space--clickable" : "fpc-space"}
      points={pts(s.ring)}
      // 용도별 색은 면적 탭에서만 칠한다. 다른 탭에서 배경이 요란하면 그 탭의
      // 주인공(문제 번호·경로)이 묻힌다.
      fill={areaMode ? SPACE_FILL[s.kind] : "var(--fill-normal)"}
      fillOpacity={areaMode ? 0.9 : 0.55}
      stroke="var(--line-normal-normal)"
      strokeWidth={1}
      onClick={onSpace ? () => onSpace(s) : undefined}
    >
      <title>{`${s.name} · ${s.areaM2}㎡${s.note ? ` · ${s.note}` : ""}`}</title>
    </polygon>
  ));

  const wallLayer = walls.map((w, i) => (
    <line
      key={`wl-${i}`}
      className="fpc-wall"
      x1={w.a.x}
      y1={w.a.y}
      x2={w.b.x}
      y2={w.b.y}
      stroke={WALL_COLOR}
      strokeWidth={7}
      strokeLinecap="square"
    />
  ));

  // 개구부는 벽 위에 얹는다. 벽을 지우고 그리면 벽이 끊긴 것처럼 보여서
  // "여기 뚫려 있다"가 아니라 "여기 도면이 빠졌다"로 읽힌다.
  const openingLayer = openings.map((o, i) => (
    <line
      key={`op-${i}`}
      className="fpc-opening"
      x1={o.a.x}
      y1={o.a.y}
      x2={o.b.x}
      y2={o.b.y}
      stroke={OPENING_COLOR[o.kind]}
      strokeWidth={OPENING_WIDTH[o.kind]}
      strokeLinecap="round"
    />
  ));

  /** 실명은 방이 넉넉할 때만 그린다. 넘치는 글자는 도면을 읽는 걸 방해한다. */
  const labelLayer = spaces.flatMap((s) => {
    const box = bbox(s.ring);
    const size = box.w > 150 && box.h > 90 ? 14 : 11;
    const textW = s.name.length * size * 0.62;
    if (box.w < textW + 10 || box.h < size * 2 + 6) return [];
    const c = centroid(s.ring);
    return [
      <text
        key={`lb-${s.id}`}
        className="fpc-label"
        x={c.x}
        y={c.y}
        textAnchor="middle"
        dominantBaseline="central"
        fontSize={size}
        fill="var(--label-neutral)"
      >
        {s.name}
      </text>,
    ];
  });

  /* ---- 모드별 오버레이 -------------------------------------------- */

  let overlay: JSX.Element | null = null;

  if (mode === "recog") {
    // 인식 결과. 못 읽은 자리를 점선 원으로 둘러싸 "여기는 자동으로 못 봤다"를
    // 도면 위에서 바로 말한다. 실선 원은 실제 구조물로 오해된다.
    const recog: RecogIssue[] = list(p.recogIssues);
    overlay = (
      <g className="fpc-overlay fpc-overlay--recog">
        {recog.map((r) => (
          <g key={`rc-${r.no}`}>
            <circle
              className="fpc-recog-ring"
              cx={r.at.x}
              cy={r.at.y}
              r={30}
              fill="none"
              stroke="var(--orange-39)"
              strokeWidth={2}
              strokeDasharray="7 5"
            />
            <Badge at={r.at} no={r.no} color="var(--orange-39)" title={r.text} />
          </g>
        ))}
      </g>
    );
  }

  if (mode === "issues") {
    const focused = typeof focusIssue === "number" ? focusIssue : null;
    overlay = (
      <g className="fpc-overlay fpc-overlay--issues">
        {issues.map((it) => {
          // 색은 데이터가 들고 있는 유일한 자리다. severityColor가 준 hex를
          // CSS 변수로 바꾸지 않는다 — 바꾸면 두 곳에서 색이 갈린다.
          const color = severityColor(it.severity);
          const isFocus = focused !== null && it.no === focused;
          return (
            <g key={`is-${it.no}`}>
              {/* 파동은 정적인 두 겹이다. 움직이면 발표 내내 시선을 뺏는다. */}
              {isFocus ? (
                <>
                  <circle cx={it.at.x} cy={it.at.y} r={40} fill={color} fillOpacity={0.1} />
                  <circle
                    cx={it.at.x}
                    cy={it.at.y}
                    r={28}
                    fill="none"
                    stroke={color}
                    strokeWidth={2}
                    strokeOpacity={0.55}
                  />
                </>
              ) : null}
              <Badge
                at={it.at}
                no={it.no}
                color={color}
                r={isFocus ? 19 : 13}
                dim={focused !== null && !isFocus}
                title={it.title}
                onClick={onIssue ? () => onIssue(it.no) : undefined}
              />
            </g>
          );
        })}
      </g>
    );
  }

  if (mode === "routes") {
    const wanted = actors && actors.length ? actors : (["visitor"] as ActorId[]);
    const shown = routes.filter((r) => wanted.includes(r.actor));
    // 겹침은 한 경로의 성질이 아니라 두 경로 사이의 관계다. 그래서 화면에 올라온
    // 경로들끼리만 그리기 직전에 계산한다 — 유형을 하나만 켜면 겹침도 없는 게 맞다.
    const overlaps: Array<[Pt, Pt]> = [];
    for (let i = 0; i < shown.length; i += 1) {
      for (let j = i + 1; j < shown.length; j += 1) {
        overlaps.push(...overlapWith(shown[i].path, shown[j].path));
      }
    }
    overlay = (
      <g className="fpc-overlay fpc-overlay--routes">
        {/* 겹침 띠를 경로보다 먼저 깔아 경로가 그 위를 지나가게 한다. */}
        {overlaps.map(([a, b], i) => (
          <line
            key={`ov-${i}`}
            className="fpc-overlap"
            x1={a.x}
            y1={a.y}
            x2={b.x}
            y2={b.y}
            stroke="var(--red-50)"
            strokeWidth={14}
            strokeOpacity={0.28}
            strokeLinecap="round"
          />
        ))}
        {shown.map((r) => (
          <RouteLine key={`rt-${r.actor}`} route={r} />
        ))}
        {shown.flatMap((r) =>
          list(r.bottlenecks).map((b, i) => (
            <Badge key={`bn-${r.actor}-${i}`} at={b} no={i + 1} color="var(--red-50)" r={11} title={`${r.label} 병목`} />
          )),
        )}
      </g>
    );
  }

  if (mode === "safety" && safetyView === "evacuation") {
    // 출구 위치는 개구부 데이터가 준다. 화살표는 건물 중심에서 그 출구를 향하는
    // 방향으로 놓는다 — 대피 경로 좌표가 데이터에 없으므로 있는 값만으로 방향을
    // 만든다. 없는 경로를 지어 그리지 않는다.
    const exits = openings.filter((o) => o.kind === "exit" || o.kind === "main");
    const center = { x: VIEW_W / 2, y: VIEW_H / 2 };
    // 막다른 통로 = 어떤 경로도 지나가지 않는 통로 공간. 데이터가 "막다른 곳"이라고
    // 말해 주진 않지만, 통로 공간과 경로 좌표에서 계산으로 나오는 값이다.
    const deadEnds = spaces.filter(
      (s) => s.kind === "circulation" && !routes.some((r) => list(r.path).some((q) => inRing(s.ring, q))),
    );
    overlay = (
      <g className="fpc-overlay fpc-overlay--evacuation">
        {deadEnds.map((s) => {
          const c = centroid(s.ring);
          return (
            <g key={`de-${s.id}`}>
              <polygon
                className="fpc-deadend"
                points={pts(s.ring)}
                fill="var(--red-50)"
                fillOpacity={0.12}
                stroke="var(--red-50)"
                strokeWidth={2}
                strokeDasharray="6 5"
              />
              <text
                className="fpc-label"
                x={c.x}
                y={c.y + 18}
                textAnchor="middle"
                fontSize={11}
                fontWeight={600}
                fill="var(--red-50)"
              >
                막다른 통로
              </text>
            </g>
          );
        })}
        {exits.map((o, i) => {
          const m = mid(o.a, o.b);
          return <Arrow key={`ex-${i}`} at={m} deg={angleDeg(center, m)} color="var(--orange-39)" size={13} />;
        })}
      </g>
    );
  }

  if (mode === "safety" && safetyView === "accessibility") {
    // 휠체어 경로와 폭 확인이 필요한 지점은 mobility 경로 하나에 다 들어 있다.
    // 그 경로가 없으면 아무것도 그리지 않는다.
    const wheel = routes.find((r) => r.actor === "mobility");
    overlay = (
      <g className="fpc-overlay fpc-overlay--accessibility">
        {wheel ? <RouteLine route={wheel} /> : null}
        {wheel
          ? list(wheel.bottlenecks).map((b, i) => (
              <g key={`wb-${i}`}>
                <circle
                  cx={b.x}
                  cy={b.y}
                  r={20}
                  fill="none"
                  stroke="var(--orange-39)"
                  strokeWidth={2}
                  strokeDasharray="5 4"
                />
                <Badge at={b} no={i + 1} color="var(--orange-39)" r={11} title="폭 확인 필요" />
              </g>
            ))
          : null}
      </g>
    );
  }

  if (mode === "improve") {
    const imp = list(p.improvements).find((x) => x.id === improvementId) ?? null;
    const from = imp?.moveFrom;
    const to = imp?.moveTo;
    overlay = (
      <g className="fpc-overlay fpc-overlay--improve">
        {imp && from && to ? (
          <>
            {/* 변경 예상 범위 — 옮기는 두 지점을 넉넉히 감싼다. 공사가 닿는 면이
                점 두 개가 아니라 그 사이 구역 전체라는 걸 보여 준다. */}
            <rect
              className="fpc-scope"
              x={Math.min(from.x, to.x) - 46}
              y={Math.min(from.y, to.y) - 46}
              width={Math.abs(to.x - from.x) + 92}
              height={Math.abs(to.y - from.y) + 92}
              rx={10}
              fill="var(--blue-50)"
              fillOpacity={0.06}
              stroke="var(--blue-50)"
              strokeWidth={2}
              strokeDasharray="8 6"
            />
            <line
              x1={from.x}
              y1={from.y}
              x2={to.x}
              y2={to.y}
              stroke="var(--blue-50)"
              strokeWidth={4}
              strokeLinecap="round"
            />
            <circle cx={from.x} cy={from.y} r={8} fill="var(--static-white)" stroke="var(--blue-50)" strokeWidth={3} />
            <Arrow at={to} deg={angleDeg(from, to)} color="var(--blue-50)" size={12} />
            <text className="fpc-label" x={from.x} y={from.y - 16} textAnchor="middle" fontSize={12} fill="var(--label-neutral)">
              지금
            </text>
            <text
              className="fpc-label"
              x={to.x}
              y={to.y - 20}
              textAnchor="middle"
              fontSize={12}
              fontWeight={600}
              fill="var(--blue-45)"
            >
              {imp.title}
            </text>
          </>
        ) : null}
      </g>
    );
  }

  if (mode === "ab") {
    const visitor = routes.find((r) => r.actor === "visitor") ?? null;
    const others = routes.filter((r) => r.actor !== "visitor");
    // 개선안 B는 "겹침이 사라진 방문자 동선"이다. A와 B의 차이는 경로 모양이 아니라
    // 겹침 구간이 있느냐 없느냐라서, 같은 폴리라인을 쓰고 겹침 띠만 A에 그린다.
    // 도면과 경로가 그대로여야 보는 눈이 차이만 본다.
    const changed = visitor ? others.flatMap((o) => overlapWith(list(visitor.path), list(o.path))) : [];
    overlay = (
      <g className="fpc-overlay fpc-overlay--ab">
        {visitor && (abView === "a" || abView === "b") ? (
          <>
            {abView === "a"
              ? changed.map(([a, b], i) => (
                  <line
                    key={`ab-ov-${i}`}
                    className="fpc-overlap"
                    x1={a.x}
                    y1={a.y}
                    x2={b.x}
                    y2={b.y}
                    stroke="var(--red-50)"
                    strokeWidth={14}
                    strokeOpacity={0.28}
                    strokeLinecap="round"
                  />
                ))
              : null}
            <RouteLine route={visitor} />
          </>
        ) : null}
        {visitor && abView === "diff" ? (
          <>
            {/* 안 바뀐 부분은 회색으로 죽인다. 둘 다 진하면 무엇이 달라졌는지 보는
                사람이 직접 비교해야 하는데, 그건 화면이 할 일이다. */}
            <RouteLine route={visitor} muted />
            {changed.map(([a, b], i) => (
              <line
                key={`ab-df-${i}`}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke="var(--red-50)"
                strokeWidth={7}
                strokeLinecap="round"
              />
            ))}
            {changed.map(([a, b], i) => (
              <Arrow key={`ab-ar-${i}`} at={mid(a, b)} deg={angleDeg(a, b)} color="var(--red-50)" size={8} />
            ))}
          </>
        ) : null}
      </g>
    );
  }

  return (
    <svg
      className="fpc-canvas"
      role="img"
      aria-label={MODE_ARIA[mode]}
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      preserveAspectRatio="xMidYMid meet"
      style={{ width: "100%", height: "100%", display: "block" }}
    >
      <defs>
        {/* 잔여공간 빗금. 색 하나로는 "쓸 데 없이 남은 자리"라는 성질이 안 산다. */}
        <pattern
          id="fpc-hatch-leftover"
          width={10}
          height={10}
          patternUnits="userSpaceOnUse"
          patternTransform="rotate(45)"
        >
          <rect width={10} height={10} fill="var(--red-95)" />
          <line x1={0} y1={0} x2={0} y2={10} stroke="var(--red-50)" strokeWidth={3} strokeOpacity={0.45} />
        </pattern>
      </defs>

      <rect x={0} y={0} width={VIEW_W} height={VIEW_H} fill="var(--background-elevated-normal)" />

      <g className="fpc-layer fpc-layer--spaces">{spaceLayer}</g>
      <g className="fpc-layer fpc-layer--walls">{wallLayer}</g>
      <g className="fpc-layer fpc-layer--openings">{openingLayer}</g>
      <g className="fpc-layer fpc-layer--labels">{labelLayer}</g>

      {overlay}
    </svg>
  );
}

/* ------------------------------------------------------------------ *
 * CSS가 필요한 클래스 — 스타일시트는 이 파일 밖에 있다.
 * (SVG 안에 <style>을 넣지 않는 게 이 컴포넌트의 규칙이다.)
 *
 *   .fpc-canvas           display:block — 부모 높이를 그대로 채운다
 *   .fpc-space            fill·fill-opacity transition (0.12s쯤)
 *   .fpc-space--clickable cursor:pointer, :hover에서 fill-opacity를 1로 올려 진해지게
 *   .fpc-badge            user-select:none
 *   .fpc-badge--clickable cursor:pointer, :hover에서 살짝 밝게(filter 또는 opacity)
 *   .fpc-badge-text       user-select:none; pointer-events:none
 *   .fpc-label            pointer-events:none; user-select:none — 글자가 공간 클릭을 막지 않게
 *   .fpc-route            pointer-events:none (title 툴팁만 필요하면 auto로)
 *   .fpc-overlap          pointer-events:none
 *   .fpc-arrow            pointer-events:none
 *   .fpc-recog-ring       pointer-events:none
 *   .fpc-deadend          pointer-events:none
 *   .fpc-scope            pointer-events:none
 * ------------------------------------------------------------------ */
