"use client";

/**
 * 필지 판정 카드 — Structured Analysis **하나만** 보고 그린다.
 *
 * LLM 호출이 0이다. 서술도 `analysis.narrative`에 이미 들어 있고(백엔드의 정적
 * 서술은 토큰 비용이 0이다), 이 컴포넌트는 판정을 다시 계산하지도 문구를 고르지도
 * 않는다. 임계값과 비교해 말투를 정하는 로직을 여기 두면 규칙이 두 곳에 생기고,
 * 그때부터 백엔드의 캘리브레이션이 화면에 반영되지 않는다.
 *
 * 화면이 반드시 지키는 것:
 *
 * 1. **금액은 개별공시지가다.** 시세로 읽히지 않게 이름을 붙이고 주의를 함께 낸다.
 * 2. **예상값은 범위로만** 보여준다 — 단일 확정값처럼 쓰지 않는다.
 * 3. **'시세 대비'가 아니라 '같은 조건 필지 대비'**로 적는다.
 * 4. 판정이 보류돼도 **금액·비교군·순위는 그대로 보인다.** 그 값들은 추정 모형이
 *    아니라 DB에 있는 사실이다.
 * 5. 신뢰도가 낮으면(`strength === "weak"`) 강조 표시를 걷는다.
 */

import type {
  Factor,
  PriceRange,
  StructuredAnalysis,
  VerdictLabel,
} from "@/lib/landAnalysis";
import { allowsAssertiveWording } from "@/lib/landAnalysis";
// 백분위 문구는 지도·목록과 같은 함수를 쓴다 — 여기서 따로 만들면 같은 값이
// 화면마다 다른 말로 나온다. 특히 가격은 낮은 쪽이 뜻이 있어서, 무조건 "상위"로
// 적으면 싼 필지가 비싼 것처럼 읽힌다(실제 화면에서 "상위 82%"로 나왔다).
import { percentileText } from "@/lib/parcelMap";

/** 판정별 색 띠. `undetermined`는 중립 회색 — 나쁜 뜻이 아니다. */
const LABEL_TONE: Record<VerdictLabel, string> = {
  undervalued: "la--under",
  cheap_with_reason: "la--cheap",
  aligned: "la--aligned",
  above_peers: "la--above",
  undetermined: "la--held",
};

/**
 * 원/㎡ → 읽을 수 있는 단위. **행마다 단위를 바꾸지 않는다.**
 *
 * 처음에 1천만원을 넘으면 "1.0천만원"으로 접었는데, 같은 카드 안에서 "830만원"과
 * "1.0천만원"이 나란히 나와 어느 쪽이 큰지 한눈에 안 보였다(실제 화면에서 확인).
 * 서울 필지는 대부분 만원 단위 세 자리 안에 들어오므로 만원으로 통일한다.
 */
function wonPerSqm(value: number): string {
  if (value >= 10_000) return `${Math.round(value / 10_000).toLocaleString()}만원`;
  return `${Math.round(value).toLocaleString()}원`;
}

function rangeText(range: PriceRange): string {
  return `${wonPerSqm(range.low)} ~ ${wonPerSqm(range.high)}/㎡`;
}

function FactorRow({ factor }: { factor: Factor }) {
  return (
    <li className={`la-factor la-factor--${factor.direction}`}>
      <span className="la-factor__mark" aria-hidden>
        {factor.direction === "positive" ? "+" : "−"}
      </span>
      <span className="la-factor__body">
        <span className="la-factor__label">{factor.label}</span>
        <span className="la-factor__mag">{factor.magnitude_label}</span>
        {factor.detail && <span className="la-factor__detail">{factor.detail}</span>}
      </span>
    </li>
  );
}

