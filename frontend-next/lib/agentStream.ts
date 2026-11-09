// AI 상담 스트림(/api/agent/ask/stream) 받기.
//
// 2026-11-06: 답을 "엔진 결과를 정해진 칸에 채운 350자"가 아니라 상담 모델이 엔진을
// 도구로 부르며 쓰는 글로 바꿨다. 도구를 두세 번 돌리면 10~20초라, 글자가 나오는 대로
// 화면에 흘린다. 서버 이벤트 형식은 backend/app/routers/agent.py agent_ask_stream 참고.

import { API_BASE_URL, type AgentAskRequest, type AgentAskResponse } from "./api.ts";

export type AgentStreamEvent =
  | { type: "status"; text: string }
  | { type: "delta"; text: string }
  | { type: "reset" }
  | { type: "done"; response: AgentAskResponse | null; fallback: boolean };

/** SSE 버퍼에서 완성된 이벤트를 꺼낸다. 끝이 잘린 조각은 rest로 돌려 다음 청크와 잇는다. */
export function parseSseBuffer(buffer: string): { events: AgentStreamEvent[]; rest: string } {
  const events: AgentStreamEvent[] = [];
  const normalized = buffer.replace(/\r\n/g, "\n");
  const parts = normalized.split("\n\n");
  const rest = parts.pop() ?? "";
  for (const part of parts) {
    const data = part
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!data) continue;
    try {
      const parsed = JSON.parse(data) as AgentStreamEvent;
      if (parsed && typeof parsed === "object" && "type" in parsed) events.push(parsed);
    } catch {
      /* 깨진 이벤트 하나 때문에 스트림 전체를 버리지 않는다. */
    }
  }
  return { events, rest };
}

/** 스트림을 끝까지 받는다. 이벤트마다 onEvent를 부르고, 마지막 done 이벤트를 돌려준다.
 *  done 없이 끊기면 fallback=true인 done을 만들어 돌려준다(화면이 종전 경로로 쓴다). */
export async function streamAgentAsk(
  body: AgentAskRequest,
  onEvent: (event: AgentStreamEvent) => void,
  signal?: AbortSignal,
): Promise<Extract<AgentStreamEvent, { type: "done" }>> {
  const fallback = { type: "done" as const, response: null, fallback: true };
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/agent/ask/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    });
  } catch {
    return fallback;
  }
  if (!response.ok || !response.body) return fallback;

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let done: Extract<AgentStreamEvent, { type: "done" }> | null = null;
  try {
    for (;;) {
      const { value, done: finished } = await reader.read();
      if (finished) break;
      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSseBuffer(buffer);
      buffer = parsed.rest;
      for (const event of parsed.events) {
        if (event.type === "done") done = event;
        else onEvent(event);
      }
    }
    buffer += decoder.decode();
    for (const event of parseSseBuffer(buffer + "\n\n").events) {
      if (event.type === "done") done = event;
      else onEvent(event);
    }
  } catch {
    return done ?? fallback;
  }
  return done ?? fallback;
}
