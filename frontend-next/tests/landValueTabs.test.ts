/**
 * 6탭이 **없는 데이터를 지어내지 않는지** 커밋된 fixture 네 경우에 대고 확인한다.
 *
 * 특히 `undetermined` 경우가 중요하다 — 지금 실서비스가 내보내는 모양이 그것이다
 * (`land_analysis_source.py`: 추정 모형이 없어 판정을 보류한다). 이 경우에 화면이
 * Gap을 "0.0%"로 그리기 시작하면 사용자는 "차이가 없다"로 읽는다.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import type { FixturePayload, StructuredAnalysis } from "../lib/landAnalysis.ts";
import {
  LAND_TABS,
  defaultTab,
  discountLabels,
  missingReason,
  percentileText,
  rangeText,
  signedPct,
  summaryRows,
  tabHasData,
  verdictTone,
  wonPerSqm,
} from "../lib/landValueTabs.ts";

const payload = JSON.parse(
  readFileSync(new URL("../public/fixtures/structured-analysis.json", import.meta.url), "utf8"),
) as FixturePayload;

const cases = payload.cases;

test("탭 여섯 개가 스펙 순서 그대로다 — 미래전망이 가치평가 앞", () => {
  assert.deepEqual(
    LAND_TABS.map((t) => t.id),
    ["overview", "future", "value", "hotspot", "place", "compare"],
  );
});

test("탭마다 답하는 질문이 하나씩 있다", () => {
  for (const tab of LAND_TABS) assert.ok(tab.question.endsWith("?"), tab.id);
});

test("fixture 네 경우가 모두 그려진다 — 어느 경우도 여섯 탭이 전부 비지 않는다", () => {
  for (const [name, analysis] of Object.entries(cases)) {
    assert.ok(
      LAND_TABS.some((t) => tabHasData(analysis, t.id)),
      `${name}: 그릴 수 있는 탭이 하나도 없다`,
    );
  }
});

test("추정 모형이 없으면 미래전망·유망입지가 빈 탭이다 — 0으로 채우지 않는다", () => {
  const analysis = cases.undetermined;
  assert.equal(analysis.price_forecast, null);
  assert.equal(analysis.growth_outlook, null);
  assert.equal(tabHasData(analysis, "future"), false);
  assert.equal(tabHasData(analysis, "hotspot"), false);
});

test("판정을 보류해도 주변비교·입지 탭은 남는다 — 사실은 사라지지 않는다", () => {
  const analysis = cases.undetermined;
  assert.equal(tabHasData(analysis, "compare"), true);
  assert.equal(tabHasData(analysis, "place"), true);
});

test("비어 있는 이유가 탭마다 다르게 설명된다", () => {
  const reasons = LAND_TABS.map((t) => missingReason(t.id));
  assert.equal(new Set(reasons).size, reasons.length);
  for (const reason of reasons) assert.ok(reason.length > 0);
});

test("빈 미래전망 탭으로 열지 않는다 — 값이 있는 첫 탭을 연다", () => {
  assert.equal(defaultTab(cases.undervalued), "future");
  const opened = defaultTab(cases.undetermined);
  assert.notEqual(opened, "future");
  assert.equal(tabHasData(cases.undetermined, opened), true);
});

test("Summary는 값이 없는 칸을 만들지 않는다", () => {
  const undetermined = summaryRows(cases.undetermined);
  const keys = undetermined.map((r) => r.key).join(" ");
  assert.ok(!keys.includes("미루어 본 값"), "예상값이 없는데 칸이 생겼다");
  assert.ok(!keys.includes("전망"), "전망이 없는데 칸이 생겼다");
  for (const row of undetermined) assert.ok(row.value.length > 0);
});

test("Summary가 공시지가를 시세로 읽히게 두지 않는다", () => {
  const rows = summaryRows(cases.undervalued);
  const official = rows.find((r) => r.key.includes("개별공시지가"));
  assert.ok(official);
  assert.equal(official.note, "시세가 아닙니다");
});

test("예상 공시지가는 범위로만 나온다 — 단일 확정값을 찍지 않는다", () => {
  const rows = summaryRows(cases.undervalued);
  const expected = rows.find((r) => r.key.includes("미루어 본 값"));
  assert.ok(expected);
  assert.ok(expected.value.includes("~"), expected.value);
  assert.equal(expected.note, "확정값이 아닌 범위");

  const point = cases.undervalued.current_valuation?.expected_official_price.point;
  assert.ok(point != null);
  assert.ok(!expected.value.includes(wonPerSqm(point)), "대표값이 단독으로 찍혔다");
});

test("높게 매겨진 쪽에 낙인 표현을 쓰지 않는다", () => {
  const analysis = cases.above_peers as StructuredAnalysis;
  assert.equal(verdictTone(analysis.verdict), "high");
  const text = [analysis.verdict.headline, ...summaryRows(analysis).map((r) => r.key)].join(" ");
  assert.ok(!text.includes("고평가"), text);
});

test("표기 함수", () => {
  assert.equal(wonPerSqm(8_300_000), "830만원/㎡");
  assert.equal(signedPct(-26.67), "−26.7%");
  assert.equal(signedPct(4.4), "+4.4%");
  assert.equal(percentileText(81.2), "상위 19%");
  assert.equal(percentileText(18.4), "하위 18%");
  assert.equal(rangeText(10_400_000, 12_600_000), "1,040만원/㎡ ~ 1,260만원/㎡");
});

test("미루어 본 값보다 비싼 필지는 '할인·단점' 어휘를 쓰지 않는다", () => {
  const above = cases.above_peers.current_valuation;
  assert.ok(above);
  assert.ok(above.discount.total_pct < 0, "이 fixture는 예상값보다 비싼 경우여야 한다");

  const labels = discountLabels(above.discount.total_pct);
  assert.equal(labels.isBelow, false);
  const text = [labels.heading, labels.total, labels.explained, labels.unexplained].join(" ");
  assert.ok(!text.includes("단점"), text);
  assert.ok(!text.includes("할인"), text);
  assert.ok(!text.includes("고평가"), text);
  assert.ok(text.includes("높은"), text);
});

test("싼 필지 쪽은 '단점으로 설명되지 않는 몫'을 그대로 쓴다", () => {
  const below = cases.undervalued.current_valuation;
  assert.ok(below);
  const labels = discountLabels(below.discount.total_pct);
  assert.equal(labels.isBelow, true);
  assert.equal(labels.unexplained, "단점으로 설명되지 않는 몫");
});

test("Summary의 차이 값에 부호를 찍지 않는다 — 방향은 말이 이미 말한다", () => {
  for (const key of ["undervalued", "above_peers"]) {
    const row = summaryRows(cases[key]).find((r) => r.key.includes("설명되지 않는 몫"));
    assert.ok(row, key);
    assert.ok(!row.value.startsWith("+") && !row.value.startsWith("−"), `${key}: ${row.value}`);
  }
});
