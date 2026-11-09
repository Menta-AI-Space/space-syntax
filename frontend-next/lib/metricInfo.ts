/**
 * 지표 표기 원칙 (사용자 요구):
 * - 화면에 처음 보이는 이름은 누구나 아는 쉬운 한국어만 쓴다. 영어 금지.
 * - '통합도' 같은 전문 용어는 ⓘ 아이콘의 설명(툴팁) 안에서만 소개한다.
 * - LLM 설명도 전문 용어를 쓰지 않는다(backend llm_explain.py 시스템 프롬프트에서 강제).
 */

export type MetricKey = "choice" | "integration" | "mean_depth" | "connectivity";

export const METRIC_INFO: Record<MetricKey, { easy: string; jargon: string; explain: string }> = {
  choice: {
    easy: "자주 지나가게 되는 위치",
    jargon: "선택도",
    explain:
      "전문 용어로는 '선택도'라고 해요. 동네에서 이곳저곳 오갈 때 이 길을 얼마나 자주 거쳐 가게 되는지를 나타내요. 값이 높을수록 오가는 사람이 많은 길이에요.",
  },
  integration: {
    easy: "찾아오기 쉬운 위치",
    jargon: "통합도",
    explain:
      "전문 용어로는 '통합도'라고 해요. 동네 어디에서든 이 길까지 얼마나 쉽게 닿는지를 나타내요. 값이 높을수록 누구나 찾아오기 쉬운 중심 길이에요.",
  },
  mean_depth: {
    easy: "큰길에서 꺾어 들어오는 정도",
    jargon: "평균 깊이",
    explain:
      "전문 용어로는 '평균 깊이'라고 해요. 이 길에 가려면 큰길에서 몇 번을 꺾어 들어가야 하는지를 나타내요. 값이 높을수록 안쪽에 숨어 있는 골목이에요.",
  },
  connectivity: {
    easy: "쉽게 다른 길과 이어지는 위치",
    jargon: "연결도",
    explain:
      "전문 용어로는 '연결도'라고 해요. 이 길과 바로 맞닿아 이어진 길이 몇 개인지를 세요. 많을수록 사방으로 잘 통하는 길이에요.",
  },
};

export const METRIC_ORDER: MetricKey[] = ["choice", "integration", "mean_depth", "connectivity"];
