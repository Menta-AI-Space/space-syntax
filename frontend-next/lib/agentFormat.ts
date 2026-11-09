// 결정 에이전트(목적 선택 → 입력 → 판정문)에서 공유하는 순수 포맷·판정 헬퍼.
// 백엔드가 null / NaN / 빈 배열을 줘도 화면에 "null"·"NaN"이 새지 않게
// 여기서 한 번에 막는다. React 없이 테스트 가능하도록 순수 함수만 둔다.

export type VerdictTone = "positive" | "info" | "caution" | "negative";

const VERDICT_TONE: Record<string, VerdictTone> = {
  recommend: "positive",
  conditional: "info",
  hold: "caution",
  avoid: "negative",
};

const SEVERITY_TONE: Record<string, VerdictTone> = {
  high: "negative",
  medium: "caution",
  low: "info",
};

/** 판정값 → 색 톤. 모르는 값(또는 없음)은 중립인 info. */
export function toneFor(verdict: string | null | undefined): VerdictTone {
  return VERDICT_TONE[String(verdict ?? "")] ?? "info";
}

/** 원인 심각도 → 색 톤. high=위험, medium=주의, low=참고. */
export function toneForSeverity(severity: string | null | undefined): VerdictTone {
  return SEVERITY_TONE[String(severity ?? "")] ?? "info";
}

/** 입력창의 문자열을 숫자로. 비었거나 숫자가 아니면 null. */
export function parseNumber(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

function isNum(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** 값이 있으면 숫자, 없으면 null — 화면에 그대로 쓸 수 있는지 판정용. */
export function hasNumber(value: number | null | undefined): boolean {
  return isNum(value);
}

/** 만원 표기. 값이 없으면 "—" (절대 "null"·"NaN"을 내보내지 않는다). */
export function fmtMan(value: number | null | undefined): string {
  if (!isNum(value)) return "—";
  return `${Math.round(value).toLocaleString()}만원`;
}

/** 사용자가 만원 단위로 적은 큰 금액을 억으로 읽어 준다 — 「120,000만원」은 0을 세어야 12억인지
 *  알 수 있어, 매입가를 잘못 적어도 알아채기 어려웠다(2026-11-09 UX 자동 개선, 단계별 입력 요약).
 *  1억 미만은 fmtMan과 같다. 예: 120000 → 「12억원」, 125000 → 「12억 5,000만원」. */
export function fmtManEok(value: number | null | undefined): string {
  if (!isNum(value)) return "—";
  const man = Math.round(value);
  if (Math.abs(man) < 10000) return `${man.toLocaleString()}만원`;
  const eok = Math.trunc(man / 10000);
  const rest = Math.abs(man % 10000);
  return rest ? `${eok.toLocaleString()}억 ${rest.toLocaleString()}만원` : `${eok.toLocaleString()}억원`;
}

/** 퍼센트 표기. 소수 한 자리까지. */
export function fmtPct(value: number | null | undefined, digits = 1): string {
  if (!isNum(value)) return "—";
  return `${value.toLocaleString(undefined, { maximumFractionDigits: digits })}%`;
}

/** 연 단위 표기 (회수 기간 등). */
export function fmtYears(value: number | null | undefined, digits = 1): string {
  if (!isNum(value)) return "—";
  return `${value.toLocaleString(undefined, { maximumFractionDigits: digits })}년`;
}

/** 개월 표기. */
export function fmtMonths(value: number | null | undefined): string {
  if (!isNum(value)) return "—";
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })}개월`;
}

/** 0~100으로 자른 점수. 값이 없으면 0 (막대 너비 계산용). */
export function clampPct(value: number | null | undefined): number {
  if (!isNum(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

/** 두 값을 나란히 비교하는 막대의 너비(%) — 큰 쪽이 100%. */
export function barWidth(value: number | null | undefined, max: number | null | undefined): number {
  if (!isNum(value) || !isNum(max) || max <= 0) return 0;
  return Math.max(0, Math.min(100, (value / max) * 100));
}

/** 판정값이 없는 응답(예: 후보 목록)에서 추천도 점수만으로 색을 정한다. */
export function toneForScore(score: number | null | undefined): VerdictTone {
  if (!isNum(score)) return "info";
  if (score >= 75) return "positive";
  if (score >= 55) return "info";
  if (score >= 40) return "caution";
  return "negative";
}

/** 배수 표기 (DSCR 등). */
export function fmtRatio(value: number | null | undefined, digits = 2): string {
  if (!isNum(value)) return "—";
  return `${value.toLocaleString(undefined, { maximumFractionDigits: digits })}배`;
}

/** 부호를 붙인 퍼센트 — 격차처럼 방향이 중요한 값에 쓴다. */
export function fmtSignedPct(value: number | null | undefined, digits = 0): string {
  if (!isNum(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toLocaleString(undefined, { maximumFractionDigits: digits })}%`;
}

