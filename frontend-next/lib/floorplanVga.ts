/**
 * 평면도 위의 공간구조 계산 — **브라우저에서 실제로 돈다. 지어낸 숫자가 아니다.**
 *
 * 정의는 백엔드 `backend/app/services/floorplan_vga.py`와 **같아야 한다.** 데모가
 * 다른 식으로 계산하면 같은 도면에서 두 화면이 다른 답을 내고, 보는 사람은 어느
 * 쪽이 제품인지 알 수 없다. 그래서 아래 네 정의를 그대로 옮겼다:
 *
 *   1. 벽을 격자에 굽고(rasterize) 개구부를 다시 뚫는다.
 *   2. 바깥에서 흘려 넣어 실내만 남기고, 가장 큰 덩어리 하나만 쓴다.
 *   3. 두 칸 사이에 벽이 없으면 서로 "보인다" — 그 관계가 가시성 그래프다.
 *   4. 연결도 = 보이는 칸 수. 통합도 = 닿는칸수^1.2 / 깊이합.
 *
 * 통합도 식의 지수 1.2는 우리가 고른 값이 아니라 도로망 엔진(`space_syntax.py`)이
 * 쓰는 것과 같다. **같은 이름의 값이 화면마다 다른 뜻이면 비교가 불가능**해지므로
 * 실내와 실외가 같은 식을 쓴다.
 *
 * 백엔드와 다른 점은 격자 크기뿐이다. 한 칸을 도면 좌표 20으로 잡아 실내가 약
 * 1,750칸이 되고 계산이 0.6초쯤 걸린다(실측). 더 잘게 쪼개면 정밀해지지만
 * 브라우저에서 O(n²) 시야 판정을 돌리므로 값이 급히 오른다 — 한 칸 18이면
 * 2,184칸·1.16초, 16이면 2,772칸·1.90초다. 데모의 목적이 정밀도가 아니라
 * **무엇을 어떻게 계산하는지 보여 주는 것**이라 여기서 끊었고, 화면이 칸 수와
 * 걸린 시간을 그대로 밝힌다.
 */

import type { Opening, Pt, Wall } from "./demoFloorplan";

/** 격자 한 칸이 도면 좌표로 몇인지. 작을수록 정밀하고 느리다. */
export const CELL = 20;

export interface VgaCell {
  /** 격자 좌표 */
  col: number;
  row: number;
  /** 도면 좌표 (칸 중심) */
  x: number;
  y: number;
  /** 이 칸에서 보이는 칸 수 */
  connectivity: number;
  /** 닿는칸수^1.2 / 깊이합 */
  integration: number;
  /** 이 칸에서 다른 칸까지 평균 몇 겹 들어가야 하는가 */
  meanDepth: number;
}

export interface VgaResult {
  cols: number;
  rows: number;
  cell: number;
  cells: VgaCell[];
  /** 격자 인덱스 → cells 배열 위치. 없으면 -1 (벽이거나 바깥) */
  indexAt: Int32Array;
  connectivityMin: number;
  connectivityMax: number;
  integrationMin: number;
  integrationMax: number;
  integrationMean: number;
  depthMin: number;
  depthMax: number;
  /** 계산에 쓴 칸 수 — 화면이 "몇 칸으로 나눠 봤는지" 밝힌다. */
  cellCount: number;
  /** 실측 소요 시간(ms). 화면이 이 값을 그대로 보여 준다. */
  elapsedMs: number;
}

/* ── 1. 벽을 굽고 개구부를 뚫는다 ─────────────────────────────────────── */

function stamp(blocked: Uint8Array, cols: number, rows: number, c: number, r: number, on: boolean) {
  if (c < 0 || r < 0 || c >= cols || r >= rows) return;
  blocked[r * cols + c] = on ? 1 : 0;
}

