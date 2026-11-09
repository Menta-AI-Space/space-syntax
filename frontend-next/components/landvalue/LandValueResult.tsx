"use client";

/**
 * 토지가치 결과 화면 — 상단 Summary + 6탭. `StructuredAnalysis` **하나만** 본다.
 *
 * `/demo`의 같은 화면과 **코드를 공유하지 않는다.** 데모는 발표 자산이라 숫자와
 * 문구가 고정돼 있어야 하고, 이쪽은 없는 값을 비워 두어야 한다 — 한 컴포넌트로
 * 묶으면 한쪽 요구가 다른 쪽을 망가뜨린다. 생김새만 데모를 따른다.
 *
 * ⚠️ **여기서 판정 문구를 만들지 않는다.** 제목·요인 라벨·주의는 백엔드가
 * 완성해 보낸다(`lib/landAnalysis.ts` 계약). 임계값과 비교해 화면이 말을 고르기
 * 시작하면 규칙이 두 곳에 생기고, G7 캘리브레이션이 화면에 반영되지 않는다.
 *
 * ⚠️ **없는 값을 채우지 않는다.** 추정 모형이 아직 없어서 실서비스 응답은
 * `current_valuation`·`growth_outlook`·`price_forecast`가 `null`로 온다. 그 탭은
 * 왜 비었는지를 말하고 비운다(`landValueTabs.missingReason`).
 */

import { useMemo, useState } from "react";

import type { Factor, StructuredAnalysis } from "@/lib/landAnalysis";
import {
  LAND_TABS,
  discountLabels,
  type LandTabId,
  defaultTab,
  missingReason,
  percentileText,
  rangeText,
  signedPct,
  summaryRows,
  tabHasData,
  verdictTone,
  wonPerSqm,
} from "@/lib/landValueTabs";

