import { test } from "node:test";
import * as assert from "node:assert/strict";

import { buildFloorplanReport, compareHeadline, formatCompareValue, issuePlace, issueZoneColor, nonDxfMessage, roomName, roomThroughRanks, type ReportInput } from "../lib/floorplanReport.ts";
import type {
  FloorplanAnalyzeResponse,
  FloorplanCompareResponse,
  FloorplanIssue,
  FloorplanRoom,
} from "../lib/api.ts";

function summary(overrides: Record<string, unknown> = {}) {
  return {
    cols: 66, rows: 33, cell_size: 24.1, cell_count: 1512,
    integration_mean: 2.082, integration_max: 2.702, connectivity_mean: 261.2,
    coverage_pct: 96.7, integration_local_mean: 1.5, mean_depth_mean: 2.15,
    choice_max: 6736, choice_sampled: false, cell_size_m: 3.6,
    best_spots: [], through_spots: [], dead_spots: [],
    ...overrides,
  } as FloorplanAnalyzeResponse["summary"];
}

const issue: FloorplanIssue = {
  severity: "medium",
  title: "남동쪽 '앵커매장 B' 부근 약 537㎡가 외진 구역입니다",
  detail: "닿기 어려운 자리입니다.",
  x: 1483, y: 760, x_m: 228, y_m: 114,
  cell_count: 41, area_m2: 537, evidence: "목 하위 10%",
};

const room: FloorplanRoom = {
  index: 1, name: "중앙통로", cell_count: 256, area_m2: 3351,
  x: 800, y: 400, x_m: 120, y_m: 60,
  integration_mean: 2.66, choice_mean: 2275, connectivity_mean: 400,
  integration_percentile: 100, analyzed_pct: 100,
};

function input(overrides: Partial<ReportInput> = {}): ReportInput {
  const result = {
    summary: summary(),
    cells: [],
    issues: [issue],
    rooms: [room],
    excluded_cells: [],
    explanation: { text: "규칙 문장입니다.", source: "rule" },
    warnings: [],
  } as unknown as FloorplanAnalyzeResponse;
  return {
    fileName: "mall.dxf",
    result,
    planImage: null,
    narrative: null,
    comparison: null,
    scaleNote: "가로 240m (도면 단위 m)",
    ...overrides,
  };
}

test("보고서에 문제·공간·축척이 모두 들어간다", () => {
  const html = buildFloorplanReport(input());
  assert.ok(html.includes("앵커매장 B"));
  assert.ok(html.includes("중앙통로"));
  assert.ok(html.includes("3351㎡"));
  assert.ok(html.includes("가로 240m"));
  assert.ok(html.includes("개선 권장"));
});

test("검증되지 않은 결과라는 한계를 항상 붙인다", () => {
  // 이 문장이 빠지면 보고서가 매출 근거처럼 읽힌다 — 상권 축과 달리 실내 축은
  // 매출로 검증된 적이 없다.
  const html = buildFloorplanReport(input());
  assert.ok(html.includes("벽 구조"));
  assert.ok(html.includes("실제 매출로 검증된 수치가 아닙니다"));
});

test("실명과 파일명에 든 HTML은 이스케이프된다", () => {
  const nasty = { ...room, name: '<script>alert("x")</script>' };
  const result = { ...input().result, rooms: [nasty] } as FloorplanAnalyzeResponse;
  const html = buildFloorplanReport(input({ result, fileName: "<img src=x onerror=1>.dxf" }));
  assert.ok(!html.includes("<script>alert"));
  assert.ok(!html.includes("<img src=x"));
  assert.ok(html.includes("&lt;script&gt;"));
});

test("AI 해석이 있으면 그것을 싣고 태그를 붙인다", () => {
  const html = buildFloorplanReport(
    input({ narrative: { text: "**한눈에** 중앙 통로가 목입니다.", source: "llm" } }),
  );
  assert.ok(html.includes("AI 해석"));
  // 옛 소제목은 새 이름으로 — 「한눈에」는 시야(한눈에 보이는 범위)로 읽혔다.
  assert.ok(html.includes("<strong>한 줄 요약</strong>"));
  assert.ok(!html.includes("<strong>한눈에</strong>"));
  assert.ok(!html.includes("규칙 문장입니다."));
});