/**
 * 선분 하나를 격자에 굽는다.
 *
 * `thick`을 켜면 지나는 칸의 상하좌우까지 막는다(백엔드 `_draw_wall`과 같다). 다만
 * **이 데모에서는 벽을 한 칸 두께로 굽는다.** 이 평면의 방이 도면 좌표로 100~400이라
 * 한 칸이 20인 격자에서 5~20칸뿐인데, 두껍게 굽으면 벽이 방을 먹어 실내가 23칸까지
 * 줄었다(실측). 벽이 축에 나란하고 바깥 판정이 4방향이므로 한 칸으로도 물이 새지
 * 않는다.
 */
function drawSegment(
  blocked: Uint8Array,
  cols: number,
  rows: number,
  a: Pt,
  b: Pt,
  on: boolean,
  thick: boolean,
) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const steps = Math.max(2, Math.ceil(Math.hypot(dx, dy) / (CELL * 0.35)));
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    const c = Math.floor((a.x + dx * t) / CELL);
    const r = Math.floor((a.y + dy * t) / CELL);
    stamp(blocked, cols, rows, c, r, on);
    if (thick) {
      stamp(blocked, cols, rows, c + 1, r, on);
      stamp(blocked, cols, rows, c - 1, r, on);
      stamp(blocked, cols, rows, c, r + 1, on);
      stamp(blocked, cols, rows, c, r - 1, on);
    }
  }
}

/* ── 2. 바깥을 걷어내고 가장 큰 실내 덩어리만 남긴다 ─────────────────── */

/**
 * 테두리에서 흘려 넣어 닿는 칸이 바깥이다. 이걸 안 하면 건물 **바깥 공터**가
 * 가장 잘 보이고 잘 통하는 자리로 나온다 — 사방이 뚫려 있으니 당연하다.
 *
 * ⚠️ **개구부를 뚫기 전의 벽으로 흘려야 한다.** 주출입구와 비상구는 외벽에
 * 뚫려 있으므로, 뚫은 뒤에 흘리면 물이 문으로 들어와 실내까지 전부 바깥으로
 * 칠한다. 그러면 남는 칸이 0개가 된다(실측). 백엔드 `_exterior_mask`도 같은
 * 순서다.
 */
function floodOutside(blocked: Uint8Array, cols: number, rows: number): Uint8Array {
  const outside = new Uint8Array(cols * rows);
  const stack: number[] = [];
  const push = (c: number, r: number) => {
    if (c < 0 || r < 0 || c >= cols || r >= rows) return;
    const i = r * cols + c;
    if (outside[i] || blocked[i]) return;
    outside[i] = 1;
    stack.push(i);
  };
  for (let c = 0; c < cols; c += 1) {
    push(c, 0);
    push(c, rows - 1);
  }
  for (let r = 0; r < rows; r += 1) {
    push(0, r);
    push(cols - 1, r);
  }
  while (stack.length) {
    const i = stack.pop() as number;
    const c = i % cols;
    const r = (i - c) / cols;
    push(c + 1, r);
    push(c - 1, r);
    push(c, r + 1);
    push(c, r - 1);
  }
  return outside;
}

/** 실내가 여러 덩어리로 갈리면 가장 큰 것만 쓴다 — 벽 사이 틈은 방이 아니다. */
function largestComponent(free: Uint8Array, cols: number, rows: number): Uint8Array {
  const label = new Int32Array(cols * rows).fill(-1);
  let best = -1;
  let bestSize = 0;
  let next = 0;

  for (let start = 0; start < free.length; start += 1) {
    if (!free[start] || label[start] >= 0) continue;
    const id = next++;
    let size = 0;
    const stack = [start];
    label[start] = id;
    while (stack.length) {
      const i = stack.pop() as number;
      size += 1;
      const c = i % cols;
      const r = (i - c) / cols;
      for (const [dc, dr] of [
        [1, 0],
        [-1, 0],
        [0, 1],
        [0, -1],
      ] as const) {
        const nc = c + dc;
        const nr = r + dr;
        if (nc < 0 || nr < 0 || nc >= cols || nr >= rows) continue;
        const j = nr * cols + nc;
        if (!free[j] || label[j] >= 0) continue;
        label[j] = id;
        stack.push(j);
      }
    }
    if (size > bestSize) {
      bestSize = size;
      best = id;
    }
  }

  const keep = new Uint8Array(cols * rows);
  // ⚠️ best가 -1이면 아무 덩어리도 못 찾은 것이다. label의 초기값도 -1이라
  // 그대로 비교하면 **빈 칸 전부가 실내로 뒤집힌다** — 0칸이어야 할 결과가
  // 격자 전체(1,750칸)로 나왔다.
  if (best < 0) return keep;
  for (let i = 0; i < keep.length; i += 1) if (label[i] === best) keep[i] = 1;
  return keep;
}

