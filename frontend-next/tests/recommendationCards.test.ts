import { test } from "node:test";
import * as assert from "node:assert/strict";

import type { SiteScoreResponse, TrendsResponse } from "../lib/api.ts";
import { buildRecommendationCards } from "../components/dashboard/dashboardUtils.ts";

// 예전 카드는 거리 이름 옆에 "점수 72 · 임대 240–290"을 적었다. 그 점수는 현재
// 지점 점수에서 목록 순번만큼 뺀 값이고, 임대료는 순번으로 고른 하드코딩
// 문자열이었다 — 그 거리에서 잰 값이 아닌데 분석 결과와 구분되지 않았다.

const site = {
  total_score: 78.4,
  grade: "B+",
  verdict_one_liner: "유동인구는 많지만 경쟁이 빡빡한 자리",
  subscores: [{ key: "demand", label: "수요", score: 82, detail: "" }],
} as unknown as SiteScoreResponse;

const trends = {
  category_label: "카페",
  street_signals: [
    { name: "연남동 골목", label: "신호 강함", category_growth_pct: 12.5, detail: "", data_scope: "" },
    { name: "합정 대로변", label: "관찰", store_growth_pct: -3, detail: "", data_scope: "" },
    { name: "망원 시장길", label: "관찰", detail: "표본이 적어 참고용", data_scope: "" },
  ],
  data_scope: "",
} as unknown as TrendsResponse;

test("현재 지점 카드는 그 지점의 실제 점수·등급을 적는다", () => {
  const [first] = buildRecommendationCards(trends, site);
  assert.equal(first.name, "유동인구는 많지만 경쟁이 빡빡한 자리");
  assert.equal(first.primary, "입지 점수 78점");
  assert.equal(first.secondary, "등급 B+");
});

test("거리 카드는 그 거리에서 나온 증감률만 적는다", () => {
  const cards = buildRecommendationCards(trends, site);
  const yeonnam = cards.find((c) => c.name === "연남동 골목");
  assert.equal(yeonnam?.primary, "신호 강함");
  assert.equal(yeonnam?.secondary, "카페 +12.5%");
});

test("업종 증감이 없으면 전체 점포 증감으로, 그것도 없으면 설명으로 물러난다", () => {
  // 지점 카드를 빼고 거리 카드만 세 장 나오게 한다.
  const cards = buildRecommendationCards(trends, null);
  assert.equal(cards.find((c) => c.name === "합정 대로변")?.secondary, "전체 점포 -3%");
  assert.equal(cards.find((c) => c.name === "망원 시장길")?.secondary, "표본이 적어 참고용");
});

test("어느 카드에도 지어낸 임대료 밴드가 없다", () => {
  const text = buildRecommendationCards(trends, site)
    .flatMap((c) => [c.name, c.primary, c.secondary])
    .join(" ");
  for (const band of ["240–290", "210–260", "180–230", "200–250"]) {
    assert.ok(!text.includes(band), `하드코딩 임대료 밴드가 남아 있다: ${band}`);
  }
  assert.ok(!text.includes("임대"), "임대료를 이 카드에서 추정하지 않는다");
});

test("순번으로 점수를 깎지 않는다", () => {
  // 예전에는 두 번째 카드가 첫 카드보다 5점 낮게 찍혔다 — 측정이 아니라 순번이었다.
  const cards = buildRecommendationCards(trends, site);
  const streetCards = cards.slice(1);
  assert.ok(streetCards.every((c) => !/\d+점/.test(c.primary)), "거리 카드에 점수가 붙었다");
});
