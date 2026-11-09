// 질의창 대화 세션 — 왼쪽 패널이 목록으로 보여 주는 실제 데이터.
//
// 브라우저 localStorage에만 저장한다. 서버에 대화를 저장하는 기능이 없고(계정도
// 없다), 그걸 기다리며 목록을 비워 두면 패널이 장식이 된다. 저장 범위를 화면에
// 그대로 밝힌다 — "이 브라우저에만 남습니다".
//
// 담는 것: 질문, 한 줄 결론, 답변 서술(마크다운), 백엔드에 넘기는 앞대화
// 요약(history). 점수·지도·차트 같은 구조화된 보고서 전문(AgentAskResponse)은
// 이 localStorage 목록에 섞지 않는다. 대신 대화별 최신 1건만 chatResultSnapshots.ts가
// IndexedDB에 보관한다. 모든 턴을 쌓으면 localStorage 한도(≈5MB)를 금방 건드린다.

import type { AgentHistoryTurn } from "@/lib/api";
import type { StructuredAnalysis } from "@/lib/landAnalysis";

export interface ChatSessionTurn {
  query: string;
  verdict: string;
  /** 그 턴에 받은 서술(마크다운). 대화를 다시 열면 이걸 그대로 그린다.
   *  없을 수 있다 — 서술 단계가 실패한 턴, 그리고 이 필드가 생기기 전에 저장된
   *  대화. 그 경우 한 줄 결론(verdict)만 보인다. */
  answer?: string;
  /** 필지를 눌러 스레드에 들어온 판정 카드.
   *
   *  **저장하지 않는다** — `saveChatSessions`가 걷어낸다. 판정 하나가 수 KB라
   *  턴마다 쌓으면 localStorage 한도(약 5MB)를 이 필드 하나가 다 먹는다. 대화를
   *  다시 열면 카드 대신 그때의 서술(`answer`)이 남는다. */
  landAnalysis?: StructuredAnalysis;
}

export interface ChatSession {
  id: string;
  /** 목록에 보이는 이름 — 그 대화의 첫 질문. */
  title: string;
  updatedAt: number;
  turns: ChatSessionTurn[];
  history: AgentHistoryTurn[];
}

const KEY = "space-syntax.chat-sessions.v1";
/** 목록이 무한히 길어지지 않게 — 넘치면 오래된 것부터 버린다. */
const MAX_SESSIONS = 40;

export function loadChatSessions(): ChatSession[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isSession).sort((a, b) => b.updatedAt - a.updatedAt);
  } catch {
    // 저장값이 깨졌거나 localStorage가 막힌 브라우저 — 목록만 비면 되고,
    // 대화 자체는 그대로 할 수 있어야 한다.
    return [];
  }
}

/** 저장 전에 무거운 필드를 걷는다. 화면의 turns는 그대로 두고 사본만 줄인다. */
function forStorage(session: ChatSession): ChatSession {
  return {
    ...session,
    turns: session.turns.map((turn) => {
      const stored = { ...turn };
      delete stored.landAnalysis;
      return stored;
    }),
  };
}

export function saveChatSessions(sessions: ChatSession[]): ChatSession[] {
  const trimmed = [...sessions].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_SESSIONS);
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(trimmed.map(forStorage)));
    } catch {
      /* 사파리 프라이빗 모드 등 — 저장만 안 되고 화면은 그대로 돈다 */
    }
  }
  return trimmed;
}

/** 같은 id가 있으면 갈아끼우고 없으면 새로 넣는다. 반환값이 새 목록이다. */
export function upsertChatSession(sessions: ChatSession[], next: ChatSession): ChatSession[] {
  const rest = sessions.filter((s) => s.id !== next.id);
  return saveChatSessions([next, ...rest]);
}

export function removeChatSession(sessions: ChatSession[], id: string): ChatSession[] {
  return saveChatSessions(sessions.filter((s) => s.id !== id));
}

export function newChatSessionId(): string {
  // crypto.randomUUID는 구형 사파리에 없다 — 없으면 시간+난수로 충분하다.
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export interface ChatSessionGroup {
  label: string;
  items: ChatSession[];
}

/** 오늘·어제·지난 7일·이전 네 묶음. 빈 묶음은 내보내지 않는다. */
export function groupChatSessions(sessions: ChatSession[], now: number = Date.now()): ChatSessionGroup[] {
  const startOfToday = new Date(now);
  startOfToday.setHours(0, 0, 0, 0);
  const today = startOfToday.getTime();
  const yesterday = today - 86_400_000;
  const week = today - 6 * 86_400_000;

  const buckets: ChatSessionGroup[] = [
    { label: "오늘", items: [] },
    { label: "어제", items: [] },
    { label: "지난 7일", items: [] },
    { label: "이전", items: [] },
  ];
  for (const s of [...sessions].sort((a, b) => b.updatedAt - a.updatedAt)) {
    if (s.updatedAt >= today) buckets[0].items.push(s);
    else if (s.updatedAt >= yesterday) buckets[1].items.push(s);
    else if (s.updatedAt >= week) buckets[2].items.push(s);
    else buckets[3].items.push(s);
  }
  return buckets.filter((b) => b.items.length > 0);
}

function isSession(value: unknown): value is ChatSession {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.id === "string" &&
    typeof v.title === "string" &&
    typeof v.updatedAt === "number" &&
    Array.isArray(v.turns) &&
    Array.isArray(v.history)
  );
}
