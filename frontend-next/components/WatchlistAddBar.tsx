"use client";

import { useEffect, useState } from "react";
import { addToWatchlist, type LastAnalyzedLocation, type WatchlistItem } from "@/lib/watchlist";

interface WatchlistAddBarProps {
  lastAnalyzedLocation: LastAnalyzedLocation | null;
  watchlist: WatchlistItem[];
  onWatchlistChange: (next: WatchlistItem[]) => void;
}

// Ported from the add-bar handlers in frontend/js/watchlist.js
// (`updateWatchlistAddUI`/`showWatchlistAddFeedback`/the `watchlist-add-btn`
// click handler). Shown once a location has been analyzed (site-score run),
// matching Vanilla's `getLastAnalyzedLocation()` gate.
export function WatchlistAddBar({ lastAnalyzedLocation, watchlist, onWatchlistChange }: WatchlistAddBarProps) {
  const [feedback, setFeedback] = useState<{ message: string; tone: "success" | "error" } | null>(null);

  useEffect(() => {
    if (!feedback || feedback.tone !== "success") return;
    const timer = window.setTimeout(() => setFeedback(null), 3000);
    return () => window.clearTimeout(timer);
  }, [feedback]);

  if (!lastAnalyzedLocation) return null;

  const label =
    lastAnalyzedLocation.address ||
    `${lastAnalyzedLocation.lat.toFixed(5)}, ${lastAnalyzedLocation.lng.toFixed(5)}`;

  function handleAdd() {
    if (!lastAnalyzedLocation) return;
    const { watchlist: next, added } = addToWatchlist(watchlist, lastAnalyzedLocation);
    if (added) {
      onWatchlistChange(next);
      setFeedback({ message: "관심 목록에 추가했습니다.", tone: "success" });
    } else {
      setFeedback({ message: "이미 관심 목록에 있는 위치입니다.", tone: "error" });
    }
  }

  return (
    <div id="watchlist-add-bar" className="watchlist-add-bar">
      <p id="site-panel-location" className="site-panel-meta">선택 위치: {label}</p>
      <button type="button" id="watchlist-add-btn" className="watchlist-add-btn" onClick={handleAdd}>
        관심 목록에 추가
      </button>
      {feedback && (
        <span
          id="watchlist-add-feedback"
          className={`watchlist-add-feedback watchlist-add-feedback--${feedback.tone}`}
        >
          {feedback.message}
        </span>
      )}
    </div>
  );
}
