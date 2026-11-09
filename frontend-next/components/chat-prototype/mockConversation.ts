// PROTOTYPE — throwaway. 3 variants가 공유하는 가짜 대화 데이터.
// 실제 AgentChat/analyze API에는 연결하지 않는다 (레이아웃 탐색용).

export type MockMessage =
  | { role: "user"; text: string }
  | { role: "assistant"; text: string; scoreCard?: MockScoreCard };

export interface MockScoreCard {
  title: string;
  score: number;
  metrics: { label: string; value: string }[];
}

export const MOCK_SCORE_CARD: MockScoreCard = {
  title: "신촌역 5번 출구 · 카페",
  score: 78,
  metrics: [
    { label: "동선 접근성", value: "상위 12%" },
    { label: "생활인구", value: "18,400명/일" },
    { label: "경쟁 밀도", value: "보통" },
    { label: "예상 임대료", value: "평당 12.4만원" },
    { label: "잠식 위험", value: "낮음" },
    { label: "투자 회수", value: "3.2년" },
  ],
};

export const MOCK_THREAD: MockMessage[] = [
  { role: "user", text: "신촌역 5번 출구 근처에 카페 하나 낼 건데, 여기 자리 어때?" },
  {
    role: "assistant",
    text: "신촌역 5번 출구 반경 150m를 공간구문 지표와 생활인구·실거래 데이터로 분석했습니다. 종합 점수는 78점(상위 12%)이고, 동선 접근성이 특히 좋습니다. 다만 반경 200m 내 카페 5곳이 있어 경쟁 밀도는 보통 수준입니다.",
    scoreCard: MOCK_SCORE_CARD,
  },
  { role: "user", text: "근처 프랜차이즈 카페랑 잠식 위험은 없어?" },
  {
    role: "assistant",
    text: "가장 가까운 동일 브랜드군 매장은 320m 거리에 있어 자기잠식 위험은 낮게 평가됩니다. 다만 도보 5분 내 스타벅스 2곳이 있어 프리미엄 가격대 포지셔닝은 다소 불리할 수 있어요.",
  },
];

export const MOCK_HISTORY = [
  { id: "1", title: "신촌역 5번 출구 카페 입지" },
  { id: "2", title: "합정 편의점 신설도로 영향" },
  { id: "3", title: "강남역 vs 역삼역 후보지 비교" },
  { id: "4", title: "이자카야 임대료 적정선" },
];

export const MOCK_SUGGESTIONS = [
  "이 자리 3년 후 상권 전망은?",
  "임대료 12만원이면 회수 몇 년?",
  "반경 300m 경쟁점 리스트 보여줘",
];
