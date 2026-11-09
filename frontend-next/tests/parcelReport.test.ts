import { test } from "node:test";
import * as assert from "node:assert/strict";

import { buildParcelReport, type ParcelReportInput } from "../lib/parcelReport.ts";
import type { ParcelStructure, ParcelStructureSummary } from "../lib/api.ts";

const summary: ParcelStructureSummary = {
  parcel_count: 3,
  graded_count: 2,
  landlocked_count: 1,
  structure_spread: 1.8,
  corner_count: 1,
};

function parcel(overrides: Partial<ParcelStructure>): ParcelStructure {
  return {
    pnu: "1168010800100090006",
    area_sqm: 240,
    frontage_count: 2,
    geometry: {
      type: "Polygon",
      coordinates: [[[127.0, 37.5], [127.001, 37.5], [127.001, 37.501], [127.0, 37.501], [127.0, 37.5]]],
    },
    grade: 1,
    percentile: 100,
    ...overrides,
  };
}

function input(overrides: Partial<ParcelReportInput> = {}): ParcelReportInput {
  return {
    title: "필지별 길 구조 등급",
    scopeNote: "고른 3필지",
    summary,
    parcels: [
      parcel({}),
      parcel({ pnu: "1168010800100090007", grade: 4, percentile: 20, allocation_manwon: 1500 }),
      parcel({ pnu: "1168010800100090008", grade: null, percentile: null }),
    ],
    caveats: ["감정평가가 아닙니다.", "등급은 고른 필지끼리의 순위입니다."],
    ...overrides,
  };
}

test("필지 경계를 SVG로 그린다 — 지도 타일 없이도 그림이 남는다", () => {
  const html = buildParcelReport(input());
  assert.ok(html.includes("<svg"));
  assert.ok(html.includes("<path d=\"M"));
});

test("등급 표와 대상 설명이 들어간다", () => {
  const html = buildParcelReport(input());
  assert.ok(html.includes("고른 3필지"));
  assert.ok(html.includes("1168010800100090006"));
  assert.ok(html.includes("1등급"));
});

test("한계 문구를 항상 싣는다", () => {
  // 이 문장이 빠지면 보고서가 감정평가서처럼 읽힌다.
  const html = buildParcelReport(input());
  assert.ok(html.includes("감정평가가 아닙니다."));
  assert.ok(html.includes("이 보고서를 읽는 법"));
});

test("등급 없는 필지는 '모른다'로 따로 적는다", () => {
  const html = buildParcelReport(input());
  assert.ok(html.includes("등급을 매기지 못한 필지 1곳"));
  assert.ok(html.includes("나쁜 자리라는 뜻이 아니라"));
});

test("총액을 넣었을 때만 배분 열이 나온다", () => {
  const withMoney = buildParcelReport(input({ totalManwon: 50000 }));
  assert.ok(withMoney.includes("<th>배분</th>"));
  const parcels = input().parcels.map((p) => ({ ...p, allocation_manwon: null }));
  assert.ok(!buildParcelReport(input({ parcels })).includes("<th>배분</th>"));
});

test("지번코드에 든 HTML은 이스케이프된다", () => {
  const parcels = [parcel({ pnu: '<script>alert(1)</script>' })];
  const html = buildParcelReport(input({ parcels }));
  assert.ok(!html.includes("<script>alert"));
  assert.ok(html.includes("&lt;script&gt;"));
});

test("경계가 없어도 보고서가 만들어진다", () => {
  const parcels = [parcel({ geometry: null })];
  const html = buildParcelReport(input({ parcels }));
  assert.ok(html.includes("그림을 그리지 못했습니다"));
  assert.ok(html.includes("등급 순"));
});
