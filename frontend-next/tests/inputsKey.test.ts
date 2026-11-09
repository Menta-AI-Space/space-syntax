import { test } from "node:test";
import * as assert from "node:assert/strict";

import type { UpjongSelection } from "../components/BusinessPicker";
import { compareInputsKey, pointKey, siteInputsKey } from "../lib/inputsKey.ts";

const cafe: UpjongSelection = {
  label: "커피전문점",
  breadcrumb: "음식 > 커피전문점",
  business_type: "cafe",
  inds_lcls_cd: "I2",
  inds_mcls_cd: "I212",
  inds_scls_cd: "I21201",
};

test("반경이 바뀌면 서명이 바뀐다", () => {
  // 결과 헤더는 현재 반경을 그대로 적는다. 반경이 바뀌었는데 서명이 같으면
  // 500m로 뽑은 표 위에 "반경 1000m"라고 적힌 채로 남는다.
  assert.notEqual(compareInputsKey(cafe, 500), compareInputsKey(cafe, 1000));
});

test("업종이 바뀌면 서명이 바뀐다", () => {
  const bakery: UpjongSelection = { ...cafe, business_type: "bakery", inds_scls_cd: "I21202" };
  assert.notEqual(compareInputsKey(cafe, 500), compareInputsKey(bakery, 500));
});

test("같은 조건이면 서명이 같다 — 렌더마다 다시 계산하면 안 된다", () => {
  assert.equal(compareInputsKey(cafe, 500), compareInputsKey({ ...cafe }, 500));
});

test("업종 미선택도 안정적인 서명을 낸다", () => {
  assert.equal(compareInputsKey(null, 500), compareInputsKey(null, 500));
  assert.notEqual(compareInputsKey(null, 500), compareInputsKey(cafe, 500));
});

test("코드 경계가 섞이지 않는다", () => {
  // 구분자 없이 이어 붙이면 ("I2","I212")와 ("I2I2","12")가 같은 서명이 된다.
  const a: UpjongSelection = { ...cafe, inds_lcls_cd: "I2", inds_mcls_cd: "I212" };
  const b: UpjongSelection = { ...cafe, inds_lcls_cd: "I2I2", inds_mcls_cd: "12" };
  assert.notEqual(compareInputsKey(a, 500), compareInputsKey(b, 500));
});

// 지점 분석 결과(임대조건 적정성 등)는 그 자리에서 나온 값이다. 다른 자리를
// 분석하고 돌아왔는데 판정이 남아 있으면 신촌 벤치마크를 강남 자리의 판정으로
// 읽게 된다.

test("위치가 바뀌면 지점 서명이 바뀐다", () => {
  const a = siteInputsKey({ lat: 37.5, lng: 127.0, radiusM: 500, upjong: cafe });
  const b = siteInputsKey({ lat: 37.6, lng: 127.0, radiusM: 500, upjong: cafe });
  assert.notEqual(a, b);
});

test("좌표 미세 흔들림으로는 결과를 버리지 않는다", () => {
  // 소수점 6자리(약 10cm) 아래는 같은 자리로 본다 — 아니면 매번 다시 계산한다.
  const a = siteInputsKey({ lat: 37.5000001, lng: 127.0, radiusM: 500, upjong: cafe });
  const b = siteInputsKey({ lat: 37.5000002, lng: 127.0, radiusM: 500, upjong: cafe });
  assert.equal(a, b);
});

test("반경·업종·부가 조건도 서명에 들어간다", () => {
  const base = { lat: 37.5, lng: 127.0, radiusM: 500 as const, upjong: cafe };
  assert.notEqual(siteInputsKey(base), siteInputsKey({ ...base, radiusM: 1000 }));
  assert.notEqual(siteInputsKey(base), siteInputsKey({ ...base, upjong: null }));
  assert.notEqual(siteInputsKey(base), siteInputsKey({ ...base, extra: ["01"] }));
  assert.notEqual(siteInputsKey({ ...base, extra: ["01"] }), siteInputsKey({ ...base, extra: ["11"] }));
});

test("위치가 없으면 서명이 안정적이다", () => {
  const a = siteInputsKey({ lat: null, lng: null, radiusM: 500, upjong: cafe });
  const b = siteInputsKey({ lat: null, lng: null, radiusM: 500, upjong: cafe });
  assert.equal(a, b);
});

test("위치 서명은 자리마다 다르고 미세 흔들림엔 같다", () => {
  assert.notEqual(pointKey({ lat: 37.5, lng: 127.0 }), pointKey({ lat: 37.5, lng: 127.001 }));
  assert.equal(pointKey({ lat: 37.50000001, lng: 127.0 }), pointKey({ lat: 37.5, lng: 127.0 }));
  // 위치가 아직 없을 때(계산만 수행)도 서명이 흔들리면 안 된다.
  assert.equal(pointKey(null), pointKey(undefined));
});
