"use client";

// PROTOTYPE — throwaway. Variant B: Claude식 — 왼쪽 챗, 오른쪽에 분석 결과가
// "캔버스"로 펼쳐지는 분할 화면. 채팅은 짧게, 숫자·차트는 오른쪽 패널이 담당한다.

import { useState } from "react";
import { ThemeToggle } from "./ThemeToggle";
import { MOCK_SCORE_CARD, MOCK_THREAD } from "./mockConversation";

export function VariantB() {
  const [theme, setTheme] = useState<"light" | "dark">("light");

  return (
    <div className="chat-proto cp-b" data-theme={theme}>
      <section className="cp-b__chat-pane">
        <div className="cp-b__topbar">
          <span className="cp-b__brand">🧭 상권 어시스턴트</span>
          <ThemeToggle theme={theme} onToggle={() => setTheme((t) => (t === "light" ? "dark" : "light"))} />
        </div>

        <div className="cp-b__thread">
          {MOCK_THREAD.map((m, i) => (
            <div key={i} className={`cp-message-row cp-message-row--${m.role}`}>
              <div className="cp-avatar">{m.role === "user" ? "나" : "AI"}</div>
              <div className="cp-bubble">{m.text}</div>
            </div>
          ))}
        </div>

        <div className="cp-b__composer">
          <div className="cp-input-bar">
            <textarea rows={1} placeholder="자리를 입력하거나 질문해보세요..." />
            <button type="button" className="cp-input-bar__send" aria-label="전송">
              ↑
            </button>
          </div>
        </div>
      </section>

      <section className="cp-b__canvas">
        <div className="cp-b__canvas-header">
          <span>{MOCK_SCORE_CARD.title}</span>
          <span>실시간 업데이트됨</span>
        </div>
        <div className="cp-b__canvas-body">
          <div className="cp-b__canvas-card">
            <div className="cp-b__canvas-title">종합 입지 점수</div>
            <div className="cp-b__canvas-bignum">{MOCK_SCORE_CARD.score}점</div>
          </div>

          <div className="cp-b__canvas-card">
            <div className="cp-b__canvas-title" style={{ marginBottom: 12 }}>
              지표 상세
            </div>
            <div className="cp-b__bars">
              {MOCK_SCORE_CARD.metrics.map((m, i) => (
                <div key={m.label} className="cp-b__bar-row">
                  <span>{m.label}</span>
                  <div className="cp-b__bar-track">
                    <div className="cp-b__bar-fill" style={{ width: `${70 - i * 8}%` }} />
                  </div>
                  <span>{m.value}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
