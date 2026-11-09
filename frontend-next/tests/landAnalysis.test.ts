/**
 * 판정 contract — 커밋된 fixture가 프런트 타입과 맞는지 본다.
 *
 * 여기가 지키는 것은 "화면이 fixture만으로 그려질 수 있는가"다. 백엔드 스키마가
 * 바뀌었는데 프런트 타입이 안 바뀌면 타입 검사는 통과하고(JSON은 any로 들어온다)
 * 화면만 조용히 빈다. 그래서 필드 존재를 실제 파일에 대고 확인한다.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  allowsAssertiveWording,
  parcelTurnFor,
  priceRangeText,
  type FixturePayload,
} from "../lib/landAnalysis.ts";

const payload = JSON.parse(
  readFileSync(new URL("../public/fixtures/structured-analysis.json", import.meta.url), "utf8"),
) as FixturePayload;

test("fixture에 네 판정 경우가 다 있다", () => {
  assert.deepEqual(Object.keys(payload.cases).sort(), [
    "above_peers",
    "cheap_with_reason",
    "undervalued",
    "undetermined",
  ]);
});

test("모든 case가 화면이 요구하는 필드를 갖는다", () => {
  for (const [key, analysis] of Object.entries(payload.cases)) {
    assert.equal(analysis.verdict.label, key, `${key}: 라벨이 키와 다르다`);
    assert.ok(analysis.verdict.headline, `${key}: headline이 없다`);
    assert.ok(analysis.peer_group.description, `${key}: 비교군 설명이 없다`);
    assert.ok(analysis.caveats.length > 0, `${key}: 주의 문구가 없다`);
    assert.ok(analysis.audit.model_version, `${key}: 감사 정보가 없다`);
  }
});

test("금액에는 공시지가 주의가 붙어 온다", () => {
  const official = payload.cases.undervalued.official_price;
  assert.ok(official);
  assert.match(official.disclaimer, /시세/);
  assert.equal(official.label, "개별공시지가");
});

test("판정을 보류해도 금액과 비교군 안 자리는 남는다", () => {
  // 한 덩어리로 묶었다가 되돌린 지점 — 모형이 없는 동안 금액까지 사라졌다.
  const held = payload.cases.undetermined;
  assert.equal(held.current_valuation, null);
  assert.ok(held.official_price);
  assert.ok(held.peer_position);
});

test("판정을 보류한 이유가 구분돼 온다", () => {
  assert.equal(payload.cases.undetermined.verdict.undetermined_reason, "few_peers");
  assert.equal(payload.cases.undervalued.verdict.undetermined_reason, null);
});

test("정적 서술이 fixture에 함께 온다 — LLM 없이 화면이 완성된다", () => {
  for (const [key, analysis] of Object.entries(payload.cases)) {
    assert.ok(analysis.narrative, `${key}: 서술이 없다`);
    assert.equal(analysis.narrative.source, "static");
    assert.ok(analysis.narrative.paragraphs.length > 0);
  }
});

test("싸다와 저평가는 서로 다른 필드다", () => {
  const cheap = payload.cases.cheap_with_reason.current_valuation;
  assert.ok(cheap);
  assert.equal(cheap.is_cheap, true);
  assert.equal(cheap.is_undervalued, false);
});

test("할인분은 설명되는 몫과 남는 몫으로 정확히 쪼개진다", () => {
  const d = payload.cases.cheap_with_reason.current_valuation!.discount;
  assert.ok(Math.abs(d.total_pct - (d.explained_pct + d.unexplained_pct)) < 0.01);
});

test("비싼 쪽에 낙인 표현이 없다", () => {
  const above = payload.cases.above_peers;
  assert.equal(above.verdict.label, "above_peers");
  assert.ok(!above.verdict.headline.includes("고평가"));
  assert.ok(above.caveats.some((c) => c.includes("고평가라는 뜻이 아닙니다")));
});

test("판정 못 한 경우는 판정 섹션이 없고 단정하지 않는다", () => {
  const empty = payload.cases.undetermined;
  assert.equal(empty.current_valuation, null);
  assert.equal(allowsAssertiveWording(empty.verdict), false);
});

test("예상 금액은 범위로 표기된다", () => {
  const range = payload.cases.undervalued.current_valuation!.expected_official_price;
  const text = priceRangeText(range);
  assert.match(text, /~/);
  assert.match(text, /원\/㎡/);
});

test("방법론 A·B·C 자리가 셋 다 채워진 예가 있다", () => {
  const rich = payload.cases.undervalued;
  assert.ok(rich.current_valuation);
  assert.ok(rich.growth_outlook);
  assert.deepEqual(rich.price_forecast?.horizons.map((h) => h.years), [1, 3, 5]);
});

test("내부 용어가 계약에 새어 나오지 않는다", () => {
  const blob = JSON.stringify(payload.cases);
  // 단어 경계로 본다 — `shape`(형상)·`integrated` 같은 정상 단어를 잡으면
  // 테스트가 쓸모없는 곳에서 깨진다.
  for (const internal of ["shap", "integration", "choice", "lightgbm", "catboost"]) {
    assert.ok(
      !new RegExp(`\\b${internal}\\b`, "i").test(blob),
      `내부 용어 노출: ${internal}`,
    );
  }
});

// ── 필지 클릭 → 스레드 한 턴 ──────────────────────────────────────────────
//
// 브라우저에서 Kakao 폴리곤 클릭을 합성 이벤트로 흉내낼 수 없어서(SDK가 자기
// 객체에 리스너를 단다) 주입 로직을 순수 함수로 빼 여기서 덮는다.

test("필지를 누르면 '이 땅 뭐야?' 턴이 만들어진다", () => {
  const turn = parcelTurnFor(payload.cases.undervalued, null);
  assert.ok(turn);
  assert.match(turn.query, /이 땅 뭐야\?$/);
  assert.match(turn.query, /여의도동/);
  assert.equal(turn.verdict, payload.cases.undervalued.verdict.headline);
  assert.equal(turn.landAnalysis, payload.cases.undervalued);
});

test("같은 필지를 다시 눌러도 턴이 또 쌓이지 않는다", () => {
  const analysis = payload.cases.undervalued;
  assert.equal(parcelTurnFor(analysis, analysis.subject.pnu), null);
});

test("다른 필지를 누르면 새 턴이다 — 두 땅을 나란히 견줄 수 있다", () => {
  const first = payload.cases.undervalued;
  const second = payload.cases.cheap_with_reason;
  assert.ok(parcelTurnFor(second, first.subject.pnu));
});

test("판정이 없으면 턴을 만들지 않는다 — 빈 카드가 스레드에 들어가면 안 된다", () => {
  assert.equal(parcelTurnFor(null, null), null);
  assert.equal(parcelTurnFor(undefined, "x"), null);
});
