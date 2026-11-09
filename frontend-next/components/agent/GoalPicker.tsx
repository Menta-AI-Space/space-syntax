"use client";

import type { DecisionGoal } from "@/lib/api";

interface GoalOption {
  id: DecisionGoal;
  title: string;
  hint: string;
}

// 다섯 목적 모두 각자의 입력 화면과 판정 화면으로 이어진다.
const GOALS: GoalOption[] = [
  { id: "find_site", title: "가게를 열 장소를 찾고 있어요", hint: "동네를 말씀하시면 자리를 골라 드려요" },
  // 제목이 「두 후보지」였는데 설명·입력은 2~5곳이라 말이 어긋났다(2026-11-07 UX 자동 개선).
  { id: "compare_candidates", title: "후보지 몇 곳 중 어디가 나은지 비교하고 싶어요", hint: "2~5곳을 넣으면 순위와 계약 가능 월세를 알려드려요" },
  { id: "evaluate_listing", title: "현재 나온 매물을 계약해도 되는지 알고 싶어요", hint: "월세가 적정한지 보고 협상 조건까지 뽑아드려요" },
  { id: "evaluate_purchase", title: "상가를 매입해도 되는지 알고 싶어요", hint: "수익률과 회수 기간, 적정 매입가를 계산합니다" },
  { id: "diagnose_store", title: "기존 매장의 매출이 낮은 이유를 알고 싶어요", hint: "얼마나 덜 벌고 있는지와 원인 순위를 알려드려요" },
];

export function GoalPicker({ onPick }: { onPick: (goal: DecisionGoal) => void }) {
  return (
    <div className="agent-stage">
      <div className="card agent-card">
        <h2 className="agent-title">무엇을 결정하려고 하시나요?</h2>
        <p className="agent-subtitle">목적을 고르면 필요한 것만 물어보고, 숫자 대신 결론으로 답해 드립니다.</p>

        <ul className="goal-list">
          {GOALS.map((goal) => (
            <li key={goal.id}>
              <button type="button" className="goal-option" onClick={() => onPick(goal.id)}>
                <span className="goal-option-text">
                  <strong className="goal-option-title">{goal.title}</strong>
                  <span className="goal-option-hint">{goal.hint}</span>
                </span>
                <span className="goal-option-arrow" aria-hidden="true">→</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
