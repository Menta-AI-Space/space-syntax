"use client";

// 왼쪽 대화 세션 패널 — Prism AI Chat 킷의 aside를 그대로 옮긴 것.
// 목록은 실제 데이터다(lib/chatSessions.ts, localStorage). 원본과 다른 점 둘:
// - 사용자 카드(김채연·Pro 플랜) 자리에는 계정이 없으므로 저장 위치를 적는다.
//   있지도 않은 계정을 그려 두면 로그인된 것처럼 읽힌다.
// - 원본에 없는 "지우기"를 항목마다 넣었다. 40개까지 쌓이는 목록이라 손으로
//   지울 방법이 없으면 곤란하다.

import { useState } from "react";
import { ChatIcon } from "@/components/agent/ChatIcon";
import { groupChatSessions, type ChatSession } from "@/lib/chatSessions";

export function ChatSidebar({
  sessions,
  activeId,
  onSelect,
  onNew,
  onDelete,
  open = false,
  onClose,
}: {
  sessions: ChatSession[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  /** 뜬 질의창(목록을 접어 두는 곳)에서 「지난 대화」로 펼쳤을 때. */
  open?: boolean;
  onClose?: () => void;
}) {
  const [searching, setSearching] = useState(false);
  const [term, setTerm] = useState("");

  const needle = term.trim().toLowerCase();
  const filtered = needle
    ? sessions.filter(
        (s) =>
          s.title.toLowerCase().includes(needle) ||
          s.turns.some((t) => t.query.toLowerCase().includes(needle)),
      )
    : sessions;
  const groups = groupChatSessions(filtered);

  return (
    <aside className={`prism-side${open ? " prism-side--open" : ""}`} aria-label="대화 목록">
      <div className="prism-side__brand">
        <span className="prism-side__logo" aria-hidden="true">
          <ChatIcon name="Chat" size={16} />
        </span>
        <span className="prism-side__name">{onClose ? "지난 대화" : "상권 어시스턴트"}</span>
        {onClose && (
          <button type="button" className="prism-side__close" onClick={onClose}>
            닫기
          </button>
        )}
      </div>

      <div className="prism-side__actions">
        <button type="button" className="prism-side__new" onClick={onNew}>
          <ChatIcon name="Plus" size={16} />
          <span>새 대화</span>
        </button>
        {searching ? (
          <div className="prism-side__search">
            <ChatIcon name="Search" size={16} />
            <input
              autoFocus
              value={term}
              onChange={(e) => setTerm(e.target.value)}
              onBlur={() => {
                if (!term.trim()) setSearching(false);
              }}
              placeholder="대화 검색"
              aria-label="대화 검색"
            />
          </div>
        ) : (
          <button type="button" className="prism-side__search-open" onClick={() => setSearching(true)}>
            <ChatIcon name="Search" size={16} />
            <span>검색</span>
          </button>
        )}
      </div>

      <div className="prism-side__list">
        {groups.length === 0 ? (
          <p className="prism-side__empty">
            {needle
              ? "찾는 대화가 없습니다."
              : onClose
                ? "아직 대화가 없습니다."
                : "아직 대화가 없습니다. 오른쪽에 질문을 적어 보세요."}
          </p>
        ) : (
          groups.map((g) => (
            <div key={g.label} className="prism-side__group">
              <div className="prism-side__group-label">{g.label}</div>
              {g.items.map((s) => (
                <div
                  key={s.id}
                  className={`prism-side__item${s.id === activeId ? " prism-side__item--active" : ""}`}
                >
                  <button type="button" className="prism-side__item-open" onClick={() => onSelect(s.id)}>
                    {s.title}
                  </button>
                  <button
                    type="button"
                    className="prism-side__item-del"
                    onClick={() => onDelete(s.id)}
                    aria-label={`${s.title} 대화 지우기`}
                    title="이 대화 지우기"
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          ))
        )}
      </div>

      <div className="prism-side__foot">
        <span className="prism-side__avatar" aria-hidden="true">
          <ChatIcon name="User" size={16} />
        </span>
        <span className="prism-side__foot-text">
          <span className="prism-side__foot-title">이 브라우저에 저장</span>
          <span className="prism-side__foot-sub">계정 없이 로컬에만 남습니다</span>
        </span>
      </div>
    </aside>
  );
}