export function LandAnalysisCard({ analysis }: { analysis: StructuredAnalysis }) {
  const { verdict, peer_group: peers, official_price: official } = analysis;
  const valuation = analysis.current_valuation;
  const position = analysis.peer_position;
  const assertive = allowsAssertiveWording(verdict);

  const positives = analysis.factors.filter((f) => f.direction === "positive");
  const negatives = analysis.factors.filter((f) => f.direction === "negative");

  return (
    <article className={`la card ${LABEL_TONE[verdict.label]}`} aria-label="필지 판정">
      <header className="la-head">
        <p className="la-eyebrow">{analysis.subject.address}</p>
        {/* 강조는 신뢰도가 허락할 때만 — 낮으면 제목 무게를 낮춘다. */}
        <h4 className={`la-title${assertive ? "" : " la-title--soft"}`}>
          {verdict.headline}
        </h4>
        <p className="la-peers">{peers.description} 기준</p>
      </header>

      {analysis.narrative && analysis.narrative.paragraphs.length > 0 && (
        <div className="la-narrative">
          {analysis.narrative.paragraphs.map((p, i) => (
            <p key={i}>{p}</p>
          ))}
        </div>
      )}

      {official && (
        <section className="la-price">
          <div className="la-price__row">
            <span className="la-price__label">{official.label}</span>
            <strong className="la-price__value">
              {wonPerSqm(official.per_sqm)}/㎡
              <span className="la-price__year"> ({official.base_year}년)</span>
            </strong>
          </div>
          {valuation && (
            <div className="la-price__row">
              {/* '시세 대비'가 아니다 — 비교 기준을 문구에 박아 둔다. */}
              <span className="la-price__label">비슷한 조건 필지로 미루어 본 값</span>
              <strong className="la-price__value la-price__value--range">
                {rangeText(valuation.expected_official_price)}
              </strong>
            </div>
          )}
          {peers.median_official_price_per_sqm != null && (
            <div className="la-price__row la-price__row--muted">
              <span className="la-price__label">비교군 중앙값</span>
              <span className="la-price__value">
                {wonPerSqm(peers.median_official_price_per_sqm)}/㎡
              </span>
            </div>
          )}
          <p className="la-disclaimer">{official.disclaimer}</p>
        </section>
      )}

      {valuation && (
        <section className="la-split">
          <h5 className="la-h5">값이 낮은 이유가 설명되는가</h5>
          {/* 두 몫을 나란히 — '싸다'와 '저평가다'가 다르다는 것이 이 그림의 요점이다. */}
          <div className="la-split__bar" role="img"
            aria-label={`단점이 설명하는 몫 ${Math.round(valuation.discount.explained_pct)}%, 설명되지 않는 몫 ${Math.round(valuation.discount.unexplained_pct)}%`}>
            <i
              className="la-split__explained"
              style={{ flexGrow: Math.max(0, valuation.discount.explained_pct) }}
            />
            <i
              className="la-split__unexplained"
              style={{ flexGrow: Math.max(0, valuation.discount.unexplained_pct) }}
            />
          </div>
          <dl className="la-split__legend">
            <div>
              <dt>단점이 설명하는 몫</dt>
              <dd>{Math.round(valuation.discount.explained_pct)}%</dd>
            </div>
            <div>
              <dt>설명되지 않는 몫</dt>
              <dd>{Math.round(valuation.discount.unexplained_pct)}%</dd>
            </div>
          </dl>
          <p className="la-split__note">
            {valuation.is_undervalued
              ? "단점을 다 감안해도 남는 차이가 있습니다."
              : valuation.is_cheap
                ? "값이 낮지만 그 차이는 단점으로 대부분 설명됩니다."
                : "같은 조건 필지들과 비슷한 수준입니다."}
          </p>
        </section>
      )}

      {position && (position.structure_percentile != null || position.price_percentile != null) && (
        <section className="la-bars">
          <h5 className="la-h5">비교군 안에서의 자리</h5>
          {position.structure_percentile != null && (
            <div className="la-bar">
              <span className="la-bar__label">목(길 구조)</span>
              <span className="la-bar__track">
                <i style={{ width: `${position.structure_percentile}%` }} />
              </span>
              <span className="la-bar__val">
                {percentileText(position.structure_percentile)}
              </span>
            </div>
          )}
          {position.price_percentile != null && (
            <div className="la-bar">
              <span className="la-bar__label">공시지가</span>
              <span className="la-bar__track la-bar__track--price">
                <i style={{ width: `${position.price_percentile}%` }} />
              </span>
              <span className="la-bar__val">
                {percentileText(position.price_percentile)}
              </span>
            </div>
          )}
        </section>
      )}

      {(positives.length > 0 || negatives.length > 0) && (
        <section className="la-factors">
          <h5 className="la-h5">이 땅의 조건</h5>
          <ul>
            {positives.map((f) => (
              <FactorRow key={f.key} factor={f} />
            ))}
            {negatives.map((f) => (
              <FactorRow key={f.key} factor={f} />
            ))}
          </ul>
        </section>
      )}

      {analysis.growth_outlook && (
        <section className="la-growth">
          <h5 className="la-h5">지난 {analysis.growth_outlook.horizon_years}년 흐름</h5>
          <dl className="la-growth__grid">
            {analysis.growth_outlook.own_growth_pct != null && (
              <div>
                <dt>이 땅</dt>
                <dd>{analysis.growth_outlook.own_growth_pct.toFixed(1)}%</dd>
              </div>
            )}
            {analysis.growth_outlook.peer_growth_pct != null && (
              <div>
                <dt>비슷한 조건 필지</dt>
                <dd>{analysis.growth_outlook.peer_growth_pct.toFixed(1)}%</dd>
              </div>
            )}
            {analysis.growth_outlook.excess_growth_pct != null && (
              <div className="la-growth__excess">
                <dt>차이</dt>
                <dd>
                  {analysis.growth_outlook.excess_growth_pct > 0 ? "+" : ""}
                  {analysis.growth_outlook.excess_growth_pct.toFixed(1)}%
                </dd>
              </div>
            )}
          </dl>
        </section>
      )}

      {analysis.price_forecast && analysis.price_forecast.horizons.length > 0 && (
        <section className="la-forecast">
          <h5 className="la-h5">앞으로의 공시지가 범위</h5>
          <ul className="la-forecast__list">
            {analysis.price_forecast.horizons.map((h) => (
              <li key={h.years}>
                <span className="la-forecast__when">{h.years}년 뒤</span>
                {/* 범위로만 — 단일 확정값처럼 보이면 감정평가액으로 읽힌다. */}
                <span className="la-forecast__range">
                  {rangeText(h.expected_official_price)}
                </span>
              </li>
            ))}
          </ul>
          <p className="la-forecast__note">
            확정된 값이 아니라 지금 조건이 이어질 때의 범위입니다.
          </p>
        </section>
      )}

      {analysis.attributes.length > 0 && (
        <section className="la-facts">
          <h5 className="la-h5">토지 정보</h5>
          <dl>
            {analysis.attributes.map((a) => (
              <div key={a.key} className="la-facts__row">
                <dt>{a.label}</dt>
                <dd>
                  {a.value}
                  {a.note && <span className="la-facts__note">{a.note}</span>}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      <footer className="la-foot">
        <p className="la-confidence">
          신뢰도 {{ high: "높음", medium: "보통", low: "낮음" }[verdict.confidence.level]} ·
          비교 필지 {verdict.confidence.peer_count.toLocaleString()}개
        </p>
        {verdict.confidence.reasons.length > 0 && (
          <ul className="la-confidence__reasons">
            {verdict.confidence.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        )}
        <ul className="la-caveats">
          {analysis.caveats.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      </footer>
    </article>
  );
}
