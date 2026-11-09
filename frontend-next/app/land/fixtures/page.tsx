"use client";

/**
 * 판정 카드 네 가지 상태를 한 화면에 — **백엔드 없이** 본다.
 *
 * fixture(`public/fixtures/structured-analysis.json`)는 백엔드가 실제 판정 규칙을
 * 통과시켜 생성한 것이라 스키마·규칙과 어긋날 수 없다. 그래서 이 화면은 데모가
 * 아니라 **검수 도구**다: 저평가·싼이유있음·같은조건대비높음·판정보류 넷을 나란히
 * 두고 표기 규칙(금액이 시세로 읽히지 않는지, 범위가 확정값처럼 보이지 않는지,
 * 비싼 쪽에 낙인이 없는지)을 눈으로 확인한다.
 *
 * 실사용 화면은 `/land`다. 여기는 fixture만 읽고 실제 필지를 부르지 않는다.
 *
 * 검수 대상은 **제품 화면인 6탭 결과**(`LandValueResult`)다. 특히 `undetermined`가
 * 지금 실서비스가 내보내는 모양이라(추정 모형 없음) 그 경우에 미래전망·유망입지
 * 탭이 "왜 비었는지"를 말하는지 여기서 눈으로 본다.
 */

import { useEffect, useState } from "react";
import { LandValueResult } from "@/components/landvalue/LandValueResult";
import { FIXTURE_URL, type FixturePayload } from "@/lib/landAnalysis";

const CASE_ORDER = ["undervalued", "cheap_with_reason", "above_peers", "undetermined"];

const CASE_TITLES: Record<string, string> = {
  undervalued: "저평가 — 설명되지 않는 차이가 크다",
  cheap_with_reason: "싸지만 이유가 설명된다",
  above_peers: "같은 조건 대비 높다 (낙인 표기 금지)",
  undetermined: "판정 보류 — 비교군이 얇다",
};

export default function LandFixturesPage() {
  const [payload, setPayload] = useState<FixturePayload | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(FIXTURE_URL)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(String(res.status)))))
      .then(setPayload)
      .catch(() => setError("fixture를 불러오지 못했습니다."));
  }, []);

  if (error) return <p style={{ padding: 24 }}>{error}</p>;
  if (!payload) return <p style={{ padding: 24 }}>불러오는 중…</p>;

  return (
    <div className="land-fixtures">
      <h1 className="land-fixtures__title">필지 판정 카드 — 네 가지 상태</h1>
      <p className="land-fixtures__note">{payload.note}</p>
      <div className="land-fixtures__grid">
        {CASE_ORDER.filter((key) => payload.cases[key]).map((key) => (
          <section key={key} className="land-fixtures__case">
            <h2 className="land-fixtures__case-title">{CASE_TITLES[key] ?? key}</h2>
            <LandValueResult analysis={payload.cases[key]} />
          </section>
        ))}
      </div>
    </div>
  );
}
