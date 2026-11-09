"use client";

// PROTOTYPE — throwaway. Variant A: ChatGPT식 — 왼쪽 대화 히스토리 사이드바 + 가운데 챗 컬럼.

import { useState } from "react";
import { ThemeToggle } from "./ThemeToggle";
import { MOCK_HISTORY, MOCK_THREAD } from "./mockConversation";

export function VariantA() {
  const [theme, setTheme] = useState<"light" | "dark">("light");

  return (
    <div className="chat-proto cp-a" data-theme={theme}>
      <aside className="cp-a__sidebar">
        <button type="button" className="cp-a__new-chat">
          + 새 입지 분석
        </button>
        <div className="cp-a__history">
          {MOCK_HISTORY.map((h, i) => (
            <div key={h.id} className={`cp-a__history-item${i === 0 ? " cp-a__history-item--active" : ""}`}>
              {h.title}
            </div>
          ))}
        </div>
        <div className="cp-a__sidebar-footer">
          <ThemeToggle theme={theme} onToggle={() => setTheme((t) => (t === "light" ? "dark" : "light"))} />
        </div>
      </aside>

      <main className="cp-a__main">
        <div className="cp-a__topbar">
          <span>신촌역 5번 출구 카페 입지</span>
          <span>공간구문 분석 엔진 v2</span>
        </div>

        <div className="cp-a__thread">
          {MOCK_THREAD.map((m, i) => (
            <div key={i} className={`cp-message-row cp-message-row--${m.role}`}>
              <div className="cp-avatar">{m.role === "user" ? "나" : "AI"}</div>
              <div>
                <div className="cp-bubble">{m.text}</div>
                {m.role === "assistant" && m.scoreCard ? (
                  <div className="cp-score-card">
                    <div className="cp-score-card__header">
                      <span>{m.scoreCard.title}</span>
                      <span className="cp-score-card__score">{m.scoreCard.score}점</span>
                    </div>
                    <div className="cp-score-card__body">
                      {m.scoreCard.metrics.map((metric) => (
                        <div key={metric.label} className="cp-score-card__metric">
                          <span className="cp-score-card__metric-label">{metric.label}</span>
                          <span className="cp-score-card__metric-value">{metric.value}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          ))}
        </div>

        <div className="cp-a__composer">
          <div className="cp-a__composer-inner">
            <div className="cp-input-bar">
              <textarea rows={1} placeholder="궁금한 자리나 상권을 물어보세요..." />
              <button type="button" className="cp-input-bar__send" aria-label="전송">
                ↑
              </button>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
