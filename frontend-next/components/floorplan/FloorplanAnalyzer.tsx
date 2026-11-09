"use client";

import { onTranslations, translateText } from "@/lib/autoTranslate";
import { SpeakButton } from "@/components/SpeakButton";
import { FloorplanVoiceEdit } from "@/components/floorplan/FloorplanVoiceEdit";
import { FloorplanChat } from "@/components/floorplan/FloorplanChat";
import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  api,
  ApiClientError,
  type FloorplanAnalyzeResponse,
  type FloorplanCell,
  type FloorplanCompareResponse,
  type FloorplanSummary,
  type FloorplanDelta,
  type FloorplanEditIntentResponse,
  type FloorplanIssue,
  type FloorplanIssueSeverity,
  type FloorplanLabel,
  type FloorplanLayer,
  type FloorplanLine,
  type FloorplanSymbol,
  type FloorplanRoom,
  type FloorplanSymbolKind,
  type FloorplanSpot,
} from "@/lib/api";
import {
  COMPARE_WHY,
  compareHeadline,
  downloadFloorplanReport,
  formatCompareValue,
  issuePlace,
  issueZoneColor,
  nonDxfMessage,
  roomName,
  roomThroughRanks,
} from "@/lib/floorplanReport";
import { applyEditPlan, confirmIntent, hasChanges, previewOf, selectionFrom, type VoiceSelection } from "@/lib/floorplanEditOps";
import { HEAT_STOPS, lerpColor, valueExtent } from "@/lib/mapUtils";
import { scrollIntoPanel, scrollParent } from "@/lib/scrollPanel";

// 도면 색 램프 — 지도 화면과 같은 무지개(HEAT_STOPS, depthmapX 관례).
// 2026-10-24에 파랑 → 회색 → 빨강 3색으로 줄였다가 같은 날 되돌렸다(사용자 "노랑과 초록이 없어").
// 이 도면은 바닥의 55%가 중간값이라 3색에선 「꽤 좋은 곳」과 「보통」이 같은 회색으로 뭉갰고,
// 공간구문론을 아는 사람은 무지개에 익숙하다.
const FLOOR_STOPS = HEAT_STOPS;

function floorColor(value: number, min: number, max: number): string {
  if (max <= min) return lerpColor(FLOOR_STOPS, 0.5);
  return lerpColor(FLOOR_STOPS, (value - min) / (max - min));
}

const FLOOR_RAMP = FLOOR_STOPS.map(([r, g, b]) => `rgb(${r}, ${g}, ${b})`);

// 화면에 보이는 캔버스 폭 — 실제 좌표(canvasWidth/canvasHeight, 백엔드 픽셀
// 공간)와는 별개의 표시 스케일이다. 도면이 아무리 커도 화면에는 이 폭으로
// 맞춰 그리고, 클릭 좌표는 이 스케일로 나눠 원래 좌표로 되돌린다.
const DISPLAY_MAX_WIDTH = 720;
// 도면 둘레 여백(캔버스 픽셀). 바깥벽은 늘 도면 테두리 위에 놓여 캔버스 가장자리·둥근
// 모서리에 반쯤 잘려 안 보였다 — 고르거나 문을 낼 수도 없었다(2026-11-08 UX 자동 개선).
const CANVAS_PAD = 12;
// 선 근처 클릭 판정 반경(표시 픽셀 기준).
const HIT_RADIUS_PX = 10;
// 샘플 도면 — 라이브에서 끝까지 도는 것만 둔다 (2026-10-22 실측). 대형 복합몰: 광장·순환
// 통로는 빨갛고 막다른 날개·골목은 파랗게 갈린다(반영률 100%, 공간 80곳). wtc-mall.dxf는
// 반영률 64%라 쓰지 않는다. 2026-10-24에 「쇼핑몰(실명 포함)」을 내렸다 — 구조가 너무 단순해
// 「자리마다 다르다」는 취지를 못 보여 준다(사용자 지적). 실명은 복합몰에도 들어 있다.
const SAMPLE_PLANS = [
  { label: "샘플 도면(대형 복합몰 · 사람 흐름)", path: "/samples/showcase-mall.dxf", fileName: "샘플_대형복합몰.dxf" },
  // 2026-11-09(해커톤 01): 로봇 통로용 가상 물류센터 — 랙 사이 통로 폭이 다르고 막다른 통로가 있다.
  // 평면이 자주 바뀌는 곳이라 「랙 옮기고 다시 분석 → 바꾼 것과 비교」가 쓸모의 핵심이다.
  { label: "샘플 도면(물류센터 · 로봇 통로, 가상)", path: "/samples/logistics-center.dxf", fileName: "샘플_물류센터_가상.dxf" },
] as const;

type OverlayMetric = "integration" | "choice" | "integration_local" | "connectivity" | "robot";

/** 「로봇 통로」 탭을 보일 도면인가 — 축척이 있고 로봇이 못 가는 통로가 조금이라도 있을 때. 통로가 다 넓은
 *  쇼핑몰(100%)에는 로봇 얘기를 꺼내지 않는다(2026-11-09). */
function hasRobotAisles(summary: FloorplanSummary): boolean {
  return summary.robot_passable_pct != null && summary.robot_passable_pct < 99;
}

/** 칸의 지표 값 — 「로봇 통로」는 통로 폭(m)이다. */
function cellValue(cell: FloorplanCell, metric: OverlayMetric): number {
  return metric === "robot" ? (cell.aisle_width_m ?? 0) : cell[metric];
}
/** 결과 뷰 — 기본은 「발견된 문제」. 지표 뷰는 그 다음이다 (SPEC_FLOORPLAN_TABS_UI 결정 1). */
type ResultView = "issues" | "rooms" | OverlayMetric;

const SEVERITY: Record<FloorplanIssueSeverity, { label: string; color: string }> = {
  high: { label: "즉시 검토", color: "#dc2626" },
  medium: { label: "개선 권장", color: "#d97706" },
  low: { label: "참고", color: "#2563eb" },
};

/**
 * 화면에 보이는 지표 이름 — 쉬운 한국어만, 전문 용어는 툴팁에서만
 * (lib/metricInfo.ts의 표기 원칙).
 *
 * Integration은 앱 공통 용어인 "목"으로 부른다. 발표 덱이 이 기능을
 * "건물 안에서의 목 / 실내 목 계산"이라고 부르므로, 화면만 다른 말을 쓰면
 * 데모에서 말이 어긋난다. UndervaluedPanel과 같이 용어 옆에 정의를 붙인다.
 *
 * Connectivity는 "목"이 아니다 — 지나다니는 자리가 아니라 한 자리에서 눈에
 * 들어오는 범위라서, 별개의 쉬운 말을 쓴다.
 *
 * METRIC_INFO를 그대로 못 쓰는 이유: 거기 문구는 전부 "길" 기준이다
 * ("쉽게 다른 길과 이어지는 위치"). 여기는 건물 안이라 방·동선을 말해야 한다.
 */
const METRIC_LABELS: Record<OverlayMetric, { easy: string; jargon: string; def: string }> = {
  integration: {
    easy: "목",
    jargon: "어디서든 닿기 쉬운 자리 · 통합도(Integration)",
    def: "건물 안을 오갈 때 자연히 지나게 되는 자리. 어디서 출발하든 닿기 쉬운 곳입니다.",
  },
  choice: {
    easy: "지나가는 길목",
    jargon: "다른 곳끼리 오갈 때 거쳐 가는 빈도 · 선택도(Choice)",
    def: "머무는 자리가 아니라 거쳐 가는 자리. 눈에 띄어야 하는 매장·광고 자리에 맞습니다.",
  },
  integration_local: {
    easy: "주변 목",
    jargon: "세 걸음 반경 안의 통합도 · 국소 통합도(Integration R3)",
    def: "건물 전체가 아니라 바로 주변 몇 걸음 안에서 닿기 쉬운 정도. 구역 안의 중심을 찾을 때 봅니다.",
  },
  connectivity: {
    easy: "한눈에 보이는 범위",
    jargon: "연결도(Connectivity)",
    def: "한 자리에 서서 눈에 들어오는 공간의 넓이입니다.",
  },
  robot: {
    easy: "로봇 통로",
    jargon: "통로 폭(국소 두께) · 로봇 기준 1.6m",
    def: "물류 로봇(폭 약 1.0m)이 지나갈 수 있는 통로인지. 양쪽 벽·랙 사이 폭이 1.6m 넘으면 지나갈 수 있다고 봅니다. 랙을 옮기고 다시 분석하면 바로 다시 잽니다.",
  },
};

// 「로봇 통로」 탭은 축척을 알아 통로 폭을 m로 잰 도면에만 보인다(robot_passable_pct가 있을 때).
const METRIC_ORDER: OverlayMetric[] = ["integration", "choice", "integration_local", "connectivity", "robot"];

// 색 범례의 양 끝 말 (2026-10-24 사용자 지적: "색깔이 무엇을 의미하는지 안 적혀 있다").
// 지표 이름이 아니라 **그 자리가 어떤 자리인지**로 쓴다.
const RAMP_ENDS: Record<OverlayMetric, { low: string; high: string; howTo: string }> = {
  integration: {
    low: "외진 곳 — 사람이 잘 안 옴",
    high: "목 좋은 곳 — 사람이 자연히 모임",
    howTo: "빨갈수록 건물 어디서 출발해도 쉽게 닿는 자리, 파랄수록 일부러 찾아가야 하는 자리입니다.",
  },
  choice: {
    low: "거의 안 지나감",
    high: "가장 많이 지나감",
    howTo: "빨갈수록 사람들이 다른 곳으로 가는 길에 거쳐 가는 자리입니다. 머무는 자리와는 다를 수 있습니다.",
  },
  integration_local: {
    low: "동네 안에서도 구석",
    high: "그 구역의 중심",
    howTo: "건물 전체가 아니라 바로 주변 몇 걸음 안에서 본 목입니다. 구역마다 중심이 어디인지 볼 때 씁니다.",
  },
  connectivity: {
    low: "주변이 잘 안 보임 (막힘)",
    high: "넓게 다 보임 (트임)",
    howTo: "빨갈수록 그 자리에 서면 눈에 들어오는 공간이 넓습니다. 파란 곳은 길을 잃기 쉽고 매장이 눈에 안 띕니다.",
  },
  robot: {
    low: "로봇 못 지나감 — 좁은 통로",
    high: "로봇 넉넉히 지나감 — 넓은 통로",
    howTo: "빨갈수록 통로가 넓어 로봇이 교행할 수 있고, 파란 곳은 폭 1.6m가 안 돼 로봇이 못 들어갑니다(사람만).",
  },
};

type SpotKey = "best_spots" | "through_spots" | "dead_spots" | "robot_dead_ends";

/** 지표별로 도면 위에 번호를 붙여 보여줄 자리 목록.
 *  `tag`는 도면 위 번호 옆에 붙는 이름표다 (2026-10-24 사용자 지적: "1234가 뭐라는 것을
 *  표시해 줘야 알지") — 범례까지 내려가 읽지 않아도 번호의 뜻이 그 자리에서 보이게. */
const SPOT_GROUPS: Record<OverlayMetric, Array<{ key: SpotKey; label: string; tag: string; best: string; color: string }>> = {
  integration: [
    { key: "best_spots", label: "목 좋은 자리", tag: "목 좋은", best: "가장 목이 좋음", color: "#dc2626" },
    { key: "dead_spots", label: "외진 자리", tag: "외진", best: "가장 외짐", color: "#2563eb" },
  ],
  choice: [{ key: "through_spots", label: "지나가는 길목", tag: "길목", best: "가장 많이 지나감", color: "#d97706" }],
  integration_local: [{ key: "best_spots", label: "목 좋은 자리", tag: "목 좋은", best: "가장 목이 좋음", color: "#dc2626" }],
  connectivity: [],
  // 막다른 끝 목록은 백엔드가 꺼 두었다(floorplan_vga.DEAD_ENDS_ENABLED) — 켜지면 그대로 나온다.
  robot: [{ key: "robot_dead_ends", label: "막다른 통로 끝", tag: "막다른", best: "가장 좁음", color: "#7c3aed" }],
};

/** 도면 위 이름표 — 문제의 「종류」만 짧게. 제목 앞부분은 위치(「남서쪽 '매장 68'」)라
 *  잘라 쓰면 무슨 문제인지가 빠진다. 제목 문형은 backend `floorplan_issues.py`가 정한다. */
const ISSUE_KINDS: Array<[string, string]> = [
  ["외진 구역", "외진 구역"],
  ["동선이 몰립니다", "동선 몰림"],
  ["시야가 막힌", "시야 막힘"],
  ["가장 목이 좋은", "가장 목 좋은 곳"],
  ["분석에서 빠졌", "계산 빠짐"],
  ["차이가 큽니다", "자리별 차이 큼"],
  ["로봇 폭보다 좁", "로봇 못 감"],
  ["막다른 끝", "막다른 끝"],
];

function issueTag(title: string): string {
  const hit = ISSUE_KINDS.find(([needle]) => title.includes(needle));
  if (hit) return hit[1];
  return title.length > 10 ? `${title.slice(0, 10)}…` : title;
}

/** 값 목록에서 v가 아래에서 몇 %인지 (0~100). 정렬된 배열을 받는다. */
function percentileOf(sorted: number[], v: number): number {
  if (!sorted.length) return 0;
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid] <= v) lo = mid + 1;
    else hi = mid;
  }
  return (lo / sorted.length) * 100;
}

/** 아래에서 pct% → "상위 N%". 맨 위가 상위 1%, 맨 아래가 상위 100%. */
function topPct(pct: number): number {
  return Math.min(100, Math.max(1, Math.round(100 - pct) + 1));
}

/** 찍은 자리의 목 수준을 한마디로. 경계는 필지 등급과 같은 감각(맨 위 10%만 특급). */
function probeVerdict(integrationPct: number): string {
  if (integrationPct >= 90) return "목 좋은 자리";
  if (integrationPct >= 60) return "괜찮은 편";
  if (integrationPct >= 30) return "보통";
  return "외진 자리";
}

const SYMBOL_LABELS: Record<FloorplanSymbolKind, { ko: string; glyph: string; color: string }> = {
  entrance: { ko: "출입구", glyph: "入", color: "#059669" },
  exit: { ko: "비상구", glyph: "非", color: "#dc2626" },
  stair: { ko: "계단", glyph: "階", color: "#7c3aed" },
  elevator: { ko: "엘리베이터", glyph: "EV", color: "#7c3aed" },
  other: { ko: "기타", glyph: "?", color: "#6b7280" },
};

/**
 * 「목 좋은 자리」·「외진 자리」 목록의 위치 글. 예전엔 「오른쪽으로 137m · 아래로 54m」 좌표였는데
 * 휴대폰에선 도면이 약 1,000px 위라 맞춰 볼 수 없고 실무자에게 안 와닿았다(2026-11-07 UX 자동 개선).
 * 문제 카드와 같은 규칙(도면의 어느 쪽 + 가까운 실명)으로 말한다 — 실명 없는 도면만 좌표.
 */
function spotWhere(spot: FloorplanSpot, labels: FloorplanLabel[], width: number, height: number): string {
  if (width > 0 && height > 0) return issuePlace(spot, labels, width, height).replace(/^도면 /, "");
  if (spot.x_m != null && spot.y_m != null) return `오른쪽으로 ${spot.x_m.toFixed(0)}m · 아래로 ${spot.y_m.toFixed(0)}m`;
  return `(${spot.x.toFixed(0)}, ${spot.y.toFixed(0)})`;
}

