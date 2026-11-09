import { test } from "node:test";
import * as assert from "node:assert/strict";

import { pricePercentileText } from "../components/profitability/profitabilityUtils.ts";

// subject_percentile = 표본 중 검토가보다 **싼** 거래의 비율. 방향을 뒤집어
// 읽으면 싼 매물을 비싸다고 판단하게 된다 — 화면에 "표본 상위 P25"라고만
// 적혀 있던 게 정확히 그 오해를 만들었다.

test("낮은 백분위는 싼 편으로 말한다", () => {
  const text = pricePercentileText(20);
  assert.match(text, /싼 편/);
  assert.doesNotMatch(text, /비싼 편/);
  // 20%가 이보다 싸다 = 80%가 이보다 비싸다
  assert.match(text, /80%가 이보다 비쌉니다/);
});

test("높은 백분위는 비싼 편으로 말한다", () => {
  const text = pricePercentileText(90);
  assert.match(text, /비싼 편/);
  assert.match(text, /90%가 이보다 쌉니다/);
});

test("가운데는 어느 쪽으로도 단정하지 않는다", () => {
  const text = pricePercentileText(50);
  assert.match(text, /중간 가격대/);
  assert.doesNotMatch(text, /싼 편|비싼 편/);
});

test("범위를 벗어난 값도 문장이 깨지지 않는다", () => {
  assert.match(pricePercentileText(-3), /100%가 이보다 비쌉니다/);
  assert.match(pricePercentileText(140), /100%가 이보다 쌉니다/);
});
