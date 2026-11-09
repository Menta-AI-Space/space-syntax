// /api/decide 의 `evidence`는 스키마가 고정되어 있지 않다 (백엔드가 계산에
// 쓴 원자료를 그대로 담는다). 그래서 여기서는 모양을 가정하지 않고 재귀로
// 훑어 "라벨: 값" 행 트리로만 바꾼다. 어떤 입력이 와도 던지지 않는 것이
// 이 모듈의 유일한 계약이다 — 근거 보기 때문에 판정문이 깨지면 안 된다.

export type EvidenceNode =
  | { kind: "leaf"; path: string; label: string; value: string }
  | { kind: "branch"; path: string; label: string; children: EvidenceNode[] };

const MAX_DEPTH = 5;
const MAX_ITEMS = 40;

// 자주 나오는 키만 한국어로 바꾼다. 모르는 키는 그대로 사람이 읽을 수 있게
// 다듬기만 한다 (스키마를 지어내지 않는다).
const KEY_LABELS: Record<string, string> = {
  total_score: "종합 점수",
  recommend_score: "추천도",
  grade: "등급",
  lat: "위도",
  lng: "경도",
  radius_m: "반경(m)",
  distance_m: "거리(m)",
  segment_count: "도로 구간 수",
  store_count: "점포 수",
  competitor_count: "경쟁 점포 수",
  same_category_count: "동일 업종 수",
  population: "생활인구",
  workers: "종사자",
  households: "가구 수",
  monthly_sales_won: "월 매출(원)",
  monthly_sales_man: "월 매출(만원)",
  per_store_monthly_won: "점포당 월매출(원)",
  expected_monthly_sales_man: "예상 월매출(만원)",
  max_acceptable_rent_man: "적정 월세 상한(만원)",
  current_rent_man: "현재 월세(만원)",
  rent_gap_man: "월세 차이(만원)",
  deposit_man: "보증금(만원)",
  area_pyeong: "면적(평)",
  floor: "층",
  risk_score: "위험 점수",
  risk_label: "위험 등급",
  overlap_pct: "상권 중복률(%)",
  vacancy_pct: "공실률(%)",
  quarter: "기준 분기",
  source: "출처",
  data_source: "데이터 출처",
  warnings: "주의",
  label: "이름",
  name: "이름",
  category: "분류",
  detail: "설명",
  note: "메모",
};

function titleize(key: string): string {
  const spaced = key.replace(/[_-]+/g, " ").trim();
  if (!spaced) return key;
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function humanizeEvidenceKey(key: string): string {
  return KEY_LABELS[key] ?? titleize(key);
}

export function formatEvidenceValue(value: unknown): string {
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "—";
    // 정수는 천단위 구분, 소수는 두 자리까지만 (0.1234 같은 비율도 읽히게).
    return Number.isInteger(value) ? value.toLocaleString() : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  if (typeof value === "boolean") return value ? "예" : "아니오";
  if (typeof value === "string") return value;
  if (typeof value === "bigint") return value.toString();
  try {
    return String(value);
  } catch {
    return "—";
  }
}

function isEmpty(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === "string" && value.trim() === "") return true;
  if (typeof value === "number" && Number.isNaN(value)) return true;
  return false;
}

// 순환 참조가 있어도(백엔드가 JSON을 주므로 보통 없지만, 이 컴포넌트는
// 임의의 객체를 받을 수 있다) 무한 재귀에 빠지지 않도록 seen으로 막는다.
function walk(value: unknown, key: string, path: string, depth: number, seen: WeakSet<object>): EvidenceNode | null {
  const label = humanizeEvidenceKey(key);
  if (isEmpty(value)) return null;

  if (typeof value !== "object") {
    return { kind: "leaf", path, label, value: formatEvidenceValue(value) };
  }

  const obj = value as object;
  if (seen.has(obj)) return { kind: "leaf", path, label, value: "(순환 참조)" };
  if (depth >= MAX_DEPTH) return { kind: "leaf", path, label, value: "(더 깊은 항목 생략)" };
  seen.add(obj);

  try {
    if (Array.isArray(value)) {
      if (value.length === 0) return null;
      // 원시값만 든 짧은 배열은 한 줄로 합치는 게 읽기 좋다.
      if (value.every((item) => item === null || typeof item !== "object")) {
        const shown = value.filter((item) => !isEmpty(item)).slice(0, MAX_ITEMS).map(formatEvidenceValue);
        if (shown.length === 0) return null;
        const suffix = value.length > MAX_ITEMS ? ` 외 ${value.length - MAX_ITEMS}개` : "";
        return { kind: "leaf", path, label, value: shown.join(", ") + suffix };
      }
      const children: EvidenceNode[] = [];
      value.slice(0, MAX_ITEMS).forEach((item, index) => {
        const child = walk(item, `${index + 1}번째`, `${path}.${index}`, depth + 1, seen);
        if (child) children.push(child);
      });
      if (children.length === 0) return null;
      return { kind: "branch", path, label: `${label} (${value.length}건)`, children };
    }

    // Date 등 평범한 객체가 아닌 것들은 굳이 파헤치지 않는다.
    const proto = Object.getPrototypeOf(obj);
    if (proto !== Object.prototype && proto !== null) {
      return { kind: "leaf", path, label, value: formatEvidenceValue(value) };
    }

    const children: EvidenceNode[] = [];
    for (const [childKey, childValue] of Object.entries(obj).slice(0, MAX_ITEMS)) {
      const child = walk(childValue, childKey, `${path}.${childKey}`, depth + 1, seen);
      if (child) children.push(child);
    }
    if (children.length === 0) return null;
    return { kind: "branch", path, label, children };
  } catch {
    // getter가 던지는 등 예상 밖의 객체여도 근거 섹션만 비고 화면은 산다.
    return { kind: "leaf", path, label, value: "—" };
  } finally {
    seen.delete(obj);
  }
}

/** evidence 객체를 렌더 가능한 행 트리로 바꾼다. 어떤 입력에도 던지지 않는다. */
export function buildEvidenceTree(evidence: unknown): EvidenceNode[] {
  try {
    if (isEmpty(evidence)) return [];
    if (typeof evidence !== "object") {
      return [{ kind: "leaf", path: "value", label: "값", value: formatEvidenceValue(evidence) }];
    }
    const root = walk(evidence, "evidence", "evidence", 0, new WeakSet());
    if (!root) return [];
    return root.kind === "branch" ? root.children : [root];
  } catch {
    return [];
  }
}
