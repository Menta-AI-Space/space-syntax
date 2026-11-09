// Ported from frontend/js/watchlist.js. Storage format is preserved exactly
// (same localStorage key + item shape) so existing users' saved watchlists
// still load after the Next.js migration.

export const WATCHLIST_STORAGE_KEY = "space-syntax-watchlist";
export const WATCHLIST_SOURCE_SITE = "site-analysis";

/** 딜 단계 — GrowthFactor Deal Dashboard의 축소판(GROWTHFACTOR_GAP P2).
 * 관심목록이 "저장한 주소 목록"에서 "검토 중인 딜 목록"이 되려면 각 자리가
 * 지금 어디까지 왔는지가 있어야 한다. */
export const DEAL_STAGES = ["reviewing", "negotiating", "contracted", "hold"] as const;
export type DealStage = (typeof DEAL_STAGES)[number];
export const DEAL_STAGE_LABELS: Record<DealStage, string> = {
  reviewing: "검토중",
  negotiating: "협상중",
  contracted: "계약함",
  hold: "보류",
};

export interface WatchlistItem {
  id: string;
  address: string;
  lat: number;
  lng: number;
  source: string;
  // 아래는 딜 파이프라인 필드 — 옛 저장분에는 없으므로 전부 선택형이다.
  // (저장 포맷을 깨면 기존 사용자의 관심목록이 통째로 사라진다.)
  stage?: DealStage;
  memo?: string;
  updated_at?: string;
}

/** 옛 저장분(stage 없음)은 검토중으로 본다 — 저장은 안 바꾸고 읽을 때만. */
export function stageOf(item: WatchlistItem): DealStage {
  return item.stage && (DEAL_STAGES as readonly string[]).includes(item.stage)
    ? item.stage
    : "reviewing";
}

export function setItemStage(
  watchlist: WatchlistItem[], id: string, stage: DealStage
): WatchlistItem[] {
  const next = watchlist.map((w) =>
    w.id === id ? { ...w, stage, updated_at: new Date().toISOString() } : w
  );
  saveWatchlistToStorage(next);
  return next;
}

export function setItemMemo(
  watchlist: WatchlistItem[], id: string, memo: string
): WatchlistItem[] {
  const next = watchlist.map((w) =>
    w.id === id ? { ...w, memo: memo.slice(0, 500), updated_at: new Date().toISOString() } : w
  );
  saveWatchlistToStorage(next);
  return next;
}

/** 단계별 개수 — "검토중 3 · 협상중 1" 요약 줄에 쓴다. */
export function stageCounts(watchlist: WatchlistItem[]): Record<DealStage, number> {
  const counts = { reviewing: 0, negotiating: 0, contracted: 0, hold: 0 } as Record<DealStage, number>;
  for (const w of watchlist) counts[stageOf(w)] += 1;
  return counts;
}

export interface LastAnalyzedLocation {
  address?: string;
  lat: number;
  lng: number;
}

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

/** Ported from `loadWatchlistFromStorage`. */
export function loadWatchlistFromStorage(): WatchlistItem[] {
  if (!isBrowser()) return [];
  try {
    const raw = window.localStorage.getItem(WATCHLIST_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

/** Ported from `saveWatchlistToStorage`. */
export function saveWatchlistToStorage(watchlist: WatchlistItem[]): void {
  if (!isBrowser()) return;
  try {
    window.localStorage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(watchlist));
  } catch {
    // setItem은 용량 초과·시크릿 모드에서 던진다. 저장에 실패했다고 관심목록
    // 추가 자체가 예외로 죽으면 안 된다 — 이번 세션에는 목록이 남고 다음에
    // 다시 저장을 시도한다. 다른 저장 지점(lease·profitability)도 같은 처리다.
  }
}

/** Ported from `getSiteAnalysisWatchlist`. */
export function getSiteAnalysisWatchlist(watchlist: WatchlistItem[]): WatchlistItem[] {
  return watchlist.filter((w) => w.source === WATCHLIST_SOURCE_SITE);
}

/** Ported from `addToWatchlist`. Returns the next list plus whether an item was added
 * (Vanilla returns `false` when the exact lat/lng pair is already saved). */
export function addToWatchlist(
  watchlist: WatchlistItem[],
  { address, lat, lng, source = WATCHLIST_SOURCE_SITE }: { address?: string | null; lat: number; lng: number; source?: string }
): { watchlist: WatchlistItem[]; added: boolean } {
  if (watchlist.some((w) => w.lat === lat && w.lng === lng)) {
    return { watchlist, added: false };
  }
  const item: WatchlistItem = {
    id: `w-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    address: address || `${lat.toFixed(5)}, ${lng.toFixed(5)}`,
    lat,
    lng,
    source,
  };
  const next = [...watchlist, item];
  saveWatchlistToStorage(next);
  return { watchlist: next, added: true };
}

/** Ported from `removeFromWatchlist`. */
export function removeFromWatchlist(watchlist: WatchlistItem[], id: string): WatchlistItem[] {
  const next = watchlist.filter((w) => w.id !== id);
  saveWatchlistToStorage(next);
  return next;
}

/** Ported from `clearWatchlist`. */
export function clearWatchlist(): WatchlistItem[] {
  saveWatchlistToStorage([]);
  return [];
}

/** Ported from `clearSiteAnalysisWatchlist`. */
export function clearSiteAnalysisWatchlist(watchlist: WatchlistItem[]): WatchlistItem[] {
  const next = watchlist.filter((w) => w.source !== WATCHLIST_SOURCE_SITE);
  saveWatchlistToStorage(next);
  return next;
}
