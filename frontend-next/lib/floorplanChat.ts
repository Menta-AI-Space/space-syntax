// 도면 상담(2026-11-09) — 화면 상태와 요청 사이의 순수 함수들. 컴포넌트에서 떼어 둬 테스트한다.

import type { FloorplanChatMessage, FloorplanIssue, FloorplanSummary } from "@/lib/api";

/** 화면에 두는 한 줄. card는 답이 가리키는 카드 번호(1부터). */
export interface FloorplanChatLine extends FloorplanChatMessage {
  card?: number | null;
  source?: string;
}

/** 서버에 보낼 대화 턴 수 상한 — 더 길면 앞을 자른다(사실 블록은 서버가 매번 다시 싣는다). */
export const MAX_SENT_TURNS = 12;

/** 보낼 메시지 목록: 화면 전용 필드를 떼고 최근 턴만 남긴다. 마지막은 항상 지금 질문. */
export function buildChatMessages(lines: FloorplanChatLine[], question: string): FloorplanChatMessage[] {
  const history = lines.map(({ role, content }) => ({ role, content })).slice(-(MAX_SENT_TURNS - 1));
  return [...history, { role: "user", content: question.trim() }];
}

/** 예시 질문 — 도면에 있는 카드 종류에 따라 고른다. 없는 것을 묻게 하지 않는다. */
export function suggestedQuestions(issues: FloorplanIssue[], summary?: Pick<FloorplanSummary, "robot_passable_pct">): string[] {
  const hasProblem = issues.some((i) => i.kind !== "best_zone");
  const hasBest = issues.some((i) => i.kind === "best_zone");
  const hasDead = issues.some((i) => i.kind === "dead_zone");
  const out: string[] = [];
  // 로봇 통로를 잰 도면(축척 있음)이면 로봇 질문을 맨 앞에 — 물류센터 샘플의 핵심 질문이다.
  if (summary?.robot_passable_pct != null && summary.robot_passable_pct < 99) out.push("로봇이 못 들어가는 통로가 어디예요?");
  if (hasProblem) out.push("이 도면에서 가장 큰 문제가 뭐예요?");
  if (hasBest) out.push("손님이 가장 잘 모이는 자리는 어디예요?");
  if (hasDead) out.push("창고나 사무실은 어디에 두면 좋아요?");
  out.push("이 결과를 얼마나 믿어도 돼요?");
  return out.slice(0, 4);
}

/** 답의 card 번호를 카드 목록 인덱스(0부터)로. 범위 밖이면 null. */
export function cardToIssueIndex(card: number | null | undefined, issueCount: number): number | null {
  if (card == null || !Number.isInteger(card)) return null;
  if (card < 1 || card > issueCount) return null;
  return card - 1;
}
