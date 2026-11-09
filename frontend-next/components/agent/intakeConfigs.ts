import type { GoalIntakeConfig } from "@/components/agent/GoalIntake";

// 목적별로 "무엇을 어떤 순서로 물을지"만 적는다 — 화면 동작은 GoalIntake가
// 공유한다. 문구는 전문용어 없이, 사장님이 실제로 쓰는 말로만 쓴다.

export const LEASE_INTAKE: GoalIntakeConfig = {
  title: "계약해도 될지 알려드릴게요",
  subtitle: "업종과 매물 위치, 월세만 알려주시면 됩니다.",
  cta: "계약해도 될지 알려주세요",
  steps: [
    { kind: "upjong", title: "어떤 업종인가요?", required: true },
    {
      kind: "location",
      title: "어느 매물인가요?",
      label: "매물 위치",
      placeholder: "주소나 상호를 입력하세요",
    },
    {
      kind: "numbers",
      title: "매달 얼마를 내나요?",
      fields: [
        { key: "monthly_rent_man", label: "월세", unit: "만원", required: true },
        { key: "maintenance_man", label: "관리비", unit: "만원" },
        { key: "deposit_man", label: "보증금", unit: "만원" },
        { key: "key_money_man", label: "권리금", unit: "만원" },
      ],
      hint: "월세만 있어도 판정할 수 있어요. 나머지는 알면 더 정확해집니다.",
    },
    {
      kind: "numbers",
      title: "가게 크기와 층은요?",
      fields: [
        { key: "area_pyeong", label: "평수", unit: "평" },
        { key: "floor", label: "층" },
      ],
      hint: "몰라도 괜찮습니다. 적으면 매출 예상이 더 정확해져요.",
    },
  ],
};

export const PURCHASE_INTAKE: GoalIntakeConfig = {
  title: "매입해도 될지 알려드릴게요",
  subtitle: "상가 위치와 가격만 알려주시면 됩니다.",
  cta: "매입해도 될지 알려주세요",
  steps: [
    {
      kind: "location",
      title: "어느 상가인가요?",
      label: "매물 위치",
      placeholder: "주소나 상호를 입력하세요",
    },
    {
      kind: "numbers",
      title: "얼마에 나왔나요?",
      fields: [
        { key: "price_manwon", label: "매입가", unit: "만원", required: true },
        { key: "area_sqm", label: "전용면적", unit: "㎡", required: true },
        { key: "floor", label: "층" },
      ],
      hint: "전용면적은 등기부·매물표에 적힌 ㎡ 숫자를 그대로 넣으시면 됩니다.",
    },
    {
      kind: "numbers",
      title: "대출을 받으시나요?",
      fields: [
        { key: "loan_principal_manwon", label: "대출금액", unit: "만원" },
        { key: "loan_annual_rate", label: "금리", unit: "%" },
        { key: "loan_term_years", label: "기간", unit: "년" },
      ],
      toggles: [{ key: "loan_interest_only", label: "당분간 이자만 냅니다 (거치식)" }],
      hint: "대출이 없으면 비워 두세요.",
    },
    { kind: "upjong", title: "어떤 업종을 받을 자리인가요?", required: false, hint: "정해두신 업종이 있으면 임대 수입 예상이 더 정확해집니다." },
  ],
};

export const DIAGNOSE_INTAKE: GoalIntakeConfig = {
  title: "왜 매출이 낮은지 찾아드릴게요",
  subtitle: "지금 매장 상황을 알려주시면 원인을 짚어 드립니다.",
  cta: "왜 그런지 알려주세요",
  steps: [
    { kind: "upjong", title: "어떤 업종인가요?", required: true },
    {
      kind: "location",
      title: "어느 매장인가요?",
      label: "매장 위치",
      placeholder: "주소나 상호를 입력하세요",
    },
    {
      kind: "numbers",
      title: "요즘 한 달 매출이 얼마인가요?",
      fields: [{ key: "current_monthly_sales_man", label: "현재 월매출", unit: "만원", required: true }],
      hint: "최근 3개월 평균이면 충분합니다.",
    },
    {
      kind: "numbers",
      title: "월세는 얼마인가요?",
      fields: [{ key: "monthly_rent_man", label: "월세", unit: "만원" }],
      hint: "몰라도 괜찮습니다. 적으면 남는 돈까지 함께 봅니다.",
    },
  ],
};

export const AREA_INTAKE: GoalIntakeConfig = {
  title: "어디가 좋을지 찾아드릴게요",
  subtitle: "업종과 동네만 알려주시면 자리를 골라 드립니다.",
  cta: "어디가 좋을지 찾아주세요",
  steps: [
    { kind: "upjong", title: "어떤 업종인가요?", required: true },
    {
      kind: "location",
      title: "어느 동네를 보고 계세요?",
      label: "찾는 동네",
      placeholder: "예: 연남동, 신촌, 강남역",
      hint: "동네 이름처럼 넓은 범위도 괜찮아요.",
      allowFreeText: true,
    },
    {
      kind: "numbers",
      title: "월세는 얼마까지 감당하시나요?",
      fields: [{ key: "budget_max_rent_man", label: "감당 가능한 월세", unit: "만원" }],
      hint: "비워 두면 예산 제한 없이 찾습니다.",
    },
    { kind: "priority", title: "무엇이 더 중요한가요?" },
  ],
};