function spotRank(key: SpotKey, spot: FloorplanSpot): string {
  if (key === "robot_dead_ends") return `통로 폭 ${spot.value.toFixed(1)}m`;
  if (key === "dead_spots") return `하위 ${Math.max(1, Math.round(spot.percentile))}%`;
  return `상위 ${Math.max(1, Math.round(100 - spot.percentile) + 1)}%`;
}

/** 레이어가 도면에서 맡은 역할 — 사람이 고칠 수 있다. */
type LayerRole = "wall" | "opening" | "ignore";

/** 선택 · 벽 그리기 · 문 뚫기 · 지우개 · 말로 고치기(2026-11-09). */
type EditMode = "select" | "wall" | "door" | "erase" | "voice";

type Selection = { list: "lines" | "openings"; index: number };

type Point = { x: number; y: number };

// 편집 도구 (2026-10-22 사용자 지적: "작동이 잘 안 되고 지우는 게 불편하다").
// 지우개는 한 번 눌러 바로 지우고, 되돌리기로 무른다 — 「고르고 → 멀리 있는 삭제
// 버튼」 두 단계가 불편의 핵심이었다.
// keys: 키보드·마우스가 있을 때만 붙이는 덧말. 휴대폰엔 Delete·Esc·Ctrl+Z·오른쪽 클릭이 없어
// 안내가 할 수 없는 일을 시켰다 — 터치 화면에선 .floorplan-desktop-hint로 숨긴다 (2026-11-06 UX 자동 개선).
const EDIT_MODES: Array<{ mode: EditMode; label: string; hint: string; keys?: string; after?: string }> = [
  { mode: "select", label: "선택", hint: "선을 누르면 고릅니다. 「고른 선 지우기」", keys: "(또는 Delete 키)", after: "로 지웁니다. 분석 뒤에는 아무 곳이나 누르면 그 자리의 점수가 나옵니다." },
  { mode: "wall", label: "벽 그리기", hint: "점을 차례로 눌러 벽을 잇습니다. 멈추려면 「그리기 끝내기」를 누르세요", keys: " (또는 Esc·오른쪽 클릭)", after: "." },
  { mode: "door", label: "문 뚫기", hint: "벽 근처에서 문 양 끝을 두 번 누릅니다 — 가까운 벽에 저절로 붙습니다. 뚫은 뒤 「바꾼 것과 비교하기」를 누르세요." },
  { mode: "erase", label: "지우개", hint: "지울 선을 누르면 바로 지워집니다." },
  // 말로 고치기 (2026-11-09 소유자: 「위치를 선택하고 말·메시지로 보내 도면을 수정 — GPT 이미지 수정처럼」).
  // GPT는 어느 벽에 무엇을 할지만 고르고 선은 서버가 계산한다 — SPEC_FLOORPLAN_TABS_UI 2026-11-09.
  { mode: "voice", label: "말로 고치기", hint: "고칠 자리를 누르거나 끌어서 네모로 고른 뒤, 도면 아래 칸에 말이나 글로 시키세요(예: 「여기 문 내줘」). AI가 「적용할까요?」라고 물으면 「응」이라고 답하세요." },
];
// 이만큼(화면 px) 안 움직이고 떼면 네모가 아니라 점으로 본다.
const VOICE_DRAG_PX = 8;
// 이 각도(도) 안이면 수평·수직으로 붙인다 — 손으로 그린 벽이 비뚤면 틈이 생긴다.
const ORTHO_SNAP_DEG = 6;
const HISTORY_LIMIT = 50;

function distanceToSegment(px: number, py: number, line: FloorplanLine): number {
  const { x1, y1, x2, y2 } = line;
  const dx = x2 - x1;
  const dy = y2 - y1;
  const lengthSq = dx * dx + dy * dy;
  if (lengthSq === 0) return Math.hypot(px - x1, py - y1);
  let t = ((px - x1) * dx + (py - y1) * dy) / lengthSq;
  t = Math.max(0, Math.min(1, t));
  const nearX = x1 + t * dx;
  const nearY = y1 + t * dy;
  return Math.hypot(px - nearX, py - nearY);
}

