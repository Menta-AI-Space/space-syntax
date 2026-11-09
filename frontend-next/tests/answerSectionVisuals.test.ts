/** 산문 섹션 미니 차트 매퍼 — LLM 문장이 아니라 엔진 값만 쓴다. */

import assert from "node:assert/strict";
import { test } from "node:test";

import { sectionVisual } from "../lib/answerSectionVisuals.ts";
import type { SiteScoreResponse } from "../lib/api.ts";

const site = {
  demand: { seoul_rank: { population_top_pct: 99, workers_top_pct: 1, youth_top_pct: 29 } },
  sales_benchmark: { per_store_monthly_won: 99_330_000, citywide_median_per_store_won: 5_310_000 },
  subscores: [{ key: "competition", label: "경쟁 밀도", score: 88, detail: "" }],
} as unknown as SiteScoreResponse;

test("배후 수요 → 서울 순위 게이지 3줄", () => {
  const v = sectionVisual("배후 수요", site);
  assert.equal(v?.kind, "gauges");
  if (v?.kind !== "gauges") return;
  assert.deepEqual(v.rows.map((r) => r.pct), [99, 1, 29]);
});

test("소비력·매출 → 이 자리 vs 서울 중앙값 비교 막대", () => {
  const v = sectionVisual("소비력·매출", site);
  assert.equal(v?.kind, "compare");
  if (v?.kind !== "compare") return;
  assert.equal(v.rows[0].display, "9,933만원");
  assert.equal(v.rows[1].display, "531만원");
});

test("경쟁 → 높을수록 불리한 inverse 막대", () => {
  const v = sectionVisual("경쟁", site);
  assert.equal(v?.kind, "score");
  if (v?.kind !== "score") return;
  assert.equal(v.inverse, true);
  assert.equal(v.score, 88);
});

test("데이터가 없으면 차트를 지어내지 않는다", () => {
  const empty = {} as SiteScoreResponse;
  assert.equal(sectionVisual("배후 수요", empty), null);
  assert.equal(sectionVisual("소비력·매출", empty), null);
  assert.equal(sectionVisual("경쟁", empty), null);
  // 행동 제안 섹션엔 그릴 값이 없다 — 억지로 붙이지 않는다.
  assert.equal(sectionVisual("그래서 이렇게 하세요", site), null);
});
