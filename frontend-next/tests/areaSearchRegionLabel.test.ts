import { test } from "node:test";
import * as assert from "node:assert/strict";

import { areaSearchRegionLabel } from "../lib/api.ts";

// 백엔드(app/routers/goals.py의 AreaSearchResponse)는 region_label이라는 필드를
// 응답에 넣지 않는다 — 지역 이름은 center.label에 실린다. 프론트가 없는
// result.region_label을 읽으면 항상 undefined라 지역 칩이 늘 비어 있었다.

test("center.label이 있으면 그 값을 칩으로 쓴다", () => {
  const result = { center: { lat: 37.5, lng: 127.0, label: "연남동" } };
  assert.equal(areaSearchRegionLabel(result), "연남동");
});

test("center.label이 빈 문자열이면(백엔드 기본값) 칩을 비운다", () => {
  const result = { center: { lat: 37.5, lng: 127.0, label: "" } };
  assert.equal(areaSearchRegionLabel(result), undefined);
});

test("center 자체가 없으면 칩을 비운다", () => {
  const result = {};
  assert.equal(areaSearchRegionLabel(result), undefined);
});

test("실제 백엔드 응답 모양(top-level region_label 없음)에서도 center.label을 읽는다", () => {
  // AreaSearchResponse 실제 스키마를 흉내: region_label은 top-level에 없고
  // center 안에만 있다.
  const backendShapedResult = {
    center: { lat: 37.5, lng: 127.0, search_radius_m: 500, label: "망원동" },
    candidates: [],
    headline: "",
    narrative_source: "rule",
    warnings: [],
  };
  assert.equal(areaSearchRegionLabel(backendShapedResult), "망원동");
});
