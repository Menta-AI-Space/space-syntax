import { test } from "node:test";
import * as assert from "node:assert/strict";

import {
  assignOverlapSlots,
  boundsForPoints,
  boundsForRadius,
  boxSpanM,
  cssVar,
  formatDistanceM,
  haversineM,
  isFiniteLatLng,
  normalizedSplit,
  overlapThresholdM,
} from "../lib/mapUtils.ts";

// 미니 지도는 "어제 본 그 지점"이 아니라 아무 지점에나 열린다. 좌표가 없거나,
// 반경이 100m거나 2km거나, 후보가 하나거나 서울 반대편까지 흩어져 있어도 같은
// 코드로 그려져야 한다. 아래는 그 경계값들을 고정한다.

test("좌표 유효성 — null·NaN·범위 밖을 모두 걸러낸다", () => {
  assert.equal(isFiniteLatLng({ lat: 37.5, lng: 127.0 }), true);
  assert.equal(isFiniteLatLng({ lat: 0, lng: 0 }), true); // 0,0은 문법상 유효(호출부에서 따로 판단)
  assert.equal(isFiniteLatLng(null), false);
  assert.equal(isFiniteLatLng({ lat: Number.NaN, lng: 127 }), false);
  assert.equal(isFiniteLatLng({ lat: 37.5, lng: Number.POSITIVE_INFINITY }), false);
  assert.equal(isFiniteLatLng({ lat: 91, lng: 127 }), false);
  assert.equal(isFiniteLatLng({ lat: 37.5, lng: 181 }), false);
  assert.equal(isFiniteLatLng({ lat: "37.5", lng: 127 } as unknown), false);
});

test("거리 계산 — 같은 점은 0, 알려진 두 점은 오차 5% 안", () => {
  const seoulCityHall = { lat: 37.5665, lng: 126.978 };
  const gangnamStation = { lat: 37.4979, lng: 127.0276 };
  assert.equal(haversineM(seoulCityHall, seoulCityHall), 0);
  // 시청 ↔ 강남역 직선거리는 약 8.8km.
  const d = haversineM(seoulCityHall, gangnamStation);
  assert.ok(d > 8_300 && d < 9_300, `expected ~8.8km, got ${d}`);
});

test("반경 사각형 — 반경이 커지면 사각형도 그만큼 커진다", () => {
  const small = boundsForRadius(37.5665, 126.978, 100);
  const large = boundsForRadius(37.5665, 126.978, 2000);
  assert.ok(small && large);
  const smallSpan = boxSpanM(small!);
  const largeSpan = boxSpanM(large!);
  assert.ok(largeSpan > smallSpan * 15, `${largeSpan} vs ${smallSpan}`);
  // 중심은 그대로여야 한다.
  assert.ok(Math.abs((small!.north + small!.south) / 2 - 37.5665) < 1e-9);
});

test("반경 사각형 — 좌표가 없거나 반경이 0·음수여도 터지지 않는다", () => {
  assert.equal(boundsForRadius(Number.NaN, 126.978, 500), null);
  const zero = boundsForRadius(37.5665, 126.978, 0);
  assert.ok(zero);
  assert.equal(zero!.north, zero!.south);
  const negative = boundsForRadius(37.5665, 126.978, -100);
  assert.ok(negative);
  assert.equal(negative!.north, negative!.south);
});

test("후보 사각형 — 한 곳뿐이어도 최소 폭이 보장돼 최대 배율로 튀지 않는다", () => {
  const box = boundsForPoints([{ lat: 37.5665, lng: 126.978 }], 400);
  assert.ok(box);
  // 최소 폭 400m를 요구했으니 위/아래로 200m씩.
  const heightM = haversineM({ lat: box!.south, lng: box!.west }, { lat: box!.north, lng: box!.west });
  assert.ok(heightM > 350 && heightM < 450, `expected ~400m, got ${heightM}`);
});

test("후보 사각형 — 같은 건물에 몰려 있어도 사각형이 점으로 찌그러지지 않는다", () => {
  const same = { lat: 37.5665, lng: 126.978 };
  const box = boundsForPoints([same, { ...same }, { ...same }], 400);
  assert.ok(box);
  assert.ok(box!.north > box!.south);
  assert.ok(box!.east > box!.west);
});

test("후보 사각형 — 멀리 흩어지면 최소 폭이 아니라 실제 범위를 쓴다", () => {
  const box = boundsForPoints(
    [
      { lat: 37.4979, lng: 127.0276 },
      { lat: 37.6584, lng: 127.0619 },
    ],
    400,
  );
  assert.ok(box);
  assert.ok(boxSpanM(box!) > 15_000, `expected >15km span, got ${boxSpanM(box!)}`);
});

