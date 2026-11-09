import type { FloorplanEditIntentResponse, FloorplanLine } from "@/lib/api";

/**
 * 말로 고치기 (2026-11-09, SPEC_FLOORPLAN_TABS_UI) — 서버가 돌려준 「더할 선 / 지울 선 번호」를
 * 지금 도면에 적용한다. 번호는 요청에 보낸 배열의 순서이므로, 요청 뒤 도면이 바뀌었으면
 * (되돌리기 등) 적용하면 안 된다 — 호출부가 배열이 같은지 확인한다.
 */

export type Point = { x: number; y: number };

/** 점(누르기) 또는 네모(끌기)로 고른 자리. 네모면 point는 그 가운데다. */
export type VoiceSelection = { point: Point; box: FloorplanLine | null };

export type EditPlanLike = Pick<
  FloorplanEditIntentResponse,
  "add_lines" | "remove_line_indices" | "add_openings" | "remove_opening_indices"
>;

export function applyEditPlan(
  lines: FloorplanLine[],
  openings: FloorplanLine[],
  plan: EditPlanLike,
): { lines: FloorplanLine[]; openings: FloorplanLine[] } {
  const dropLines = new Set(plan.remove_line_indices);
  const dropOpenings = new Set(plan.remove_opening_indices);
  return {
    lines: [...lines.filter((_, i) => !dropLines.has(i)), ...plan.add_lines],
    openings: [...openings.filter((_, i) => !dropOpenings.has(i)), ...plan.add_openings],
  };
}

/** 미리보기에 그릴 것 — 사라지는 선(빨간 점선)과 새로 생기는 선(초록). */
export function previewOf(
  lines: FloorplanLine[],
  openings: FloorplanLine[],
  plan: EditPlanLike,
): { removed: FloorplanLine[]; removedOpenings: FloorplanLine[]; added: FloorplanLine[]; addedOpenings: FloorplanLine[] } {
  return {
    removed: plan.remove_line_indices.map((i) => lines[i]).filter((l): l is FloorplanLine => !!l),
    removedOpenings: plan.remove_opening_indices.map((i) => openings[i]).filter((l): l is FloorplanLine => !!l),
    added: plan.add_lines,
    addedOpenings: plan.add_openings,
  };
}

export function hasChanges(plan: EditPlanLike): boolean {
  return (
    plan.add_lines.length + plan.remove_line_indices.length + plan.add_openings.length + plan.remove_opening_indices.length >
    0
  );
}

/**
 * 누른 자리와 뗀 자리로 고른 자리를 만든다. ``dragThreshold``(도면 단위)보다 적게 움직였으면
 * 점으로 본다 — 손가락은 누르는 사이에도 조금씩 움직인다.
 */
export function selectionFrom(start: Point, end: Point, dragThreshold: number): VoiceSelection {
  if (Math.hypot(end.x - start.x, end.y - start.y) < dragThreshold) {
    return { point: start, box: null };
  }
  const box = {
    x1: Math.min(start.x, end.x),
    y1: Math.min(start.y, end.y),
    x2: Math.max(start.x, end.x),
    y2: Math.max(start.y, end.y),
  };
  return { point: { x: (box.x1 + box.x2) / 2, y: (box.y1 + box.y2) / 2 }, box };
}

/** 자주 쓰는 지시 — 휴대폰에서 글 치기 번거로울 때 한 번에 보낸다. */
export const QUICK_EDITS = ["여기 문 내줘", "이 벽 없애 줘", "이 문 막아 줘", "여기 벽 세워서 막아 줘"] as const;

/**
 * LLM이 「적용할까요?」라고 물은 뒤 사용자의 답을 가른다 (2026-11-09 소유자 「LLM이 물으라고」).
 * 「응」「적용해」→ yes, 「아니」「취소」→ no. 「아니 2미터로」처럼 뒤에 내용이 붙으면 고쳐 말한 것이라
 * null — 호출부가 직전 지시와 함께 다시 보낸다. 말소리 인식은 문장부호·띄어쓰기가 들쭉날쭉해 지운 뒤 본다.
 */
const YES = /^(응|웅|어|엉|네|넹|넵|예|ㅇㅇ|ㅇㅋ|오케이|오키|ok|okay|yes|좋아|좋아요|좋습니다|그래|그래요|그렇게|그렇게해|그렇게해줘|그렇게해주세요|적용|적용해|적용해줘|적용해주세요|적용할게|적용하자|해|해줘|해주세요|하자|바꿔|바꿔줘|바꿔주세요|그걸로|그걸로해|맞아|맞아요|고|ㄱㄱ|콜)+(요)?$/i;
const NO = /^(아니|아니야|아니요|아뇨|아냐|노|no|ㄴㄴ|취소|취소해|취소해줘|됐어|됐어요|괜찮아|싫어|그만|하지마|하지말아줘|안해|안할래|놔둬|놔둬요|그대로둬)+(요)?$/i;

export function confirmIntent(text: string): "yes" | "no" | null {
  const t = text.replace(/[\s.,!?~。…ㅠㅜ]+/g, "").toLowerCase();
  if (!t) return null;
  if (YES.test(t)) return "yes";
  if (NO.test(t)) return "no";
  return null;
}

/** 제안 문장(「문을 냅니다」)을 적용 뒤 말투(「문을 냈습니다」)로. */
export function pastTense(text: string): string {
  return text
    .replace(/냅니다/g, "냈습니다")
    .replace(/지웁니다/g, "지웠습니다")
    .replace(/막습니다/g, "막았습니다")
    .replace(/세웁니다/g, "세웠습니다");
}
