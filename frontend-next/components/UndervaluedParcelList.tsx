"use client";

import { useEffect, useMemo, useState } from "react";
import { api, type UndervaluedParcel } from "@/lib/api";
import { ParcelMap } from "@/components/parcels/ParcelMap";
import {
  ParcelDetailPanel,
  type ParcelDetail,
} from "@/components/parcels/ParcelDetailPanel";
import { centerOf } from "@/lib/parcelMap";

/**
 * 저평가 판정 **2단계** — 동 하나 안의 필지 목록.
 *
 * 1단계(동)는 후보 구역을 좁힐 뿐이다. 동 평균은 같은 동 안 대로변과 이면도로의
 * 차이를 지우는데 **그 차이가 공간구문론이 파는 신호**라, 답은 여기 필지에 있다.
 *
 * 화면이 백분위를 본문으로 두고 금액을 보조로 두는 것은 표기 규칙이다.
 * 개별공시지가는 나지상정 평가액이고 시세반영률이 100%가 아니라서 "시세보다
 * 싸다"로 읽히면 안 된다 — 서버가 같은 취지의 note를 함께 보내고, 그 문구를
 * 백엔드 테스트가 지키고 있다.
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

/** 주소에서 "서울특별시 OO구 "를 덜어낸다 — 이미 동 카드 아래라 반복이다. */
function shortAddress(address: string): string {
  return address.replace(/^서울특별시\s+\S+구\s+/, "");
}

export function UndervaluedParcelList({ admCd }: { admCd: string }) {
  const [parcels, setParcels] = useState<UndervaluedParcel[]>([]);
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  // 지도에서 누른 필지와 목록에서 누른 필지가 같은 패널을 연다. 목록에 없는
  // 필지도 지도에서 눌릴 수 있으므로 둘을 아우르는 좁은 타입으로 든다.
  const [selected, setSelected] = useState<ParcelDetail | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    api
      .undervaluedParcels(admCd, 10)
      .then((res) => {
        if (cancelled) return;
        setParcels(res.parcels);
        setNote(res.note);
        // 동을 바꾸면 직전 동의 필지 상세가 남아 있으면 안 된다.
        setSelected(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "필지를 불러오지 못했습니다.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [admCd]);

  // 지도 중심은 목록이 바뀔 때만 다시 센다 — 매 렌더 새 객체를 주면 ParcelMap이
  // 지도를 통째로 다시 만든다.
  const center = useMemo(() => centerOf(parcels), [parcels]);

  if (loading) {
    return <p className="uv-status uv-status--nested">필지를 불러오는 중…</p>;
  }

  if (error) {
    return (
      <div className="uv-empty uv-empty--nested">
        <p className="uv-empty-body">{error}</p>
      </div>
    );
  }

  if (parcels.length === 0) {
    return (
      <div className="uv-empty uv-empty--nested">
        <p className="uv-empty-body">
          이 동에는 조건에 맞는 필지가 없습니다. 건축 가능한 대지 60㎡ 이상만
          봅니다.
        </p>
      </div>
    );
  }

  return (
    <div className="uv-parcels">
      <p className="uv-parcels-head">
        같은 동 · 같은 용도지역 필지끼리 비교한 상위 {parcels.length}곳
      </p>
      {center && (
        <ParcelMap
          center={center}
          selectedPnu={selected?.pnu ?? null}
          onSelect={setSelected}
        />
      )}
      {selected && (
        <ParcelDetailPanel parcel={selected} onClose={() => setSelected(null)} />
      )}
      <ol className="uv-parcel-list">
        {parcels.map((p) => (
          <li key={p.pnu} className="uv-parcel">
            <button
              type="button"
              className="uv-parcel-open"
              aria-pressed={selected?.pnu === p.pnu}
              onClick={() => setSelected(selected?.pnu === p.pnu ? null : p)}
            >
            <div className="uv-parcel-main">
              <span className="uv-parcel-addr">{shortAddress(p.address)}</span>
              <span className="uv-parcel-tags">
                <span className="uv-tag">
                  {ZONE_LABELS[p.zone_group] ?? p.zone_group}
                </span>
                {p.area_sqm != null && (
                  <span className="uv-tag uv-tag--quiet">
                    {Math.round(p.area_sqm)}㎡
                  </span>
                )}
              </span>
            </div>
            <div className="uv-parcel-metrics">
              <span className="uv-metric uv-metric--structure">
                <em>목</em>
                {Math.round(p.structure_percentile)}
              </span>
              <span className="uv-metric">
                <em>공시지가</em>
                {Math.round(p.price_percentile)}
              </span>
              <span className="uv-gap">
                {p.gap_score > 0 ? "+" : ""}
                {Math.round(p.gap_score)}
              </span>
            </div>
            <p className="uv-parcel-peer">
              같은 조건 {p.stratum_size.toLocaleString()}필지 중
            </p>
            </button>
          </li>
        ))}
      </ol>
      <p className="uv-note uv-note--nested">{note}</p>
    </div>
  );
}
