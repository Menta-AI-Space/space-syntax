import { test } from "node:test";
import * as assert from "node:assert/strict";

import type { RealCompetitionResponse } from "../lib/api.ts";
import { competitionReachLine } from "../lib/realCompetitionCard.ts";

const base: RealCompetitionResponse = {
  vertical: "restaurant",
  total_weighted_score: 62,
  total_intensity: "높음",
  buckets: [],
};

test("comp가 없으면 권역 문구를 만들지 않는다", () => {
  assert.equal(competitionReachLine(null), null);
  assert.equal(competitionReachLine(undefined), null);
});

test("reach_label이 없으면 권역을 지어내지 않는다 — reach_mode·reach_minutes만 있어도 조합하지 않는다", () => {
  const comp: RealCompetitionResponse = { ...base, reach_mode: "walk", reach_minutes: 5, reach_label: null };
  assert.equal(competitionReachLine(comp), null);
});

test("reach_label이 있으면 backend가 준 문자열 그대로 붙여 보여준다", () => {
  const comp: RealCompetitionResponse = { ...base, reach_mode: "walk", reach_minutes: 5, reach_label: "도보 5분" };
  assert.equal(competitionReachLine(comp), "분석 권역: 도보 5분");
});

test("reach_mode가 걸어서가 아니어도 backend 라벨을 그대로 쓴다(프런트가 새로 조합하지 않는다)", () => {
  const comp: RealCompetitionResponse = { ...base, reach_mode: "drive", reach_minutes: 10, reach_label: "차도 10분" };
  assert.equal(competitionReachLine(comp), "분석 권역: 차도 10분");
});
