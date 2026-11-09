/**
 * 필지 지도의 순수 변환 — 색과 좌표.
 *
 * 여기가 틀리면 화면이 **조용히** 거짓말을 한다. 갭 점수의 부호를 뒤집어 칠하면
 * 고평가 필지가 저평가처럼 보이는데, 지도는 그걸 오류로 알려 주지 않는다.
 * Kakao SDK 없이 검증할 수 있게 지도 컴포넌트에서 분리해 둔 이유다.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  GAP_BANDS,
  UNJUDGED,
  centerOf,
  fillOpacity,
  gapBand,
  outerRings,
  percentileText,
} from "../lib/parcelMap.ts";

// ── 백분위 문장 ─────────────────────────────────────────────────────────

test("백분위 0은 '상위 100%'가 아니라 '하위 1%'로 읽힌다", () => {
  // 실측 회귀: 독산동 필지의 공시지가 백분위 0이 화면에 "상위 100%"로 찍혔다.
  assert.equal(percentileText(0), "하위 1%");
  assert.equal(percentileText(99), "상위 1%");
  assert.equal(percentileText(100), "상위 1%");
  // 중간값은 어느 쪽으로 말해도 되지만 경계가 흔들리면 안 된다.
  assert.equal(percentileText(50), "상위 50%");
  assert.equal(percentileText(49), "하위 49%");
});

// ── 색: 부호를 뒤집으면 안 된다 ─────────────────────────────────────────

test("양수 갭은 '낮음' 쪽, 음수 갭은 '높음' 쪽 — 부호가 뒤집히지 않는다", () => {
  assert.match(gapBand(46).label, /낮음/);
  assert.match(gapBand(-46).label, /높음/);
  // 같은 색을 쓰면 지도에서 두 상태를 구분할 수 없다.
  assert.notEqual(gapBand(46).color, gapBand(-46).color);
});

test("범례에 낙인 표현이 없다", () => {
  // 같은 화면의 판정 카드는 above_peers를 "같은 조건 대비 높음"으로 적는다.
  // 범례만 "고평가"라고 적혀 있으면 한 화면이 두 말을 한다.
  for (const band of GAP_BANDS) {
    assert.ok(!band.label.includes("고평가"), `낙인 표현: ${band.label}`);
    assert.ok(!band.label.includes("저평가"), `단정 표현: ${band.label}`);
  }
});

test("갭 0 근처는 '비슷' — 없는 정밀도를 색으로 주장하지 않는다", () => {
  assert.equal(gapBand(0).label, "비슷");
  assert.equal(gapBand(9).label, "비슷");
  assert.equal(gapBand(-9).label, "비슷");
  // 구간 경계는 하한 포함이다.
  assert.equal(gapBand(10).label, "낮음");
  assert.equal(gapBand(-10).label, "비슷");
  assert.equal(gapBand(-11).label, "높음");
});

test("판정 없는 필지는 '비슷'이 아니라 '판정 없음'이다", () => {
  // 회색을 '보통'으로 칠하면 판정 못 한 땅과 갭이 0인 땅이 섞인다 —
  // 여의도동 실측이 폴리곤 529 대 판정 283이라 절반이 이 상태다.
  assert.equal(gapBand(null).label, UNJUDGED.label);
  assert.equal(gapBand(undefined).label, UNJUDGED.label);
  assert.notEqual(gapBand(null).color, gapBand(0).color);
});

test("NaN·Infinity도 판정 없음으로 떨어진다", () => {
  assert.equal(gapBand(NaN).label, UNJUDGED.label);
  assert.equal(gapBand(Infinity).label, UNJUDGED.label);
});

test("판정 없는 필지는 옅게 칠해 시선을 뺏지 않는다", () => {
  assert.ok(fillOpacity(null) < fillOpacity(0));
});

// ── 좌표: 필지 하나 때문에 지도 전체가 죽으면 안 된다 ────────────────────

const RING: [number, number][] = [
  [126.9324, 37.52],
  [126.9325, 37.52],
  [126.9325, 37.5201],
  [126.9324, 37.52],
];

test("MultiPolygon에서 폴리곤마다 바깥 고리를 꺼낸다", () => {
  const rings = outerRings({ type: "MultiPolygon", coordinates: [[RING], [RING]] });
  assert.equal(rings.length, 2);
  assert.deepEqual(rings[0], RING);
});

test("Polygon도 받는다 — 서버가 둘 다 보낼 수 있다", () => {
  assert.equal(outerRings({ type: "Polygon", coordinates: [RING] }).length, 1);
});

test("구멍(inner ring)은 버린다 — 바깥 고리만 그린다", () => {
  const withHole = { type: "Polygon", coordinates: [RING, RING] };
  assert.equal(outerRings(withHole).length, 1);
});

test("망가진 기하는 예외 대신 빈 배열 — 필지 하나만 빠진다", () => {
  // 예외를 던지면 필지 하나 때문에 지도 전체가 안 그려진다.
  assert.deepEqual(outerRings(null), []);
  assert.deepEqual(outerRings({ type: "Polygon", coordinates: null }), []);
  assert.deepEqual(outerRings({ type: "Point", coordinates: [126.9, 37.5] }), []);
  // 점 두 개는 면이 아니다.
  assert.deepEqual(
    outerRings({ type: "Polygon", coordinates: [[[126.9, 37.5], [126.91, 37.5]]] }),
    [],
  );
  // 좌표에 숫자가 아닌 것이 섞인 경우.
  assert.deepEqual(
    outerRings({ type: "Polygon", coordinates: [[["a", "b"], [1, 2], [3, 4]]] }),
    [],
  );
});

// ── 중심: 지도가 아프리카로 날아가지 않게 ────────────────────────────────

test("좌표 있는 필지들의 평균을 낸다", () => {
  const c = centerOf([
    { lat: 37.52, lng: 126.93 },
    { lat: 37.54, lng: 126.95 },
  ]);
  assert.ok(c);
  assert.equal(Number(c!.lat.toFixed(4)), 37.53);
  assert.equal(Number(c!.lng.toFixed(4)), 126.94);
});

test("좌표 없는 필지는 평균을 오염시키지 않는다", () => {
  // null을 0으로 세면 중심이 기니만 앞바다로 끌려간다.
  const c = centerOf([
    { lat: 37.52, lng: 126.93 },
    { lat: null, lng: null },
  ]);
  assert.deepEqual(c, { lat: 37.52, lng: 126.93 });
});

test("좌표가 하나도 없으면 null — 호출자가 지도를 띄우지 않는다", () => {
  assert.equal(centerOf([]), null);
  assert.equal(centerOf([{ lat: null, lng: null }]), null);
});
