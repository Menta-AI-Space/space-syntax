import { test } from "node:test";
import * as assert from "node:assert/strict";

import type { SiteScoreResponse, SubScore } from "../lib/api.ts";
import {
  isInverseMetric,
  leadIdsForMetric,
  metricGoodness,
  rankTopPctText,
  subscoreValue,
  topFactorChips,
  type CompareRow,
} from "../lib/metricDirection.ts";

function candidate(id: string, subscores: Partial<SubScore>[]): CompareRow {
  return {
    candidate: { id, lat: 37.5, lng: 127.0 },
    data: { subscores } as unknown as SiteScoreResponse,
  };
}

test("경쟁은 적은 쪽이 이긴다", () => {
  // 백엔드의 경쟁 서브스코어는 rank_top_pct 없이 '경쟁 밀도'만 준다(높을수록
  // 경쟁이 심하다). 필드 유무로 방향을 정하던 때는 가장 경쟁이 심한 후보에
  // "항목 최고" ▲가 붙었다 — 표가 최악을 추천한 셈이다.
  const rows = [
    candidate("A", [{ key: "competition", score: 90 }]),
    candidate("B", [{ key: "competition", score: 20 }]),
  ];
  assert.deepEqual([...leadIdsForMetric(rows, "competition")], ["B"]);
});

test("수요는 많은 쪽이 이긴다", () => {
  const rows = [
    candidate("A", [{ key: "demand", score: 90 }]),
    candidate("B", [{ key: "demand", score: 20 }]),
  ];
  assert.deepEqual([...leadIdsForMetric(rows, "demand")], ["A"]);
});

test("순위 지표(상위 %)는 낮은 쪽이 이긴다", () => {
  const rows = [
    candidate("A", [{ key: "integration", score: 60, rank_top_pct: 30 }]),
    candidate("B", [{ key: "integration", score: 40, rank_top_pct: 8 }]),
  ];
  assert.deepEqual([...leadIdsForMetric(rows, "integration")], ["B"]);
});

test("다 같거나 비교 대상이 부족하면 아무도 강조하지 않는다", () => {
  const same = [
    candidate("A", [{ key: "competition", score: 50 }]),
    candidate("B", [{ key: "competition", score: 50 }]),
  ];
  assert.equal(leadIdsForMetric(same, "competition").size, 0);
  assert.equal(leadIdsForMetric([same[0]], "competition").size, 0);
});

test("동점이면 둘 다 강조한다", () => {
  const rows = [
    candidate("A", [{ key: "competition", score: 10 }]),
    candidate("B", [{ key: "competition", score: 10 }]),
    candidate("C", [{ key: "competition", score: 80 }]),
  ];
  assert.deepEqual([...leadIdsForMetric(rows, "competition")].sort(), ["A", "B"]);
});

test("방향은 지표 이름으로 정해진다", () => {
  assert.equal(isInverseMetric("competition"), true);
  assert.equal(isInverseMetric("cannibalization_risk"), true);
  assert.equal(isInverseMetric("demand"), false);
  const comp = subscoreValue(
    { subscores: [{ key: "competition", score: 70 }] } as unknown as SiteScoreResponse,
    "competition",
  );
  assert.deepEqual(comp, { v: 70, better: "low" });
});

test("높을수록 나쁜 지표는 '상위 %'에 방향을 밝힌다", () => {
  // 빨간 막대 옆에 "상위 0.1%"만 적혀 있으면 아주 좋은 자리로 읽힌다.
  assert.equal(rankTopPctText(100, null, true), "불리한 쪽 상위 0.1%");
  assert.equal(rankTopPctText(90, null, false), "상위 10%");
  // 서버가 순위를 주면 그 값을 그대로 쓴다.
  assert.equal(rankTopPctText(40, 8.3, false), "상위 8.3%");
});

// 종합점수 옆 "핵심 근거" 칩. 예전에는 점수 크기순으로 잘라, 높을수록 나쁜
// 경쟁 밀도가 늘 상위에 올랐다 — 라이브에서 서울 주요 상권 다섯 곳을 재 보니
// 전부 "핵심 근거: 경쟁 밀도 100점 · 매우 높음"이 붙었다.

const subs = [
  { key: "competition", label: "경쟁 밀도", score: 100, display: "매우 높음" },
  { key: "demand", label: "수요", score: 96, display: "매우 좋음" },
  { key: "integration", label: "접근성", score: 91, display: "상위 8.6%", rank_top_pct: 8.6 },
  { key: "connectivity", label: "연결성", score: 86, display: "상위 14.1%", rank_top_pct: 14.1 },
];

