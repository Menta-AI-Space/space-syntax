"use client";

import { useState } from "react";
import { api, type SiteScoreResponse, type TrendsResponse } from "@/lib/api";
import { buildRecommendationCards } from "./dashboardUtils";


// "임대료 밴드 필터"는 예전엔 area_search처럼 여러 후보지를 임대료로 걸러
// 주는 것으로 들렸지만, 이 채팅은 지금 선택된 지점 하나만 안다 — 실제로는
// 그 지점 하나를 lease_verdict 엔진으로 판정해 준다(chat_consultation.py의
// _rent_band_reply). 칩 문구도 실제로 되는 일과 맞춘다.
const CHIPS = [
  { label: "상권 스캐너 실행", query: "이 위치 상권을 요약해줘" },
  { label: "임대료 조건 판정", query: "월세 300이면 이 자리 계약해도 될까?" },
  { label: "잠식 검사", query: "동일업종 잠식 위험을 설명해줘" },
];

interface ChatMessage { role: "user" | "bot"; text: string }

interface ChatSectionProps {
  trends: TrendsResponse | null;
  siteScore: SiteScoreResponse | null;
  lat: number | null;
  lng: number | null;
  businessType?: string;
}

// Ported from `renderChatSection`/`submitChatMessage`/`bindChatForm` in
// frontend/js/dashboard.js.
export function ChatSection({ trends, siteScore, lat, lng, businessType }: ChatSectionProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const recCards = buildRecommendationCards(trends, siteScore);

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setMessages((m) => [...m, { role: "user", text: trimmed }]);
    setInput("");
    setSending(true);
    try {
      const res = await api.chat({
        messages: [{ role: "user", content: trimmed }],
        lat,
        lng,
        business_type: businessType || "custom",
      });
      setMessages((m) => [...m, { role: "bot", text: res.reply }]);
    } catch (err) {
      setMessages((m) => [...m, { role: "bot", text: `오류: ${err instanceof Error ? err.message : String(err)}` }]);
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="dash-card dash-card--chat">
      <header className="dash-card-header">
        <span className="dash-card-icon" aria-hidden="true">💬</span>
        <h3>대화형 입지 상담</h3>
      </header>
      <div className="chat-log">
        {messages.map((m, i) => (
          <div key={i} className={`chat-msg chat-msg--${m.role}`}>
            <div className={`chat-bubble chat-bubble--${m.role}`}>{m.text}</div>
          </div>
        ))}
      </div>
      <div className="dash-chip-row" role="group" aria-label="빠른 질문">
        {CHIPS.map((c) => (
          <button key={c.query} type="button" className="dash-chip" onClick={() => void send(c.query)} disabled={sending}>{c.label}</button>
        ))}
      </div>
      <p className="dash-footnote">
        ※ 이 채팅은 지금 선택된 지점 하나만 분석합니다 — 여러 후보지를 찾아
        비교하려면 화면 위쪽의 질의창을 이용해 주세요.
      </p>
      <form
        className="chat-form"
        onSubmit={(e) => { e.preventDefault(); void send(input); }}
      >
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="예: 월세 250이면 이 자리 계약해도 될까?"
          autoComplete="off"
          disabled={sending}
        />
        <button type="submit" disabled={sending}>{sending ? "전송 중..." : "전송"}</button>
      </form>
      {recCards.length > 0 && (
        <div className="dash-rec-cards">
          {recCards.map((c, i) => (
            <article key={i} className="dash-rec-card">
              <h5>{c.name}</h5>
              <p className="dash-rec-score"><strong>{c.primary}</strong></p>
              {c.secondary && <p className="dash-rec-rent">{c.secondary}</p>}
            </article>
          ))}
        </div>
      )}
      <p className="dash-footnote">
        ※ 첫 카드는 지금 분석한 지점, 나머지는 이번 분기 성장 신호가 큰 거리입니다.
        임대료는 여기서 추정하지 않습니다 — 「임대조건 적정성」과 「투자 수익성」에서 실거래로 계산합니다.
      </p>
    </section>
  );
}