/* ── 3. 시야 그래프 ───────────────────────────────────────────────────── */

/** 두 칸 사이에 막힌 칸이 하나라도 있으면 안 보인다. */
function visible(
  blocked: Uint8Array,
  cols: number,
  c0: number,
  r0: number,
  c1: number,
  r1: number,
): boolean {
  const dc = c1 - c0;
  const dr = r1 - r0;
  const span = Math.max(Math.abs(dc), Math.abs(dr));
  if (span <= 1) return true;
  for (let s = 1; s < span; s += 1) {
    const t = s / span;
    const c = Math.round(c0 + dc * t);
    const r = Math.round(r0 + dr * t);
    if (blocked[r * cols + c]) return false;
  }
  return true;
}

/* ── 4. 깊이 — 비트셋으로 한 겹씩 넓힌다 ──────────────────────────────── */

/**
 * 시야 그래프는 빽빽하다(트인 방에서는 거의 모두가 서로 보인다). 그래서 칸마다
 * 따로 최단경로를 돌면 같은 변을 몇 번이고 다시 걷는다. 대신 "이미 닿은 집합"을
 * 한 겹씩 넓히면서 새로 들어온 칸 수만 세면 된다 — 백엔드 `_all_pairs_depth`가
 * 행렬 곱으로 하는 일을 여기서는 비트셋 OR로 한다.
 */
function allPairsDepth(rowsBits: Uint32Array[], n: number): { totalDepth: Float64Array; nodeCount: Float64Array } {
  const words = Math.ceil(n / 32);
  const totalDepth = new Float64Array(n);
  const nodeCount = new Float64Array(n);

  for (let i = 0; i < n; i += 1) {
    // 시작은 자기 자신과 직접 보이는 칸들.
    const reached = new Uint32Array(words);
    reached.set(rowsBits[i]);
    reached[i >>> 5] |= 1 << (i & 31);

    let deg = 0;
    for (let w = 0; w < words; w += 1) deg += popcount(rowsBits[i][w]);
    totalDepth[i] = deg;
    nodeCount[i] = deg;

    let step = 1;
    const frontier = new Uint32Array(words);
    frontier.set(rowsBits[i]);

    for (;;) {
      step += 1;
      const next = new Uint32Array(words);
      // 지금 경계에 있는 칸들이 보는 칸을 모은다.
      for (let w = 0; w < words; w += 1) {
        let bits = frontier[w];
        while (bits) {
          // 최하위 1비트의 자리. clz32는 정확하다 — log2는 부동소수라 31비트에서 흔들린다.
          const j = (w << 5) + (31 - Math.clz32((bits & -bits) >>> 0));
          const row = rowsBits[j];
          for (let k = 0; k < words; k += 1) next[k] |= row[k];
          bits &= bits - 1;
        }
      }
      let added = 0;
      for (let w = 0; w < words; w += 1) {
        const fresh = next[w] & ~reached[w];
        if (fresh) {
          added += popcount(fresh);
          reached[w] |= fresh;
        }
        frontier[w] = fresh;
      }
      if (added === 0) break;
      totalDepth[i] += step * added;
      nodeCount[i] += added;
    }
  }
  return { totalDepth, nodeCount };
}

