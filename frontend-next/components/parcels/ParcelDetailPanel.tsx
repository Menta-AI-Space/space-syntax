"use client";

import { gapBand, percentileText } from "@/lib/parcelMap";

/**
 * 이 패널이 필요한 필드만. `MapParcel`(지도에서 누른 것)과
 * `UndervaluedParcel`(목록에서 누른 것)이 둘 다 이 모양을 만족한다 — 같은 설명을
 * 두 벌 쓰지 않기 위해 좁은 타입을 여기서 정의한다. 기하는 쓰지 않는다.
 */
export interface ParcelDetail {
  pnu: string;
  address: string;
  land_category: string | null;
  zone_name?: string | null;
  zone_group: string | null;
  road_side_name: string | null;
  area_sqm: number | null;
  official_price_per_sqm: number | null;
  gap_score: number | null;
  structure_percentile: number | null;
  price_percentile: number | null;
  stratum_size: number | null;
}

/**
 * 필지 하나를 눌렀을 때 읽는 설명.
 *
 * **표기 규칙이 이 화면의 전부다.** `official_price_per_sqm`은 개별공시지가이지
 * 시세도 감정평가액도 아니다(`CONTEXT.md`). 나지상정 평가액이고 시세반영률이
 * 100%가 아니라서, 금액을 크게 띄우면 "이 땅이 이 값이다"로 읽힌다. 그래서
 * **본문은 백분위이고 금액은 보조**로 둔다.
 *
 * 판정이 없는 필지도 열린다. 그 경우 갭 대신 "왜 판정하지 않았는지"를 말한다 —
 * 빈 칸을 두면 사용자가 0으로 읽는다.
 */

const ZONE_LABELS: Record<string, string> = {
  residential_1_exclusive: "1종전용주거",
  residential_2_exclusive: "2종전용주거",
  residential_1_general: "1종일반주거",
  residential_2_general: "2종일반주거",
  residential_3_general: "3종일반주거",
  residential_semi: "준주거",
  commercial_central: "중심상업",
  commercial_general: "일반상업",
  commercial_neighbor: "근린상업",
  commercial_distribution: "유통상업",
  industrial_exclusive: "전용공업",
  industrial_general: "일반공업",
  industrial_semi: "준공업",
  green_conservation: "보전녹지",
  green_production: "생산녹지",
  green_natural: "자연녹지",
};

/** 원/㎡ → 읽을 수 있는 단위. 백만 단위를 넘는 값이 대부분이다. */
function wonPerSqm(value: number | null): string {
  if (value == null) return "—";
  if (value >= 10_000_000) return `${(value / 10_000_000).toFixed(1)}천만원/㎡`;
  if (value >= 10_000) return `${Math.round(value / 10_000).toLocaleString()}만원/㎡`;
  return `${Math.round(value).toLocaleString()}원/㎡`;
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="pd-row">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

export function ParcelDetailPanel({
  parcel,
  onClose,
}: {
  parcel: ParcelDetail;
  onClose: () => void;
}) {
  const band = gapBand(parcel.gap_score);
  const judged = parcel.gap_score != null;

  return (
    <aside className="pd" aria-label="필지 상세">
      <header className="pd-head">
        <div>
          <p className="pd-eyebrow">필지</p>
          <h4 className="pd-title">{parcel.address || parcel.pnu}</h4>
        </div>
        <button type="button" className="pd-close" onClick={onClose} aria-label="닫기">
          ×
        </button>
      </header>

      <div className="pd-verdict" style={{ borderColor: band.color }}>
        <span className="pd-verdict-dot" style={{ background: band.color }} aria-hidden />
        <div>
          <strong>{band.label}</strong>
          {judged ? (
            <span className="pd-verdict-num">
              갭 {parcel.gap_score! > 0 ? "+" : ""}
              {Math.round(parcel.gap_score!)}
            </span>
          ) : (
            <span className="pd-verdict-num">—</span>
          )}
        </div>
      </div>

      {judged ? (
        <>
          <div className="pd-bars">
            <div className="pd-bar">
              <span className="pd-bar-label">목(길 구조)</span>
              <span className="pd-bar-track">
                <i style={{ width: `${parcel.structure_percentile}%` }} />
              </span>
              <span className="pd-bar-val">
                {percentileText(parcel.structure_percentile!)}
              </span>
            </div>
            <div className="pd-bar">
              <span className="pd-bar-label">공시지가</span>
              <span className="pd-bar-track pd-bar-track--price">
                <i style={{ width: `${parcel.price_percentile}%` }} />
              </span>
              <span className="pd-bar-val">
                {percentileText(parcel.price_percentile!)}
              </span>
            </div>
          </div>
          <p className="pd-explain">
            같은 동 · 같은 용도지역 {parcel.stratum_size?.toLocaleString()}필지와
            비교했습니다. 길 구조 순위가 공시지가 순위보다{" "}
            {parcel.gap_score! >= 0 ? "위" : "아래"}에 있습니다.
          </p>
        </>
      ) : (
        <p className="pd-explain">
          이 필지는 판정하지 않았습니다. 접한 도로의 구조값·용도지역·공시지가 중
          하나라도 없으면 판정에서 뺍니다 — 없는 값을 0으로 채우면 &ldquo;구조가
          나쁜 땅&rdquo;과 &ldquo;값을 못 붙인 땅&rdquo;이 섞입니다.
        </p>
      )}

      <dl className="pd-facts">
        <Row
          label="용도지역"
          value={
            parcel.zone_name ||
            (parcel.zone_group ? (ZONE_LABELS[parcel.zone_group] ?? parcel.zone_group) : "—")
          }
        />
        <Row label="지목" value={parcel.land_category || "—"} />
        <Row
          label="면적"
          value={parcel.area_sqm != null ? `${Math.round(parcel.area_sqm).toLocaleString()}㎡` : "—"}
        />
        <Row label="도로접면" value={parcel.road_side_name || "—"} />
        <Row label="개별공시지가" value={wonPerSqm(parcel.official_price_per_sqm)} />
      </dl>

      <p className="pd-note">
        개별공시지가는 나지(裸地)를 상정해 행정이 매긴 값이고 시세가 아닙니다.
        값이 낮다·높다는 금액이 아니라 <strong>같은 조건 필지들 사이의 순위 차이</strong>를
        말합니다. 값이 높은 것을 &ldquo;고평가&rdquo;로 읽지 마세요 — 우리가 보지 않는
        요인(한강뷰·학군·역세권)으로 높을 수 있습니다.
      </p>
    </aside>
  );
}
