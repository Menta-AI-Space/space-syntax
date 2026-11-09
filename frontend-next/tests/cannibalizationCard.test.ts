import { test } from "node:test";
import * as assert from "node:assert/strict";

import type { CannibalizationResponse } from "../lib/api.ts";
import { buildCannibalizationView } from "../lib/cannibalizationCard.ts";

// self_total_count가 0/undefined인 응답 — 브랜드를 안 줬거나 브랜드는 줬지만
// 근처에 자사 매장이 없는 경우다. 둘을 구분할 수 없으므로 "자기잠식 없음"이라
// 지어내지 않고 자기잠식 관련 값을 아예 만들지 않아야 한다.
const noBrand: CannibalizationResponse = {
  risk_score: 42,
  risk_label: "중간",
  competitors: [
    { name: "이디야 신촌점", category: "커피전문점", distance_m: 120, address: "" },
    { name: "빽다방 신촌점", category: "커피전문점", distance_m: 340, address: "" },
  ],
  overlap_pct: 18,
  overlap_with: "이디야 신촌점",
  overlap_recommended_max_pct: 25,
  detail: "도보 5분 도달권 동일업종 2곳",
  mode: "walk",
  minutes: 5,
  total_count: 2,
};

test("브랜드를 몰라 자사 매장을 안 찾은 응답은 자기잠식 타일·목록을 만들지 않는다", () => {
  const view = buildCannibalizationView(noBrand, "도보");
  assert.equal(view.hasSelfData, false);
  assert.equal(view.selfCompetitors.length, 0);
  assert.equal(view.demandFactor, null);
  assert.ok(!view.tiles.some((t) => t.label.includes("내 브랜드")));
  assert.ok(!view.tiles.some((t) => t.label.includes("자기잠식")));
});

test("자사 매장을 찾았지만 겹침이 권장 기준 이하면 자기잠식 타일이 positive다", () => {
  const data: CannibalizationResponse = {
    ...noBrand,
    self_total_count: 1,
    self_competitors: [{ name: "스타벅스 신촌역점", category: "커피전문점", distance_m: 210, address: "" }],
    self_overlap_pct: 12,
    demand_factor: 1.1,
  };
  const view = buildCannibalizationView(data, "도보");
  assert.equal(view.hasSelfData, true);
  assert.equal(view.selfTotal, 1);
  assert.deepEqual(view.selfCompetitors, ["스타벅스 신촌역점 · 210m"]);
  assert.equal(view.selfOverRecommended, false);
  const selfOverlapTile = view.tiles.find((t) => t.label === "자기잠식 겹침");
  assert.equal(selfOverlapTile?.value, "12%");
  assert.equal(selfOverlapTile?.tone, "positive");
  assert.equal(view.demandFactor, 1.1);
});

test("자사 매장 겹침이 권장 기준을 넘으면 negative 톤이 된다", () => {
  const data: CannibalizationResponse = {
    ...noBrand,
    self_total_count: 1,
    self_competitors: [{ name: "스타벅스 신촌역점", category: "커피전문점", distance_m: 80, address: "" }],
    self_overlap_pct: 63,
    overlap_recommended_max_pct: 25,
  };
  const view = buildCannibalizationView(data, "도보");
  assert.equal(view.selfOverRecommended, true);
  const selfOverlapTile = view.tiles.find((t) => t.label === "자기잠식 겹침");
  assert.equal(selfOverlapTile?.tone, "negative");
});

test("같은 브랜드 매장 수는 실제로 찾은 개수를 그대로 적는다 — 지어내지 않는다", () => {
  const data: CannibalizationResponse = {
    ...noBrand,
    self_total_count: 3,
    self_competitors: [
      { name: "스타벅스 A점", category: "커피전문점", distance_m: 50, address: "" },
      { name: "스타벅스 B점", category: "커피전문점", distance_m: 90, address: "" },
      { name: "스타벅스 C점", category: "커피전문점", distance_m: 400, address: "" },
    ],
  };
  const view = buildCannibalizationView(data, "도보");
  const brandTile = view.tiles.find((t) => t.label === "내 브랜드 매장");
  assert.equal(brandTile?.value, "3곳");
  assert.equal(view.selfCompetitors.length, 3);
});