test("A/B 비교가 있으면 한 절로 함께 나간다", () => {
  const comparison = {
    deltas: [
      { key: "coverage", label: "도면 반영률", before: 96.7, after: 100, change_pct: 3.4, direction: "better", unit: "%", note: "" },
      { key: "dead_area", label: "문제로 지목된 넓이", before: 1008, after: 1152, change_pct: 14.3, direction: "worse", unit: "㎡", note: "" },
    ],
    appeared: [],
    resolved: [issue],
    summary: "좋아진 것과 나빠진 것이 함께 있습니다.",
    before_summary: summary(),
    after_summary: summary({ coverage_pct: 100 }),
    warnings: [],
  } as unknown as FloorplanCompareResponse;
  const html = buildFloorplanReport(input({ comparison }));
  assert.ok(html.includes("바꾸면 무엇이 달라지나"));
  assert.ok(html.includes("좋아짐"));
  assert.ok(html.includes("나빠짐"));
  assert.ok(html.includes("없어진 구석 1"));
});

test("도면 그림이 없어도 보고서가 만들어진다", () => {
  const html = buildFloorplanReport(input({ planImage: null }));
  assert.ok(!html.includes("<img class=\"plan\""));
  assert.ok(html.includes("발견된 문제"));
});

test("비교표는 단위 없는 지표값을 고치기 전=100 지수나 일상 단위로 보인다", () => {
  assert.equal(formatCompareValue({ key: "integration_mean", unit: "" }, 1.74, 1656), "—");
  assert.equal(formatCompareValue({ key: "choice_max", unit: "" }, 105762, 1656), "—");
  // 고치기 전 값을 알면 그 값을 100으로 둔 지수로 보인다.
  assert.equal(formatCompareValue({ key: "integration_mean", unit: "", before: 1.74 }, 1.74, 1656), "100 (기준)");
  assert.equal(formatCompareValue({ key: "integration_mean", unit: "", before: 1.74 }, 1.83, 1656), "105");
  assert.equal(formatCompareValue({ key: "choice_max", unit: "", before: 105762 }, 98000, 1656), "93");
  assert.equal(formatCompareValue({ key: "choice_max", unit: "", before: 0 }, 5, 1656), "—");
  assert.equal(formatCompareValue({ key: "connectivity_mean", unit: "" }, 189.8, 1656), "실내의 11%");
  assert.equal(formatCompareValue({ key: "mean_depth", unit: "번" }, 2.599, 1656), "2.6번");
  assert.equal(formatCompareValue({ key: "dead_area", unit: "㎡" }, 1336.4, 1656), "1,336㎡");
});

test("비교 첫 줄은 손님 발길이 안 닿는 넓이의 변화를 말한다", () => {
  const make = (before: number, after: number, change_pct: number, direction: string) =>
    ({ deltas: [{ key: "dead_area", before, after, change_pct, direction }] }) as unknown as FloorplanCompareResponse;
  assert.equal(compareHeadline(make(1336, 871, -34.9, "better")), "손님 발길이 잘 안 닿는 구석이 35% 줄었습니다");
  assert.equal(compareHeadline(make(1008, 1152, 14.3, "worse")), "손님 발길이 잘 안 닿는 구석이 14% 늘었습니다");
  assert.equal(compareHeadline(make(1000, 1001, 0.1, "same")), "손님 발길이 잘 안 닿는 구석은 거의 그대로입니다");
});

