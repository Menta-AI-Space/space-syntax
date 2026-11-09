import { test } from "node:test";
import * as assert from "node:assert/strict";

/**
 * 음성 입력은 한 번에 한 문장만 받는다(브라우저 API의 continuous=false).
 * 길게 말하려면 여러 번 눌러야 하는데, 덮어쓰면 마지막 문장만 남는다 —
 * 2026-10-01에 소유자가 실제로 그렇게 막혔다. 이어 붙이기 규칙을 고정해 둔다.
 */
function append(previous: string, spoken: string): string {
  const before = previous.trimEnd();
  if (!before) return spoken;
  return `${before} ${spoken}`;
}

test("여러 번 말하면 이어 붙는다", () => {
  let text = "";
  for (const said of ["신촌역 근처에", "월세 300만원짜리", "카페 자리 어때?"]) {
    text = append(text, said);
  }
  assert.equal(text, "신촌역 근처에 월세 300만원짜리 카페 자리 어때?");
});

test("처음 한 번은 그대로 들어간다", () => {
  assert.equal(append("", "강남역 편의점"), "강남역 편의점");
});

test("직접 친 글 뒤에도 이어 붙는다", () => {
  assert.equal(append("강남역 ", "편의점 낼 만할까?"), "강남역 편의점 낼 만할까?");
});

test("지우고 다시 말하면 새로 시작한다", () => {
  assert.equal(append("   ", "홍대입구"), "홍대입구");
});
