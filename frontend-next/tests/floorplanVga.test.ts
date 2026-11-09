/**
 * 평면 공간구조 계산.
 *
 * 이건 예시 데이터가 아니라 **계산**이다. 그래서 테스트가 지킬 것도 표기가 아니라
 * 성질이다 — 바깥이 섞여 들어오면 건물 밖 공터가 가장 좋은 자리로 나오고, 벽을
 * 너무 두껍게 굽으면 방이 사라진다. 둘 다 실제로 겪은 사고다.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { buildFloorplanDemo } from "../lib/demoFloorplan.ts";
import { CELL, computeVga, norm, readVga } from "../lib/floorplanVga.ts";
import { HEATMAP_TERMS, TERMS, TERM_ORDER, rankText } from "../lib/floorplanTerms.ts";

const VIEW_W = 1000;
const VIEW_H = 700;

const plan = buildFloorplanDemo();
const vga = computeVga(plan.walls, plan.openings, VIEW_W, VIEW_H);

test("실내가 충분히 남는다 — 벽을 두껍게 굽으면 방이 사라진다", () => {
  // 한 칸이 20인 격자에서 이 평면(880×580)의 실내는 800칸을 넘어야 한다(실측 927).
  // 예전에 벽을 5칸 두께로 굽다가 23칸까지 줄었다.
  assert.ok(vga.cellCount > 800, `칸 수 ${vga.cellCount}`);
  assert.equal(vga.cell, CELL);
});

test("칸 수가 격자 전체와 같을 수 없다 — 같으면 벽이 아무것도 막지 못한 것이다", () => {
  // 실내를 못 찾았을 때 largestComponent가 라벨 초기값(-1)과 충돌해 격자 전체를
  // 돌려준 적이 있다. 0칸이어야 할 결과가 1,750칸으로 나와 성공처럼 보였다.
  assert.notEqual(vga.cellCount, vga.cols * vga.rows);
  assert.ok(vga.cellCount < vga.cols * vga.rows * 0.8, `칸 수 ${vga.cellCount}`);
});

test("모든 방에 칸이 잡힌다 — 문이 안 뚫리면 방이 통째로 빠진다", () => {
  // 바깥 물을 개구부 뚫은 뒤에 흘렸을 때 실내가 복도 띠(337칸)만 남고 남북
  // 업무공간이 통째로 빠졌다. 방 하나라도 0칸이면 그 사고가 돌아온 것이다.
  const inside = (pt: { x: number; y: number }, ring: { x: number; y: number }[]) => {
    let w = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const a = ring[i];
      const b = ring[j];
      if (a.y > pt.y !== b.y > pt.y && pt.x < ((b.x - a.x) * (pt.y - a.y)) / (b.y - a.y) + a.x) {
        w = !w;
      }
    }
    return w;
  };
  for (const s of plan.spaces) {
    const n = vga.cells.filter((c) => inside(c, s.ring)).length;
    assert.ok(n >= 3, `${s.name}에 잡힌 칸이 ${n}개다`);
  }
});

test("건물 바깥은 계산에 들어오지 않는다", () => {
  // 외벽 bbox는 60~940 / 60~640이다. 창·출입구가 뚫린 칸은 벽면 위에 앉으므로
  // 한 칸(20)만큼 여유를 준다. 그보다 밖의 칸이 있으면 바깥을 걷어내는 단계가
  // 깨진 것이고, 그때는 사방이 트인 공터가 가장 좋은 자리로 나온다.
  for (const c of vga.cells) {
    assert.ok(c.x > 40 && c.x < 960, `x ${c.x}`);
    assert.ok(c.y > 40 && c.y < 660, `y ${c.y}`);
  }
});

test("고정 — 같은 도면이면 같은 결과다", () => {
  const again = computeVga(plan.walls, plan.openings, VIEW_W, VIEW_H);
  assert.equal(again.cellCount, vga.cellCount);
  assert.deepEqual(
    again.cells.map((c) => [c.col, c.row, c.connectivity]),
    vga.cells.map((c) => [c.col, c.row, c.connectivity]),
  );
});

test("모든 칸은 적어도 이웃 하나를 본다 — 외톨이 칸은 덩어리 고르기가 깨진 것이다", () => {
  for (const c of vga.cells) {
    assert.ok(c.connectivity >= 1, `(${c.col},${c.row}) 연결도 ${c.connectivity}`);
  }
});

test("값의 범위가 성립한다", () => {
  assert.ok(vga.connectivityMin >= 1);
  assert.ok(vga.connectivityMax > vga.connectivityMin, "연결도가 전부 같을 수는 없다");
  assert.ok(vga.connectivityMax < vga.cellCount, "자기 자신은 세지 않는다");

  assert.ok(vga.integrationMin > 0);
  assert.ok(vga.integrationMax >= vga.integrationMean);
  assert.ok(vga.integrationMean >= vga.integrationMin);

  // 깊이 1은 "모두가 서로 보인다"는 뜻이다. 벽이 있는 평면에서 그럴 수 없다.
  assert.ok(vga.depthMax > 1, `최대 깊이 ${vga.depthMax}`);
  assert.ok(vga.depthMin >= 1);
});

test("통합도와 깊이는 반대로 움직인다 — 깊은 자리가 찾아오기 쉬울 수 없다", () => {
  const sorted = [...vga.cells].sort((a, b) => b.integration - a.integration);
  const top10 = sorted.slice(0, Math.floor(sorted.length * 0.1));
  const bottom10 = sorted.slice(-Math.floor(sorted.length * 0.1));
  const mean = (xs: number[]) => xs.reduce((s, v) => s + v, 0) / xs.length;
  assert.ok(
    mean(top10.map((c) => c.meanDepth)) < mean(bottom10.map((c) => c.meanDepth)),
    "통합도 상위 10%의 평균 깊이가 하위 10%보다 얕아야 한다",
  );
});

test("연결도가 높은 칸이 통합도도 대체로 높다", () => {
  // 완전한 단조는 아니다(가시성 그래프에서는 트인 방 구석도 많이 본다).
  // 상위·하위 10%의 평균만 비교한다.
  const byConn = [...vga.cells].sort((a, b) => b.connectivity - a.connectivity);
  const n = Math.floor(byConn.length * 0.1);
  const mean = (xs: number[]) => xs.reduce((s, v) => s + v, 0) / xs.length;
  assert.ok(
    mean(byConn.slice(0, n).map((c) => c.integration)) >
      mean(byConn.slice(-n).map((c) => c.integration)),
  );
});

test("5초 룰 안에 든다", () => {
  assert.ok(vga.elapsedMs < 5_000, `${vga.elapsedMs}ms`);
});

test("indexAt이 cells를 정확히 가리킨다", () => {
  for (let i = 0; i < vga.cells.length; i += 1) {
    const c = vga.cells[i];
    assert.equal(vga.indexAt[c.row * vga.cols + c.col], i);
  }
  const marked = [...vga.indexAt].filter((v) => v >= 0).length;
  assert.equal(marked, vga.cells.length);
});

test("해석 문장에 전문 용어를 쓰지 않는다", () => {
  const r = readVga(vga);
  for (const word of ["Integration", "통합도", "Connectivity", "연결도", "가시성 그래프", "VGA"]) {
    assert.ok(!r.text.includes(word), `해석 문장에 "${word}"가 있다`);
  }
  assert.ok(r.text.includes("칸으로 나눠"), "몇 칸으로 나눴는지 밝혀야 한다");
  assert.ok(r.basis.length >= 4);
});

test("해석은 한계를 함께 말한다 — 가구와 문 폭은 계산에 없다", () => {
  const r = readVga(vga);
  assert.ok(r.text.includes("가구 배치나 문 폭"), "못 보는 것을 밝히지 않았다");
});

test("norm은 0~1로 자른다", () => {
  assert.equal(norm(5, 0, 10), 0.5);
  assert.equal(norm(-3, 0, 10), 0);
  assert.equal(norm(30, 0, 10), 1);
  assert.equal(norm(7, 7, 7), 0.5);
});

/* ── 용어 ─────────────────────────────────────────────────────────────── */

