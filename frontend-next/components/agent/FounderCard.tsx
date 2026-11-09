"use client";

import type { FounderVerdict, SiteScoreResponse } from "@/lib/api";

// 창업자 결정 카드 — 답의 맨 위에 서는 한 장(docs/SPEC_ANSWER_DECISION.md §2).
// 신호등과 문장은 엔진(services/founder_verdict.py)이 정한 그대로 그린다. 여기서
// 판정을 다시 계산하거나 문구를 바꾸지 않는다 — 화면과 백엔드가 어긋나면 사용자는
// 어느 쪽을 믿어야 할지 모른다.

const SIGNAL_ICON: Record<FounderVerdict["signal"], string> = { go: "🟢", conditional: "🟡", no: "🔴" };

/** 근거 줄의 점 다섯 개 — 좋을수록 많이 찬다. 경쟁은 많을수록 불리하므로 거꾸로. */
function Dots({ filled }: { filled: number }) {
  return (
    <span className="founder-card__dots" aria-hidden="true">
      {[0, 1, 2, 3, 4].map((i) => (
        <i key={i} className={i < filled ? "on" : ""} />
      ))}
    </span>
  );
}

const FLOW_DOTS = { good: 5, ok: 3, weak: 1 } as const;
const COMP_DOTS = { low: 4, high: 2, very_high: 1 } as const;
// 같은 업종 가게 수를 못 센 자리는 엔진이 competition을 "low"로 두고 라벨만 「확인 못 함」으로
// 바꾼다(founder_verdict.py). 그대로 그리면 점 넷이 찬 채 「확인 못 함」이라 「경쟁 적음」으로
// 읽힌다(2026-11-06 UX 자동 개선). 판정은 건드리지 않고 점만 비운다.
const COMP_UNKNOWN_LABEL = "확인 못 함";

// 손님 줄 — 엔진의 demand 점수(반경 안 주민 수 + 직장인 수, 업종에 맞는 연령 비중을
// 조금 섞음)를 그대로 쓴다. 「배후 수요」는 어려운 말이라 무엇을 셌는지 드러나게 쓴다.
// 문턱은 엔진의 score_qualitative_label(85/70/55/40)과 같다 — 글과 점이 어긋나지 않게.
function demandRow(site: SiteScoreResponse): { dots: number; text: string } | null {
  const score = site.subscores.find((s) => s.key === "demand")?.score;
  if (score == null) return null;
  const [dots, amount] =
    score >= 85 ? [5, "매우 많음"] : score >= 70 ? [4, "많음"] : score >= 55 ? [3, "보통"] : score >= 40 ? [2, "적음"] : [1, "매우 적음"];
  return { dots, text: `주변 주민·직장인 ${amount}` };
}

export function FounderCard({ verdict, site }: { verdict: FounderVerdict; site: SiteScoreResponse }) {
  const demand = demandRow(site);
  return (
    <section className={`founder-card founder-card--${verdict.signal}`} aria-label="결정">
      <p className="founder-card__headline">
        <span aria-hidden="true">{SIGNAL_ICON[verdict.signal]}</span> {verdict.headline}
      </p>
      <p className="founder-card__reason">{verdict.reason}</p>

      <p className="founder-card__rent">{verdict.rent_line}</p>

      <ul className="founder-card__rows">
        <li>
          <span className="founder-card__label">사람 흐름</span>
          <Dots filled={FLOW_DOTS[verdict.flow]} />
          <span>서울 상위 {Math.max(1, Math.round(verdict.integration_top_pct))}%</span>
        </li>
        {demand && (
          <li>
            <span className="founder-card__label">손님</span>
            <Dots filled={demand.dots} />
            <span>{demand.text}</span>
          </li>
        )}
        <li>
          <span className="founder-card__label">경쟁</span>
          <Dots filled={verdict.competition_label === COMP_UNKNOWN_LABEL ? 0 : COMP_DOTS[verdict.competition]} />
          <span>
            {verdict.competition_label}
            {verdict.same_category_count != null ? ` · 같은 업종 ${verdict.same_category_count}곳` : ""}
          </span>
        </li>
      </ul>

      {verdict.cautions.map((c) => (
        <p key={c} className="founder-card__caution">⚠ {c}</p>
      ))}
      <p className="founder-card__disclaimer">{verdict.disclaimer}</p>
    </section>
  );
}
