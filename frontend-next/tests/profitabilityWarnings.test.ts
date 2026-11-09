import { test } from "node:test";
import * as assert from "node:assert/strict";

import { profitabilityWarnings } from "../lib/profitabilityWarnings.ts";

// T-097: ProfitabilityToolCard가 최상위 warnings(시장/임대료 조회 실패)만 보여주고
// calculation.warnings(자기자본 0 이하, 대출원금 초과 같은 계산 자체의 위험)는
// 숨기고 있었다. 두 목록이 각각 따로 채워져도 합쳐서 다 보여야 한다.

function calc(warnings: string[]) {
  return { warnings };
}

test("계산 경고만 있어도 그대로 나온다", () => {
  const result = profitabilityWarnings({
    calculation: calc(["대출원금이 매입가를 초과합니다."]),
    warnings: [],
  });
  assert.deepEqual(result, ["대출원금이 매입가를 초과합니다."]);
});

test("최상위 경고만 있어도 그대로 나온다", () => {
  const result = profitabilityWarnings({
    calculation: calc([]),
    warnings: ["시장 데이터 조회 실패: timeout"],
  });
  assert.deepEqual(result, ["시장 데이터 조회 실패: timeout"]);
});

test("계산 경고와 최상위 경고가 둘 다 있으면 둘 다 나온다", () => {
  const result = profitabilityWarnings({
    calculation: calc(["대출+보증금이 총투자원가 이상입니다 — 자기자본이 0 이하라 CoC·IRR을 계산할 수 없습니다."]),
    warnings: ["임대료 참고치 조회 실패: 500"],
  });
  assert.deepEqual(result, [
    "대출+보증금이 총투자원가 이상입니다 — 자기자본이 0 이하라 CoC·IRR을 계산할 수 없습니다.",
    "임대료 참고치 조회 실패: 500",
  ]);
});

test("둘 다 없으면 빈 배열", () => {
  const result = profitabilityWarnings({ calculation: calc([]), warnings: [] });
  assert.deepEqual(result, []);
});

test("같은 문구가 양쪽에 있으면 중복 없이 한 번만 나온다", () => {
  const result = profitabilityWarnings({
    calculation: calc(["동일 경고"]),
    warnings: ["동일 경고"],
  });
  assert.deepEqual(result, ["동일 경고"]);
});
