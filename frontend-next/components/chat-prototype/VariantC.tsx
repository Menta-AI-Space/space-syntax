"use client";

// PROTOTYPE — throwaway. Variant C: 기존 대시보드 위에 떠 있는 챗 버블/패널.
// 상시 사이드바나 분할 화면 없이, 대시보드를 보다가 필요할 때만 챗을 부른다.
// 모바일 앱처럼 하단 탭 내비게이션을 쓴다.

import { useState } from "react";
import { ThemeToggle } from "./ThemeToggle";
import { MOCK_SCORE_CARD, MOCK_SUGGESTIONS, MOCK_THREAD } from "./mockConversation";

const TABS = [
  { key: "home", label: "홈", icon: "🏠" },
  { key: "analyze", label: "분석", icon: "📍" },
  { key: "watchlist", label: "관심목록", icon: "⭐" },
  { key: "me", label: "설정", icon: "⚙️" },
];

export function VariantC() {
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [open, setOpen] = useState(true);
  const [activeTab, setActiveTab] = useState("analyze");

  return (
    <div className="chat-proto cp-c" data-theme={theme}>
      <div className="cp-c__backdrop">
        <div className="cp-c__backdrop-card">
          <div style={{ fontSize: "0.8rem", color: "var(--cp-text-faint)", marginBottom: 8 }}>
            {MOCK_SCORE_CARD.title}
          </div>
          <div style={{ fontSize: "2rem", fontWeight: 700, color: "var(--cp-accent)" }}>
            {MOCK_SCORE_CARD.score}점
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginTop: 16 }}>
            {MOCK_SCORE_CARD.metrics.map((m) => (
              <div key={m.label}>
                <div style={{ fontSize: "0.68rem", color: "var(--cp-text-faint)" }}>{m.label}</div>
                <div style={{ fontSize: "0.88rem", fontWeight: 600 }}>{m.value}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="cp-c__topnav">
        <span style={{ fontWeight: 700 }}>Space Syntax</span>
        <ThemeToggle theme={theme} onToggle={() => setTheme((t) => (t === "light" ? "dark" : "light"))} />
      </div>

      {open ? (
        <div className="cp-c__panel">
          <div className="cp-c__panel-header">
            <span style={{ fontWeight: 600, fontSize: "0.85rem" }}>💬 상권 어시스턴트</span>
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="챗 닫기"
              style={{ border: "none", background: "none", color: "var(--cp-text-muted)", fontSize: "1rem" }}
            >
              ✕
            </button>
          </div>
          <div className="cp-c__panel-thread">
            {MOCK_THREAD.map((m, i) => (
              <div key={i} className={`cp-message-row cp-message-row--${m.role}`}>
                <div className="cp-avatar">{m.role === "user" ? "나" : "AI"}</div>
                <div className="cp-bubble">{m.text}</div>
              </div>
            ))}
          </div>
          <div className="cp-c__chips">
            {MOCK_SUGGESTIONS.map((s) => (
              <button key={s} type="button" className="cp-suggestion-chip">
                {s}
              </button>
            ))}
          </div>
          <div className="cp-c__panel-composer">
            <div className="cp-input-bar">
              <textarea rows={1} placeholder="메시지 입력..." />
              <button type="button" className="cp-input-bar__send" aria-label="전송">
                ↑
              </button>
            </div>
          </div>
        </div>
      ) : (
        <button type="button" className="cp-c__launcher" onClick={() => setOpen(true)} aria-label="챗 열기">
          💬
        </button>
      )}

      <div className="cp-c__bottomnav">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            className={`cp-c__bottomnav-item${activeTab === tab.key ? " cp-c__bottomnav-item--active" : ""}`}
            onClick={() => setActiveTab(tab.key)}
          >
            <span>{tab.icon}</span>
            <span>{tab.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
