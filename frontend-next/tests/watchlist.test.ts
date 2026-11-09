import { test } from "node:test";
import * as assert from "node:assert/strict";

import {
  addToWatchlist,
  loadWatchlistFromStorage,
  saveWatchlistToStorage,
  type WatchlistItem,
} from "../lib/watchlist.ts";

/** localStorage 대역. setItem이 던지는 상황(용량 초과·시크릿 모드)을 재현한다. */
function stubStorage(opts: { raw?: string | null; throwOnSet?: boolean } = {}) {
  const store: Record<string, string> = {};
  (globalThis as Record<string, unknown>).window = {
    localStorage: {
      getItem: () => (opts.raw === undefined ? null : opts.raw),
      setItem: (k: string, v: string) => {
        if (opts.throwOnSet) throw new DOMException("quota", "QuotaExceededError");
        store[k] = v;
      },
    },
  };
  return store;
}

test("저장이 실패해도 예외로 죽지 않는다", () => {
  // setItem은 용량 초과·시크릿 모드에서 던진다. 그걸 그대로 두면 관심목록에
  // 추가하는 동작 자체가 터진다 — lease·profitability는 이미 감싸고 있었다.
  stubStorage({ throwOnSet: true });
  const items: WatchlistItem[] = [];
  assert.doesNotThrow(() => saveWatchlistToStorage(items));
});

test("저장에 성공하면 그대로 기록한다", () => {
  const store = stubStorage();
  saveWatchlistToStorage([{ id: "w-1", address: "신촌", lat: 37.5, lng: 127.0, source: "site" } as WatchlistItem]);
  assert.ok(Object.values(store)[0]?.includes("신촌"));
});

test("손상된 저장값은 빈 목록으로 읽는다", () => {
  // 배열이 아닌 값이 들어 있으면 뒤에서 .map()이 터진다.
  stubStorage({ raw: '{"not":"an array"}' });
  assert.deepEqual(loadWatchlistFromStorage(), []);

  stubStorage({ raw: "깨진 JSON {" });
  assert.deepEqual(loadWatchlistFromStorage(), []);
});

test("같은 좌표는 두 번 담기지 않는다", () => {
  const first = addToWatchlist([], { address: "신촌", lat: 37.5, lng: 127.0 });
  assert.equal(first.added, true);
  const again = addToWatchlist(first.watchlist, { address: "다른 이름", lat: 37.5, lng: 127.0 });
  assert.equal(again.added, false);
  assert.equal(again.watchlist.length, 1);
});
