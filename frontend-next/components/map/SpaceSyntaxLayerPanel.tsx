"use client";

import { METRIC_INFO, METRIC_ORDER, type MetricKey } from "@/lib/metricInfo";

/**
 * 길 구조 지표(Space Syntax) 레이어를 켜는 자리.
 *
 * **왜 접어 두는가.** 지금 당장 창업하려는 사람에게 필요한 건 유동인구·매출
 * 같은 "지금" 값이다. 거기에 통합도·선택도까지 한 화면에 쏟아 놓으면 무슨
 * 뜻인지 몰라서 전부 안 보게 된다. 그래서 기본은 접고, 필요한 사람만 편다.
 *
 * **왜 지우지 않는가.** 감정평가사·개발·토지 경매를 하는 사람에게는 이 값이
 * 핵심이다. 유동인구와 매출은 결과이고, 길 구조는 그 결과를 만드는 원인이다.
 * 다른 플랫폼이 안 주는 게 이 축이라 우리 차별점이기도 하다.
 *
 * **설명을 같이 두는 이유.** 값만 보여 주면 "빨간 길이 좋은 길" 정도로만
 * 읽힌다. 이 지표의 쓸모는 *현재 실적과 어긋날 때* 나온다 — 구조는 좋은데
 * 아직 매출이 안 나오는 골목, 반대로 지금 잘 되지만 구조가 안 받쳐 주는 곳.
 * 그 읽는 법을 옆에 적어 두지 않으면 지표가 있어도 쓰이지 않는다.
 */
export function SpaceSyntaxLayerPanel({
  metric,
  onMetricChange,
  roadLayer,
  onRoadLayerChange,
}: {
  metric: string;
  onMetricChange: (next: MetricKey) => void;
  roadLayer: boolean;
  onRoadLayerChange: (next: boolean) => void;
}) {
  return (
    <details className="sslayer">
      <summary className="sslayer__summary">
        길 구조로 보는 <strong>미래 가치</strong>
        <span className="sslayer__badge">전문가</span>
      </summary>

      <div className="sslayer__body">
        <p className="sslayer__lede">
          유동인구·매출은 <strong>지금</strong>을 보여줍니다. 이 지표는 다릅니다 —
          길이 어떻게 이어져 있는지, 그래서 사람이 <strong>지나갈 수밖에 없는 자리</strong>인지를 봅니다.
        </p>

        <p className="sslayer__note">
          가게와 건물은 몇 년이면 바뀌지만 길은 잘 안 바뀝니다. 그래서 이 값은
          쉽게 흔들리지 않는 바탕이 됩니다.
        </p>

        <ul className="sslayer__read">
          <li>
            <span className="sslayer__read-tag sslayer__read-tag--up">구조 높음 · 지금 매출 낮음</span>
            아직 안 터진 자리일 수 있습니다. 올라올 여지를 봅니다.
          </li>
          <li>
            <span className="sslayer__read-tag sslayer__read-tag--down">구조 낮음 · 지금 매출 높음</span>
            유행이나 특정 건물에 기대고 있을 수 있습니다.
          </li>
        </ul>

        <p className="sslayer__caveat">
          단정이 아니라 <strong>길 구조가 주는 신호</strong>입니다. 실제 판단은
          유동인구·매출과 함께 보셔야 합니다.
        </p>

        <p className="sslayer__who">감정평가 · 개발 · 토지 경매에서 보는 값입니다.</p>

        <fieldset className="form-fieldset sslayer__pick">
          <legend>지도 색으로 볼 값</legend>
          <div className="metric-options">
            {METRIC_ORDER.map((value) => (
              <label className="radio-label metric-option" key={value}>
                <input
                  type="radio"
                  checked={metric === value}
                  onChange={() => onMetricChange(value)}
                />{" "}
                {METRIC_INFO[value].easy}
                <span
                  className="metric-info-icon"
                  title={METRIC_INFO[value].explain}
                  aria-label={`${METRIC_INFO[value].easy} 설명`}
                  role="img"
                >
                  ⓘ
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        <label className="radio-label">
          <input
            type="checkbox"
            checked={roadLayer}
            onChange={(e) => onRoadLayerChange(e.target.checked)}
          />{" "}
          지도에 길 색칠하기
        </label>
        <p className="sslayer__legend-hint">
          색이 무엇을 뜻하는지는 지도 아래 범례에 나옵니다.
        </p>
      </div>
    </details>
  );
}
