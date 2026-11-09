"use client";

import { useEffect, useRef, useState } from "react";
import { api, type FranchiseBrandDetail } from "@/lib/api";

// 창업비용 표기 — AreaInsightsSection.fmtManwon과 같은 억/만원 규칙.
function fmtCostManwon(value?: number | null): string {
  if (value === null || value === undefined) return "—";
  if (value >= 10_000) return `${(value / 10_000).toFixed(1)}억`;
  return `${Math.round(value).toLocaleString()}만원`;
}

function fmtCount(value?: number | null): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString();
}

// 공정위 가맹사업 정보공개서 기반 브랜드 카드. 데이터가 없거나(테이블 미적재
// → 404) 요청이 실패하면 아무것도 렌더하지 않고 조용히 사라진다.
export function FranchiseBrandCard({ brand }: { brand: string }) {
  const [detail, setDetail] = useState<FranchiseBrandDetail | null>(null);
  const seqRef = useRef(0);

  useEffect(() => {
    const name = brand.trim();
    // 매 변경마다 시퀀스를 올려서 늦게 도착한 이전 브랜드 응답이 최신 상태를
    // 덮어쓰지 못하게 한다 (BusinessPicker와 같은 레이스 가드 패턴).
    const seq = ++seqRef.current;
    if (!name) {
      setDetail(null);
      return;
    }
    const timer = window.setTimeout(() => {
      api.franchiseBrandDetail(name)
        .then((res) => { if (seq === seqRef.current) setDetail(res && res.brand_nm ? res : null); })
        .catch(() => { if (seq === seqRef.current) setDetail(null); });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [brand]);

  if (!detail) return null;

  return (
    <div className="card">
      <h3>프랜차이즈 브랜드</h3>
      <ul className="insights-trades">
        <li>
          <span className="insights-trade-name">브랜드명</span>
          <span>{detail.brand_nm}{detail.hq_conm ? ` (${detail.hq_conm})` : ""}</span>
        </li>
        <li>
          <span className="insights-trade-name">가맹점 수</span>
          <span>{fmtCount(detail.frcs_cnt)}곳</span>
        </li>
        <li>
          <span className="insights-trade-name">신규 개점 / 계약 종료·해지{detail.yr != null ? ` (${detail.yr}년 기준)` : ""}</span>
          <span>{fmtCount(detail.new_open_cnt)} / {fmtCount(detail.contract_end_cnt)}·{fmtCount(detail.contract_cancel_cnt)}</span>
        </li>
        <li>
          <span className="insights-trade-name">폐점률</span>
          <span>{detail.churn_pct != null ? `${detail.churn_pct}%` : "—"}</span>
        </li>
        <li>
          <span className="insights-trade-name">창업비용</span>
          <span>{fmtCostManwon(detail.startup_cost_total_manwon)}</span>
        </li>
      </ul>
      {detail.industry_mid && <p className="site-panel-meta">{detail.industry_mid} · 공정위 가맹사업 정보공개서</p>}
    </div>
  );
}