export function LandValueResult({ analysis }: { analysis: StructuredAnalysis }) {
  // 필지가 바뀌면 탭도 그 필지에 맞는 default로 돌아간다. 앞 필지에서 열어 둔
  // 탭이 새 필지에서는 빈 탭일 수 있다.
  const [tabByPnu, setTabByPnu] = useState<Record<string, LandTabId>>({});
  const pnu = analysis.subject.pnu;
  const tab = tabByPnu[pnu] ?? defaultTab(analysis);
  const setTab = (next: LandTabId) => setTabByPnu((prev) => ({ ...prev, [pnu]: next }));

  const rows = useMemo(() => summaryRows(analysis), [analysis]);
  const tone = verdictTone(analysis.verdict);

  return (
    <article className="lv">
      <header className="lv-head">
        <p className="lv-eyebrow">
          {analysis.subject.address} · 비교군 {analysis.peer_group.size.toLocaleString("ko-KR")}필지
        </p>
        <h2 className="lv-title">토지가치 종합분석</h2>

        {rows.length > 0 && (
          <div className="lv-sum">
            {rows.map((row) => (
              <div key={row.key}>
                <span className="lv-sk">{row.key}</span>
                <span className={`lv-sv lv-sv--${row.tone}`}>{row.value}</span>
                {row.note && <span className="lv-snote">{row.note}</span>}
              </div>
            ))}
          </div>
        )}

        <p className={`lv-verdict lv-verdict--${tone}`}>
          종합 판정 <b>{analysis.verdict.headline}</b>
        </p>
      </header>

      <nav className="lv-tabs" role="tablist" aria-label="토지가치 분석 탭">
        {LAND_TABS.map((t) => {
          const has = tabHasData(analysis, t.id);
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className={`lv-tab${tab === t.id ? " lv-tab--on" : ""}${has ? "" : " lv-tab--empty"}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
              {/* 빈 탭임을 누르기 전에 알린다 — 눌러 보고 알게 하면 여섯 번 누른다. */}
              {!has && <span className="lv-tab-dot" aria-label="아직 값 없음" />}
            </button>
          );
        })}
      </nav>

      <div className="lv-body">
        <TabPane analysis={analysis} id={tab} onTab={setTab} />
      </div>

      <footer className="lv-foot">
        <div className="lv-foot-grid">
          <span>
            공시지가 기준연도 <b>{analysis.official_price?.base_year ?? analysis.subject.base_year}</b>
          </span>
          <span>
            모형 <b>{analysis.audit.model_version}</b>
          </span>
          <span>
            판정 규칙 <b>{analysis.audit.rules_version}</b>
          </span>
          <span>
            비교군 <b>{analysis.peer_group.level}</b>
          </span>
        </div>
        {analysis.caveats.map((caveat) => (
          <p key={caveat} className="lv-caveat">
            {caveat}
          </p>
        ))}
      </footer>
    </article>
  );
}

function TabPane({
  analysis,
  id,
  onTab,
}: {
  analysis: StructuredAnalysis;
  id: LandTabId;
  onTab: (t: LandTabId) => void;
}) {
  const def = LAND_TABS.find((t) => t.id === id)!;

  if (!tabHasData(analysis, id)) {
    return (
      <section className="lv-pane">
        <p className="lv-q">{def.question}</p>
        <Empty reason={missingReason(id)} />
      </section>
    );
  }

  return (
    <section className="lv-pane">
      <p className="lv-q">{def.question}</p>
      {id === "overview" && <Overview analysis={analysis} onTab={onTab} />}
      {id === "future" && <Future analysis={analysis} />}
      {id === "value" && <Value analysis={analysis} />}
      {id === "hotspot" && <Hotspot analysis={analysis} />}
      {id === "place" && <Place analysis={analysis} />}
      {id === "compare" && <Compare analysis={analysis} />}
    </section>
  );
}

function Empty({ reason }: { reason: string }) {
  return (
    <p className="lv-empty" role="note">
      {reason}
    </p>
  );
}

/* ── 종합 ─────────────────────────────────────────────────────────────── */

function Overview({
  analysis,
  onTab,
}: {
  analysis: StructuredAnalysis;
  onTab: (t: LandTabId) => void;
}) {
  const others = LAND_TABS.filter((t) => t.id !== "overview");
  return (
    <>
      {analysis.narrative && (
        <div className="lv-narrative">
          {analysis.narrative.paragraphs.map((p) => (
            <p key={p}>{p}</p>
          ))}
        </div>
      )}

      <h3 className="lv-h">탭마다 무엇을 답하는가</h3>
      <ul className="lv-jump">
        {others.map((t) => {
          const has = tabHasData(analysis, t.id);
          return (
            <li key={t.id}>
              <button type="button" className="lv-jump-btn" onClick={() => onTab(t.id)}>
                <span className="lv-jump-label">{t.label}</span>
                <span className={`lv-jump-state${has ? "" : " lv-jump-state--empty"}`}>
                  {has ? t.question : "아직 값 없음"}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </>
  );
}

/* ── 미래전망 ─────────────────────────────────────────────────────────── */

function Future({ analysis }: { analysis: StructuredAnalysis }) {
  const horizons = analysis.price_forecast?.horizons ?? [];
  const official = analysis.official_price;

  return (
    <>
      <div className="lv-scroll">
        <table className="lv-table">
          <thead>
            <tr>
              <th scope="col">시점</th>
              <th scope="col">예상 공시지가 (범위)</th>
              <th scope="col">연 환산</th>
            </tr>
          </thead>
          <tbody>
            {official && (
              <tr className="lv-row--self">
                <th scope="row">지금 ({official.base_year})</th>
                <td>{wonPerSqm(official.per_sqm)}</td>
                <td>—</td>
              </tr>
            )}
            {horizons.map((h) => (
              <tr key={h.years}>
                <th scope="row">{h.years}년 뒤</th>
                <td>
                  {rangeText(h.expected_official_price.low, h.expected_official_price.high)}
                </td>
                <td>{h.annualized_change_pct == null ? "—" : signedPct(h.annualized_change_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="lv-note">
        전망은 <b>확정된 값이 아니라 지금 조건이 이어질 때의 범위</b>입니다. 단일 금액이 아니라
        범위로 읽어 주세요. 인과 추정이 아니며 개통 전후 비교로 검증하지 않았습니다.
      </p>
    </>
  );
}

/* ── 가치평가 ─────────────────────────────────────────────────────────── */

function Value({ analysis }: { analysis: StructuredAnalysis }) {
  const { official_price: official, peer_position: position, current_valuation: valuation } = analysis;
  const labels = discountLabels(valuation?.discount.total_pct ?? 0);

  return (
    <>
      <div className="lv-pair">
        {official && (
          <div>
            <span className="lv-sk">
              {official.base_year} {official.label}
            </span>
            <span className="lv-sv lv-sv--neutral">{wonPerSqm(official.per_sqm)}</span>
          </div>
        )}
        {valuation && (
          <div>
            <span className="lv-sk">조건이 비슷한 필지로 미루어 본 값</span>
            <span className="lv-sv lv-sv--neutral">
              {rangeText(
                valuation.expected_official_price.low,
                valuation.expected_official_price.high,
              )}
            </span>
          </div>
        )}
      </div>

      {official && <p className="lv-note">{official.disclaimer}</p>}

      {valuation ? (
        <>
          <h3 className="lv-h">{labels.heading}</h3>
          <ul className="lv-bars">
            <Bar label={labels.total} pct={valuation.discount.total_pct} kind="total" />
            <Bar label={labels.explained} pct={valuation.discount.explained_pct} kind="explained" />
            <Bar
              label={labels.unexplained}
              pct={valuation.discount.unexplained_pct}
              kind="unexplained"
            />
          </ul>
          <p className="lv-note">
            {labels.isBelow ? (
              <>
                <b>단점으로 설명되지 않는 몫이 저평가의 정의</b>입니다. 단순히 싼 것과는 다릅니다
                — 맹지·부정형처럼 값을 설명하는 단점이 있으면 싸도 저평가가 아닙니다. 이 필지는{" "}
                {valuation.is_cheap ? "쌉니다" : "싸지 않습니다"},{" "}
                {valuation.is_undervalued ? "그리고 저평가입니다" : "그러나 저평가는 아닙니다"}.
              </>
            ) : (
              <>
                이 필지는 미루어 본 값보다 <b>높게</b> 매겨져 있습니다. 이것을 &ldquo;고평가&rdquo;로
                읽지 마세요 — 한강뷰·학군·역세권처럼 <b>모델이 보지 않는 요인</b>으로 비쌀 수
                있고, 우리는 그것을 재지 않았습니다.
              </>
            )}
          </p>
        </>
      ) : (
        <Empty reason="예상 공시지가를 내는 추정 모형이 아직 없어, 차이를 갈라 보여줄 수 없습니다." />
      )}

      {position && (
        <>
          <h3 className="lv-h">비교군 안에서의 자리</h3>
          <ul className="lv-kv">
            {position.price_percentile != null && (
              <li>
                <span>공시지가</span>
                <b>{percentileText(position.price_percentile)}</b>
              </li>
            )}
            {position.structure_percentile != null && (
              <li>
                <span>길 구조</span>
                <b>{percentileText(position.structure_percentile)}</b>
              </li>
            )}
            {position.gap_score != null && (
              <li>
                <span>갭 점수</span>
                <b>{position.gap_score.toFixed(1)}</b>
              </li>
            )}
          </ul>
          <p className="lv-note">
            갭 점수는 <b>추정 모형 없이</b> 같은 조건 필지들 안에서의 순위만으로 계산합니다 — 길
            구조는 좋은데 공시지가는 낮은 자리를 가립니다. 위의 「설명되지 않는 몫」과는 다른
            방법이라 두 값이 어긋날 수 있습니다 (
            {analysis.audit.rank_check.agreement === "agree"
              ? "이 필지는 두 방법이 같은 방향을 가리킵니다"
              : analysis.audit.rank_check.agreement === "disagree"
                ? "이 필지는 두 방법이 다른 방향을 가리킵니다"
                : "이 필지는 한쪽 값이 없어 맞대볼 수 없습니다"}
            ).
          </p>
        </>
      )}
    </>
  );
}

/**
 * 막대는 **크기만** 그린다. 방향은 이미 `discountLabels`가 말로 바꿔 놓았으므로
 * 여기서 부호까지 찍으면 "높은 폭 −30.0%"처럼 뜻이 뒤집힌 말이 나온다.
 */
function Bar({ label, pct, kind }: { label: string; pct: number; kind: string }) {
  const width = Math.max(0, Math.min(100, Math.abs(pct)));
  return (
    <li className="lv-bar">
      <span className="lv-bar-label">{label}</span>
      <span className="lv-bar-track">
        <span className={`lv-bar-fill lv-bar-fill--${kind}`} style={{ width: `${width}%` }} />
      </span>
      <b className="lv-bar-value">{Math.abs(pct).toFixed(1)}%</b>
    </li>
  );
}

/* ── 유망입지 ─────────────────────────────────────────────────────────── */

function Hotspot({ analysis }: { analysis: StructuredAnalysis }) {
  const outlook = analysis.growth_outlook!;
  return (
    <>
      <div className="lv-scroll">
        <table className="lv-table">
          <thead>
            <tr>
              <th scope="col">지난 {outlook.horizon_years}년 공시지가 상승</th>
              <th scope="col">이 필지</th>
              <th scope="col">같은 조건 필지들</th>
              <th scope="col">차이</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">상승률</th>
              <td>{outlook.own_growth_pct == null ? "—" : signedPct(outlook.own_growth_pct)}</td>
              <td>{outlook.peer_growth_pct == null ? "—" : signedPct(outlook.peer_growth_pct)}</td>
              <td>
                {outlook.excess_growth_pct == null ? "—" : signedPct(outlook.excess_growth_pct)}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {outlook.hot_probability != null && (
        <ul className="lv-kv">
          <li>
            <span>주변보다 빨리 오를 자리일 가능성</span>
            <b>{Math.round(outlook.hot_probability * 100)}%</b>
          </li>
        </ul>
      )}

      <p className="lv-note">
        <b>지나간 상승을 견준 값입니다.</b> 앞으로도 같은 차이가 이어진다는 뜻이 아니고, 어떤
        요인이 그 차이를 만들었는지도 여기서는 가리지 않습니다.
      </p>
    </>
  );
}

/* ── 입지·도시계획 ────────────────────────────────────────────────────── */

function Place({ analysis }: { analysis: StructuredAnalysis }) {
  const positive = analysis.factors.filter((f) => f.direction === "positive");
  const negative = analysis.factors.filter((f) => f.direction === "negative");

  return (
    <>
      {analysis.attributes.length > 0 && (
        <ul className="lv-kv">
          {analysis.attributes.map((attribute) => (
            <li key={attribute.key}>
              <span>{attribute.label}</span>
              <b>{attribute.value}</b>
              {attribute.note && <em>{attribute.note}</em>}
            </li>
          ))}
        </ul>
      )}

      {analysis.factors.length > 0 && (
        <>
          <h3 className="lv-h">값에 걸리는 것</h3>
          <div className="lv-factors">
            <FactorList title="올리는 쪽" factors={positive} />
            <FactorList title="내리는 쪽" factors={negative} />
          </div>
          <p className="lv-note">
            요인은 <b>이 필지의 토지특성에서 규칙으로 뽑은 것</b>이고, 학습된 모형의 기여도가
            아닙니다. 「내리는 쪽」에 <b>싼 이유</b> 표가 붙은 항목은 낮은 값을 설명하는 단점이라
            저평가에서 빠집니다.
          </p>
        </>
      )}
    </>
  );
}

function FactorList({ title, factors }: { title: string; factors: Factor[] }) {
  return (
    <div className="lv-factor-col">
      <h4 className="lv-factor-title">{title}</h4>
      {factors.length === 0 ? (
        <p className="lv-factor-none">없음</p>
      ) : (
        <ul>
          {factors.map((factor) => (
            <li key={factor.key}>
              <span className="lv-factor-label">{factor.label}</span>
              <span className="lv-factor-mag">{factor.magnitude_label}</span>
              {factor.explains_discount && <span className="lv-factor-tag">싼 이유</span>}
              {factor.detail && <span className="lv-factor-detail">{factor.detail}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ── 주변비교 ─────────────────────────────────────────────────────────── */

function Compare({ analysis }: { analysis: StructuredAnalysis }) {
  const peer = analysis.peer_group;
  const official = analysis.official_price;

  return (
    <>
      <p className="lv-peer">{peer.description}</p>

      <div className="lv-scroll">
        <table className="lv-table">
          <thead>
            <tr>
              <th scope="col">대상</th>
              <th scope="col">개별공시지가</th>
            </tr>
          </thead>
          <tbody>
            {official && (
              <tr className="lv-row--self">
                <th scope="row">이 필지</th>
                <td>{wonPerSqm(official.per_sqm)}</td>
              </tr>
            )}
            <tr>
              <th scope="row">비교군 중앙값 ({peer.size.toLocaleString("ko-KR")}필지)</th>
              <td>
                {peer.median_official_price_per_sqm == null
                  ? "—"
                  : wonPerSqm(peer.median_official_price_per_sqm)}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {peer.criteria.length > 0 && (
        <>
          <h3 className="lv-h">무엇을 같게 두고 견줬는가</h3>
          <ul className="lv-chips">
            {peer.criteria.map((criterion) => (
              <li key={criterion}>{criterion}</li>
            ))}
          </ul>
        </>
      )}

      <p className="lv-note">
        비교군의 <b>중앙값</b>만 견줍니다. 유사필지를 하나씩 늘어놓는 화면은 아직 없습니다 — 개별
        필지를 뽑아 오는 조회 경로가 없어서, 있는 척하지 않습니다.
      </p>
    </>
  );
}
