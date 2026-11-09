/**
 * 데모 필지·판정 생성.
 *
 * 예시 데이터이지만 **부호와 정의는 예시가 아니다.** Gap의 부호가 뒤집히면
 * 고평가 필지가 저평가로 칠해지고 지도는 그걸 오류로 알려 주지 않는다. 발표
 * 도중 숫자가 바뀌면 설명한 것과 화면이 어긋나므로 고정 시드도 함께 본다.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ANALYSIS_KINDS,
  DEMO_CENTER,
  GAP_BANDS,
  buildDemoSites,
  comparableMedian,
  gapBand,
  heroSite,
  percentileText,
  signedPct,
  signedPp,
  verdictOf,
  wonPerM2,
} from "../lib/demoParcels.ts";

test("Gap이 양수면 모델 예측이 실제보다 높다 — 저평가 후보다", () => {
  for (const s of buildDemoSites()) {
    if (s.gapPct > 0) assert.ok(s.predicted > s.actual, s.jibun);
    if (s.gapPct < 0) assert.ok(s.predicted < s.actual, s.jibun);
  }
});

test("Residual과 Gap%가 실제·예측값과 어긋나지 않는다", () => {
  for (const s of buildDemoSites()) {
    assert.equal(s.residual, s.predicted - s.actual);
    // 예측값을 만원 단위로 반올림하므로 Gap%는 완전히 같지 않다 — 0.2%p 안이면 된다.
    const recomputed = ((s.predicted - s.actual) / s.actual) * 100;
    assert.ok(Math.abs(recomputed - s.gapPct) < 0.2, `${s.jibun}: ${recomputed} vs ${s.gapPct}`);
  }
});

test("판정은 Gap의 부호를 따른다 — ±5% 안은 어느 쪽도 아니다", () => {
  assert.equal(verdictOf(21.4), "저평가 후보");
  assert.equal(verdictOf(5), "저평가 후보");
  assert.equal(verdictOf(4.9), "모델과 비슷");
  assert.equal(verdictOf(-4.9), "모델과 비슷");
  assert.equal(verdictOf(-5), "고평가 후보");
  assert.equal(verdictOf(-16.8), "고평가 후보");
});

test("색 구간은 경계에서 갈린다", () => {
  assert.equal(gapBand(15).label, "저평가 후보");
  assert.equal(gapBand(14.9).label, "약한 저평가");
  assert.equal(gapBand(0).label, "모델과 비슷");
  assert.equal(gapBand(-15).label, "약한 고평가");
  assert.equal(gapBand(-15.1).label, "고평가 후보");
});

test("고평가 쪽에 빨강을 쓰지 않는다 — anomaly이지 '나쁜 땅'이 아니다", () => {
  // 빨강은 고평가를 "나쁜 땅"으로 읽히게 한다. 어느 구간도 빨강이 아니어야 하고,
  // 빨강 판정은 색이 붉은지가 아니라 R이 G·B를 압도하는지로 본다.
  for (const b of GAP_BANDS) {
    const [r, g, bl] = [1, 3, 5].map((i) => parseInt(b.color.slice(i, i + 2), 16));
    assert.ok(!(r > 180 && g < 110 && bl < 110), `${b.label}: ${b.color}`);
  }
  assert.notEqual(gapBand(-30).color, gapBand(0).color);
});

test("고정 시드 — 두 번 만들어도 같은 무대다", () => {
  assert.deepEqual(buildDemoSites(), buildDemoSites());
});

test("시나리오가 자동으로 누르는 지점은 저평가 필지다", () => {
  const hero = heroSite(buildDemoSites());
  assert.ok(hero.gapPct > 0, `${hero.jibun}: ${hero.gapPct}`);
  assert.equal(verdictOf(hero.gapPct), "저평가 후보");
});

test("데모 무대에 저평가와 고평가가 둘 다 있다 — 한쪽만 있으면 색 범례가 거짓말이다", () => {
  const sites = buildDemoSites();
  assert.ok(sites.some((s) => verdictOf(s.gapPct) === "저평가 후보"));
  assert.ok(sites.some((s) => verdictOf(s.gapPct) === "고평가 후보"));
});

test("미래 예측은 현재에서 시작하고 지평이 길수록 누적 상승률이 크다", () => {
  for (const s of buildDemoSites()) {
    assert.deepEqual(
      s.future.map((f) => f.horizon),
      [0, 1, 3, 5],
      s.jibun,
    );
    const [now, y1, y3, y5] = s.future;
    assert.equal(now.pct, 0);
    assert.equal(now.price, s.actual);
    assert.ok(y1.pct < y3.pct && y3.pct < y5.pct, s.jibun);
    // 해가 갈수록 값도 커져야 한다 — %와 금액이 따로 놀면 표가 어긋난다.
    assert.ok(y1.price < y3.price && y3.price < y5.price, s.jibun);
  }
});

test("유망입지는 '주변보다 더'다 — 초과성장은 두 상승률의 차다", () => {
  for (const s of buildDemoSites()) {
    assert.ok(Math.abs(s.excessGrowthPp - (s.growth3y - s.peerGrowth3y)) < 0.05, s.jibun);
    assert.ok(s.hotspotScore >= 0 && s.hotspotScore <= 100, s.jibun);
    assert.ok(s.percentile >= 0 && s.percentile <= 100, s.jibun);
  }
});

test("3년 전망은 미래전망 탭과 유망입지 탭에서 같은 값이다", () => {
  for (const s of buildDemoSites()) {
    const y3 = s.future.find((f) => f.horizon === 3);
    assert.ok(y3);
    assert.ok(Math.abs(y3.pct - s.growth3y) < 0.05, `${s.jibun}: ${y3.pct} vs ${s.growth3y}`);
  }
});

test("지점과 비교 필지는 신촌권 안에 있다 — 좌표가 뒤집히면 지도가 딴 데로 날아간다", () => {
  const near = (lat: number, lng: number) =>
    Math.abs(lat - DEMO_CENTER.lat) < 0.03 && Math.abs(lng - DEMO_CENTER.lng) < 0.03;

  for (const s of buildDemoSites()) {
    assert.ok(near(s.lat, s.lng), `${s.jibun} ${s.lat},${s.lng}`);
    assert.ok(s.comparables.length >= 3, s.jibun);
    for (const c of s.comparables) assert.ok(near(c.lat, c.lng), `${s.jibun} → ${c.name}`);
  }
});

test("비교군 중앙값은 비교 필지 값들 사이에 있다", () => {
  for (const s of buildDemoSites()) {
    const prices = s.comparables.map((c) => c.price);
    const median = comparableMedian(s);
    assert.ok(median >= Math.min(...prices) && median <= Math.max(...prices), s.jibun);
  }
});

test("토지가치분석은 업종을 묻지 않고 상권분석만 묻는다", () => {
  const byId = Object.fromEntries(ANALYSIS_KINDS.map((k) => [k.id, k]));
  assert.equal(byId.land.needsIndustry, false);
  assert.equal(byId.trade.needsIndustry, true);
});

test("표기 — 부호를 항상 붙이고 백분위 0을 '상위 100%'로 쓰지 않는다", () => {
  assert.equal(signedPct(21.4), "+21.4%");
  assert.equal(signedPct(-16.8), "−16.8%");
  assert.equal(signedPp(9.2), "+9.2%p");
  assert.equal(percentileText(0), "하위 1%");
  assert.equal(percentileText(100), "상위 1%");
  assert.equal(percentileText(89), "상위 11%");
  assert.equal(wonPerM2(12_400_000), "1,240만원/㎡");
});