test("후보 사각형 — 좌표가 하나도 없으면 null(지도를 그리지 않는다)", () => {
  assert.equal(boundsForPoints([]), null);
  assert.equal(
    boundsForPoints([{ lat: Number.NaN, lng: 127 }, { lat: 37.5, lng: Number.NaN }]),
    null,
  );
});

test("후보 사각형 — 유효한 좌표만 골라 쓴다", () => {
  const box = boundsForPoints([
    { lat: Number.NaN, lng: 127 },
    { lat: 37.5665, lng: 126.978 },
  ]);
  assert.ok(box);
  assert.ok(Math.abs((box!.north + box!.south) / 2 - 37.5665) < 1e-6);
});

test("겹침 기준 — 축척(m/px)에 비례한다", () => {
  // 같은 화면 폭이라도 표시 범위가 10배면 겹침 기준 거리도 10배.
  const tight = overlapThresholdM(1_000, 320, 46);
  const wide = overlapThresholdM(10_000, 320, 46);
  assert.ok(Math.abs(wide / tight - 10) < 1e-6);
  // 잘못된 입력에도 0으로 나누지 않는다.
  assert.equal(Number.isFinite(overlapThresholdM(Number.NaN, 0, 46)), true);
  assert.equal(overlapThresholdM(0, 320, 46), 0);
});

test("마커 겹침 — 떨어져 있으면 전부 원래 자리(슬롯 0)", () => {
  const slots = assignOverlapSlots(
    [
      { lat: 37.5, lng: 127.0 },
      { lat: 37.6, lng: 127.1 },
      { lat: 37.7, lng: 127.2 },
    ],
    50,
  );
  assert.deepEqual(slots, [0, 0, 0]);
});

test("마커 겹침 — 같은 자리에 몰리면 앞 순위가 0, 뒤가 차례로 쌓인다", () => {
  const same = { lat: 37.5665, lng: 126.978 };
  const slots = assignOverlapSlots([same, { ...same }, { ...same }], 100);
  assert.deepEqual(slots, [0, 1, 2]);
});

test("마커 겹침 — 겹치는 무리와 떨어진 점이 섞여도 무리별로만 쌓인다", () => {
  const a = { lat: 37.5665, lng: 126.978 };
  const far = { lat: 37.6584, lng: 127.0619 };
  const slots = assignOverlapSlots([a, far, { ...a }], 100);
  assert.deepEqual(slots, [0, 0, 1]);
});

test("마커 겹침 — 좌표가 깨졌거나 기준이 0이면 전부 0으로 안전하게 떨어진다", () => {
  assert.deepEqual(
    assignOverlapSlots([{ lat: Number.NaN, lng: 127 }, { lat: 37.5, lng: 127 }], 100),
    [0, 0],
  );
  const same = { lat: 37.5665, lng: 126.978 };
  assert.deepEqual(assignOverlapSlots([same, { ...same }], 0), [0, 0]);
});

test("남녀 비율 — 반올림으로 합이 100이 아니어도 막대가 100을 채운다", () => {
  const split = normalizedSplit(49, 50);
  assert.ok(split);
  assert.ok(Math.abs(split!.a + split!.b - 100) < 1e-9);
  assert.ok(split!.a < split!.b);
});

test("남녀 비율 — 한쪽만 오거나 값이 없으면 안전하게 처리한다", () => {
  const onlyMale = normalizedSplit(80, null);
  assert.deepEqual(onlyMale, { a: 100, b: 0 });
  assert.equal(normalizedSplit(null, undefined), null);
  assert.equal(normalizedSplit(0, 0), null); // 둘 다 0이면 그릴 게 없다
  assert.equal(normalizedSplit(Number.NaN, Number.NaN), null);
  assert.equal(normalizedSplit(-10, -10), null);
});

test("남녀 비율 — 한쪽 쏠림도 그대로 유지된다", () => {
  const skewed = normalizedSplit(5, 95);
  assert.deepEqual(skewed, { a: 5, b: 95 });
});

test("거리 표기 — m와 km를 나누고, 값이 없으면 대시", () => {
  assert.equal(formatDistanceM(0), "—");
  assert.equal(formatDistanceM(Number.NaN), "—");
  assert.equal(formatDistanceM(-5), "—");
  assert.equal(formatDistanceM(500), "500m");
  assert.equal(formatDistanceM(999), "999m");
  assert.equal(formatDistanceM(1000), "1km");
  assert.equal(formatDistanceM(1234), "1.2km");
  assert.equal(formatDistanceM(15_400), "15km");
});

test("CSS 토큰 읽기 — 브라우저가 없으면 fallback을 쓴다(SSR·테스트)", () => {
  assert.equal(cssVar("--blue-50", "#2563eb"), "#2563eb");
});