export function FloorplanAnalyzer({
  handoff,
}: {
  /** 다른 탭(신도시 계획도면)에서 넘겨받은 도면. ``id``가 바뀔 때마다 새로 읽는다. */
  handoff?: { file: File; id: number } | null;
} = {}) {
  const [file, setFile] = useState<File | null>(null);
  const [canvasWidth, setCanvasWidth] = useState(0);
  const [canvasHeight, setCanvasHeight] = useState(0);
  const [lines, setLines] = useState<FloorplanLine[]>([]);
  // DXF의 문·창 엔티티 — 벽에서 뚫어야 할 개구부. CAD는 벽을 끊김 없이 그리고
  // 문을 별도 레이어에 두기 때문에, 이게 없으면 모든 방이 막힌 채로 분석된다.
  const [openings, setOpenings] = useState<FloorplanLine[]>([]);
  // 축척 — DXF 헤더에 단위가 있으면 자동, 없으면 사용자가 도면 가로 길이(m)를
  // 넣는다. 없어도 분석은 되지만 결과가 캔버스 좌표로만 나온다.
  const [metersPerUnit, setMetersPerUnit] = useState<number | null>(null);
  // 의미 요소 — 실명(TEXT)과 심볼(INSERT). 도면에 없으면 비어 있고, 그건 오류가 아니다.
  const [labels, setLabels] = useState<FloorplanLabel[]>([]);
  const [symbols, setSymbols] = useState<FloorplanSymbol[]>([]);
  const [showLabels, setShowLabels] = useState(true);
  // 레이어 목록과 사람이 고친 역할. 자동 추측이 틀리는 일이 흔해서(사무소마다
  // 벽 레이어 이름이 다르다) 화면에서 바로잡고 다시 읽을 수 있어야 한다.
  const [layers, setLayers] = useState<FloorplanLayer[]>([]);
  const [layerRoles, setLayerRoles] = useState<Record<string, LayerRole>>({});
  const [layersGuessed, setLayersGuessed] = useState(false);
  // 벽으로 안 썼는데 선이 많은 레이어 — 내벽·칸막이가 거기 있을 수 있다.
  const [suspectLayers, setSuspectLayers] = useState<string[]>([]);
  const [showLayerPicker, setShowLayerPicker] = useState(false);
  // A/B — 분석을 한 번 돌린 뒤 그때의 벽·문을 "원본"으로 붙잡아 둔다. 이후 벽을
  // 고치고 「바꾼 것 비교」를 누르면 붙잡아 둔 판과 지금 판을 함께 계산한다.
  const [baseline, setBaseline] = useState<{ lines: FloorplanLine[]; openings: FloorplanLine[] } | null>(null);
  const [comparison, setComparison] = useState<FloorplanCompareResponse | null>(null);
  const [comparing, setComparing] = useState(false);
  // 휴대폰에서 도면을 2.5배로 키워 손가락으로 벽·문을 고를 수 있게 한다 (2026-11-07 UX 자동 개선).
  const [canvasZoomed, setCanvasZoomed] = useState(false);
  const canvasScrollRef = useRef<HTMLDivElement | null>(null);
  const [compareError, setCompareError] = useState("");
  const [dxfUnits, setDxfUnits] = useState<string | null>(null);
  const [widthMInput, setWidthMInput] = useState("");
  // 도면 단위로 자동으로 읽은 가로 길이 — 칸을 비웠을 때 안내에 되살려 보여 준다.
  const [autoWidthM, setAutoWidthM] = useState("");
  // 선택은 벽과 개구부 양쪽에서 일어난다 — 잘못 찍은 문도 지울 수 있어야 한다.
  const [selected, setSelected] = useState<Selection | null>(null);
  const [mode, setMode] = useState<EditMode>("select");
  const [pendingStart, setPendingStart] = useState<Point | null>(null);
  // 고치기 안내 한 줄 — 문 뚫기가 「인식 안 됨」으로 보였다(2026-10-27): 한 번만 누르면 아무 일도
  // 안 일어난 것 같고, 벽에서 먼 곳을 누르면 효과 없는 문이 소리 없이 생겼다.
  const [editNotice, setEditNotice] = useState("");
  // 결과를 낸 뒤 도면을 고쳤다 — 결과는 남겨 두되 「고치기 전」 것이라고 밝힌다. 예전엔 결과를
  // 통째로 지워 「문이 안 먹었다」로 보였고, 다시 분석하면 원본이 덮여 비교 버튼까지 사라졌다.
  const [resultStale, setResultStale] = useState(false);
  // 그리는 중 마우스 자리(미리보기 선)와, 선택·지우개에서 마우스 아래 선.
  const [cursor, setCursor] = useState<Point | null>(null);
  const [hover, setHover] = useState<Selection | null>(null);
  const historyRef = useRef<Array<{ lines: FloorplanLine[]; openings: FloorplanLine[] }>>([]);
  // 문 첫 점이 붙은 벽 — 둘째 점을 같은 벽 위로 끌어 붙인다.
  const doorWallRef = useRef<FloorplanLine | null>(null);
  const [historySize, setHistorySize] = useState(0);
  // 말로 고치기: 고른 자리 → 지시 → LLM이 제안하고 「적용할까요?」라고 묻는다(pending) → 사용자가 「응」이라고
  // 말·글로 답하면 적용(2026-11-09 소유자 「LLM이 물으라고」). pending은 요청 때의 벽·문 배열(for)에만 맞다.
  // 적용 뒤에도 바뀐 자리는 초록·빨간 점선으로 남긴다 — 도면이 그 판(after)일 때만. 되돌리면 저절로 사라진다.
  const [voicePending, setVoicePending] = useState<{
    plan: FloorplanEditIntentResponse;
    forLines: FloorplanLine[];
    forOpenings: FloorplanLine[];
    instruction: string;
  } | null>(null);
  const [voiceSel, setVoiceSel] = useState<VoiceSelection | null>(null);
  const [voiceDrag, setVoiceDrag] = useState<{ start: Point; end: Point } | null>(null);
  const [voiceChange, setVoiceChange] = useState<{
    plan: FloorplanEditIntentResponse;
    preview: ReturnType<typeof previewOf>;
    afterLines: FloorplanLine[];
    afterOpenings: FloorplanLine[];
  } | null>(null);
  // 요청을 보낸 뒤 응답이 오기 전에 도면이 바뀌었는지(되돌리기 등) 보려고 지금 판을 들고 있는다.
  const currentPlanRef = useRef<{ lines: FloorplanLine[]; openings: FloorplanLine[] }>({ lines: [], openings: [] });
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [voiceError, setVoiceError] = useState("");
  const [voiceReply, setVoiceReply] = useState("");
  const voiceSeqRef = useRef(0);
  // 영어 모드에서 도면 이름표 번역이 도착하면 캔버스를 다시 그린다.
  const [trVersion, setTrVersion] = useState(0);
  useEffect(() => onTranslations(() => setTrVersion((v) => v + 1)), []);

  const [extracting, setExtracting] = useState(false);
  const [extractError, setExtractError] = useState("");
  // 서버 쪽 실패(무응답·시간 초과)면 같은 도면을 다시 보내 볼 수 있게 「다시 시도」를 단다.
  // 도면 내용 탓(400)은 다시 보내도 같으니 달지 않는다 (2026-11-08 UX 자동 개선).
  const [extractRetryable, setExtractRetryable] = useState(false);
  const lastRolesRef = useRef<Record<string, LayerRole> | undefined>(undefined);
  const [extractWarnings, setExtractWarnings] = useState<string[]>([]);
  // 사람이 고르지 않아도 프로그램이 알아서 한 일. 손님은 건축사사무소에서 받은
  // 파일을 그대로 넣고, 우리가 한 일을 확인만 한다 (2026-10-01 결정).
  const [autoNotes, setAutoNotes] = useState<string[]>([]);

  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState("");
  const [result, setResult] = useState<FloorplanAnalyzeResponse | null>(null);
  const [view, setView] = useState<ResultView>("issues");
  // LLM 해석은 분석 결과가 뜬 뒤 따로 온다. 오기 전엔 규칙 문장을 보여준다.
  const [narrative, setNarrative] = useState<{ text: string; source: string } | null>(null);
  const [narrativeLoading, setNarrativeLoading] = useState(false);
  // 문제 뷰에서도 바탕 히트맵은 목으로 깐다 — 마커만 문제로 바뀐다.
  const overlayMetric: OverlayMetric = view === "issues" || view === "rooms" ? "integration" : view;
  // 표에서 고른 방 — 도면 위에서 그 방만 테두리로 강조한다.
  const [activeRoom, setActiveRoom] = useState<number | null>(null);
  // 문제 목록에서 마우스를 올린 문제 — 큰 도면에 그 구역을 칠한다.
  const [activeIssue, setActiveIssue] = useState<number | null>(null);
  const [showExcluded, setShowExcluded] = useState(true);
  // 사용자가 찍은 자리 (2026-10-24 사용자 요청: "원하는 곳을 찍으면 분석해 주는 걸로").
  // 모든 칸의 값이 이미 result.cells에 와 있으므로 다시 계산하지 않고 찾아서 보여 준다.
  const [probe, setProbe] = useState<Point | null>(null);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  // 결과 아래에서 도면을 바꾸는 행동(표 줄 고르기·「지도 보기」 지표 바꾸기)을 했는데 도면이
  // 화면 밖이면 도면을 화면 가운데로 올린다. 이미 보이면(데스크톱 대부분) 그대로 둔다.
  const revealCanvas = () => {
    const el = canvasRef.current;
    if (!el || typeof window === "undefined") return;
    const box = el.getBoundingClientRect();
    if (box.bottom < 0 || box.top > window.innerHeight) {
      scrollIntoPanel(el, "center");
    }
  };
  // 휴대폰(390px)에선 샘플·파일을 읽어도 화면이 맨 위 그대로라 읽은 도면과 「분석 실행」이
  // 1,300px쯤 아래에 있었다 — 읽기가 끝나면 도면 카드가 화면 밖(또는 아래 40%)일 때만 내려 준다.
  // 레이어를 짐작한 경고가 뜬 경우는 경고를 먼저 읽어야 해서 내리지 않는다 (2026-11-06 UX 자동 개선).
  const editorCardRef = useRef<HTMLDivElement>(null);
  const [readSeq, setReadSeq] = useState(0);
  useEffect(() => {
    const el = editorCardRef.current;
    if (!readSeq || !el || typeof window === "undefined") return;
    const top = el.getBoundingClientRect().top;
    if (top < 0 || top > window.innerHeight * 0.6) {
      scrollIntoPanel(el, "start");
    }
  }, [readSeq]);
  // 휴대폰에선 「분석 실행」 뒤에도 화면이 도면 카드 위 그대로라 「분석 결과」가 1,000px쯤
  // 아래에 생긴 줄 몰랐다 — 결과 카드가 화면 아래 40%보다 밑이면 결과 카드로 내려 준다.
  // 데스크톱처럼 이미 보이는 곳에 뜨면 그대로 둔다 (2026-11-06 UX 자동 개선).
  const resultCardRef = useRef<HTMLDivElement>(null);
  const [analyzeSeq, setAnalyzeSeq] = useState(0);
  useEffect(() => {
    const el = resultCardRef.current;
    if (!analyzeSeq || !el || typeof window === "undefined") return;
    const top = el.getBoundingClientRect().top;
    if (top < 0 || top > window.innerHeight * 0.6) {
      scrollIntoPanel(el, "start");
    }
  }, [analyzeSeq]);

  const displayScale = canvasWidth > 0 ? Math.min(1, DISPLAY_MAX_WIDTH / canvasWidth) : 1;
  const displayWidth = canvasWidth * displayScale;
  const displayHeight = canvasHeight * displayScale;
  const hasCanvasForKeys = canvasWidth > 0 && canvasHeight > 0;

  const resetAll = useCallback(() => {
    setFile(null);
    setCanvasWidth(0);
    setCanvasHeight(0);
    setLines([]);
    setOpenings([]);
    setMetersPerUnit(null);
    setLayers([]);
    setLayerRoles({});
    setLayersGuessed(false);
    setSuspectLayers([]);
    setShowLayerPicker(false);
    setBaseline(null);
    setComparison(null);
    setCompareError("");
    setLabels([]);
    setSymbols([]);
    setDxfUnits(null);
    setWidthMInput("");
    setAutoWidthM("");
    setSelected(null);
    setMode("select");
    setPendingStart(null);
    setCursor(null);
    setHover(null);
    historyRef.current = [];
    setHistorySize(0);
    setVoiceSel(null);
    setVoiceDrag(null);
    setVoiceChange(null);
    setVoicePending(null);
    setVoiceError("");
    setVoiceReply("");
    setExtractError("");
    setExtractWarnings([]);
    setAnalyzeError("");
    setResult(null);
    setResultStale(false);
    setEditNotice("");
  }, []);

  // 시연·첫 방문용. 도면이 없어도 한 번에 결과까지 가 본다.
  async function handleSample(sample: (typeof SAMPLE_PLANS)[number]) {
    resetAll();
    setExtracting(true);
    try {
      const res = await fetch(sample.path);
      if (!res.ok) throw new Error();
      const blob = await res.blob();
      await handleFileChange(new File([blob], sample.fileName, { type: "application/dxf" }));
    } catch {
      setExtracting(false);
      setExtractError("샘플 도면을 불러오지 못했습니다. 잠시 뒤 다시 눌러 주세요.");
    }
  }

  async function handleFileChange(picked: File | null) {
    if (!picked) return;
    resetAll();
    if (!/\.dxf$/i.test(picked.name)) {
      setExtractError(nonDxfMessage(picked.name));
      return;
    }
    setFile(picked);
    await runExtract(picked);
  }

  // 계획도면 탭에서 「이 도면을 건축 평면도 분석에서 열기」로 넘어온 파일을 바로 읽는다.
  const handoffId = handoff?.id;
  useEffect(() => {
    if (handoff) void handleFileChange(handoff.file);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 넘겨받을 때(id)만 한 번 읽는다
  }, [handoffId]);

  /** ``roles``가 있으면 사람이 고른 레이어로 다시 읽는다. */
  async function runExtract(picked: File, roles?: Record<string, LayerRole>) {
    setExtracting(true);
    setExtractError("");
    setExtractRetryable(false);
    lastRolesRef.current = roles;
    setResult(null);
    // 다시 읽은 도면은 다른 판이다 — 예전 원본과 견주지 않는다.
    setBaseline(null);
    setComparison(null);
    try {
      const payload = roles
        ? {
            wall: Object.keys(roles).filter((n) => roles[n] === "wall"),
            opening: Object.keys(roles).filter((n) => roles[n] === "opening"),
          }
        : undefined;
      const response = await api.floorplanExtract(picked, payload);
      setCanvasWidth(response.canvas_width);
      setCanvasHeight(response.canvas_height);
      setLines(response.lines);
      setOpenings(response.openings || []);
      setMetersPerUnit(response.meters_per_unit ?? null);
      const gotLayers = response.layers || [];
      setLayers(gotLayers);
      setLayersGuessed(!!response.layers_guessed);
      setSuspectLayers(response.suspect_wall_layers || []);
      setShowLayerPicker(!!response.layers_guessed || !!(response.suspect_wall_layers || []).length);
      if (!roles) {
        const guessed: Record<string, LayerRole> = {};
        for (const layer of gotLayers) {
          guessed[layer.name] =
            layer.guess === "wall" ? "wall" : layer.guess === "opening" ? "opening" : "ignore";
        }
        setLayerRoles(guessed);
      }
      setLabels(response.labels || []);
      setSymbols(response.symbols || []);
      setDxfUnits(response.dxf_units ?? null);
      const readWidthM = response.meters_per_unit
        ? (response.canvas_width * response.meters_per_unit).toFixed(1)
        : "";
      setWidthMInput(readWidthM);
      setAutoWidthM(readWidthM);
      setExtractWarnings(response.warnings || []);
      setAutoNotes(response.auto_notes || []);
      // 레이어를 다시 고른 재읽기는 이미 도면을 보는 중이라 내려 주지 않는다.
      if (!roles && !response.layers_guessed && !(response.suspect_wall_layers || []).length) {
        setReadSeq((n) => n + 1);
      }
    } catch (error) {
      setExtractError(error instanceof Error ? error.message : "벽 추출에 실패했습니다.");
      setExtractRetryable(!(error instanceof ApiClientError) || error.status >= 500);
    } finally {
      setExtracting(false);
    }
  }

  function setLayerRole(name: string, role: LayerRole) {
    setLayerRoles((prev) => ({ ...prev, [name]: role }));
  }

  async function handleAnalyze() {
    if (!lines.length || canvasWidth <= 0 || canvasHeight <= 0) return;
    setAnalyzing(true);
    setAnalyzeError("");
    setResult(null);
    setNarrative(null);
    try {
      const response = await api.floorplanAnalyze({
        canvas_width: canvasWidth,
        canvas_height: canvasHeight,
        lines,
        openings,
        meters_per_unit: metersPerUnit,
        labels,
      });
      setResult(response);
      setResultStale(false);
      setAnalyzeSeq((n) => n + 1);
      // 2026-11-09 소유자: 「말로 고치기를 앞으로 돌출하게 해서 잘 보여 줘야지」 — 분석이 끝나면 말로 고치기
      // 창을 바로 연다(도구 단추를 찾아 누르지 않아도). 「선택」을 누르면 종전처럼 찍은 자리 점수로 돌아간다.
      if (mode === "select") changeMode("voice");
      // 원본은 **처음 분석한 도면**으로 둔다 — 문을 뚫고 다시 분석하면 원본이 고친 판으로
      // 덮여 「바꾼 것과 비교하기」가 사라졌다(2026-10-27 「문이 인식 안 된다」).
      setBaseline((prev) => prev ?? { lines, openings });
      setComparison(null);
      setNarrativeLoading(true);
      api
        .floorplanExplain({ summary: response.summary, issues: response.issues || [], rule_text: response.explanation.text })
        .then((n) => {
          if (n.source === "llm") setNarrative(n);
        })
        .catch(() => undefined)
        .finally(() => setNarrativeLoading(false));
    } catch (error) {
      setAnalyzeError(error instanceof Error ? error.message : "분석에 실패했습니다.");
    } finally {
      setAnalyzing(false);
    }
  }

  function canvasPointFromEvent(e: { clientX: number; clientY: number }): Point | null {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    // ⚠️ CSS가 캔버스를 줄여 그리면(좁은 화면·옆 창) 화면 픽셀과 캔버스 픽셀이
    // 달라진다. 그 비율을 빼먹어 클릭이 엉뚱한 자리에 찍혔다(2026-10-22).
    const displayX = ((e.clientX - rect.left) * canvas.width) / rect.width;
    const displayY = ((e.clientY - rect.top) * canvas.height) / rect.height;
    return { x: (displayX - CANVAS_PAD) / displayScale, y: (displayY - CANVAS_PAD) / displayScale };
  }

  function hitTest(point: Point): Selection | null {
    let hit: Selection | null = null;
    let closestDist = HIT_RADIUS_PX / displayScale;
    const consider = (list: "lines" | "openings", items: FloorplanLine[]) => {
      items.forEach((line, index) => {
        const dist = distanceToSegment(point.x, point.y, line);
        if (dist <= closestDist) {
          closestDist = dist;
          hit = { list, index };
        }
      });
    };
    // 개구부를 먼저 보므로 벽과 겹쳐 있어도 문을 집어 지울 수 있다.
    consider("openings", openings);
    consider("lines", lines);
    return hit;
  }

  /** 가까운 벽 끝점에 붙이고, 아니면 수평·수직에 붙인다 — 그린 벽이 안 닿으면 바깥이 샌다. */
  function snapPoint(point: Point, from: Point | null): Point {
    const radius = HIT_RADIUS_PX / displayScale;
    let best: Point | null = null;
    let bestDist = radius;
    for (const line of lines) {
      for (const end of [
        { x: line.x1, y: line.y1 },
        { x: line.x2, y: line.y2 },
      ]) {
        const d = Math.hypot(end.x - point.x, end.y - point.y);
        if (d <= bestDist) {
          bestDist = d;
          best = end;
        }
      }
    }
    if (best) return best;
    if (from) {
      const angle = Math.abs((Math.atan2(point.y - from.y, point.x - from.x) * 180) / Math.PI);
      if (Math.min(angle, 180 - angle) <= ORTHO_SNAP_DEG) return { x: point.x, y: from.y };
      if (Math.abs(angle - 90) <= ORTHO_SNAP_DEG) return { x: from.x, y: point.y };
    }
    return point;
  }

  /**
   * 문은 벽 **위에** 있어야 벽을 뚫는다. 손으로 찍으면 벽 옆 빈 곳에 놓여 아무것도
   * 안 바뀌었다(2026-10-24 사용자: "문뚫기 하고 분석 다시 해도 바뀌지 않는다"). 그래서
   * 가까운 벽에 붙이고, 두 번째 점은 첫 점이 붙은 벽의 연장선 위로 끌어 붙인다.
   */
  function snapDoor(point: Point): Point {
    const project = (l: FloorplanLine, clamp: boolean): Point => {
      const dx = l.x2 - l.x1;
      const dy = l.y2 - l.y1;
      const len2 = dx * dx + dy * dy || 1;
      let t = ((point.x - l.x1) * dx + (point.y - l.y1) * dy) / len2;
      if (clamp) t = Math.max(0, Math.min(1, t));
      return { x: l.x1 + t * dx, y: l.y1 + t * dy };
    };
    if (pendingStart && doorWallRef.current) return project(doorWallRef.current, false);
    const radius = (HIT_RADIUS_PX * 2) / displayScale;
    let best: FloorplanLine | null = null;
    let bestDist = radius;
    for (const line of lines) {
      const d = distanceToSegment(point.x, point.y, line);
      if (d <= bestDist) {
        bestDist = d;
        best = line;
      }
    }
    return best ? project(best, true) : point;
  }

  function nearestWall(point: Point): FloorplanLine | null {
    const radius = (HIT_RADIUS_PX * 2) / displayScale;
    let best: FloorplanLine | null = null;
    let bestDist = radius;
    for (const line of lines) {
      const d = distanceToSegment(point.x, point.y, line);
      if (d <= bestDist) {
        bestDist = d;
        best = line;
      }
    }
    return best;
  }

  /** 바꾸기 직전 판을 쌓아 둔다 — 되돌리기용. */
  function remember() {
    historyRef.current.push({ lines, openings });
    if (historyRef.current.length > HISTORY_LIMIT) historyRef.current.shift();
    setHistorySize(historyRef.current.length);
  }

  function undo() {
    const prev = historyRef.current.pop();
    setHistorySize(historyRef.current.length);
    if (!prev) return;
    setLines(prev.lines);
    setOpenings(prev.openings);
    setSelected(null);
    setHover(null);
    setPendingStart(null);
    setResultStale(true);
  }

  /** ``at``을 주면 그 자리에 겹쳐 그려진 같은 방향 선까지 함께 지운다. */
  function removeLine(target: Selection, at?: Point) {
    remember();
    const items = target.list === "openings" ? openings : lines;
    const picked = items[target.index];
    // ⚠️ 캐드 도면은 같은 벽을 두 번 그린 경우가 흔하다(통로 테두리와 매장 테두리).
    // 하나만 지우면 겹친 다른 선이 그대로 막고 있어 「지워도 그대로」가 된다
    // (2026-10-22 실측: 대형 복합몰 샘플에서 벽 460개 중 어느 하나를 지워도 지표 변화 0).
    const doomed = new Set<number>([target.index]);
    if (at && picked) {
      const angle = (l: FloorplanLine) => Math.atan2(l.y2 - l.y1, l.x2 - l.x1);
      const radius = HIT_RADIUS_PX / displayScale;
      const mid = (l: FloorplanLine) => ({ x: (l.x1 + l.x2) / 2, y: (l.y1 + l.y2) / 2 });
      items.forEach((line, i) => {
        if (distanceToSegment(at.x, at.y, line) > radius) return;
        const diff = Math.abs(Math.sin(angle(line) - angle(picked)));
        if (diff >= Math.sin((5 * Math.PI) / 180)) return;
        // 겹쳐 그린 선만 — 끝이 맞닿은 이웃 토막(같은 벽의 다음 칸)은 남긴다.
        const m1 = mid(line);
        const m2 = mid(picked);
        if (distanceToSegment(m1.x, m1.y, picked) <= radius || distanceToSegment(m2.x, m2.y, line) <= radius) {
          doomed.add(i);
        }
      });
    }
    const remove = (prev: FloorplanLine[]) => prev.filter((_, i) => !doomed.has(i));
    if (target.list === "openings") setOpenings(remove);
    else setLines(remove);
    setSelected(null);
    setHover(null);
    setResultStale(true);
  }

  // 문 뚫기·벽 그리기에서 첫 점을 찍으면 도면 위에 안내 한 줄과 「문 뚫기 취소」 단추가 새로
  // 생겨 도면이 아래로 56px(문을 낸 뒤 24px 더) 밀렸다 — 두 번째 점을 찍으려던 손가락 밑에서 도면이
  // 움직여 엉뚱한 자리를 찍게 됐다(390·1280 모두). 누르기 직전 도면 위치를 기억했다가, 위 내용이
  // 바뀐 만큼 패널을 같이 굴려 도면을 손가락 밑에 그대로 둔다 (2026-11-08 UX 자동 개선).
  const canvasAnchorRef = useRef<number | null>(null);
  useLayoutEffect(() => {
    const before = canvasAnchorRef.current;
    canvasAnchorRef.current = null;
    const el = canvasRef.current;
    if (before == null || !el) return;
    const delta = el.getBoundingClientRect().top - before;
    if (Math.abs(delta) < 1) return;
    const panel = scrollParent(el);
    if (panel) panel.scrollTop += delta;
  }, [editNotice, pendingStart, selected]);

  // --- 말로 고치기: 누르면 점, 끌면 네모 -------------------------------------------------
  currentPlanRef.current = { lines, openings };
  const voiceChangeLive =
    voiceChange && voiceChange.afterLines === lines && voiceChange.afterOpenings === openings ? voiceChange : null;
  const voicePendingLive =
    voicePending && voicePending.forLines === lines && voicePending.forOpenings === openings ? voicePending : null;

  function handleVoicePointerDown(e: React.PointerEvent<HTMLCanvasElement>) {
    if (mode !== "voice") return;
    const p = canvasPointFromEvent(e);
    if (!p) return;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    setVoiceDrag({ start: p, end: p });
  }

  function handleVoicePointerMove(e: React.PointerEvent<HTMLCanvasElement>) {
    if (mode !== "voice" || !voiceDrag) return;
    const p = canvasPointFromEvent(e);
    if (p) setVoiceDrag({ start: voiceDrag.start, end: p });
  }

  function handleVoicePointerUp(e: React.PointerEvent<HTMLCanvasElement>) {
    if (mode !== "voice" || !voiceDrag) return;
    const end = canvasPointFromEvent(e) ?? voiceDrag.end;
    setVoiceDrag(null);
    // 새 자리를 고르면 방금 바뀐 자리 표시는 지운다 — 다른 자리 얘기다(바뀐 것 자체는 남는다).
    setVoiceSel(selectionFrom(voiceDrag.start, end, VOICE_DRAG_PX / displayScale));
    setVoiceChange(null);
    setVoicePending(null);
    setVoiceReply("");
    setVoiceError("");
  }

  function voiceSelectionText(sel: VoiceSelection | null): string | null {
    if (!sel) return null;
    const { x, y } = sel.point;
    let near = "";
    let best = Infinity;
    for (const l of labels) {
      const d = Math.hypot(l.x - x, l.y - y);
      if (d < best) {
        best = d;
        near = l.text;
      }
    }
    const reach = metersPerUnit ? 10 / metersPerUnit : Math.max(canvasWidth, canvasHeight) / 15;
    const where = near && best <= reach ? `${roomName(near)} 근처` : "도면 위";
    if (!sel.box) return `${where} 한 점`;
    const w = sel.box.x2 - sel.box.x1;
    const h = sel.box.y2 - sel.box.y1;
    const size = metersPerUnit ? ` (약 ${Math.round(w * metersPerUnit)}×${Math.round(h * metersPerUnit)}m)` : "";
    return `${where} 네모${size}`;
  }

  /** LLM이 물은 제안을 적용한다 — 사용자가 「응」이라고 답했거나 「적용」을 눌렀을 때. */
  function applyVoicePending() {
    const pending = voicePendingLive;
    if (!pending) {
      setVoicePending(null);
      setVoiceError("그사이 도면이 바뀌었습니다. 다시 시켜 주세요.");
      return;
    }
    const next = applyEditPlan(lines, openings, pending.plan);
    remember();
    setLines(next.lines);
    setOpenings(next.openings);
    setResultStale(true);
    setVoicePending(null);
    setVoiceChange({
      plan: pending.plan,
      preview: previewOf(lines, openings, pending.plan),
      afterLines: next.lines,
      afterOpenings: next.openings,
    });
    setVoiceReply("");
    setVoiceError("");
    setEditNotice(
      "적용했습니다. " +
        (baseline ? "「바꾼 것과 비교하기」를 누르면 무엇이 달라지는지 나옵니다." : "「분석 실행」을 누르세요."),
    );
  }

  function cancelVoicePending() {
    voiceSeqRef.current += 1;
    setVoiceBusy(false);
    setVoicePending(null);
    setVoiceReply("취소했어요. 다르게 시키려면 다시 말해 주세요.");
  }

  async function handleVoiceSubmit(text: string) {
    if (!voiceSel || !lines.length) return;
    // LLM이 「적용할까요?」라고 물은 상태면 답부터 가른다. 「아니 2미터로」처럼 내용이 붙으면 고쳐 말한 것 —
    // 직전 지시와 함께 다시 보내 새 제안을 받는다.
    const pending = voicePendingLive;
    if (pending) {
      const intent = confirmIntent(text);
      if (intent === "yes") {
        applyVoicePending();
        return;
      }
      if (intent === "no") {
        cancelVoicePending();
        return;
      }
    }
    const seq = ++voiceSeqRef.current;
    const forLines = lines;
    const forOpenings = openings;
    setVoiceBusy(true);
    setVoiceError("");
    setVoiceReply("");
    setVoiceChange(null);
    setVoicePending(null);
    try {
      const plan = await api.floorplanEditIntent({
        instruction: text,
        previous_instruction: pending?.instruction ?? null,
        point: voiceSel.point,
        box: voiceSel.box,
        canvas_width: canvasWidth,
        canvas_height: canvasHeight,
        meters_per_unit: metersPerUnit,
        labels,
        lines: forLines,
        openings: forOpenings,
      });
      if (seq !== voiceSeqRef.current) return;
      if (plan.question || !hasChanges(plan)) {
        setVoiceReply(plan.question || "바꿀 것을 찾지 못했어요. 자리를 다시 고르거나 다르게 말해 주세요.");
        return;
      }
      const instruction = (pending ? `${pending.instruction} ${text}` : text).slice(-300);
      setVoicePending({ plan, forLines, forOpenings, instruction });
      setVoiceReply(`들은 말: 「${text}」`);
    } catch (error) {
      if (seq !== voiceSeqRef.current) return;
      setVoiceError(error instanceof Error ? error.message : "알아듣지 못했습니다. 다시 시도해 주세요.");
    } finally {
      if (seq === voiceSeqRef.current) setVoiceBusy(false);
    }
  }

  function handleCanvasClick(e: React.MouseEvent<HTMLCanvasElement>) {
    if (mode === "voice") return; // 누르기·끌기는 pointer 이벤트가 맡는다.
    const raw = canvasPointFromEvent(e);
    if (!raw) return;
    canvasAnchorRef.current = canvasRef.current?.getBoundingClientRect().top ?? null;
    // 아무 상태도 안 바뀐 누르기면 기억을 다음 프레임에 버린다 — 나중 변경에 엉뚱하게 쓰이지 않게.
    requestAnimationFrame(() => {
      canvasAnchorRef.current = null;
    });

    if (mode === "wall" || mode === "door") {
      const point = mode === "door" ? snapDoor(raw) : snapPoint(raw, pendingStart);
      if (!pendingStart) {
        if (mode === "door") {
          const wall = nearestWall(raw);
          if (!wall) {
            setEditNotice("벽 위를 눌러 주세요 — 근처에 벽이 없어 문을 낼 수 없습니다.");
            return;
          }
          doorWallRef.current = wall;
          setEditNotice("문 한쪽 끝을 찍었습니다. 같은 벽에서 다른 끝을 한 번 더 누르세요.");
        }
        setPendingStart(point);
        return;
      }
      if (Math.hypot(point.x - pendingStart.x, point.y - pendingStart.y) < 1e-6) return;
      const segment = { x1: pendingStart.x, y1: pendingStart.y, x2: point.x, y2: point.y };
      // 문은 벽을 뚫는 자리이므로 openings로 간다 — 벽 선을 지우는 것과 다르다.
      // 지우면 그 벽 전체가 통짜로 사라져 실제 구조와 달라진다.
      if (mode === "door") {
        const widthM = metersPerUnit ? Math.hypot(segment.x2 - segment.x1, segment.y2 - segment.y1) * metersPerUnit : null;
        if (widthM != null && widthM < 0.5) {
          setEditNotice("문이 너무 좁습니다(0.5m 미만) — 양 끝을 더 떨어뜨려 다시 누르세요.");
          return;
        }
        remember();
        setOpenings((prev) => [...prev, segment]);
        setPendingStart(null);
        doorWallRef.current = null;
        setEditNotice(
          `문을 냈습니다${widthM != null ? `(폭 약 ${widthM.toFixed(1)}m)` : ""}. ` +
            (baseline ? "「바꾼 것과 비교하기」를 누르면 무엇이 달라지는지 나옵니다." : "「분석 실행」을 누르세요."),
        );
        setResultStale(true);
        return;
      }
      remember();
      setLines((prev) => [...prev, segment]);
      // 벽은 이어 그린다 — 방 하나를 그리려고 매번 시작점을 다시 찍지 않게.
      setPendingStart(point);
      setResultStale(true);
      return;
    }

    const hit = hitTest(raw);
    if (mode === "erase") {
      if (hit) removeLine(hit, raw);
      return;
    }
    setSelected(hit);
    // 분석 결과가 있으면 찍은 자리의 점수를 보여 준다. 선 근처(10px)를 눌렀을 때도 보여 준다 —
    // 칸이 촘촘한 도면에서는 빈 곳을 눌러도 거의 늘 어느 벽 10px 안이라, 「빈 곳만」으로 막으면
    // 점수가 안 뜨고 벽만 골라진다(2026-10-24 실측).
    setProbe(result ? raw : null);
  }

  function handleCanvasMove(e: React.MouseEvent<HTMLCanvasElement>) {
    const raw = canvasPointFromEvent(e);
    if (!raw) return;
    if (mode === "wall" || mode === "door") {
      setCursor(mode === "door" ? snapDoor(raw) : snapPoint(raw, pendingStart));
      setHover(null);
    } else if (mode === "voice") {
      // 말로 고치기에선 선 하나를 집지 않는다 — 마우스 밑 선이 주황으로 켜지면 「그 선만」으로 읽힌다.
      setCursor(null);
      setHover(null);
    } else {
      setCursor(null);
      setHover(hitTest(raw));
    }
  }

  function finishDrawing(e?: React.MouseEvent) {
    e?.preventDefault();
    setPendingStart(null);
  }

  function deleteSelected() {
    if (selected) removeLine(selected);
  }

  function changeMode(next: EditMode) {
    if (next !== "voice") {
      voiceSeqRef.current += 1;
      setVoiceSel(null);
      setVoiceDrag(null);
      setVoiceChange(null);
    setVoicePending(null);
      setVoiceBusy(false);
      setVoiceError("");
      setVoiceReply("");
    }
    setMode(next);
    setSelected(null);
    setHover(null);
    setPendingStart(null);
    setCursor(null);
    setEditNotice("");
  }

  // 키보드: Esc 그리기·선택 취소, Delete/Backspace 선택한 선 지우기, Ctrl+Z 되돌리기.
  const keyActions = useRef({ undo, deleteSelected, finishDrawing });
  keyActions.current = { undo, deleteSelected, finishDrawing };
  useEffect(() => {
    if (!hasCanvasForKeys) return;
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      if (e.key === "Escape") {
        keyActions.current.finishDrawing();
        setSelected(null);
        setVoiceDrag(null);
      } else if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        keyActions.current.deleteSelected();
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        keyActions.current.undo();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hasCanvasForKeys]);

  async function handleCompare() {
    if (!baseline) return;
    setComparing(true);
    setCompareError("");
    try {
      const response = await api.floorplanCompare({
        canvas_width: canvasWidth,
        canvas_height: canvasHeight,
        meters_per_unit: metersPerUnit,
        labels,
        before_lines: baseline.lines,
        before_openings: baseline.openings,
        after_lines: lines,
        after_openings: openings,
      });
      setComparison(response);
    } catch (error) {
      setCompareError(error instanceof Error ? error.message : "비교에 실패했습니다.");
    } finally {
      setComparing(false);
    }
  }

  // 개수가 아니라 판 자체로 본다 — 하나 지우고 하나 그리면 개수는 같아도 바뀐 것이다.
  // 되돌리기로 원래 판에 돌아오면 같은 배열이 돌아오므로 다시 「안 바뀜」이 된다.
  const edited = baseline !== null && (baseline.lines !== lines || baseline.openings !== openings);

  function handleDownloadReport() {
    if (!result) return;
    // 지금 화면에 그려진 그대로를 담는다 — 사용자가 고른 지표·마커가 보고서의
    // 문제 번호와 맞아야 하기 때문이다.
    let planImage: string | null = null;
    try {
      planImage = canvasRef.current?.toDataURL("image/png") ?? null;
    } catch {
      planImage = null;
    }
    downloadFloorplanReport({
      fileName: file?.name || "도면",
      result,
      planImage,
      narrative,
      comparison,
      labels,
      scaleNote: metersPerUnit
        ? `가로 ${(canvasWidth * metersPerUnit).toFixed(0)}m${dxfUnits ? ` (도면 단위 ${dxfUnits})` : ""}`
        : "축척 미확인",
    });
  }

  function handleWidthMChange(text: string) {
    setWidthMInput(text);
    const widthM = Number(text);
    setMetersPerUnit(Number.isFinite(widthM) && widthM > 0 && canvasWidth > 0 ? widthM / canvasWidth : null);
    setResult(null);
  }

  // 새 결과가 오거나 결과가 지워지면 예전에 찍은 자리는 의미가 없다.
  useEffect(() => {
    setProbe(null);
  }, [result]);

  // 칸 찾기용 색인과 백분위용 정렬 값 — 결과마다 한 번만 만든다.
  const probeIndex = useMemo(() => {
    if (!result) return null;
    const byKey = new Map<string, (typeof result.cells)[number]>();
    for (const cell of result.cells) byKey.set(`${cell.row}:${cell.col}`, cell);
    const excluded = new Set((result.excluded_cells || []).map((c) => `${c.row}:${c.col}`));
    const sortedOf = (k: "integration" | "choice" | "connectivity") =>
      result.cells.map((c) => c[k]).sort((a, b) => a - b);
    return {
      byKey,
      excluded,
      integration: sortedOf("integration"),
      choice: sortedOf("choice"),
      connectivity: sortedOf("connectivity"),
    };
  }, [result]);

  const probeInfo = useMemo(() => {
    if (!probe || !result || !probeIndex) return null;
    const size = result.summary.cell_size;
    const key = `${Math.floor(probe.y / size)}:${Math.floor(probe.x / size)}`;
    const cell = probeIndex.byKey.get(key);
    // 가장 가까운 실명 — "어디쯤인지"를 좌표가 아니라 도면의 이름으로 말한다.
    let near: string | null = null;
    let best = size * 4;
    for (const label of labels) {
      const d = Math.hypot(label.x - probe.x, label.y - probe.y);
      if (d < best) {
        best = d;
        near = label.text;
      }
    }
    if (!cell) return { cell: null, excluded: probeIndex.excluded.has(key), near } as const;
    const cellM2 = result.summary.cell_size_m != null ? result.summary.cell_size_m ** 2 : null;
    const integrationPct = percentileOf(probeIndex.integration, cell.integration);
    return {
      cell,
      excluded: false,
      near,
      verdict: probeVerdict(integrationPct),
      integrationTop: topPct(integrationPct),
      choiceTop: topPct(percentileOf(probeIndex.choice, cell.choice)),
      visible: cellM2 != null ? `약 ${Math.round(cell.connectivity * cellM2).toLocaleString()}㎡` : `${Math.round(cell.connectivity)}칸`,
      visibleTop: topPct(percentileOf(probeIndex.connectivity, cell.connectivity)),
      turns: cell.mean_depth,
    } as const;
  }, [probe, result, probeIndex, labels]);

  const cellColorExtent = useMemo(() => {
    if (!result) return { min: 0, max: 1 };
    const values = result.cells.map((cell) => cellValue(cell, overlayMetric));
    return valueExtent(values);
  }, [result, overlayMetric]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || canvasWidth <= 0 || canvasHeight <= 0) return;
    canvas.width = displayWidth + CANVAS_PAD * 2;
    canvas.height = displayHeight + CANVAS_PAD * 2;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    // 여기부터는 도면 좌표 × displayScale 그대로 그린다 — 여백만큼 옮겨 둔다.
    ctx.translate(CANVAS_PAD, CANVAS_PAD);

    if (result) {
      const cellPx = result.summary.cell_size * displayScale;
      for (const cell of result.cells) {
        ctx.fillStyle = floorColor(cellValue(cell, overlayMetric), cellColorExtent.min, cellColorExtent.max);
        ctx.globalAlpha = 0.6;
        ctx.fillRect(cell.col * cellPx, cell.row * cellPx, cellPx + 0.5, cellPx + 0.5);
      }
      ctx.globalAlpha = 1;

      // 분석에서 빠진 칸 — 색 램프와 헷갈리지 않게 회색 빗금으로 덮는다.
      // "반영률 54%"가 어느 방을 못 봤다는 뜻인지 여기서 바로 보인다.
      const excluded = showExcluded ? result.excluded_cells || [] : [];
      if (excluded.length) {
        ctx.save();
        ctx.fillStyle = "#6b7280";
        ctx.globalAlpha = 0.35;
        for (const cell of excluded) {
          ctx.fillRect(cell.col * cellPx, cell.row * cellPx, cellPx + 0.5, cellPx + 0.5);
        }
        ctx.globalAlpha = 0.9;
        ctx.strokeStyle = "#dc2626";
        ctx.lineWidth = 1;
        for (const cell of excluded) {
          const x = cell.col * cellPx;
          const y = cell.row * cellPx;
          ctx.beginPath();
          ctx.moveTo(x, y + cellPx);
          ctx.lineTo(x + cellPx, y);
          ctx.stroke();
        }
        ctx.restore();
      }
    }

    // 선택은 빨강, 마우스 아래는 주황(지우개면 빨강) — 무엇을 누르게 될지 미리 보인다.
    const hoverColor = mode === "erase" ? "#dc2626" : "#f59e0b";
    lines.forEach((line, index) => {
      const isSelected = selected?.list === "lines" && selected.index === index;
      const isHover = hover?.list === "lines" && hover.index === index;
      ctx.lineWidth = isSelected || isHover ? 3.5 : 1.6;
      ctx.strokeStyle = isSelected ? "#dc2626" : isHover ? hoverColor : "#1f2937";
      ctx.beginPath();
      ctx.moveTo(line.x1 * displayScale, line.y1 * displayScale);
      ctx.lineTo(line.x2 * displayScale, line.y2 * displayScale);
      ctx.stroke();
    });

    // 개구부(문·창)는 벽을 뚫는 자리라 벽과 구별되게 초록 점선으로.
    if (openings.length) {
      ctx.save();
      ctx.lineWidth = 3;
      ctx.setLineDash([6, 4]);
      openings.forEach((opening, index) => {
        const isSelected = selected?.list === "openings" && selected.index === index;
        const isHover = hover?.list === "openings" && hover.index === index;
        ctx.strokeStyle = isSelected ? "#dc2626" : isHover ? hoverColor : "#059669";
        ctx.beginPath();
        ctx.moveTo(opening.x1 * displayScale, opening.y1 * displayScale);
        ctx.lineTo(opening.x2 * displayScale, opening.y2 * displayScale);
        ctx.stroke();
      });
      ctx.restore();
    }

    // 말로 고치기 — 고른 자리(파랑)와 미리보기(초록 = 생김, 빨간 점선 = 없어짐).
    if (mode === "voice") {
      const s = displayScale;
      ctx.save();
      const box = voiceDrag
        ? selectionFrom(voiceDrag.start, voiceDrag.end, VOICE_DRAG_PX / displayScale).box
        : voiceSel?.box ?? null;
      const point = voiceDrag ? (box ? null : voiceDrag.start) : voiceSel && !voiceSel.box ? voiceSel.point : null;
      ctx.strokeStyle = "#2563eb";
      ctx.fillStyle = "rgba(37, 99, 235, 0.12)";
      ctx.lineWidth = 2;
      if (box) {
        ctx.setLineDash([6, 4]);
        ctx.fillRect(box.x1 * s, box.y1 * s, (box.x2 - box.x1) * s, (box.y2 - box.y1) * s);
        ctx.strokeRect(box.x1 * s, box.y1 * s, (box.x2 - box.x1) * s, (box.y2 - box.y1) * s);
        ctx.setLineDash([]);
      }
      if (point) {
        ctx.beginPath();
        ctx.arc(point.x * s, point.y * s, 12, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(point.x * s, point.y * s, 3, 0, Math.PI * 2);
        ctx.fillStyle = "#2563eb";
        ctx.fill();
      }
      const prev = voicePendingLive
        ? previewOf(lines, openings, voicePendingLive.plan)
        : voiceChangeLive?.preview ?? null;
      if (prev) {
        const seg = (l: FloorplanLine) => {
          ctx.beginPath();
          ctx.moveTo(l.x1 * s, l.y1 * s);
          ctx.lineTo(l.x2 * s, l.y2 * s);
          ctx.stroke();
        };
        ctx.lineCap = "round";
        ctx.strokeStyle = "#dc2626";
        ctx.lineWidth = 4;
        ctx.setLineDash([5, 4]);
        prev.removed.forEach(seg);
        prev.removedOpenings.forEach(seg);
        ctx.setLineDash([]);
        // 새로 생긴 것은 흰 테두리를 깔아 칠한 색 위에서도 보이게 한다.
        for (const [list, dash] of [
          [prev.added, [] as number[]],
          [prev.addedOpenings, [7, 4]],
        ] as const) {
          ctx.setLineDash([...dash]);
          ctx.strokeStyle = "#ffffff";
          ctx.lineWidth = 7;
          list.forEach(seg);
          ctx.strokeStyle = "#059669";
          ctx.lineWidth = 4.5;
          list.forEach(seg);
        }
      }
      ctx.restore();
    }

    const drawColor = mode === "door" ? "#059669" : "#2563eb";
    if (pendingStart) {
      ctx.fillStyle = drawColor;
      ctx.beginPath();
      ctx.arc(pendingStart.x * displayScale, pendingStart.y * displayScale, 4, 0, Math.PI * 2);
      ctx.fill();
      // 미리보기 선 — 다음 클릭에 어디까지 그려지는지.
      if (cursor) {
        ctx.save();
        ctx.strokeStyle = drawColor;
        ctx.lineWidth = 2;
        ctx.setLineDash([5, 4]);
        ctx.beginPath();
        ctx.moveTo(pendingStart.x * displayScale, pendingStart.y * displayScale);
        ctx.lineTo(cursor.x * displayScale, cursor.y * displayScale);
        ctx.stroke();
        ctx.restore();
      }
    }
    if (cursor && (mode === "wall" || mode === "door")) {
      ctx.save();
      ctx.strokeStyle = drawColor;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(cursor.x * displayScale, cursor.y * displayScale, 6, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }

    // 실명과 심볼 — 분석 전에도 "무엇을 읽었는지" 보이게 한다 (0단계 인식 화면).
    if (showLabels && (labels.length || symbols.length)) {
      ctx.save();
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.font = "10px Pretendard, sans-serif";
      ctx.fillStyle = "#1f2937";
      // 실명끼리 겹치면 둘 다 못 읽었다(1280 샘플 「매장 20」+「매장 76」, 「중앙 광장」 위 다른 이름).
      // 이미 그린 이름과 겹치는 이름은 건너뛴다 — 구역 이름(광장·통로)을 매장 번호보다 먼저
      // 그린다 (2026-11-08 UX 자동 개선).
      const drawn: { x0: number; x1: number; y0: number; y1: number }[] = [];
      const ordered = [...labels].sort(
        (a, b) => Number(/^매장\s*\d+$/.test(a.text)) - Number(/^매장\s*\d+$/.test(b.text)),
      );
      for (const label of ordered) {
        const x = label.x * displayScale;
        const y = label.y * displayScale;
        // 영어 모드면 도면 이름표도 옮긴다(캔버스 글은 화면 자동 번역이 못 닿는다 — lib/autoTranslate).
        const shown = translateText(label.text);
        const half = ctx.measureText(shown).width / 2 + 1;
        const box = { x0: x - half, x1: x + half, y0: y - 6, y1: y + 6 };
        if (drawn.some((d) => box.x0 < d.x1 && box.x1 > d.x0 && box.y0 < d.y1 && box.y1 > d.y0)) continue;
        drawn.push(box);
        ctx.fillText(shown, x, y);
      }
      ctx.font = "bold 9px Pretendard, sans-serif";
      for (const sym of symbols) {
        const meta = SYMBOL_LABELS[sym.kind];
        const x = sym.x * displayScale;
        const y = sym.y * displayScale;
        ctx.fillStyle = meta.color;
        ctx.fillRect(x - 8, y - 8, 16, 16);
        ctx.fillStyle = "#ffffff";
        ctx.fillText(meta.glyph, x, y);
      }
      ctx.restore();
    }

    // 설명이 가리키는 자리를 번호로 찍는다 — 좌표 숫자만 주면 사용자가 도면에서
    // 못 찾는다.
    if (result) {
      ctx.save();
      ctx.font = "bold 11px Pretendard, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      const marker = (px: number, py: number, color: string, label: string) => {
        const x = px * displayScale;
        const y = py * displayScale;
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(x, y, 9, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.fillStyle = "#ffffff";
        ctx.fillText(label, x, y);
      };
      // 번호 오른쪽의 이름표 — 흰 바탕을 깔아 히트맵 위에서도 읽히게.
      const tag = (px: number, py: number, color: string, raw: string) => {
        const text = translateText(raw);
        const x = px * displayScale + 12;
        const y = py * displayScale;
        ctx.save();
        ctx.font = "bold 10px Pretendard, sans-serif";
        ctx.textAlign = "left";
        const w = ctx.measureText(text).width + 8;
        ctx.fillStyle = "rgba(255,255,255,0.92)";
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.roundRect(x, y - 8, w, 16, 4);
        ctx.fill();
        ctx.stroke();
        ctx.fillStyle = color;
        ctx.fillText(text, x + 4, y + 0.5);
        ctx.restore();
      };
      if (view === "rooms") {
        for (const room of result.rooms || []) {
          const on = activeRoom === room.index;
          marker(room.x, room.y, on ? "#111827" : room.analyzed_pct === 0 ? "#9ca3af" : "#2563eb", String(room.index));
        }
      } else if (view === "issues") {
        // 목록에서 고른 문제의 구역을 도면 위에 칠한다 — 글만으로는 어디인지 모른다.
        const picked = activeIssue != null ? (result.issues || [])[activeIssue] : null;
        if (picked?.cells?.length) {
          const cellPx = result.summary.cell_size * displayScale;
          ctx.save();
          ctx.fillStyle = issueZoneColor(picked);
          ctx.globalAlpha = 0.45;
          for (const [row, col] of picked.cells) ctx.fillRect(col * cellPx, row * cellPx, cellPx + 0.5, cellPx + 0.5);
          ctx.restore();
        }
        (result.issues || []).forEach((issue, i) => {
          // 번호 동그라미도 구역과 같은 뜻의 색 — 중요도는 카드의 글자 딱지가 맡는다.
          const color = issueZoneColor(issue);
          marker(issue.x, issue.y, color, String(i + 1));
          tag(issue.x, issue.y, color, issueTag(issue.title));
        });
      } else {
        for (const group of SPOT_GROUPS[overlayMetric]) {
          (result.summary[group.key] ?? []).forEach((spot, i) => {
            marker(spot.x, spot.y, group.color, String(i + 1));
            tag(spot.x, spot.y, group.color, `${group.tag} ${i + 1}위`);
          });
        }
      }

      // 사용자가 찍은 자리 — 검은 테두리 과녁. 번호 마커(색 원)와 모양이 달라야 헷갈리지 않는다.
      if (probe) {
        const x = probe.x * displayScale;
        const y = probe.y * displayScale;
        ctx.lineWidth = 3;
        ctx.strokeStyle = "#ffffff";
        ctx.beginPath();
        ctx.arc(x, y, 10, 0, Math.PI * 2);
        ctx.stroke();
        ctx.lineWidth = 2;
        ctx.strokeStyle = "#111827";
        ctx.beginPath();
        ctx.arc(x, y, 10, 0, Math.PI * 2);
        ctx.moveTo(x - 15, y);
        ctx.lineTo(x - 5, y);
        ctx.moveTo(x + 5, y);
        ctx.lineTo(x + 15, y);
        ctx.moveTo(x, y - 15);
        ctx.lineTo(x, y - 5);
        ctx.moveTo(x, y + 5);
        ctx.lineTo(x, y + 15);
        ctx.stroke();
        tag(probe.x, probe.y, "#111827", "찍은 자리");
      }
      ctx.restore();
    }
  }, [
    canvasWidth,
    canvasHeight,
    displayWidth,
    displayHeight,
    displayScale,
    lines,
    openings,
    selected,
    mode,
    pendingStart,
    cursor,
    hover,
    voiceSel,
    voiceDrag,
    voiceChangeLive,
    voicePendingLive,
    trVersion,
    labels,
    symbols,
    showLabels,
    result,
    view,
    activeRoom,
    activeIssue,
    overlayMetric,
    cellColorExtent,
    showExcluded,
    probe,
  ]);

  const hasCanvas = canvasWidth > 0 && canvasHeight > 0;

  return (
    <div className="floorplan-panel fp-analyzer">
      <div className="card floorplan-upload-card">
        <div className="floorplan-card-head">
          <span className="floorplan-eyebrow">건축 평면도 분석</span>
          <h2>도면 한 장으로 건물 안의 목 좋은 자리를 찾습니다</h2>
          <p className="floorplan-help-text">
            CAD 원본(DXF)을 올리면 벽과 문을 읽어, 바닥의 모든 자리마다 사람이 얼마나 자연히
            모이고 지나가는지 계산합니다. 사진·스캔 이미지는 받지 않습니다.
          </p>
        </div>
        <label className={`floorplan-dropzone${extracting ? " floorplan-dropzone--busy" : ""}`}>
          <input
            type="file"
            accept=".dxf"
            onChange={(e) => {
              handleFileChange(e.target.files?.[0] || null);
              // 같은 파일을 다시 골라도 onChange가 오게 비운다 — 캐드에서 고쳐 같은 이름으로
              // 저장한 도면, 잘못 고른 사진을 다시 고른 경우 무반응이었다(2026-11-08 UX 자동 개선,
              // 계획도면 탭과 같은 처리).
              e.target.value = "";
            }}
            className="floorplan-file-input"
          />
          <span className="floorplan-dropzone-title">{file ? file.name : "DXF 파일 선택"}</span>
          <span className="floorplan-dropzone-sub">
            {file ? (
              "다른 파일을 고르려면 다시 누르세요"
            ) : (
              <>
                눌러서 고르세요<span className="floorplan-desktop-hint"> · 끌어다 놓아도 됩니다</span> · .dxf
              </>
            )}
          </span>
        </label>
        <div className="floorplan-sample-row">
          <span>도면이 없다면</span>
          {SAMPLE_PLANS.map((sample) => (
            <button
              key={sample.path}
              type="button"
              className="floorplan-link-button"
              disabled={extracting}
              onClick={() => handleSample(sample)}
            >
              {sample.label}
            </button>
          ))}
        </div>
        {extracting && <div className="status-banner status-banner--loading">벽을 찾는 중입니다…</div>}
        {extractError && (
          <div className="status-banner status-banner--error floorplan-error-row" role="alert">
            <span>{extractError}</span>
            {/* 서버가 잠깐 응답하지 않아 실패하면 할 일이 「같은 파일을 다시 고르기」뿐이었다 —
                휴대폰에선 파일 창을 다시 열어 찾아야 했다 (2026-11-08 UX 자동 개선). */}
            {extractRetryable && file && !extracting && (
              <button
                type="button"
                className="map-error__retry"
                onClick={() => void runExtract(file, lastRolesRef.current)}
              >
                다시 시도
              </button>
            )}
          </div>
        )}
        {!!autoNotes.length && (
          <ul className="auto-notes-banner">
            <strong>이렇게 읽었습니다</strong>
            {autoNotes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        )}
        {!!extractWarnings.length && (
          <ul className="warnings-banner">
            {extractWarnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        )}
        {(layersGuessed || suspectLayers.length > 0) && (
          <p className="floorplan-layer-alert">
            {layersGuessed ? "벽이 어느 종이(레이어)에 있는지 짐작했습니다." : "벽일 수도 있는 종이(레이어)가 있습니다."}{" "}
            <button
              type="button"
              className="floorplan-link-button"
              onClick={() => {
                const card = document.getElementById("floorplan-layer-card");
                if (card) scrollIntoPanel(card, "start");
              }}
            >
              맨 아래 「도면을 이렇게 읽었어요」에서 확인하기
            </button>
          </p>
        )}
      </div>


      {hasCanvas && (
        <div className="card floorplan-editor-card" ref={editorCardRef}>
          <div className="floorplan-card-head floorplan-card-head--row">
            <div>
              <span className="floorplan-eyebrow">읽은 도면</span>
              <h2>{result ? "자리마다 색으로 칠했습니다" : "벽과 문이 맞는지 확인하세요"}</h2>
            </div>
            <span className="floorplan-line-count">
              벽 {lines.length.toLocaleString()} · 문 {openings.length.toLocaleString()}
            </span>
          </div>
          <div className="floorplan-editor-toolbar">
            <div className="floorplan-mode-group" role="radiogroup" aria-label="편집 도구">
              {EDIT_MODES.map((m) => (
                <button
                  key={m.mode}
                  type="button"
                  role="radio"
                  aria-checked={mode === m.mode}
                  className={`floorplan-mode-btn${m.mode === "voice" ? " floorplan-mode-btn--cta" : ""}${mode === m.mode ? ` floorplan-mode-btn--on floorplan-mode-btn--${m.mode}` : ""}`}
                  onClick={() => changeMode(m.mode)}
                >
                  {m.mode === "voice" ? "🎤 말로 고치기" : m.label}
                </button>
              ))}
            </div>
            <button type="button" className="secondary-button" onClick={undo} disabled={historySize === 0}>
              되돌리기
            </button>
            {/* 휴대폰·태블릿엔 Esc도 오른쪽 클릭도 없어 벽을 잇다가 멈출 방법이 없었다
                (2026-11-06 UX 자동 개선, 390px 테스트) — 그리는 중엔 버튼으로 끝낸다. */}
            {(mode === "wall" || mode === "door") && pendingStart && (
              <button type="button" className="secondary-button" onClick={() => finishDrawing()}>
                {mode === "wall" ? "그리기 끝내기" : "문 뚫기 취소"}
              </button>
            )}
            {mode === "select" && selected && (
              <button type="button" className="secondary-button" onClick={deleteSelected}>
                고른 선 지우기
              </button>
            )}
            {/* 휴대폰에선 도면이 약 306×200px라 벽 하나·문 양 끝을 손가락으로 고르기 어려웠다
                (2026-11-07 UX 자동 개선, 390px) — 크게 보면 도면만 2.5배로 키워 그 칸 안에서
                밀어 옮긴다. 640px 넘는 화면엔 단추가 없다. */}
            <button
              type="button"
              className="secondary-button floorplan-zoom-toggle"
              aria-pressed={canvasZoomed}
              onClick={() => {
                const next = !canvasZoomed;
                setCanvasZoomed(next);
                if (next) {
                  requestAnimationFrame(() => {
                    const box = canvasScrollRef.current;
                    if (!box) return;
                    box.scrollLeft = (box.scrollWidth - box.clientWidth) / 2;
                    box.scrollTop = (box.scrollHeight - box.clientHeight) / 2;
                  });
                }
              }}
            >
              {canvasZoomed ? "도면 작게 보기" : "도면 크게 보기"}
            </button>
          </div>
          <p className="floorplan-mode-hint">
            {(() => {
              const m = EDIT_MODES.find((row) => row.mode === mode);
              if (!m) return null;
              return (
                <>
                  {m.hint}
                  {m.keys && <span className="floorplan-desktop-hint">{m.keys}</span>}
                  {m.after}
                </>
              );
            })()}{" "}
            잘못했으면 「되돌리기」<span className="floorplan-desktop-hint">(Ctrl+Z)</span>.
          </p>
          {editNotice && (
            <p className="floorplan-edit-notice" role="status">
              {editNotice}
            </p>
          )}

          <div
            ref={canvasScrollRef}
            className={`floorplan-canvas-scroll${canvasZoomed ? " floorplan-canvas-scroll--zoomed" : ""}`}
          >
          <canvas
            ref={canvasRef}
            className={`floorplan-canvas floorplan-canvas--${mode}`}
            onClick={handleCanvasClick}
            onPointerDown={handleVoicePointerDown}
            onPointerMove={handleVoicePointerMove}
            onPointerUp={handleVoicePointerUp}
            onPointerCancel={() => setVoiceDrag(null)}
            onMouseMove={handleCanvasMove}
            onMouseLeave={() => {
              setHover(null);
              setCursor(null);
            }}
            onContextMenu={(e) => {
              if (mode === "wall" || mode === "door") finishDrawing(e);
            }}
          />
          </div>
          {mode === "voice" && (
            <FloorplanVoiceEdit
              selectionText={voiceSelectionText(voiceSel)}
              busy={voiceBusy}
              error={voiceError}
              reply={voiceReply}
              pending={voicePendingLive?.plan ?? null}
              done={voiceChangeLive?.plan ?? null}
              onSubmit={(text) => void handleVoiceSubmit(text)}
              onApply={applyVoicePending}
              onCancel={cancelVoicePending}
              onUndo={undo}
            />
          )}
          {result && <ProbeCard info={probeInfo} armed={mode === "select"} onClose={() => setProbe(null)} />}
          {result && (
            <FloorplanLegend
              metric={overlayMetric}
              view={view}
              hasExcluded={showExcluded && !!(result.excluded_cells || []).length}
            />
          )}

          {/* 2026-11-06 UX 자동 개선: 「도면에서 읽은 것」·가로 길이를 캔버스 아래로 내렸다.
              편집 도구 줄과 캔버스 사이에 끼어 있어 휴대폰에선 도구를 고르고 한참 스크롤해야
              도면에 닿았다. 둘 다 자동으로 채워져 대개 손댈 일이 없다. */}
          <div className="floorplan-recognized">
            <strong>도면에서 읽은 것</strong>
            <ul>
              <li>실명 {labels.length}</li>
              {(["entrance", "exit", "stair", "elevator"] as FloorplanSymbolKind[]).map((kind) => {
                const n = symbols.filter((s) => s.kind === kind).length;
                return n > 0 ? <li key={kind}>{SYMBOL_LABELS[kind].ko} {n}</li> : null;
              })}
            </ul>
            {(labels.length > 0 || symbols.length > 0) && (
              <label className="floorplan-excluded-toggle">
                <input type="checkbox" checked={showLabels} onChange={(e) => setShowLabels(e.target.checked)} />
                도면 위에 표시
              </label>
            )}
            {labels.length === 0 && symbols.length === 0 && (
              <span className="floorplan-scale-hint">실명·계단·출입구 표시가 없는 도면입니다. 문제 설명은 방향과 넓이로만 나옵니다.</span>
            )}
          </div>

          <label className="floorplan-scale-row">
            <span>도면 가로 길이</span>
            <input
              type="number"
              min={1}
              step={0.1}
              inputMode="decimal"
              value={widthMInput}
              onChange={(e) => handleWidthMChange(e.target.value)}
              placeholder="예: 42.5"
              className="floorplan-scale-input"
            />
            <span>m</span>
            <span className="floorplan-scale-hint">
              {/* 칸을 비우거나 0을 넣어도 「자동으로 읽었습니다」가 그대로라, 결과 넓이가 왜
                  「약 135칸」으로 나오는지 알 수 없었다 — 비었을 땐 그렇게 된다고 말한다
                  (2026-11-07 UX 자동 개선). */}
              {metersPerUnit == null && widthMInput.trim() !== ""
                ? "0보다 큰 숫자를 넣어 주세요. 비워 두면 넓이가 m² 대신 칸 수로 나옵니다."
                : metersPerUnit == null && autoWidthM
                  ? `비워 두면 넓이가 m² 대신 칸 수로 나옵니다. 도면 단위(${dxfUnits ?? "m"})로 읽은 값은 ${autoWidthM}m입니다.`
                  : autoWidthM && Number(widthMInput) !== Number(autoWidthM)
                    ? `직접 넣은 값입니다. 도면 단위(${dxfUnits ?? "m"})로 읽은 값은 ${autoWidthM}m였습니다.`
                  : dxfUnits
                    ? `도면 단위(${dxfUnits})에서 자동으로 읽었습니다. 다르면 고쳐 주세요.`
                    : "도면에 단위가 없습니다. 입력하면 결과를 m로 보여줍니다."}
            </span>
          </label>

          <button
            type="button"
            className="primary-button floorplan-analyze-btn"
            onClick={handleAnalyze}
            disabled={analyzing || lines.length === 0}
          >
            {analyzing ? "분석 중…" : "분석 실행"}
          </button>
          {edited && (
            <button
              type="button"
              className="secondary-button floorplan-analyze-btn"
              onClick={handleCompare}
              disabled={comparing}
            >
              {comparing ? "비교 중…" : "바꾼 것과 비교하기"}
            </button>
          )}
          {edited && !comparing && (
            <p className="floorplan-scale-hint">
              처음 분석한 도면을 원본으로 두고 지금 고친 것과 견줍니다.
            </p>
          )}
          {compareError && <div className="status-banner status-banner--error">{compareError}</div>}
          {analyzeError && <div className="status-banner status-banner--error">{analyzeError}</div>}
        </div>
      )}

      {comparison && <CompareCard comparison={comparison} onClose={() => setComparison(null)} />}

      {result && (
        <div className="card floorplan-result-card" ref={resultCardRef}>
          {resultStale && (
            <p className="status-banner floorplan-stale-banner">
              도면을 고쳤습니다. 아래 결과는 <strong>고치기 전</strong> 도면의 것입니다 — 「바꾼 것과 비교하기」로 달라진 점을 보거나,
              「분석 실행」으로 고친 도면을 다시 분석하세요.
            </p>
          )}
          <div className="floorplan-result-header">
            <div>
              <span className="floorplan-eyebrow">분석 결과</span>
              <h2>{view === "issues" ? "발견된 문제" : view === "rooms" ? "공간별 비교" : `지도 보기 · ${METRIC_LABELS[overlayMetric].easy}`}</h2>
            </div>
            <button type="button" className="secondary-button floorplan-report-btn" onClick={handleDownloadReport}>
              보고서 내려받기
            </button>
          </div>
          {/* 2026-11-07: 결론(AI 해석 또는 규칙 요약문)이 문제 카드 4장·수치 카드 밑, 휴대폰에서 약 3,000px
              아래에 있어 「그래서 이 도면이 어떻다는 건지」를 맨 끝에야 읽었다 — 결과 머리 바로 밑으로 올린다
              (UX 자동 개선, 390px). 수치 카드는 세부라 아래에 둔다. 버린 안: 맨 위에 「요약 보기 ↓」 링크만 두기 —
              한 번 더 눌러야 하고 내려간 뒤 카드로 돌아오기 어렵다. */}
          {narrative ? (
            <div className="floorplan-narrative">
              <div className="floorplan-narrative-tag">AI 해석</div>
              <SpeakButton text={narrative.text} />
              {narrative.text.split(/\n{2,}/).map((para, i) => (
                <p key={i} className="floorplan-explanation">
                  {para.split(/(\*\*[^*]+\*\*)/).map((chunk, j) =>
                    chunk.startsWith("**") && chunk.endsWith("**") ? <strong key={j}>{chunk.slice(2, -2)}</strong> : <span key={j}>{chunk}</span>,
                  )}
                </p>
              ))}
            </div>
          ) : (
            <>
              <p className="floorplan-explanation">{result.explanation.text}</p>
              {!narrativeLoading && <SpeakButton text={result.explanation.text} />}
              {narrativeLoading && <p className="floorplan-narrative-loading">AI 해석을 쓰는 중…</p>}
            </>
          )}
          {/* 2026-11-09: 해석을 읽고 되묻는 상담 창. key로 새 분석마다 대화를 비운다 — 옛 도면 얘기가 남으면
              모델이 바뀐 카드 번호를 옛 뜻으로 읽는다. 답이 가리키는 카드는 도면에 칠하고 위로 올려 보여 준다. */}
          <FloorplanChat
            key={analyzeSeq}
            result={result}
            narrative={narrative?.text ?? null}
            onShowCard={(index) => {
              setView("issues");
              setActiveIssue(index);
              const canvas = canvasRef.current;
              if (canvas) scrollIntoPanel(canvas, "start");
            }}
          />
          {/* 2026-10-24: 같은 모양 버튼 7개가 줄지어 있어 무엇이 켜졌는지 안 보였다 — 밑줄 탭으로,
              「결과」와 「지도 보기」 두 묶음으로 나눈다. */}
          <div className="floorplan-tabs" role="tablist" aria-label="결과 보기">
            <button
              type="button"
              role="tab"
              aria-selected={view === "issues"}
              className={`floorplan-tab${view === "issues" ? " floorplan-tab--on" : ""}`}
              onClick={() => setView("issues")}
            >
              발견된 문제
              {/* 개수엔 「가장 목이 좋은 곳」(좋은 구역 안내) 카드를 넣지 않는다 — 넣으면 문제 3개인 도면이
                  「발견된 문제 4」로 보였다(2026-11-07 UX 자동 개선). 카드 목록엔 그대로 둔다. */}
              {(() => {
                const problems = (result.issues || []).filter((issue) => issue.kind !== "best_zone").length;
                return problems > 0 ? <span className="floorplan-tab-count">{problems}</span> : null;
              })()}
            </button>
            {!!(result.rooms && result.rooms.length) && (
              <button
                type="button"
                role="tab"
                aria-selected={view === "rooms"}
                className={`floorplan-tab${view === "rooms" ? " floorplan-tab--on" : ""}`}
                onClick={() => setView("rooms")}
              >
                공간별 비교
              </button>
            )}
            <span className="floorplan-tab-divider" aria-hidden="true" />
            <span className="floorplan-tab-group-label">지도 보기</span>
            {METRIC_ORDER.filter((metric) => metric !== "robot" || hasRobotAisles(result.summary)).map((metric) => (
              <button
                key={metric}
                type="button"
                role="tab"
                aria-selected={view === metric}
                className={`floorplan-tab${view === metric ? " floorplan-tab--on" : ""}`}
                onClick={() => {
                  setView(metric);
                  // 「지도 보기」는 위 도면의 색을 바꾸는 단추인데, 휴대폰에선 도면이 약 1,000px 위라
                  // 눌러도 아래 설명 한 줄만 바뀌어 보였다 — 도면이 화면 밖이면 올려 보여 준다 (2026-11-06 UX 자동 개선).
                  revealCanvas();
                }}
                title={METRIC_LABELS[metric].jargon}
              >
                {METRIC_LABELS[metric].easy}
              </button>
            ))}
          </div>

          {view === "rooms" ? (
            <RoomTable
              rooms={result.rooms || []}
              active={activeRoom}
              onHover={setActiveRoom}
              onPick={(index) => {
                setActiveRoom(index);
                // 휴대폰에선 도면이 표보다 약 1,000px 위라 줄을 눌러도 아무 일 없어 보였다 (2026-11-06 UX 자동 개선).
                revealCanvas();
              }}
            />
          ) : view === "issues" ? (
            (result.issues || []).length === 0 ? (
              <p className="floorplan-def">이 도면에서 눈에 띄는 문제를 찾지 못했습니다. 지표 뷰에서 자리별 차이를 확인하세요.</p>
            ) : (
              <>
              <p className="floorplan-issue-guide">
                카드 앞 번호(1, 2, 3…)는 위 도면에 같은 번호로 찍힌 동그라미 자리입니다. 카드 그림에 칠한 곳은 그 카드가
                말하는 구역입니다 — <span className="floorplan-zone-swatch floorplan-zone-swatch--good" /> 주황은 손님이 잘
                모이는 곳, <span className="floorplan-zone-swatch floorplan-zone-swatch--bad" /> 파랑은 손님 발길이 잘 안 닿는
                구석(도면 점수 색과 같은 방향).
              </p>
              <ol className="floorplan-issue-list">
                {(result.issues || []).map((issue, i, all) => (
                  <Fragment key={`${issue.title}-${i}`}>
                  {/* 탭은 「발견된 문제 3」인데 카드가 4장 — 넷째(가장 목 좋은 곳, 「참고」)가 문제 카드와
                      똑같이 이어져 개수가 틀린 것처럼 읽혔다. 첫 좋은 구역 카드 앞에 한 줄로 갈라 둔다
                      (2026-11-08 UX 자동 개선). 번호는 도면 동그라미와 맞춰야 해 그대로 둔다. */}
                  {issue.kind === "best_zone" && all.findIndex((x) => x.kind === "best_zone") === i && (
                    <li className="floorplan-issue-sep">문제가 아니라 참고로 — 손님이 잘 모이는 곳</li>
                  )}
                  <li
                    className={`floorplan-issue floorplan-issue--${issue.severity}${activeIssue === i ? " floorplan-issue--active" : ""}`}
                    onMouseEnter={() => setActiveIssue(i)}
                    onMouseLeave={() => setActiveIssue(null)}
                    onClick={() => setActiveIssue(i)}
                  >
                    <span className="floorplan-issue-marker" style={{ background: issueZoneColor(issue) }}>
                      {i + 1}
                    </span>
                    <IssueThumb
                      issue={issue}
                      number={i + 1}
                      lines={lines}
                      openings={openings}
                      labels={labels}
                      cellSize={result.summary.cell_size}
                      canvasWidth={canvasWidth}
                      canvasHeight={canvasHeight}
                    />
                    <div className="floorplan-issue-body">
                      <div className="floorplan-issue-head">
                        <strong>{issue.title}</strong>
                        <span className={`floorplan-sev-pill floorplan-sev-pill--${issue.severity}`}>
                          {SEVERITY[issue.severity].label}
                        </span>
                      </div>
                      <p>{issue.detail}</p>
                      <small>
                        {issue.evidence}
                        {/* 방위 기준은 backend 제목과 같은 격자 범위 — 캔버스 폭으로 재면 「남서쪽」 제목 아래 「서쪽」이 나왔다. */}
                        {` · ${issuePlace(issue, labels, result.summary.cols * result.summary.cell_size, result.summary.rows * result.summary.cell_size)}`}
                      </small>
                    </div>
                  </li>
                  </Fragment>
                ))}
              </ol>
              </>
            )
          ) : (
            <p className="floorplan-def">
              <strong>{METRIC_LABELS[overlayMetric].easy}</strong> — {METRIC_LABELS[overlayMetric].def}
            </p>
          )}

          {view !== "issues" && view !== "rooms" && SPOT_GROUPS[overlayMetric].length > 0 && (
            <div className="floorplan-spot-lists">
              {SPOT_GROUPS[overlayMetric].map((group) => (
                <div key={group.key} className="floorplan-spot-list">
                  <h3>
                    <span className="floorplan-spot-swatch" style={{ background: group.color }} aria-hidden="true" />
                    {group.label}
                  </h3>
                  <ol>
                    {(result.summary[group.key] ?? []).map((spot, i) => (
                      <li key={`${group.key}-${i}`}>
                        <span>{spotWhere(spot, labels, result.summary.cols * result.summary.cell_size, result.summary.rows * result.summary.cell_size)}</span>
                        <span className="floorplan-spot-pct">{spotRank(group.key, spot)}</span>
                      </li>
                    ))}
                  </ol>
                </div>
              ))}
            </div>
          )}

          {!!(result.excluded_cells && result.excluded_cells.length) && (
            <div className="floorplan-excluded-legend">
              <label className="floorplan-excluded-toggle">
                <input
                  type="checkbox"
                  checked={showExcluded}
                  onChange={(e) => setShowExcluded(e.target.checked)}
                />
                <span className="floorplan-excluded-swatch" aria-hidden="true" />
                분석에서 빠진 공간 표시
              </label>
              <span className="floorplan-excluded-hint">
                빗금 친 칸은 벽에 막혀 나머지 공간과 이어지지 않은 곳입니다. 그 경계에서
                실제 문이 있는 자리를 <strong>문 자리 표시</strong>로 찍고 다시 분석하면
                함께 계산됩니다.
              </span>
            </div>
          )}
          {/* 2026-10-24: 「평균 목 1.888」 같은 숫자는 아무도 못 읽는다(사용자 지적). 같은 값을
              ㎡·배·번 같은 일상 단위로 바꿔 보여 주고, 원래 지표값은 마우스를 올리면 보인다. */}
          {(() => {
            const s = result.summary;
            const cellM2 = s.cell_size_m != null ? s.cell_size_m * s.cell_size_m : null;
            const ratio = s.integration_mean > 0 ? s.integration_max / s.integration_mean : null;
            return (
              <dl className="floorplan-summary-grid">
                <div title={`격자 칸 ${s.cell_count.toLocaleString()}개`}>
                  <dt>분석한 바닥</dt>
                  <dd>{cellM2 != null ? `${Math.round(s.cell_count * cellM2).toLocaleString()}㎡` : `${s.cell_count.toLocaleString()}칸`}</dd>
                  <small>사람이 다닐 수 있는 실내 넓이</small>
                </div>
                <div title={`${METRIC_LABELS.integration.jargon} — 최고 ${s.integration_max.toFixed(3)} / 평균 ${s.integration_mean.toFixed(3)}`}>
                  <dt>가장 좋은 자리</dt>
                  <dd>{ratio != null ? `평균의 ${ratio.toFixed(1)}배` : "—"}</dd>
                  <small>건물 평균보다 그만큼 닿기 쉬움</small>
                </div>
                <div title={`${METRIC_LABELS.connectivity.jargon} — 평균 ${s.connectivity_mean.toFixed(1)}칸`}>
                  <dt>한 자리에서 보이는 범위</dt>
                  {/* ㎡ 하나만 두면 "너무 크다"로 읽힌다 — 광장·복도 칸이 평균을 끌어올리기 때문.
                      전체 실내 대비 몇 %로 먼저 보이고 ㎡는 보조로 둔다. */}
                  <dd>실내의 {s.cell_count > 0 ? Math.max(1, Math.round((s.connectivity_mean / s.cell_count) * 100)) : 0}%</dd>
                  <small>
                    섰을 때 벽에 가리지 않고 보이는 바닥의 평균
                    {cellM2 != null ? ` (약 ${Math.round(s.connectivity_mean * cellM2).toLocaleString()}㎡)` : ""}
                    {" "}— 넓은 광장·복도에선 크고, 매장 안쪽에선 작음
                  </small>
                </div>
                <div title={`평균 깊이(Mean Depth) ${s.mean_depth_mean.toFixed(2)}`}>
                  <dt>어디든 가는 데</dt>
                  <dd>평균 {s.mean_depth_mean.toFixed(1)}번 꺾기</dd>
                  <small>시야가 바뀌는 횟수 — 적을수록 길 찾기 쉬움</small>
                </div>
                <div>
                  <dt>도면 반영률</dt>
                  <dd className={s.coverage_pct < 70 ? "floorplan-coverage-low" : undefined}>{s.coverage_pct.toFixed(0)}%</dd>
                  <small>{s.coverage_pct < 70 ? "막힌 문이 있어 일부만 계산됨" : "도면 전체를 계산함"}</small>
                </div>
              </dl>
            );
          })()}
          {/* 엔진이 알아서 바로잡은 것(벽 끝 이어 붙임)은 경고가 아니라 회색 안내 — 2026-11-08 */}
          {!!(result.notes && result.notes.length) && (
            <ul className="auto-notes-banner">
              <strong>이렇게 계산했습니다</strong>
              {result.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
          {!!(result.warnings && result.warnings.length) && (
            <ul className="warnings-banner">
              {result.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {/* 2026-10-24: 「도면을 이렇게 읽었어요」(레이어)는 맨 아래로 — 평소엔 안 봐도 되는 비상 손잡이라
          흐름(올리기 → 도면 → 결과)을 끊지 않게 한다(사용자 제안). 헷갈릴 때만 위에 한 줄 안내가 뜬다. */}
      {!!layers.length && (
        <div className="card floorplan-layer-card" id="floorplan-layer-card">
          <div className="floorplan-result-header">
            <h2>도면을 이렇게 읽었어요</h2>
            <button
              type="button"
              className="secondary-button"
              onClick={() => setShowLayerPicker((v) => !v)}
            >
              {showLayerPicker ? "접기" : "레이어 고치기"}
            </button>
          </div>
          <p className="floorplan-scale-hint">
            {layersGuessed
              ? "레이어 이름으로 벽을 찾지 못해 선이 가장 많은 레이어를 벽으로 짐작했습니다. 아래에서 확인해 주세요."
              : suspectLayers.length
                ? `벽으로 쓰지 않은 레이어에도 선이 많습니다 (${suspectLayers.slice(0, 3).join(" · ")}). 내벽·칸막이가 거기 있으면 방이 나뉘지 않으니 확인해 주세요.`
                : "벽과 문을 제대로 찾았습니다. 결과가 이상할 때만 고치세요."}
          </p>
          {/* 2026-10-24: 표를 두 번 설명해도 「이해가 안 간다」(사용자) — 화면이 먼저 말한다. */}
          <p className="floorplan-scale-hint">
            캐드 도면은 투명한 종이(레이어) 여러 장을 겹친 것입니다. 종이마다 벽으로 쓸지, 문으로 쓸지,
            안 쓸지를 정했습니다. 예를 들어 칸막이 종이를 「안 씀」으로 잘못 읽으면 방이 하나로 붙으니,
            그럴 때 「벽」으로 바꾸고 다시 읽으세요.
          </p>
          {showLayerPicker && (
            <>
              <table className="floorplan-room-table floorplan-layer-table">
                <thead>
                  <tr>
                    <th>종이 이름</th>
                    <th>선 개수</th>
                    <th>선 보통 길이</th>
                    <th>글자·기호</th>
                    <th>어떻게 쓸지</th>
                  </tr>
                </thead>
                <tbody>
                  {layers.map((layer) => (
                    <tr key={layer.name}>
                      <td>
                        {layer.name}
                        {layer.guessed_by === "size" && <span className="floorplan-room-noname"> (짐작)</span>}
                        {suspectLayers.includes(layer.name) && (
                          <span className="floorplan-layer-suspect"> · 벽일 수도</span>
                        )}
                      </td>
                      <td>{layer.segment_count.toLocaleString()}</td>
                      <td>{layer.median_length ? layer.median_length.toFixed(1) : "—"}</td>
                      <td>
                        {[
                          layer.text_count ? `방 이름 ${layer.text_count}개` : "",
                          layer.block_count ? `기호 ${layer.block_count}개` : "",
                        ]
                          .filter(Boolean)
                          .join(" · ") || "—"}
                      </td>
                      <td>
                        {!layer.segment_count ? (
                          <span className="floorplan-room-noname">설명에만 씀</span>
                        ) : (
                        <select
                          value={layerRoles[layer.name] ?? "ignore"}
                          onChange={(e) => setLayerRole(layer.name, e.target.value as LayerRole)}
                          className="floorplan-layer-select"
                          disabled={!layer.segment_count}
                        >
                          <option value="wall">벽</option>
                          <option value="opening">문·창</option>
                          <option value="ignore">안 씀</option>
                        </select>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <button
                type="button"
                className="secondary-button"
                onClick={() => file && runExtract(file, layerRoles)}
                disabled={extracting || !file || !Object.values(layerRoles).includes("wall")}
              >
                {extracting ? "다시 읽는 중…" : "이 설정으로 다시 읽기"}
              </button>
              {!Object.values(layerRoles).includes("wall") && (
                <p className="floorplan-scale-hint">벽 레이어를 하나 이상 골라 주세요.</p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

type ProbeInfo = {
  cell: unknown;
  excluded: boolean;
  near: string | null;
  verdict?: string;
  integrationTop?: number;
  choiceTop?: number;
  visible?: string;
  visibleTop?: number;
  turns?: number;
};

/** 찍은 자리 하나의 점수. 지표 원값 대신 「건물 안에서 상위 몇 %」와 일상 단위로 말한다. */
function ProbeCard({ info, armed, onClose }: { info: ProbeInfo | null; armed: boolean; onClose: () => void }) {
  if (!info) {
    return (
      <p className="floorplan-probe-hint">
        {armed
          ? "도면에서 궁금한 자리를 누르면 그 자리의 점수가 여기에 나옵니다."
          : "자리 점수를 보려면 위 도구에서 「선택」을 고른 뒤 도면을 누르세요."}
      </p>
    );
  }
  // 다른 화면은 이름을 「」로 묶는데 여기만 곧은 따옴표('중앙 광장')였다 (2026-11-08 UX 자동 개선).
  const where = info.near ? `「${info.near}」 근처` : "찍은 자리";
  return (
    <div className="floorplan-probe" role="status">
      <div className="floorplan-probe-head">
        <strong>
          {where}
          {info.verdict ? ` — ${info.verdict}` : ""}
        </strong>
        <button type="button" className="floorplan-link-button" onClick={onClose}>
          닫기
        </button>
      </div>
      {info.cell == null ? (
        <p className="floorplan-probe-none">
          {info.excluded
            ? "이 자리는 벽에 막혀 다른 공간과 이어지지 않아 계산에서 빠졌습니다. 문 자리를 찍고 다시 분석하면 나옵니다."
            : "이 자리는 사람이 다니는 바닥이 아닙니다 (벽 위이거나 건물 바깥). 조금 옆을 눌러 보세요."}
        </p>
      ) : (
        <dl className="floorplan-probe-grid">
          <div>
            <dt>목 좋은 정도</dt>
            <dd>상위 {info.integrationTop}%</dd>
          </div>
          <div>
            <dt>지나가는 정도</dt>
            <dd>상위 {info.choiceTop}%</dd>
          </div>
          <div>
            <dt>여기서 보이는 넓이</dt>
            <dd>
              {info.visible} <small>(상위 {info.visibleTop}%)</small>
            </dd>
          </div>
          <div>
            <dt>어디든 가는 데</dt>
            <dd>평균 {info.turns?.toFixed(1)}번 꺾기</dd>
          </div>
        </dl>
      )}
      <small className="floorplan-probe-note">「상위 몇 %」는 이 건물 안 다른 자리들과 견준 순위입니다. 1%에 가까울수록 좋습니다.</small>
    </div>
  );
}

/** 방 하나가 한 줄. 목이 좋은 순으로 이미 정렬돼 온다 — 사용자는 "내 매장이 몇
 *  번째인가"를 보러 오므로 지표값보다 순위·백분위를 앞에 둔다. */
/** 도면 아래 범례 — 색과 표시가 무엇인지 쉬운 말로.
 *  문제 번호 안내는 「누르면」만 말한다 — 「마우스를 올리면」은 휴대폰에 없는 동작이고,
 *  누르기는 데스크톱에서도 같은 일을 한다 (2026-11-06 UX 자동 개선). */
function FloorplanLegend({ metric, view, hasExcluded }: { metric: OverlayMetric; view: ResultView; hasExcluded: boolean }) {
  const ends = RAMP_ENDS[metric];
  const ramp = FLOOR_RAMP;
  const markers =
    view === "issues"
      ? [{ color: "#dc2626", label: "번호 = 아래 「발견된 문제」 번호 (카드를 누르면 그 구역이 칠해집니다)" }]
      : view === "rooms"
        ? [{ color: "#2563eb", label: "번호 = 아래 표의 공간 번호" }]
        : SPOT_GROUPS[metric].map((g) => ({ color: g.color, label: `번호 = ${g.label} 순위 (1위가 ${g.best})` }));
  return (
    <div className="floorplan-legend" aria-label="도면 색 읽는 법">
      <div className="floorplan-legend-ramp">
        <span>{ends.low}</span>
        <i style={{ background: `linear-gradient(90deg, ${ramp.join(", ")})` }} aria-hidden />
        <span>{ends.high}</span>
      </div>
      <p className="floorplan-legend-how">{ends.howTo}</p>
      <ul className="floorplan-legend-keys">
        <li><i className="floorplan-legend-wall" aria-hidden />벽</li>
        <li><i className="floorplan-legend-door" aria-hidden />문·창 (사람이 지나갈 수 있는 곳)</li>
        {hasExcluded && <li><i className="floorplan-legend-excluded" aria-hidden />회색 빗금 = 다른 곳과 이어지지 않아 계산에서 빠진 곳</li>}
        {markers.map((m) => (
          <li key={m.label}><i className="floorplan-legend-dot" style={{ background: m.color }} aria-hidden />{m.label}</li>
        ))}
      </ul>
    </div>
  );
}

const THUMB_W = 240;
const THUMB_H = 160;

/**
 * 문제 하나가 도면의 어디인지 잘라 보여 주는 작은 그림 (2026-10-24 사용자 지적:
 * 「남서쪽 '매장 68' 부근」이라고만 하면 모른다). 구역 칸을 칠하고 주변 벽과 실명을 함께
 * 그려, 큰 도면을 뒤지지 않아도 어디인지 알게 한다.
 */
function IssueThumb({
  issue,
  number,
  lines,
  openings,
  labels,
  cellSize,
  canvasWidth,
  canvasHeight,
}: {
  issue: FloorplanIssue;
  number: number;
  lines: FloorplanLine[];
  openings: FloorplanLine[];
  labels: FloorplanLabel[];
  cellSize: number;
  canvasWidth: number;
  canvasHeight: number;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [trVersion, setTrVersion] = useState(0);
  useEffect(() => onTranslations(() => setTrVersion((v) => v + 1)), []);
  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const cells = issue.cells || [];
    // 구역의 범위(캔버스 좌표). 칸이 없으면 마커 한 점.
    let x0 = issue.x, x1 = issue.x, y0 = issue.y, y1 = issue.y;
    for (const [row, col] of cells) {
      x0 = Math.min(x0, col * cellSize);
      x1 = Math.max(x1, (col + 1) * cellSize);
      y0 = Math.min(y0, row * cellSize);
      y1 = Math.max(y1, (row + 1) * cellSize);
    }
    // 주변이 보이게 넉넉히 — 구역 크기만큼, 적어도 칸 10개만큼 둘러싼다.
    const pad = Math.max((x1 - x0) * 0.8, (y1 - y0) * 0.8, cellSize * 10);
    let vx0 = Math.max(0, x0 - pad), vx1 = Math.min(canvasWidth, x1 + pad);
    let vy0 = Math.max(0, y0 - pad), vy1 = Math.min(canvasHeight, y1 + pad);
    // 그림 비율에 맞춰 짧은 쪽을 늘린다.
    const want = THUMB_W / THUMB_H;
    if ((vx1 - vx0) / (vy1 - vy0) < want) {
      const extra = (vy1 - vy0) * want - (vx1 - vx0);
      vx0 -= extra / 2; vx1 += extra / 2;
    } else {
      const extra = (vx1 - vx0) / want - (vy1 - vy0);
      vy0 -= extra / 2; vy1 += extra / 2;
    }
    const s = THUMB_W / (vx1 - vx0);
    const X = (x: number) => (x - vx0) * s;
    const Y = (y: number) => (y - vy0) * s;

    ctx.clearRect(0, 0, THUMB_W, THUMB_H);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, THUMB_W, THUMB_H);
    // 구역 색은 중요도가 아니라 뜻 — 모이는 곳 주황, 구석 파랑 (issueZoneColor 주석).
    const color = issueZoneColor(issue);
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.4;
    for (const [row, col] of cells) ctx.fillRect(X(col * cellSize), Y(row * cellSize), cellSize * s + 0.5, cellSize * s + 0.5);
    ctx.globalAlpha = 1;
    ctx.strokeStyle = "#111827";
    ctx.lineWidth = 1.5;
    for (const l of lines) {
      ctx.beginPath();
      ctx.moveTo(X(l.x1), Y(l.y1));
      ctx.lineTo(X(l.x2), Y(l.y2));
      ctx.stroke();
    }
    ctx.strokeStyle = "#059669";
    ctx.lineWidth = 2;
    for (const l of openings) {
      ctx.beginPath();
      ctx.moveTo(X(l.x1), Y(l.y1));
      ctx.lineTo(X(l.x2), Y(l.y2));
      ctx.stroke();
    }
    // 이름은 구역에 가까운 몇 개만 — 다 쓰면 글자가 겹쳐 오히려 못 읽는다.
    ctx.font = "bold 11px Pretendard, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    // 가까운 순서는 번호 동그라미(issue.x·y)에서 잰다 — 카드 위치 글(issuePlace)과 같은 기준.
    // 예전엔 구역 테두리 가운데에서 재서 글은 「16번·15번 매장 사이」인데 그림에는 「매장 17」
    // 「매장 16」이 찍혔다(샘플 몰 4번 카드, 2026-11-08 UX 자동 개선).
    const nearby = labels
      .filter((l) => X(l.x) > 20 && X(l.x) < THUMB_W - 20 && Y(l.y) > 10 && Y(l.y) < THUMB_H - 10)
      .sort((a, b) => Math.hypot(a.x - issue.x, a.y - issue.y) - Math.hypot(b.x - issue.x, b.y - issue.y))
      .slice(0, 6);
    // 번호 동그라미 자리를 먼저 잡아 둔다 — 작은 구역에선 동그라미가 바로 그 구역 매장
    // 이름(카드 글이 「8번·7번 매장 사이」라 가리키는 이름)을 덮어 못 읽었다(2026-11-06
    // UX 자동 개선, 390). 겹치는 이름은 동그라미 위나 아래로 비켜 쓴다.
    const mx = X(issue.x), my = Y(issue.y);
    const drawn: Array<[number, number, number, number]> = [[mx - 12, my - 12, mx + 12, my + 12]];
    const hits = (b: [number, number, number, number]) =>
      drawn.some((d) => b[0] < d[2] && b[2] > d[0] && b[1] < d[3] && b[3] > d[1]);
    for (const label of nearby) {
      const lx = X(label.x);
      const shown = translateText(label.text);
      const w = ctx.measureText(shown).width + 6;
      const at = (y: number): [number, number, number, number] => [lx - w / 2, y - 8, lx + w / 2, y + 8];
      const away = Y(label.y) < my ? [my - 21, my + 21] : [my + 21, my - 21]; // 원래 있던 쪽으로 먼저
      const ly = [Y(label.y), ...away].find((y) => y > 8 && y < THUMB_H - 8 && !hits(at(y)));
      if (ly === undefined) continue;
      if (drawn.length >= 5) break;
      drawn.push(at(ly));
      ctx.fillStyle = "rgba(255,255,255,0.9)";
      ctx.fillRect(lx - w / 2, ly - 8, w, 16);
      ctx.fillStyle = "#111827";
      ctx.fillText(shown, lx, ly);
    }
    // 구역 테두리와 번호 — 큰 도면의 마커와 같은 번호.
    if (cells.length) {
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.setLineDash([5, 3]);
      ctx.strokeRect(X(x0) - 2, Y(y0) - 2, (x1 - x0) * s + 4, (y1 - y0) * s + 4);
      ctx.setLineDash([]);
    }
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(mx, my, 10, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.fillStyle = "#ffffff";
    ctx.font = "bold 11px Pretendard, sans-serif";
    ctx.fillText(String(number), mx, my);
  }, [issue, number, lines, openings, labels, cellSize, canvasWidth, canvasHeight, trVersion]);

  return (
    <canvas
      ref={ref}
      width={THUMB_W}
      height={THUMB_H}
      className="floorplan-issue-thumb"
      aria-label={`${number}번 문제의 도면 위치`}
    />
  );
}

function RoomTable({
  rooms,
  active,
  onHover,
  onPick,
}: {
  rooms: FloorplanRoom[];
  active: number | null;
  onHover: (index: number | null) => void;
  onPick: (index: number) => void;
}) {
  const [pinned, setPinned] = useState<number | null>(null);
  const analyzed = rooms.filter((r) => r.analyzed_pct > 0);
  const unreached = rooms.length - analyzed.length;
  const through = useMemo(() => roomThroughRanks(rooms), [rooms]);
  return (
    <div className="floorplan-room-table-wrap">
      <p className="floorplan-def">
        벽으로 닫힌 공간 {rooms.length}곳을 찾아 목이 좋은 순으로 세웠습니다.
        {" "}「지나가는 길목」은 이 공간들끼리 견준 통행 순위입니다.
        {/* 마우스를 올려야 되는 동작이라 터치 화면에선 숨긴다 (2026-11-06 UX 자동 개선). */}
        <span className="floorplan-desktop-hint"> 표에 마우스를 올리면 위 도면에서 그 자리가 짙어집니다.</span>
        <span className="floorplan-touch-hint"> 줄을 누르면 위 도면으로 올라가 그 공간을 짙게 보여 줍니다.</span>
        {unreached > 0 && ` ${unreached}곳은 문이 없어 계산에서 빠졌습니다.`}
      </p>
      <table className="floorplan-room-table">
        <thead>
          <tr>
            <th>순위</th>
            <th>공간</th>
            <th>넓이</th>
            <th>목</th>
            <th>지나가는 길목</th>
          </tr>
        </thead>
        <tbody>
          {rooms.map((room) => (
            <tr
              key={room.index}
              className={active === room.index ? "floorplan-room-row--active" : undefined}
              // 터치의 호환 마우스 이벤트가 누른 직후 강조를 지우지 않도록 마우스일 때만 따라간다.
              // 누른 줄은 마우스가 떠나도(도면으로 내려 준 뒤에도) 강조를 남긴다.
              onPointerEnter={(e) => e.pointerType === "mouse" && onHover(room.index)}
              onPointerLeave={(e) => e.pointerType === "mouse" && onHover(pinned)}
              onClick={() => {
                setPinned(room.index);
                onPick(room.index);
              }}
            >
              <td>{room.index}</td>
              <td>{room.name || <span className="floorplan-room-noname">이름 없음</span>}</td>
              <td>{room.area_m2 != null ? `${room.area_m2.toFixed(0)}㎡` : `${room.cell_count}칸`}</td>
              <td>
                {room.analyzed_pct === 0 ? (
                  <span className="floorplan-room-noname">문 없음</span>
                ) : (
                  `상위 ${Math.max(1, Math.round(100 - room.integration_percentile) + 1)}%`
                )}
              </td>
              <td>{through.get(room.index) ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A/B 결과. 한 줄 점수로 합치지 않는다 — 좋아진 것과 나빠진 것을 나란히 둔다
 *  (SPEC_FLOORPLAN_TABS_UI 결정 3). */
function CompareCard({
  comparison,
  onClose,
}: {
  comparison: FloorplanCompareResponse;
  onClose: () => void;
}) {
  const arrow: Record<FloorplanDelta["direction"], string> = {
    better: "좋아짐",
    worse: "나빠짐",
    same: "그대로",
  };
  const cells = comparison.before_summary.cell_count || 0;
  const headline = compareHeadline(comparison);
  return (
    <div className="card floorplan-compare-card">
      <div className="floorplan-result-header">
        <h2>바꾸면 무엇이 달라지나</h2>
        <button type="button" className="secondary-button" onClick={onClose}>
          닫기
        </button>
      </div>
      <p className="floorplan-compare-headline">{headline}</p>
      {/* 휴대폰에선 줄마다 「무엇을 봤나」 밑에 전·후·변화 세 칸을 놓는다 — data-label이 칸 이름 (styles/floorplan.css). */}
      <table className="floorplan-room-table floorplan-compare-table">
        <thead>
          <tr>
            <th>무엇을 봤나</th>
            <th>고치기 전</th>
            <th>고친 뒤</th>
            <th>변화</th>
          </tr>
        </thead>
        <tbody>
          {comparison.deltas.map((delta) => (
            <tr key={delta.key} title={delta.note}>
              <td>
                {delta.label}
                {delta.note && <small className="floorplan-compare-note">{delta.note}</small>}
              </td>
              <td data-label="고치기 전">{formatCompareValue(delta, delta.before, cells)}</td>
              <td data-label="고친 뒤">{formatCompareValue(delta, delta.after, cells)}</td>
              <td data-label="변화" className={`floorplan-delta floorplan-delta--${delta.direction}`}>
                {delta.direction === "same" ? "그대로" : `${delta.change_pct > 0 ? "+" : ""}${delta.change_pct.toFixed(0)}% ${arrow[delta.direction]}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="floorplan-compare-why">{COMPARE_WHY}</p>

      <div className="floorplan-spot-lists">
        <div className="floorplan-spot-list">
          <h3>없어진 구석 {comparison.resolved.length}</h3>
          <ol>
            {comparison.resolved.map((issue, i) => (
              <li key={`r-${i}`}>{issue.title}</li>
            ))}
            {!comparison.resolved.length && <li className="floorplan-room-noname">없음</li>}
          </ol>
        </div>
        <div className="floorplan-spot-list">
          <h3>새로 생긴 구석 {comparison.appeared.length}</h3>
          <ol>
            {comparison.appeared.map((issue, i) => (
              <li key={`a-${i}`}>{issue.title}</li>
            ))}
            {!comparison.appeared.length && <li className="floorplan-room-noname">없음</li>}
          </ol>
        </div>
      </div>

      <p className="floorplan-explanation">{comparison.summary}</p>
      {!!(comparison.warnings && comparison.warnings.length) && (
        <ul className="warnings-banner">
          {comparison.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
