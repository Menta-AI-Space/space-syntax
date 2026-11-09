import { test } from "node:test";
import * as assert from "node:assert/strict";

import type { SiteScoreResponse } from "../lib/api.ts";
import { siteScoreWarnings } from "../lib/siteScoreWarnings.ts";

const base: SiteScoreResponse = {
  lat: 37.5,
  lng: 127.0,
  radius_m: 300,
  business_type: "cafe",
  business_label: "카페",
  total_score: 70,
  grade: "B",
  segment: {
    osm_id: 1,
    highway: "residential",
    distance_m: 10,
    integration_top_pct: 50,
    choice_top_pct: 50,
    mean_depth_top_pct: 50,
    connectivity_top_pct: 50,
    connectivity_degree: 3,
  },
  subscores: [],
};

test("warnings가 없으면(필드 자체가 없으면) 빈 배열 — 없는 값을 지어내지 않는다", () => {
  assert.deepEqual(siteScoreWarnings(base), []);
});

test("site가 null/undefined면 빈 배열", () => {
  assert.deepEqual(siteScoreWarnings(null), []);
  assert.deepEqual(siteScoreWarnings(undefined), []);
});

test("warnings 배열이 있으면 backend가 준 문자열을 그대로 돌려준다", () => {
  const withWarnings: SiteScoreResponse = {
    ...base,
    warnings: ["DepthmapX 미사용 (Python 엔진으로 대체, 느릴 수 있음): timeout", "교통 접근: API 실패"],
  };
  assert.deepEqual(siteScoreWarnings(withWarnings), [
    "DepthmapX 미사용 (Python 엔진으로 대체, 느릴 수 있음): timeout",
    "교통 접근: API 실패",
  ]);
});

test("빈 문자열이나 문자열이 아닌 항목은 걸러낸다", () => {
  const dirty = {
    ...base,
    warnings: ["실제 경고", "", "   ", 42, null],
  } as unknown as SiteScoreResponse;
  assert.deepEqual(siteScoreWarnings(dirty), ["실제 경고"]);
});

test("warnings가 배열이 아니면(잘못된 응답 모양) 빈 배열로 방어한다", () => {
  const malformed = { ...base, warnings: "not-an-array" } as unknown as SiteScoreResponse;
  assert.deepEqual(siteScoreWarnings(malformed), []);
});