function popcount(v: number): number {
  let x = v >>> 0;
  x = x - ((x >>> 1) & 0x55555555);
  x = (x & 0x33333333) + ((x >>> 2) & 0x33333333);
  x = (x + (x >>> 4)) & 0x0f0f0f0f;
  return (x * 0x01010101) >>> 24;
}

/* ── 조립 ─────────────────────────────────────────────────────────────── */

export function computeVga(
  walls: Wall[],
  openings: Opening[],
  viewW: number,
  viewH: number,
): VgaResult {
  const t0 =
    typeof performance !== "undefined" && typeof performance.now === "function"
      ? performance.now()
      : 0;

  const cols = Math.ceil(viewW / CELL);
  const rows = Math.ceil(viewH / CELL);
  const blocked = new Uint8Array(cols * rows);

  for (const w of walls) drawSegment(blocked, cols, rows, w.a, w.b, true, false);

  // 바깥을 정할 때는 **개구부까지 벽으로 세운다.**
  //
  // 이 도면의 `WALLS`는 창·주출입구 자리에서 끊어져 있다(그 자리에 개구부를
  // 겹쳐 그리지 않으려고 일부러 토막으로 뒀다). 그래서 벽만으로는 외피가 닫히지
  // 않고, 그 상태로 물을 흘리면 창틈으로 새어 들어와 실내 대부분이 바깥으로
  // 칠해진다 — 실측으로 실내가 1,276칸에서 442칸까지 줄었다. 개구부는 벽면에
  // 있는 것이므로 벽∪개구부가 곧 외피다.
  const sealed = new Uint8Array(cols * rows);
  sealed.set(blocked);
  for (const o of openings) drawSegment(sealed, cols, rows, o.a, o.b, true, false);
  const outside = floodOutside(sealed, cols, rows);

  // 분석용 격자에서는 개구부를 뚫는다 — 문으로 시야가 지나가야 한다. 두껍게
  // 지우지 않는다: 문 옆의 벽까지 사라지면 방이 통째로 트인 것처럼 계산된다.
  for (const o of openings) drawSegment(blocked, cols, rows, o.a, o.b, false, false);
  const free = new Uint8Array(cols * rows);
  for (let i = 0; i < free.length; i += 1) free[i] = !blocked[i] && !outside[i] ? 1 : 0;

  const keep = largestComponent(free, cols, rows);

  const indexAt = new Int32Array(cols * rows).fill(-1);
  const coords: { c: number; r: number }[] = [];
  for (let i = 0; i < keep.length; i += 1) {
    if (!keep[i]) continue;
    indexAt[i] = coords.length;
    const c = i % cols;
    coords.push({ c, r: (i - c) / cols });
  }

  const n = coords.length;
  const words = Math.ceil(Math.max(1, n) / 32);
  const rowsBits: Uint32Array[] = Array.from({ length: n }, () => new Uint32Array(words));
  const connectivity = new Float64Array(n);

  for (let i = 0; i < n; i += 1) {
    for (let j = i + 1; j < n; j += 1) {
      if (!visible(blocked, cols, coords[i].c, coords[i].r, coords[j].c, coords[j].r)) continue;
      rowsBits[i][j >>> 5] |= 1 << (j & 31);
      rowsBits[j][i >>> 5] |= 1 << (i & 31);
      connectivity[i] += 1;
      connectivity[j] += 1;
    }
  }

  const { totalDepth, nodeCount } = allPairsDepth(rowsBits, n);

  const cells: VgaCell[] = coords.map((p, i) => {
    const integration = totalDepth[i] > 0 ? Math.pow(nodeCount[i], 1.2) / totalDepth[i] : 0;
    const meanDepth = nodeCount[i] > 0 ? totalDepth[i] / nodeCount[i] : 0;
    return {
      col: p.c,
      row: p.r,
      x: (p.c + 0.5) * CELL,
      y: (p.r + 0.5) * CELL,
      connectivity: connectivity[i],
      integration,
      meanDepth,
    };
  });

  const conn = cells.map((c) => c.connectivity);
  const integ = cells.map((c) => c.integration);
  const dep = cells.map((c) => c.meanDepth);
  const t1 =
    typeof performance !== "undefined" && typeof performance.now === "function"
      ? performance.now()
      : 0;

  return {
    cols,
    rows,
    cell: CELL,
    cells,
    indexAt,
    connectivityMin: min(conn),
    connectivityMax: max(conn),
    integrationMin: min(integ),
    integrationMax: max(integ),
    integrationMean: integ.length ? integ.reduce((s, v) => s + v, 0) / integ.length : 0,
    depthMin: min(dep),
    depthMax: max(dep),
    cellCount: n,
    elapsedMs: Math.round(t1 - t0),
  };
}