test("문제 구역 색은 뜻을 따른다 — 가장 좋은 곳은 주황, 구석은 파랑", () => {
  assert.equal(issueZoneColor({ title: "중앙 광장·메인 몰 일대가 가장 목이 좋은 곳입니다 — 손님이 가장 쉽게 닿는 바닥 약 886㎡" }), "#ea580c");
  assert.equal(issueZoneColor({ title: "남동쪽 외진 구역 — 찾아가기 먼 바닥 약 1,031㎡" }), "#2563eb");
  assert.equal(issueZoneColor({ title: "남서쪽 68번 매장 근처 시야가 막힌 구역 — 벽에 가려 잘 안 보이는 바닥 약 191㎡" }), "#2563eb");
});

test("위치 글은 도면 쪽 + 가까운 실명 둘", () => {
  const labels = [
    { text: "매장 16", x: 520, y: 500 },
    { text: "매장 23", x: 480, y: 500 },
    { text: "매장 99", x: 10, y: 10 },
  ];
  assert.equal(issuePlace({ x: 500, y: 500, x_m: 50, y_m: 50 }, labels, 1000, 1000), "도면 가운데쯤, 16번·23번 매장 사이");
  assert.equal(issuePlace({ x: 900, y: 900, x_m: 90, y_m: 90 }, [], 1000, 1000), "도면 남동쪽 (도면 왼쪽 위에서 오른쪽으로 90m, 아래로 90m)");
  // 12% 안엔 없고 24% 안에 실명 → 「근처」, 그마저 없으면 방향만(좌표 괄호 없음)
  assert.equal(issuePlace({ x: 510, y: 650, x_m: 51, y_m: 65 }, labels, 1000, 1000), "도면 가운데쯤, 16번 매장 근처");
  assert.equal(issuePlace({ x: 900, y: 900, x_m: 90, y_m: 90 }, labels, 1000, 1000), "도면 남동쪽");
  assert.equal(roomName("매장 07"), "7번 매장");
});

test("nonDxfMessage tells DWG and photo/PDF owners what to do next", () => {
  assert.match(nonDxfMessage("1층평면.DWG"), /다른 이름으로 저장/);
  assert.match(nonDxfMessage("도면사진.jpg"), /사진·스캔·PDF/);
  assert.match(nonDxfMessage("plan.pdf"), /샘플 도면/);
  assert.match(nonDxfMessage("plan.zip"), /DXF 형식으로 저장/);
  assert.match(nonDxfMessage("noext"), /DXF 도면만/);
});

test("roomThroughRanks: 지나가는 길목 원값 대신 공간끼리 상위 %", () => {
  const r = (index: number, choice_mean: number, analyzed_pct = 100) =>
    ({ index, cell_count: 10, x: 0, y: 0, integration_mean: 1, choice_mean, connectivity_mean: 1, integration_percentile: 50, analyzed_pct }) as FloorplanRoom;
  const m = roomThroughRanks([r(1, 807), r(2, 2154), r(3, 0, 0), r(4, 807), r(5, 10)]);
  assert.equal(m.get(2), "상위 25%");
  assert.equal(m.get(1), "상위 50%");
  assert.equal(m.get(4), "상위 50%");
  assert.equal(m.get(5), "상위 100%");
  assert.equal(m.has(3), false);
});


test("compareHeadline leads with the robot share when the plan measured it", () => {
  const base = { label: "", before: 0, after: 0, change_pct: 0, unit: "", note: "" };
  const comparison = {
    deltas: [
      { ...base, key: "dead_area", before: 100, after: 90, change_pct: -10, direction: "better", unit: "㎡" },
      { ...base, key: "robot_passable", before: 86, after: 97, change_pct: 12.8, direction: "better", unit: "%" },
    ],
    appeared: [], resolved: [], summary: "", warnings: [],
  } as unknown as FloorplanCompareResponse;
  assert.equal(compareHeadline(comparison), "로봇이 지나갈 수 있는 바닥이 86% → 97%로 늘었습니다");
  // 로봇 줄이 「그대로」면 종전처럼 손님 구석 머리글
  comparison.deltas[1].direction = "same";
  assert.match(compareHeadline(comparison), /손님 발길이 잘 안 닿는 구석이/);
});
