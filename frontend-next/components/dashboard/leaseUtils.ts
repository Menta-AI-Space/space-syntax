// Ported from frontend/js/lease_analysis.js (constants/pure helpers) and the
// upjong-classification helpers in frontend/js/business_picker.js
// (`inferArchetypeFromCodes`, `isAcademyUpjong`) that lease_analysis.js reads
// via `getSelectedUpjong()`. `UpjongSelection` (components/BusinessPicker.tsx)
// does not carry an explicit `archetype` field the way Vanilla's
// `selectedUpjong` does — Vanilla itself always derives it as
// `next.archetype || inferArchetypeFromCodes(codes)` and never sets an
// explicit archetype elsewhere, so recomputing it from `inds_mcls_cd` here is
// equivalent, not an approximation.

import type { UpjongSelection } from "@/components/BusinessPicker";

export interface UpjongCodes {
  inds_lcls_cd?: string | null;
  inds_mcls_cd?: string | null;
  inds_scls_cd?: string | null;
  business_type?: string;
}

/** Ported from `inferArchetypeFromCodes`. */
export function inferArchetypeFromCodes(u: UpjongCodes | null | undefined): string {
  const m = u?.inds_mcls_cd;
  if (!m) return "general";
  if (m.startsWith("I2")) return "food_drink";
  if (m.startsWith("G2")) return "daily_retail";
  if (m.startsWith("Q1")) return "medical";
  if (m.startsWith("P10")) return "education";
  if (m.startsWith("S2")) return "beauty_service";
  return "general";
}

/** Ported from `isAcademyUpjong`. */
export function isAcademyUpjong(u: UpjongCodes | null | undefined): boolean {
  if (!u) return false;
  if (u.business_type === "academy") return true;
  if (inferArchetypeFromCodes(u) === "education") return true;
  return !!u.inds_mcls_cd?.startsWith("P10");
}

/** Ported from `isMedicalUpjong`. */
export function isMedicalUpjong(u: UpjongCodes | null | undefined): boolean {
  if (!u) return false;
  return u.inds_lcls_cd === "Q1" || inferArchetypeFromCodes(u) === "medical";
}

/** Ported from `supportsRealCompetition`. */
export function supportsRealCompetition(u: UpjongSelection | null | undefined): boolean {
  if (!u) return false;
  const m = u.inds_mcls_cd;
  const s = u.inds_scls_cd;
  if (m === "I212" || s === "I21201") return true;
  if (s === "I21001") return true;
  if (m?.startsWith("I2") && m !== "I212") return true;
  if (u.inds_lcls_cd === "I2" && !!m && m !== "I212") return true;
  if (u.inds_lcls_cd === "Q1" || isMedicalUpjong(u) || m?.startsWith("Q1")) return true;
  if (m?.startsWith("P10") || inferArchetypeFromCodes(u) === "education") return true;
  return ["cafe", "restaurant", "clinic", "academy", "bakery"].includes(u.business_type || "");
}

/** Ported from `VERTICAL_UI`. */
export const VERTICAL_UI: Record<string, { icon: string; compTitle: string; fitTitle: string }> = {
  restaurant: { icon: "🍽", compTitle: "식당 실질 경쟁", fitTitle: "식당 모델 적합도" },
  cafe: { icon: "☕", compTitle: "카페 실질 경쟁", fitTitle: "카페 모델 적합도" },
  clinic: { icon: "🏥", compTitle: "병원 입지 적합도", fitTitle: "과목별 적합도" },
  academy: { icon: "🎓", compTitle: "학원 입지 적합도", fitTitle: "학원 모델 적합도" },
  bakery: { icon: "🥐", compTitle: "베이커리 입지 적합도", fitTitle: "모델별 적합도" },
};

export const ACADEMY_SUBJECTS = [
  { id: "", label: "과목 선택 (필수)" },
  { id: "math", label: "수학학원" },
  { id: "english", label: "영어학원" },
  { id: "korean", label: "국어·논술학원" },
  { id: "science", label: "과학학원" },
  { id: "exam_cram", label: "입시·보습학원" },
  { id: "arts", label: "예체능학원" },
  { id: "coding", label: "코딩학원" },
  { id: "adult", label: "성인·자격증학원" },
];