test("경쟁이 극심한 곳에서 경쟁은 핵심 근거가 아니다", () => {
  const chips = topFactorChips(subs);
  assert.ok(!chips.some((c) => c.key === "competition"), "경쟁 밀도 100이 근거로 올라왔다");
  assert.deepEqual(chips.map((c) => c.key), ["demand", "integration", "connectivity"]);
});

test("경쟁이 적으면 그건 진짜 근거다", () => {
  const quiet = [{ ...subs[0], score: 10, display: "매우 낮음" }, ...subs.slice(1)];
  const chips = topFactorChips(quiet);
  const comp = chips.find((c) => c.key === "competition");
  // 경쟁 10점 = 우위 90 → 수요 96 다음이지만 근거에는 분명히 들어간다.
  assert.ok(comp, "경쟁이 적은데도 근거에서 빠졌다");
  // 값은 내부 점수(10점)가 아니라 지표가 스스로 쓰는 표현으로 보여 준다.
  assert.equal(comp?.value, "매우 낮음");
});

test("근거라고 할 만한 게 없으면 아무것도 내세우지 않는다", () => {
  const weak = [
    { key: "demand", label: "수요", score: 20 },
    { key: "competition", label: "경쟁 밀도", score: 95 },
  ];
  assert.deepEqual(topFactorChips(weak), []);
});

test("점수가 없는 지표는 근거에서 빠진다", () => {
  const chips = topFactorChips([{ key: "demand", label: "수요", score: null }, ...subs]);
  assert.ok(!chips.some((c) => c.value === "NaN점"));
  assert.ok(chips.every((c) => c.value));
});

test("방향을 맞춘 점수", () => {
  assert.equal(metricGoodness("competition", 100), 0);
  assert.equal(metricGoodness("demand", 100), 100);
});

// 9개 업종의 경쟁/포화 키가 backend/app/services/industry_kpis.py의
// _KPI_REGISTRY 실제 응답 키와 어긋나 있어 방향 반전이 전혀 적용되지 않았다
// — 경쟁이 가장 심한 후보가 초록 막대·"항목 최고"로 표시됐다.
const SATURATION_KEYS_BY_INDUSTRY: Record<string, string> = {
  restaurant: "competition_pressure",
  clinic: "same_specialty_supply",
  academy: "same_subject_competition",
  lodging: "lodging_competition",
  professional_office: "professional_competition",
  fitness: "fitness_competition",
  leisure: "leisure_competition",
  retail_specialty: "retail_competition",
  pharmacy: "pharmacy_competition",
};

for (const [industry, key] of Object.entries(SATURATION_KEYS_BY_INDUSTRY)) {
  test(`${industry}의 포화 지표(${key})는 점수가 높을수록 나쁘다`, () => {
    assert.equal(isInverseMetric(key), true);
    assert.equal(metricGoodness(key, 100), 0);
    assert.equal(metricGoodness(key, 0), 100);

    const rows = [
      candidate("A", [{ key, score: 90 }]),
      candidate("B", [{ key, score: 20 }]),
    ];
    // 포화도가 낮은 후보(B)가 이겨야 한다 — 포화도가 높은 후보(A)가 이기면
    // 세부 지표 막대가 초록으로 칠해져 "가장 붐비는 자리가 최고"로 보인다.
    assert.deepEqual([...leadIdsForMetric(rows, key)], ["B"]);
  });
}

test("franchise의 잠식 지표(market_overlap)는 점수가 높을수록 나쁘다", () => {
  assert.equal(isInverseMetric("market_overlap"), true);
  const rows = [
    candidate("A", [{ key: "market_overlap", score: 80 }]),
    candidate("B", [{ key: "market_overlap", score: 10 }]),
  ];
  assert.deepEqual([...leadIdsForMetric(rows, "market_overlap")], ["B"]);
});

test("경쟁까지 거리 지표(competition_distance·service_competition)는 멀수록 유리해 반전하지 않는다", () => {
  // _kpi_competition_distance는 "최근접 동일업종까지 거리"라 이미 높을수록
  // 좋다(멀수록 유리) — 여기 넣으면 진짜로 방향이 뒤집힌다.
  assert.equal(isInverseMetric("competition_distance"), false);
  assert.equal(isInverseMetric("service_competition"), false);
  const rows = [
    candidate("A", [{ key: "competition_distance", score: 90 }]),
    candidate("B", [{ key: "competition_distance", score: 20 }]),
  ];
  assert.deepEqual([...leadIdsForMetric(rows, "competition_distance")], ["A"]);
});
