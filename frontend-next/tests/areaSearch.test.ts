import { test } from "node:test";
import * as assert from "node:assert/strict";

import { areaSearchRegionLabel } from "../lib/api.ts";

// 백엔드(app/routers/goals.py AreaSearchResponse)는 최상위 region_label을 준
// 적이 없다 — 실제 라벨은 center.label에 있다. 화면 칩은 이 필드로 채워져야
// 한다.
test("reads the '어디를 찾았는지' chip label from center.label, matching the backend's actual response shape", () => {
  assert.equal(
    areaSearchRegionLabel({ center: { lat: 37.5, lng: 127.0, label: "성수동", search_radius_m: 800 } }),
    "성수동",
  );
});

test("hides the chip instead of showing an empty one when the backend has no label", () => {
  assert.equal(areaSearchRegionLabel({ center: { lat: 37.5, lng: 127.0, label: "", search_radius_m: 800 } }), undefined);
  assert.equal(areaSearchRegionLabel({ center: { lat: 37.5, lng: 127.0 } }), undefined);
  assert.equal(areaSearchRegionLabel({ center: null }), undefined);
  assert.equal(areaSearchRegionLabel(undefined), undefined);
});

// 예전 버그: 존재하지 않는 최상위 region_label을 읽어서 항상 undefined였다.
// 응답에 region_label이 섞여 들어와도(다른 클라이언트·목업 데이터 등) 그 값을
// 쓰지 않고 center.label만 신뢰해야, 백엔드가 실제로 계산한 값과 화면이 다시
// 어긋나지 않는다.
test("ignores a stray top-level region_label and only trusts center.label", () => {
  const withStrayField = {
    center: { lat: 37.5, lng: 127.0, label: "성수동" },
    region_label: "다른 동네",
  };
  assert.equal(areaSearchRegionLabel(withStrayField), "성수동");
});
