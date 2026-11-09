import { test } from "node:test";
import * as assert from "node:assert/strict";

import { directionFrom, pickTopStreets } from "../lib/topStreets.ts";
import type { AnalyzeResponse } from "../lib/api.ts";

// 경도 0.0015 ≈ 132m (위도 37.5에서). 한 칸씩 떨어진 길을 만든다.
function seg(i: number, choice: number, integ: number, highway = "residential", lat = 37.5) {
  const lng = 127 + i * 0.0015;
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: [[lng, lat], [lng + 0.0002, lat]] as [number, number][] },
    properties: { osm_id: i, highway, length_m: 40, walkable: true, choice_walk: choice, integration_walk: integ },
  };
}

function data(features: ReturnType<typeof seg>[]): AnalyzeResponse {
  return { center: { lat: 37.5, lng: 127 }, radius_m: 500, segment_count: features.length, segments: { type: "FeatureCollection", features } };
}

test("ranks by choice and integration together, best first", () => {
  const picks = pickTopStreets(data([seg(0, 0.1, 0.1), seg(1, 0.9, 0.9), seg(2, 0.5, 0.5), seg(3, 0.8, 0.2)]));
  assert.deepEqual(picks.map((p) => p.osmId), [1, 2, 3]);
  assert.equal(picks[0].rank, 1);
  assert.equal(picks[0].kind, "골목");
});

test("skips service roads and footways but still uses them for percentiles", () => {
  const picks = pickTopStreets(data([seg(0, 1, 1, "service"), seg(1, 0.5, 0.5), seg(2, 0.2, 0.2)]), 3);
  assert.deepEqual(picks.map((p) => p.osmId), [1, 2]);
});

test("does not return three pieces of the same street", () => {
  // 0·1·2는 서로 20m 떨어진 같은 길 — 하나만 남아야 한다.
  const close = [0, 1, 2].map((i) => {
    const s = seg(0, 0.9 - i * 0.01, 0.9 - i * 0.01);
    s.properties.osm_id = 100 + i;
    s.geometry.coordinates = [[127 + i * 0.0002, 37.5], [127 + i * 0.0002 + 0.0001, 37.5]];
    return s;
  });
  const picks = pickTopStreets(data([...close, seg(3, 0.5, 0.5), seg(4, 0.4, 0.4)]));
  assert.equal(picks.length, 3);
  assert.equal(picks[0].osmId, 100);
  assert.deepEqual(picks.slice(1).map((p) => p.osmId), [3, 4]);
});

test("top percent is at least 1 and empty input gives nothing", () => {
  const picks = pickTopStreets(data(Array.from({ length: 200 }, (_, i) => seg(i, i / 200, i / 200))));
  assert.equal(picks[0].topPct, 1);
  assert.deepEqual(pickTopStreets(data([])), []);
});

test("direction from the searched place reads like a person would say it", () => {
  const c = { lat: 37.5, lng: 127 };
  assert.equal(directionFrom(c, { lat: 37.5 + 200 / 111_320, lng: 127 }), "북쪽 200m");
  assert.equal(directionFrom(c, { lat: 37.5, lng: 127 - 0.0015 }), "서쪽 130m");
  assert.equal(directionFrom(c, { lat: 37.5 - 0.001, lng: 127 + 0.00126 }), "남동쪽 160m");
  assert.equal(directionFrom(c, { lat: 37.5001, lng: 127 }), "바로 앞");
});
