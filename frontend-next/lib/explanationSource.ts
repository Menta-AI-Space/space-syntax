// ExplanationOut.source(backend/app/models/schemas.py)는 서술문이 LLM이 쓴
// 것인지 규칙 템플릿(폴백 포함)인지를 담아 온다. 값이 다르면 화면에 보이는
// 라벨도 반드시 달라져야 한다 — 그래야 LLM이 실패해 규칙 문구가 나가도
// 사용자가 구분할 수 있다.
export function explanationSourceLabel(source: string | null | undefined): string | null {
  if (source === "llm") return "AI가 생성한 설명";
  if (source === "rule") return "규칙 기반 설명(AI 미사용)";
  return null;
}
