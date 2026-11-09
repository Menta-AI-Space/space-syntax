/** LLM 답변 파서 — 서식이 어떤 꼴로 흘러와도 사람이 읽게 만드는 게 임무다. */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  normalizeCollapsedTable,
  parseAnswerBlock,
  parseAnswerBlocks,
  rowTopPercent,
} from "../lib/answerBlocks.ts";

test("한 줄로 붙어 온 표도 표로 인식한다 — 라이브에서 실제로 이 꼴로 왔다", () => {
  // "서울 비교" 섹션이 정확히 이렇게 노출됐다: 행 구분 줄바꿈 없이 공백으로만.
  const collapsed =
    "| 지표 | 이 동네 | 서울 비교 | |---|---|---| " +
    "| 거주인구 | 3,472명 | 서울 상위 99% | | 직장인 | 233,630명 | 서울 상위 1% |";

  const block = parseAnswerBlock(collapsed);
  assert.equal(block.kind, "table");
  if (block.kind !== "table") return;
  assert.deepEqual(block.header.map((c) => c[0]?.text), ["지표", "이 동네", "서울 비교"]);
  assert.equal(block.rows.length, 2);
  assert.equal(block.rows[0][0][0].text, "거주인구");
  assert.equal(block.rows[1][2][0].text, "서울 상위 1%");
});

test("줄바꿈으로 제대로 온 표는 그대로 표다", () => {
  const proper = "| 지표 | 값 |\n|---|---|\n| 거주인구 | 3,472명 |";
  const block = parseAnswerBlock(proper);
  assert.equal(block.kind, "table");
});

test("표가 아닌 문장의 파이프는 건드리지 않는다", () => {
  // 구분줄(---)이 없으면 정규화 자체를 안 한다 — 문장 속 "A | B"가 쪼개지면 안 된다.
  const prose = "선택지는 A | B 둘입니다";
  assert.equal(normalizeCollapsedTable(prose), prose);
  assert.equal(parseAnswerBlock(prose).kind, "text");
});

test("상위 N%가 있는 행에서 숫자를 뽑는다 — 순위 막대의 입력", () => {
  const table = parseAnswerBlock(
    "| 지표 | 값 | 비교 |\n|---|---|---|\n| 직장인 | 233,630명 | 서울 상위 1% |\n| 시세 | 1,594만원 | 서울 중앙값 1,324만원 (+20%) |"
  );
  assert.equal(table.kind, "table");
  if (table.kind !== "table") return;
  assert.equal(rowTopPercent(table.rows[0]), 1);
  // 상위%가 없는 행(중앙값 비교)은 막대를 그리지 않는다 — (+20%)를 순위로 오독하면 안 된다.
  assert.equal(rowTopPercent(table.rows[1]), null);
});

test("표·리스트·본문이 섞인 답변이 각자 블록으로 나뉜다", () => {
  const answer =
    "**한 줄 결론** — 신중하게 봐야 합니다.\n\n" +
    "| 지표 | 값 | |---|---| | 거주인구 | 3,472명 |\n\n" +
    "- 첫째 근거\n- 둘째 근거";
  const blocks = parseAnswerBlocks(answer);
  assert.deepEqual(blocks.map((b) => b.kind), ["text", "table", "list"]);
  assert.equal(blocks[0].kind === "text" ? blocks[0].title : "", "한 줄 결론");
});

test("제목과 표가 한 블록에 붙어 와도 표로 인식한다 — 라이브 재발 형태", () => {
  // "**서울 비교**" 다음 줄에 빈 줄 없이 표가 바로 이어졌다. 제목 패턴이 먼저
  // 잡아 표 전체가 본문으로 빨려 들어가 파이프가 그대로 노출됐다.
  const block = parseAnswerBlock(
    "**서울 비교**\n| 지표 | 이 동네 | 서울 비교 |\n|---|---|---|\n| 직장인 | 74,456명 | 서울 상위 3% |"
  );
  assert.equal(block.kind, "table");
  if (block.kind !== "table") return;
  assert.equal(block.title, "서울 비교");
  assert.equal(block.rows[0][2][0].text, "서울 상위 3%");
});

test("한 줄 제목+본문(— 구분)은 종전대로 텍스트다", () => {
  const block = parseAnswerBlock("**한 줄 결론** — 신중하게 봐야 합니다.");
  assert.equal(block.kind, "text");
  if (block.kind !== "text") return;
  assert.equal(block.title, "한 줄 결론");
  assert.equal(block.body[0].text, "신중하게 봐야 합니다.");
});

test("빈 답은 블록이 없다 — 실패 카드 밑에 빈 칸을 그리지 않는다", () => {
  assert.deepEqual(parseAnswerBlocks(""), []);
  assert.deepEqual(parseAnswerBlocks("  \n\n "), []);
});

test("굵은 말로 시작하는 문장은 제목으로 떼지 않는다 — 「는 길 구조가…」로 시작하던 문제", () => {
  const block = parseAnswerBlock("**망원동 카페 자리**는 길 구조가 받쳐 주는 편입니다.");
  assert.equal(block.kind, "text");
  if (block.kind !== "text") return;
  assert.equal(block.title, "");
  const text = block.body.map((r) => r.text).join("");
  assert.equal(text, "망원동 카페 자리는 길 구조가 받쳐 주는 편입니다.");
  assert.equal(block.body[0].bold, true);

  const colon = parseAnswerBlock("**결론**: 신중하게 봐야 합니다.");
  if (colon.kind !== "text") return assert.fail("text여야 한다");
  assert.equal(colon.title, "결론");
});
