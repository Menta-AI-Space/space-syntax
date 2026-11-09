import { test } from "node:test";
import * as assert from "node:assert/strict";

import type { AgentAskResponse, AreaSearchResponse } from "../lib/api.ts";
import { errorMessage, withAgentAskAreaSearchRegionLabel, withAreaSearchRegionLabel } from "../lib/api.ts";

// 백엔드 /api/area-search 응답에는 region_label 필드가 아예 없다(내부 변수로만
// 쓰고 안 돌려준다) — 그래서 화면 지역 칩이 항상 빈값이었다. 실제로 이 검색에
// 쓰인 값(요청에 실어 보낸 region_query)을 그대로 옮겨야 한다.

const baseAreaSearch: AreaSearchResponse = {
  headline: "",
  narrative_source: "rule",
  warnings: [],
  candidates: [],
};

test("area-search 응답에 region_label이 없으면 요청에 쓴 region_query로 채운다", () => {
  const filled = withAreaSearchRegionLabel(baseAreaSearch, "신촌");
  assert.equal(filled.region_label, "신촌");
});

test("백엔드가 이미 region_label을 보냈으면 그 값을 덮어쓰지 않는다", () => {
  const withValue: AreaSearchResponse = { ...baseAreaSearch, region_label: "역삼동" };
  const filled = withAreaSearchRegionLabel(withValue, "신촌");
  assert.equal(filled.region_label, "역삼동");
});

test("요청에도 region_query가 없으면 값을 지어내지 않고 null로 둔다", () => {
  const filled = withAreaSearchRegionLabel(baseAreaSearch, "");
  assert.equal(filled.region_label, null);
});

function baseAgentAsk(overrides: Partial<AgentAskResponse> = {}): AgentAskResponse {
  return {
    query: "신촌에서 카페",
    address: "서울 서대문구 신촌동",
    business_type: "cafe",
    business_label: "카페",
    steps: [],
    site: {} as AgentAskResponse["site"],
    answer: "",
    answer_source: "rule",
    tool: "area_search",
    tool_result: { candidates: [] },
    ...overrides,
  };
}

test("agent/ask가 area_search 도구를 돌렸으면 tool_result.region_label을 응답의 address로 채운다", () => {
  const filled = withAgentAskAreaSearchRegionLabel(baseAgentAsk());
  assert.equal((filled.tool_result as { region_label?: unknown })?.region_label, "서울 서대문구 신촌동");
});

test("area_search가 아닌 다른 도구는 tool_result를 건드리지 않는다", () => {
  const original = baseAgentAsk({ tool: "decide", tool_result: { verdict: "good" } });
  const result = withAgentAskAreaSearchRegionLabel(original);
  assert.equal(result, original);
  assert.equal((result.tool_result as { region_label?: unknown }).region_label, undefined);
});

test("tool_result가 없으면 아무 것도 만들어내지 않는다", () => {
  const original = baseAgentAsk({ tool_result: null });
  const result = withAgentAskAreaSearchRegionLabel(original);
  assert.equal(result.tool_result, null);
});

test("tool_result에 이미 region_label이 있으면 address로 덮어쓰지 않는다", () => {
  const original = baseAgentAsk({ tool_result: { candidates: [], region_label: "이미 있는 값" } });
  const result = withAgentAskAreaSearchRegionLabel(original);
  assert.equal((result.tool_result as { region_label?: unknown }).region_label, "이미 있는 값");
});

// 지도 분석 탭은 streetGrades에 signal을 넘긴다. 전엔 signal이 있으면 20초 제한이 안 걸려,
// 서버가 답을 안 주면 「길을 불러오는 중…」에서 영영 멈췄다 (2026-11-07 UX 자동 개선).
function hangingFetch(): typeof fetch {
  return ((_url: unknown, init?: RequestInit) =>
    new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    })) as typeof fetch;
}

test("signal을 넘겨도 시간 제한이 지나면 「요청 시간이 초과」로 끝난다", async (t) => {
  const { api } = await import("../lib/api.ts");
  const realFetch = globalThis.fetch;
  globalThis.fetch = hangingFetch();
  t.mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const pending = api.streetGrades({ west: 126.9, south: 37.5, east: 127, north: 37.6 }, new AbortController().signal);
    t.mock.timers.tick(20_000);
    await assert.rejects(pending, /요청 시간이 초과/);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("호출한 쪽이 끊으면 시간 초과 문장이 아니라 원래 중단 오류로 끝난다", async () => {
  const { api } = await import("../lib/api.ts");
  const realFetch = globalThis.fetch;
  globalThis.fetch = hangingFetch();
  try {
    const caller = new AbortController();
    const pending = api.streetGrades({ west: 126.9, south: 37.5, east: 127, north: 37.6 }, caller.signal);
    caller.abort();
    await assert.rejects(pending, (err: unknown) => err instanceof DOMException && err.name === "AbortError");
  } finally {
    globalThis.fetch = realFetch;
  }
});

// 배포 중 Railway가 HTML 503을 돌려주면 본문에 detail이 없다 — 숫자 대신 할 일을 말한다.
test("errorMessage: detail 없는 서버 실패는 숫자 대신 다시 시도 안내", () => {
  const m503 = errorMessage(null, 503);
  assert.ok(!m503.includes("503"));
  assert.match(m503, /잠시 후 다시 시도/);
  assert.ok(!errorMessage(null, 404).includes("404"));
  assert.equal(errorMessage({ detail: "'부산'은 서울이 아니에요" }, 400), "'부산'은 서울이 아니에요");
});