/**
 * 밴드(구간) 막대 위에서 값이 놓일 위치(%)를 구한다. 밴드 바깥 값도 보이도록
 * 아래위로 여유를 두며, 밴드가 한 점이거나 값이 없으면 null (막대를 그리지 않음).
 */
export function bandPosition(
  value: number | null | undefined,
  low: number | null | undefined,
  high: number | null | undefined
): { valuePct: number; lowPct: number; highPct: number } | null {
  if (!isNum(low) || !isNum(high) || high < low) return null;
  const span = high - low;
  const pad = span > 0 ? span * 0.6 : Math.max(Math.abs(high), 1) * 0.3;
  const min = Math.min(low - pad, isNum(value) ? value - pad * 0.2 : low - pad);
  const max = Math.max(high + pad, isNum(value) ? value + pad * 0.2 : high + pad);
  const range = max - min;
  if (!(range > 0)) return null;
  const at = (v: number) => Math.max(0, Math.min(100, ((v - min) / range) * 100));
  return {
    valuePct: isNum(value) ? at(value) : -1,
    lowPct: at(low),
    highPct: at(high),
  };
}

/**
 * 라벨에 목적격 조사(을/를)를 붙인다.
 *
 * 한글은 앞말의 받침 유무로 조사가 갈린다 — "월세를"이지만 "보증금을"이다.
 * 문자열 끝에 단위나 괄호가 붙을 수 있어 마지막 "한글 음절"을 기준으로
 * 판단하고, 한글이 없는 라벨(영문·숫자)에는 조사를 붙이지 않는다.
 * 한글 음절 코드에서 (code - 0xAC00) % 28 == 0 이면 받침이 없다.
 */
export function withObjectParticle(label: string): string {
  const syllables = label.match(/[가-힣]/g);
  if (!syllables?.length) return label;
  const last = syllables[syllables.length - 1].charCodeAt(0);
  const hasFinalConsonant = (last - 0xac00) % 28 !== 0;
  return `${label}${hasFinalConsonant ? "을" : "를"}`;
}


/** 서울 상권분석서비스의 yyq("20254")를 사람 말("2025년 4분기")로. 형식이
 * 다르면 원문 그대로 — 지어내지 않는다. */
export function fmtQuarter(yyq: string | null | undefined): string {
  if (!yyq || !/^\d{5}$/.test(yyq)) return yyq ?? "";
  return `${yyq.slice(0, 4)}년 ${yyq[4]}분기`;
}

/** 장소가 있는 답 뒤 입력창 예시. 사용자가 월세를 이미 말했으면 같은 질문(「월세 300이면
 *  계약할까?」)을 다시 권하지 않고 그 값을 바꿔 묻는 법을 보여 준다.
 *  2026-11-08 UX 자동 개선 — 「합정역 카페 월세 300이면 어때?」 답 뒤에도 예시가
 *  「월세 300이면 계약할까?」라 방금 한 질문을 되풀이하라는 것처럼 읽혔다. */
export function followUpExample(parameters: Record<string, number> | null | undefined): string {
  const rent = parameters?.monthly_rent_man;
  if (typeof rent === "number" && Number.isFinite(rent) && rent > 0) {
    const step = rent >= 1000 ? 100 : 50;
    return `예: 월세 ${Math.round(rent / step) * step + step}이면 어때?`;
  }
  return "예: 월세 300이면 계약할까?";
}
