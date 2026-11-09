import { test } from "node:test";
import * as assert from "node:assert/strict";

import type { LeaseSiteContext } from "../lib/api.ts";
import { buildLeaseSiteContextTiles, leaseBestModels } from "../lib/leaseSiteContextCard.ts";

const full: LeaseSiteContext = {
  total_score: 72.4,
  grade: "B",
  competition_label: "높음",
  same_category_count: 9,
  cannibalization_risk: 63.2,
  cannibalization_label: "매우 높음",
  business_label: "카페",
  best_restaurant_models: [],
  best_cafe_models: ["회전율형", "배달특화형"],
};

test("종합점수·경쟁·잠식 타일을 값 그대로 만든다 — 지어내지 않는다", () => {
  const tiles = buildLeaseSiteContextTiles(full);
  const score = tiles.find((t) => t.label === "입지 종합점수");
  assert.equal(score?.value, "72점 · B");
  const comp = tiles.find((t) => t.label === "주변 경쟁");
  assert.equal(comp?.value, "높음 · 동일업종 9곳");
  assert.equal(comp?.toneLabel, "높음");
  const cann = tiles.find((t) => t.label === "잠식 위험");
  assert.equal(cann?.value, "매우 높음 (63점)");
  assert.equal(cann?.toneLabel, "매우 높음");
});

test("경쟁·잠식 라벨이 빈 문자열이면(값을 못 구했으면) 해당 타일을 만들지 않는다", () => {
  const partial: LeaseSiteContext = {
    total_score: null,
    grade: "",
    competition_label: "",
    same_category_count: 0,
    cannibalization_risk: null,
    cannibalization_label: "",
    business_label: "",
    best_restaurant_models: [],
    best_cafe_models: [],
  };
  const tiles = buildLeaseSiteContextTiles(partial);
  assert.deepEqual(tiles, []);
});

test("site_context가 없으면(도구 실패 등) 빈 배열 — undefined를 흘려보내지 않는다", () => {
  assert.deepEqual(buildLeaseSiteContextTiles(null), []);
  assert.deepEqual(buildLeaseSiteContextTiles(undefined), []);
});

test("업종별 적합 모델은 실제로 계산된 쪽(카페)만 담긴다", () => {
  assert.deepEqual(leaseBestModels(full), ["회전율형", "배달특화형"]);
});

test("적합 모델이 둘 다 비어 있으면 빈 배열", () => {
  const noModels: LeaseSiteContext = { ...full, best_restaurant_models: [], best_cafe_models: [] };
  assert.deepEqual(leaseBestModels(noModels), []);
});
