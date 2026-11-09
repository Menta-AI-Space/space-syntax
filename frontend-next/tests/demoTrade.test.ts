/**
 * 데모 상권분석.
 *
 * 예시 데이터이지만 **관계는 예시가 아니다.** 도달권이 넓어지는데 배후 인구가
 * 줄거나, 손익분기 객수가 객단가와 안 맞으면 화면이 조용히 거짓말을 한다.
 * 발표 중에 같은 업종을 두 번 눌러 숫자가 달라지는 것도 사고다.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { INDUSTRIES } from "../lib/demoParcels.ts";
import {
  buildTradeAnalysis,
  cannibalLabel,
  eokwon,
  manwon,
  percentileLabel,
  vsSeoulPct,
} from "../lib/demoTrade.ts";

const REGIONS = ["sinchon", "jongno"];

function all() {
  return REGIONS.flatMap((r) => INDUSTRIES.map((i) => buildTradeAnalysis(r, i)));
}

test("고정 표 — 같은 입력이면 같은 결과다", () => {
  assert.deepEqual(buildTradeAnalysis("sinchon", "카페"), buildTradeAnalysis("sinchon", "카페"));
  assert.deepEqual(buildTradeAnalysis("jongno", "학원"), buildTradeAnalysis("jongno", "학원"));
});

test("업종이 다르면 결과도 다르다 — 업종을 받는 이유가 화면에 있어야 한다", () => {
  const cafe = buildTradeAnalysis("sinchon", "카페");
  const chicken = buildTradeAnalysis("sinchon", "치킨·주류");
  assert.notEqual(cafe.totalScore, chicken.totalScore);
  assert.notEqual(cafe.footfall.peakHour, chicken.footfall.peakHour);
  assert.notEqual(cafe.ticketWon, chicken.ticketWon);
});

test("여섯 업종 · 두 지역 모두 결과가 선다", () => {
  const rows = all();
  assert.equal(rows.length, REGIONS.length * INDUSTRIES.length);
  for (const t of rows) {
    assert.ok(t.totalScore > 0 && t.totalScore <= 100, t.industry);
    assert.equal(t.subscores.length, 6, t.industry);
    assert.ok(t.verdict.length > 10, t.industry);
  }
});

test("도달권은 넓어질수록 면적·인구·점포가 함께 는다", () => {
  for (const t of all()) {
    const [a, b, c] = t.reach;
    assert.deepEqual([a.minutes, b.minutes, c.minutes], [5, 10, 15], t.industry);
    assert.ok(a.areaHa < b.areaHa && b.areaHa < c.areaHa, t.industry);
    assert.ok(a.population < b.population && b.population < c.population, t.industry);
    assert.ok(a.stores < b.stores && b.stores < c.stores, t.industry);
    assert.ok(a.sameCategory < b.sameCategory, t.industry);
  }
});

test("요약의 동일 업종 수는 도보 5분 값이다 — 두 숫자가 다르면 어느 쪽이 맞는지 모른다", () => {
  for (const t of all()) {
    assert.equal(t.sameCategoryCount, t.reach[0].sameCategory, t.industry);
  }
});

test("손익분기 — 하루 매출과 하루 객수가 월매출·객단가와 맞는다", () => {
  for (const t of all()) {
    // 하루 매출은 만원 단위로 끊는다 — 소수점은 없는 정밀도를 있는 것처럼 보이게 한다.
    assert.equal(t.breakEvenDailyMan, Math.round(t.breakEvenMonthlyMan / 26), t.industry);
    const expected = Math.round((t.breakEvenMonthlyMan * 10_000) / 26 / t.ticketWon);
    assert.equal(t.ordersPerDay, expected, t.industry);
    // 손익분기가 추정매출을 넘으면 "괜찮은 자리"라고 말할 수 없다.
    assert.ok(t.breakEvenMonthlyMan < t.perStoreMonthlyMan, t.industry);
  }
});

test("적정 임대료는 밴드다 — 하한이 상한보다 클 수 없다", () => {
  for (const t of all()) {
    assert.ok(t.rentLowMan < t.rentHighMan, t.industry);
  }
});

test("시간대 유동은 24칸이고 업종마다 정점이 다르다", () => {
  for (const t of all()) {
    assert.equal(t.footfall.hourly.length, 24, t.industry);
    assert.deepEqual(
      t.footfall.hourly.map((h) => h.hour),
      Array.from({ length: 24 }, (_, i) => i),
      t.industry,
    );
    for (const h of t.footfall.hourly) {
      assert.ok(h.weekday >= 0 && h.weekend >= 0, `${t.industry} ${h.hour}시`);
    }
  }
  // 밤 업종의 정점은 낮 업종보다 늦다.
  assert.ok(
    buildTradeAnalysis("sinchon", "치킨·주류").footfall.peakHour >
      buildTradeAnalysis("sinchon", "카페").footfall.peakHour,
  );
});

test("연령·요일 비중은 각각 100%를 이룬다", () => {
  for (const t of all()) {
    const ages = t.footfall.ages.reduce((s, a) => s + a.pct, 0);
    const days = t.footfall.days.reduce((s, d) => s + d.pct, 0);
    assert.equal(ages, 100, `${t.industry} 연령 ${ages}`);
    assert.equal(days, 100, `${t.industry} 요일 ${days}`);
  }
});

test("추세의 마지막 분기는 현재 점포 수와 같고, 증감 부호와 방향이 맞는다", () => {
  for (const t of all()) {
    assert.equal(t.trend.length, 8, t.industry);
    const last = t.trend[t.trend.length - 1];
    assert.ok(Math.abs(last.sameCategory - t.sameCategoryCount) < 1.1, t.industry);
    const rising = last.sameCategory > t.trend[0].sameCategory;
    assert.equal(rising, t.categoryGrowthPct > 0, `${t.industry}: ${t.categoryGrowthPct}`);
  }
});

test("경쟁 점포에 실재하는 상호를 쓰지 않는다", () => {
  for (const t of all()) {
    assert.ok(t.competitors.length >= 3, t.industry);
    for (const c of t.competitors) {
      // 업종명 + 알파벳 한 글자. 지어낸 판정을 실제 가게에 붙이지 않는다.
      assert.match(c.label, /\s[A-Z]$/, `${t.industry}: ${c.label}`);
      assert.ok(c.distanceM > 0, c.label);
    }
    // 가까운 순서로 나온다 — 뒤섞이면 "가장 가까운 경쟁"을 읽을 수 없다.
    const d = t.competitors.map((c) => c.distanceM);
    assert.deepEqual(d, [...d].sort((a, b) => a - b), t.industry);
  }
});

test("잠식 위험은 구간으로 읽는다 — 경계에서만 갈린다", () => {
  assert.equal(cannibalLabel(55), "높음");
  assert.equal(cannibalLabel(54.9), "주의");
  assert.equal(cannibalLabel(40), "주의");
  assert.equal(cannibalLabel(39.9), "보통");
  assert.equal(cannibalLabel(25), "보통");
  assert.equal(cannibalLabel(24.9), "낮음");
  for (const t of all()) {
    assert.equal(t.cannibalLabel, cannibalLabel(t.cannibalPct), t.industry);
  }
});

test("지역이 크면 상권도 크게 잡힌다 — 배율이 한 방향으로만 걸린다", () => {
  const a = buildTradeAnalysis("sinchon", "카페");
  const b = buildTradeAnalysis("jongno", "카페");
  assert.ok(b.reach[0].population > a.reach[0].population);
  assert.ok(b.perStoreMonthlyMan > a.perStoreMonthlyMan);
  assert.ok(b.breakEvenMonthlyMan > a.breakEvenMonthlyMan);
});

test("표기 — 서울 대비는 중앙값이 100이고 단위가 섞이지 않는다", () => {
  assert.equal(vsSeoulPct(1_640, 1_640), 100);
  assert.equal(vsSeoulPct(2_180, 1_640), 133);
  assert.equal(manwon(2_180), "2,180만원");
  assert.equal(eokwon(38.2), "38.2억원");
  assert.equal(percentileLabel(96), "상위 4%");
  assert.equal(percentileLabel(22), "하위 22%");
  assert.equal(percentileLabel(100), "상위 1%");
});