function min(v: number[]): number {
  return v.length ? v.reduce((a, b) => (b < a ? b : a), v[0]) : 0;
}
function max(v: number[]): number {
  return v.length ? v.reduce((a, b) => (b > a ? b : a), v[0]) : 0;
}

/* ── 해석 ─────────────────────────────────────────────────────────────── */

export interface Reading {
  /** 화면에 나가는 문장. **전문 용어를 쓰지 않는다.** */
  text: string;
  /** 근거가 된 값. 툴팁·근거 칸에서만 전문 용어를 꺼낸다. */
  basis: { k: string; v: string }[];
}

/**
 * 값을 문장으로 옮긴다. 백엔드 `rule_based_floorplan_explanation`과 같은 규칙이다 —
 * **퍼짐(최댓값÷평균)이 2.5를 넘으면 공간이 뚜렷하게 나뉜 것으로 읽는다.**
 * 그 문턱은 근거가 있어서가 아니라 한 곳에서만 정하기 위해 고정한 값이고,
 * 화면이 그렇게 밝힌다.
 */
export function readVga(v: VgaResult): Reading {
  if (!v.cells.length) {
    return { text: "분석할 수 있는 실내 공간을 찾지 못했습니다.", basis: [] };
  }
  const sorted = [...v.cells].sort((a, b) => b.integration - a.integration);
  const top = sorted[0];
  const bottom = sorted[sorted.length - 1];
  const spread = v.integrationMean > 0 ? v.integrationMax / v.integrationMean : 0;

  const parts = [
    `실내를 ${v.cellCount.toLocaleString("ko-KR")}칸으로 나눠 서로 보이는 정도를 계산했습니다.`,
    spread >= 2.5
      ? "공간이 뚜렷하게 나뉘어 있어, 사람이 자연히 지나게 되는 가운데 자리와 구석진 곳의 차이가 큽니다."
      : "어느 자리에서 봐도 조건이 비슷해 고른 편입니다.",
    "가장 찾아오기 쉬운 자리와 가장 외진 자리를 도면에 표시했습니다.",
    "도면에서 뽑은 벽을 기준으로 계산한 결과이며, 가구 배치나 문 폭 같은 세부는 반영되지 않았습니다.",
  ];

  return {
    text: parts.join(" "),
    basis: [
      { k: "격자 칸 수", v: `${v.cellCount.toLocaleString("ko-KR")}칸` },
      { k: "퍼짐 (최댓값÷평균)", v: spread.toFixed(2) },
      { k: "찾아오기 쉬운 자리", v: `(${Math.round(top.x)}, ${Math.round(top.y)})` },
      { k: "가장 외진 자리", v: `(${Math.round(bottom.x)}, ${Math.round(bottom.y)})` },
      { k: "계산 시간", v: `${v.elapsedMs}ms` },
    ],
  };
}

/** 값 → 0~1. 히트맵 색을 정할 때 쓴다. */
export function norm(value: number, lo: number, hi: number): number {
  if (hi <= lo) return 0.5;
  return Math.min(1, Math.max(0, (value - lo) / (hi - lo)));
}
