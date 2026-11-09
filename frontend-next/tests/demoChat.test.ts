/**
 * AI 예시 대화.
 *
 * 예시라도 **숫자와 말꼬리는 지켜야 한다.** AI가 탭 화면과 다른 숫자를 말하면
 * 데모를 보는 사람은 어느 쪽이 제품인지 알 수 없고, 표기 규칙을 어기면 데모가
 * 규칙을 어기는 법을 가르치게 된다.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { CHAT_INTRO, CHAT_TURNS, FOLLOW_UPS } from "../lib/demoChat.ts";
import { buildDemoSites, heroSite, signedPct } from "../lib/demoParcels.ts";
import { buildTradeAnalysis, manwon } from "../lib/demoTrade.ts";

function textOf(turnId: string): string {
  const t = CHAT_TURNS.find((x) => x.id === turnId);
  assert.ok(t, turnId);
  return t.blocks
    .map((b) =>
      b.kind === "table"
        ? [...b.head, ...b.rows.flat()].join(" ")
        : b.kind === "ul"
          ? b.items.join(" ")
          : b.text,
    )
    .join("\n");
}

test("두 분석에 각각 하나씩 — 상권과 토지가치를 모두 보여 준다", () => {
  assert.equal(CHAT_TURNS.length, 2);
  assert.deepEqual(
    CHAT_TURNS.map((t) => t.scope),
    ["상권분석", "토지가치분석"],
  );
  for (const t of CHAT_TURNS) {
    assert.ok(t.q.endsWith("?"), t.id);
    assert.ok(t.blocks.length >= 3, t.id);
    assert.ok(t.tools.length >= 1, t.id);
    assert.ok(t.sources.length >= 2, t.id);
    assert.ok(FOLLOW_UPS[t.id]?.length >= 2, t.id);
  }
});

test("모든 답에 표기 각주가 붙는다 — 숫자만 던지지 않는다", () => {
  for (const t of CHAT_TURNS) {
    assert.ok(
      t.blocks.some((b) => b.kind === "note"),
      `${t.id}에 note 블록이 없다`,
    );
  }
});

test("토지가치 답이 화면과 같은 숫자를 말한다", () => {
  const hero = heroSite(buildDemoSites());
  const y3 = hero.future.find((f) => f.horizon === 3);
  assert.ok(y3);
  const body = textOf("land");

  assert.ok(body.includes(signedPct(y3.pct)), `3년 상승률 ${signedPct(y3.pct)}가 답에 없다`);
  assert.ok(body.includes(signedPct(hero.gapPct)), `Gap ${signedPct(hero.gapPct)}가 답에 없다`);
  assert.ok(body.includes(`상위 ${100 - Math.round(hero.percentile)}%`), "유망입지 백분위 불일치");
});

test("상권 답이 화면과 같은 숫자를 말한다", () => {
  const trade = buildTradeAnalysis("sinchon", "카페");
  const body = textOf("trade");

  assert.ok(body.includes(`${trade.sameCategoryCount}곳`), "동일 업종 수 불일치");
  assert.ok(body.includes(`${trade.cannibalPct}%`), "잠식 위험 불일치");
  assert.ok(body.includes(manwon(trade.breakEvenMonthlyMan)), "손익분기 불일치");
  assert.ok(body.includes(`${trade.ordersPerDay}잔`), `하루 객수 ${trade.ordersPerDay} 불일치`);
  assert.ok(body.includes(String(trade.ticketWon.toLocaleString("ko-KR"))), "객단가 불일치");
});

test("표기 규칙 — 금지된 말을 쓰지 않는다", () => {
  const banned = ["자본화 상한", "시세보다 싸", "이 땅의 가치", "확실히 오"];
  for (const t of CHAT_TURNS) {
    const body = textOf(t.id);
    for (const word of banned) {
      assert.ok(!body.includes(word), `${t.id}에 "${word}"가 있다`);
    }
  }
});

test("토지가치 답이 '행정이 매길 값'임을 밝힌다 — 시장가 예측으로 읽히면 안 된다", () => {
  const body = textOf("land");
  assert.ok(body.includes("행정이 매길 값"), "행정/시장 구분이 답에 없다");
  assert.ok(body.includes("실거래 시장가격 대비 평가가 아니라"), "각주 문구가 없다");
});

test("상권 답이 추정매출을 점포 실적으로 말하지 않는다", () => {
  const body = textOf("trade");
  assert.ok(body.includes("특정 점포의 실적이 아닙니다"), "추정매출 각주가 없다");
});

test("소개 세 칸이 AI의 자리를 말한다 — 입구가 아니라 후속 창구", () => {
  assert.equal(CHAT_INTRO.length, 3);
  const joined = CHAT_INTRO.map((c) => `${c.k} ${c.v}`).join(" ");
  assert.ok(joined.includes("이미 완결"));
  assert.ok(joined.includes("그다음"));
});