// Mirrors backend `COMPETITION_MINUTES_BY_MODE` in app/services/isochrone.py
// (3-minute fine-grained band added; 1-2 min excluded — see backend comment).
export const COMPETITION_MINUTES_BY_MODE: Record<string, number[]> = {
  walk: [3, 5, 10, 15],
  drive: [3, 5, 10, 15],
  moto: [3, 5, 10, 15, 20, 25],
  bike: [3, 5, 10, 15],
};

export const COMPETITION_MODE_LABELS: Record<string, string> = { walk: "도보", drive: "차도", moto: "오토바이" };

/** Ported from `verdictClass`. */
export function verdictClass(verdict: string): string {
  if (verdict === "위험") return "lease-verdict--danger";
  if (verdict === "양호") return "lease-verdict--good";
  return "lease-verdict--warn";
}

/** Ported from `intensityClass`. */
export function intensityClass(label: string): string {
  if (label === "매우 높음" || label === "높음") return "dash-intensity--high";
  if (label === "보통") return "dash-intensity--mid";
  return "dash-intensity--low";
}

/** Ported from `modelVerdictClass`. */
export function modelVerdictClass(v: string): string {
  if (v === "좋음") return "model-fit--good";
  if (v === "낮음") return "model-fit--low";
  return "model-fit--mid";
}

/** Ported from `scoreBarLevel`. */
export function scoreBarLevel(value: number | null | undefined, invert = false): { label: string; cls: string } {
  const v = Number(value) || 0;
  if (invert) {
    if (v >= 70) return { label: "높음", cls: "academy-score--high" };
    if (v >= 40) return { label: "보통", cls: "academy-score--mid" };
    return { label: "낮음", cls: "academy-score--low" };
  }
  if (v >= 70) return { label: "좋음", cls: "academy-score--good" };
  if (v >= 40) return { label: "보통", cls: "academy-score--mid" };
  return { label: "낮음", cls: "academy-score--low" };
}

/** Ported from `leaseFmtMan`. */
export function leaseFmtMan(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return `${Number(n).toLocaleString("ko-KR")}만`;
}

/** Ported from `competitionReachLabel`. */
export function competitionReachLabel(mode: string, minutes: number): string {
  return `${COMPETITION_MODE_LABELS[mode] || mode} ${minutes}분`;
}

// --- Lease input persistence (ported from `loadLeaseInputs`/`saveLeaseInputs`/
// `defaultLeaseInputs`). Storage key/shape preserved exactly. ---

const LEASE_STORAGE_KEY = "space_syntax_lease_inputs";

export interface LeaseInputs {
  monthly_rent_man: number | "";
  maintenance_man: number | "";
  key_money_man: number | "";
  area_pyeong: number | "";
  staff_count: number | "";
  ticket_price: number | "";
  cogs_ratio_pct: number | "";
  delivery_share_pct: number | "";
}

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

export function loadLeaseInputs(): Partial<LeaseInputs> {
  if (!isBrowser()) return {};
  try {
    return JSON.parse(window.localStorage.getItem(LEASE_STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

export function saveLeaseInputs(data: Partial<LeaseInputs>): void {
  if (!isBrowser()) return;
  try {
    window.localStorage.setItem(LEASE_STORAGE_KEY, JSON.stringify(data));
  } catch {
    /* ignore */
  }
}

export function defaultLeaseInputs(): LeaseInputs {
  const saved = loadLeaseInputs();
  return {
    monthly_rent_man: saved.monthly_rent_man ?? 350,
    maintenance_man: saved.maintenance_man ?? 40,
    key_money_man: saved.key_money_man ?? "",
    area_pyeong: saved.area_pyeong ?? 15,
    staff_count: saved.staff_count ?? 2,
    ticket_price: saved.ticket_price ?? 4500,
    cogs_ratio_pct: saved.cogs_ratio_pct ?? 30,
    delivery_share_pct: saved.delivery_share_pct ?? 0,
  };
}