test("화면에 처음 보이는 이름에 영어가 없다", () => {
  for (const k of TERM_ORDER) {
    assert.ok(!/[A-Za-z]/.test(TERMS[k].easy), `${k}: ${TERMS[k].easy}`);
    assert.ok(!/[A-Za-z]/.test(TERMS[k].high), `${k}: ${TERMS[k].high}`);
    assert.ok(!/[A-Za-z]/.test(TERMS[k].low), `${k}: ${TERMS[k].low}`);
    // 무엇을 세는 값인지 설명하는 문장에도 전문 용어를 쓰지 않는다.
    assert.ok(!/[A-Za-z]/.test(TERMS[k].what), `${k}: ${TERMS[k].what}`);
  }
});

test("전문 용어와 영어 원어는 따로 칸을 갖는다 — 섞지 않는다", () => {
  for (const k of TERM_ORDER) {
    assert.ok(TERMS[k].jargon.length > 0);
    assert.ok(/^[A-Za-z ]+$/.test(TERMS[k].en), `${k}: ${TERMS[k].en}`);
  }
});

test("모든 용어에 오해 경고가 붙는다 — 높은 값이 좋은 값은 아니다", () => {
  for (const k of TERM_ORDER) {
    assert.ok(TERMS[k].caution.length > 20, k);
  }
});

test("계산하지 않는 지표는 히트맵에 오르지 않는다", () => {
  assert.equal(TERMS.choice.computed, false);
  assert.ok(!HEATMAP_TERMS.includes("choice"));
  assert.equal(HEATMAP_TERMS.length, 3);
  for (const k of HEATMAP_TERMS) assert.equal(TERMS[k].computed, true);
});

test("순위 표기는 '상위 100%'를 만들지 않는다 — 문장으로 뜻이 없다", () => {
  assert.equal(rankText(10, [1, 2, 3, 10]), "상위 25%");
  // 최하위는 "상위 100%"가 아니라 "하위 n%"로 뒤집어 말한다.
  assert.equal(rankText(1, [1, 2, 3, 10]), "하위 1%");
  assert.equal(rankText(0, []), "—");
  for (const v of [1, 2, 3, 10]) {
    assert.ok(!rankText(v, [1, 2, 3, 10]).includes("100%"), `${v}`);
  }
});
