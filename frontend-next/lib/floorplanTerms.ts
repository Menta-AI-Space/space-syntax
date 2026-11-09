/**
 * 평면도 공간구조 분석이 쓰는 말 — **용어를 먼저 정하고 그 말만 쓴다.**
 *
 * 이 파일은 `lib/metricInfo.ts`의 표기 원칙을 실내 평면으로 옮긴 것이다. 원칙은
 * 그 파일에 이미 적혀 있고 여기서 바꾸지 않는다:
 *
 *   - 화면에 **처음 보이는 이름은 누구나 아는 쉬운 한국어**만 쓴다. 영어 금지.
 *   - '통합도' 같은 전문 용어는 **설명(ⓘ) 안에서만** 소개한다.
 *   - 값을 문장으로 옮길 때도 전문 용어를 쓰지 않는다.
 *
 * 왜 따로 두는가: `metricInfo.ts`의 설명은 **도로망**의 말로 쓰여 있다("동네",
 * "길", "골목"). 같은 네 지표라도 실내에서는 대상이 길이 아니라 **방 안의 자리**다.
 * 같은 문장을 실내에 그대로 쓰면 "이 길까지 얼마나 쉽게 닿는지"가 되어 무엇을
 * 말하는지 알 수 없다. 그래서 **같은 지표 · 같은 식 · 다른 말**이다 —
 * 지표 키(`MetricKey`)는 일부러 그쪽과 같은 이름을 쓴다.
 *
 * 각 항목에 `caution`을 둔 이유: 이 지표들은 **오해되는 방식이 정해져 있다.**
 * 통합도가 높은 자리를 "좋은 자리"로, 깊은 자리를 "나쁜 자리"로 읽는 것이 가장
 * 흔하다. 금고실과 수술실은 깊어야 한다. 그 경고를 지표 옆에 붙여 둔다.
 */

/** `lib/metricInfo.ts`의 MetricKey와 같은 이름을 쓴다 — 같은 지표이기 때문이다. */
export type TermKey = "integration" | "connectivity" | "mean_depth" | "choice";

export interface Term {
  /** 화면에 처음 보이는 이름. 쉬운 한국어만. */
  easy: string;
  /** 전문 용어. 설명 안에서만 꺼낸다. */
  jargon: string;
  /** 영어 원어. 문헌을 찾을 사람을 위해 설명 안에만 둔다. */
  en: string;
  /** 무엇을 세는 값인가 — 한 문장. */
  what: string;
  /** 이걸로 무엇을 알 수 있는가. */
  use: string;
  /** 어떻게 오해되는가. */
  caution: string;
  /** 값이 크면 무슨 뜻인가 / 작으면 무슨 뜻인가. */
  high: string;
  low: string;
  /** 실제로 계산하고 있는가. 데모가 이걸 화면에 그대로 밝힌다. */
  computed: boolean;
}

export const TERMS: Record<TermKey, Term> = {
  integration: {
    easy: "찾아오기 쉬운 자리",
    jargon: "통합도",
    en: "Integration",
    what: "건물 안 모든 자리에서 이 자리까지 평균 몇 겹을 지나 와야 하는지를 뒤집은 값입니다.",
    use: "안내데스크·대기공간처럼 누구나 쉽게 닿아야 하는 것을 어디에 둘지 고를 때 봅니다.",
    caution:
      "높은 자리가 곧 좋은 자리는 아닙니다. 금고실·서버실·수술실은 오히려 찾아오기 어려운 자리에 있어야 합니다.",
    high: "어디서든 쉽게 닿는 가운데 자리",
    low: "구석지고 외진 자리",
    computed: true,
  },
  connectivity: {
    easy: "한눈에 보이는 범위",
    jargon: "연결도",
    en: "Connectivity",
    what: "이 자리에 서서 벽에 가리지 않고 볼 수 있는 칸이 몇 개인지 셉니다.",
    use: "감시·안내가 되는 자리와, 서 있어도 아무것도 안 보이는 자리를 가릅니다.",
    caution:
      "많이 보이는 것이 늘 좋은 것은 아닙니다. 상담실·탈의실은 보이지 않아야 하는 자리입니다.",
    high: "사방이 트여 멀리까지 보이는 자리",
    low: "벽에 둘러싸여 시야가 막힌 자리",
    computed: true,
  },
  mean_depth: {
    easy: "들어와야 하는 깊이",
    jargon: "평균 깊이",
    en: "Mean Depth",
    what: "이 자리에서 다른 자리들까지 평균 몇 번 시야가 끊기는지를 셉니다.",
    use: "안쪽에 숨어 있는 공간과 겉에 붙은 공간을 가릅니다.",
    caution:
      "깊다는 것은 '나쁘다'가 아니라 '안쪽이다'입니다. 어떤 용도는 깊은 자리를 필요로 합니다.",
    high: "여러 번 꺾어 들어가야 닿는 안쪽",
    low: "겉에서 바로 닿는 자리",
    computed: true,
  },
  choice: {
    easy: "지나다니게 되는 자리",
    jargon: "선택도",
    en: "Choice",
    what: "건물 안을 오갈 때의 최단 경로들이 이 자리를 얼마나 많이 지나가는지 셉니다.",
    use: "가만히 있어도 사람이 지나가는 자리와, 일부러 찾아가야 하는 자리를 가릅니다.",
    caution:
      "지나다니는 자리는 매장에는 좋고 집중이 필요한 자리에는 나쁩니다. 용도를 정하고 봐야 합니다.",
    high: "오가는 사람이 자연히 지나가는 목",
    low: "일부러 찾아가야 하는 자리",
    // ⚠️ 계산하지 않는다. 백엔드 floorplan_vga.py도 통합도·연결도만 낸다.
    // 없는 값을 화면에 그리지 않기 위해 이 표시로 갈라 둔다.
    computed: false,
  },
};

/** 화면에 내는 순서. 쉬운 것부터, 계산하지 않는 것은 맨 뒤로. */
export const TERM_ORDER: TermKey[] = ["connectivity", "integration", "mean_depth", "choice"];

/** 실제로 계산해서 지도에 칠할 수 있는 것만. */
export const HEATMAP_TERMS: TermKey[] = TERM_ORDER.filter((k) => TERMS[k].computed);

/**
 * 값을 "상위 n%"로 옮긴다.
 *
 * 원시값을 그대로 내보내지 않는 이유: 통합도 2.14가 높은지 낮은지는 **이 도면
 * 안에서만** 뜻이 있다. 다른 건물의 2.14와 비교할 수 있는 값이 아니다. 그래서
 * 순위로만 말한다.
 */
export function rankText(value: number, all: number[]): string {
  if (!all.length) return "—";
  const below = all.filter((v) => v < value).length;
  const pct = Math.round((below / all.length) * 100);
  // "상위 100%"는 문장으로 뜻이 없다 — 아래쪽 절반은 "하위"로 뒤집어 말한다.
  // `lib/demoParcels.ts`의 percentileText와 같은 규칙이다.
  return pct >= 50 ? `상위 ${Math.max(1, 100 - pct)}%` : `하위 ${Math.max(1, pct)}%`;
}

/** 화면에서 쓰는 이름만 돌려준다 — 전문 용어를 실수로 흘리지 않기 위한 좁은 문. */
export function easyName(k: TermKey): string {
  return TERMS[k].easy;
}
