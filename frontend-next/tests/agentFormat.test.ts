import { test } from "node:test";
import * as assert from "node:assert/strict";

import {
  bandPosition,
  barWidth,
  clampPct,
  fmtMan,
  fmtManEok,
  fmtMonths,
  fmtPct,
  fmtRatio,
  fmtSignedPct,
  fmtYears,
  hasNumber,
  parseNumber,
  toneFor,
  toneForScore,
  toneForSeverity,
  withObjectParticle,
} from "../lib/agentFormat.ts";

// 백엔드가 아직 없거나 필드를 빼먹어도 화면에 "null"·"NaN"이 새면 안 된다.
test("formats missing numbers as a dash, never null or NaN", () => {
  for (const value of [null, undefined, Number.NaN, Number.POSITIVE_INFINITY]) {
    assert.equal(fmtMan(value), "—");
    assert.equal(fmtPct(value), "—");
    assert.equal(fmtYears(value), "—");
    assert.equal(fmtMonths(value), "—");
    assert.equal(fmtRatio(value), "—");
    assert.equal(fmtSignedPct(value), "—");
    assert.equal(hasNumber(value), false);
  }
  assert.equal(fmtMan(12345.6), "12,346만원");
  assert.equal(fmtPct(31.25), "31.3%");
  assert.equal(fmtRatio(1.238), "1.24배");
  assert.equal(fmtYears(12.34), "12.3년");
  assert.equal(fmtMonths(8.25), "8.3개월");
});

test("signs percentages so the direction of a gap is readable", () => {
  assert.equal(fmtSignedPct(-67), "-67%");
  assert.equal(fmtSignedPct(12), "+12%");
  assert.equal(fmtSignedPct(0), "0%");
});

test("maps verdicts and severities to tones, defaulting to neutral", () => {
  assert.equal(toneFor("recommend"), "positive");
  assert.equal(toneFor("avoid"), "negative");
  assert.equal(toneFor("hold"), "caution");
  assert.equal(toneFor(undefined), "info");
  assert.equal(toneFor("무언가 새로운 값"), "info");
  assert.equal(toneForSeverity("high"), "negative");
  assert.equal(toneForSeverity("medium"), "caution");
  assert.equal(toneForSeverity(null), "info");
});

// 후보 목록에는 verdict 없이 점수만 오는 경우가 있다.
test("falls back to score-based tone when no verdict is given", () => {
  assert.equal(toneForScore(90), "positive");
  assert.equal(toneForScore(60), "info");
  assert.equal(toneForScore(45), "caution");
  assert.equal(toneForScore(10), "negative");
  assert.equal(toneForScore(null), "info");
});

test("parses form text into numbers, rejecting junk", () => {
  assert.equal(parseNumber(" 300 "), 300);
  assert.equal(parseNumber(""), null);
  assert.equal(parseNumber("삼백"), null);
  assert.equal(parseNumber("1e3"), 1000);
  assert.equal(parseNumber("1,000"), 1000);
  assert.equal(parseNumber("300만원"), 300);
  assert.equal(parseNumber(" 30 평"), 30);
  assert.equal(parseNumber("만원"), null);
  assert.equal(parseNumber("3000000원"), null);
});

test("keeps bar widths inside 0~100", () => {
  assert.equal(clampPct(140), 100);
  assert.equal(clampPct(-5), 0);
  assert.equal(clampPct(null), 0);
  assert.equal(barWidth(50, 200), 25);
  assert.equal(barWidth(500, 200), 100);
  assert.equal(barWidth(50, 0), 0);
  assert.equal(barWidth(null, 200), 0);
});

test("places a value inside its band, and refuses to draw a band it does not have", () => {
  assert.equal(bandPosition(300, null, 400), null);
  assert.equal(bandPosition(300, 400, 200), null);

  const inside = bandPosition(300, 200, 400);
  assert.ok(inside);
  assert.ok(inside.lowPct < inside.valuePct && inside.valuePct < inside.highPct);

  const above = bandPosition(600, 200, 400);
  assert.ok(above && above.valuePct > above.highPct);

  // 값이 없어도 밴드 자체는 그릴 수 있어야 한다 (표식만 생략).
  const noValue = bandPosition(null, 200, 400);
  assert.ok(noValue && noValue.valuePct === -1 && noValue.highPct > noValue.lowPct);

  // 밴드 폭이 0이어도(상·하한이 같아도) 0으로 나누지 않는다.
  const point = bandPosition(300, 300, 300);
  assert.ok(point && Number.isFinite(point.valuePct));
});

test("withObjectParticle picks 을/를 by the final consonant", () => {
  // 받침 없음 → 를
  assert.equal(withObjectParticle("월세"), "월세를");
  assert.equal(withObjectParticle("관리비"), "관리비를");
  assert.equal(withObjectParticle("매입가"), "매입가를");
  // 받침 있음 → 을
  assert.equal(withObjectParticle("보증금"), "보증금을");
  assert.equal(withObjectParticle("권리금"), "권리금을");
  assert.equal(withObjectParticle("층"), "층을");
  // 한글이 없으면 조사를 붙이지 않는다
  assert.equal(withObjectParticle("NOI"), "NOI");
  assert.equal(withObjectParticle(""), "");
});

test("followUpExample does not suggest the rent the user already asked about", async () => {
  const { followUpExample } = await import("../lib/agentFormat.ts");
  assert.equal(followUpExample({ monthly_rent_man: 300 }), "예: 월세 350이면 어때?");
  assert.equal(followUpExample({ monthly_rent_man: 1200 }), "예: 월세 1300이면 어때?");
  assert.equal(followUpExample({}), "예: 월세 300이면 계약할까?");
  assert.equal(followUpExample(undefined), "예: 월세 300이면 계약할까?");
});

test("fmtManEok reads large 만원 amounts in 억", () => {
  assert.equal(fmtManEok(120000), "12억원");
  assert.equal(fmtManEok(125000), "12억 5,000만원");
  assert.equal(fmtManEok(9999), "9,999만원");
  assert.equal(fmtManEok(330), "330만원");
  assert.equal(fmtManEok(null), "—");
});
