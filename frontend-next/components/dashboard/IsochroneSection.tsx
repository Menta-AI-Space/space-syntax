"use client";

import { useEffect } from "react";
import type { IsochroneResponse } from "@/lib/api";
import { formatDashNum, ISO_MODE_LABELS } from "./dashboardUtils";
import { clearReach, publishReachContext, type ReachMode } from "@/lib/reachCircle";

const MODE_ORDER = ["walk", "drive", "bike", "moto"];

interface IsochroneSectionProps {
  iso: IsochroneResponse | null;
  categoryLabel?: string;
  lat: number | null;
  lng: number | null;
  activeMinutes: number;
  selectedMinutes: number[];
  loading: boolean;
  onModeChange: (mode: string) => void;
  onMinutesToggle: (minutes: number) => void;
  onBandClick: (minutes: number) => void;
}

export function IsochroneSection({ iso, categoryLabel, lat, lng, activeMinutes, loading, onModeChange, onBandClick }: IsochroneSectionProps) {
  const mode = iso?.mode || "walk";

  // 도달권 컨텍스트를 지도 슬라이더(ReachSlider)+원(KakaoMap)에 공유한다.
  // 밴드 실측거리(max_distance_m)를 앵커로 넘겨, 슬라이더 1분 단위 값은
  // 그 사이를 선형보간해 "실제 반경 기반"으로 원을 그린다.
  const bandMinutes = (iso?.bands || []).map((b) => b.minutes);
  const bandKey = bandMinutes.join(",");
  const distancesKey = (iso?.bands || []).map((b) => `${b.minutes}:${Math.round(b.max_distance_m)}`).join(",");
  useEffect(() => {
    if (lat == null || lng == null || bandMinutes.length === 0) {
      clearReach();
      return;
    }
    const distancesM: Record<number, number> = {};
    for (const band of iso?.bands || []) distancesM[band.minutes] = band.max_distance_m;
    publishReachContext({ lat, lng, mode: mode as ReachMode, distancesM, bandMinutes });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lat, lng, mode, bandKey, distancesKey]);

  // 카드가 사라지면(다른 탭·재분석) 지도 원·슬라이더도 함께 지운다.
  useEffect(() => () => clearReach(), []);

  const modeLabel = ISO_MODE_LABELS[mode] || mode;
  const compCol = categoryLabel ? `경쟁 ${categoryLabel}` : "경쟁 점포";

  if (!iso?.bands?.length) {
    return (
      <section className="dash-card dash-card--isochrone" data-dash="isochrone">
        <header className="dash-card-header">
          <span className="dash-card-icon" aria-hidden="true">📍</span>
          <h3>도달권 분석</h3>
        </header>
        <p className="demand-meta">도로망에 연결되지 않아 도달권을 계산할 수 없습니다.</p>
      </section>
    );
  }

  return (
    <section className={`dash-card dash-card--isochrone${loading ? " dash-card--loading" : ""}`} data-dash="isochrone">
      <header className="dash-card-header">
        <span className="dash-card-icon" aria-hidden="true">📍</span>
        <h3>도달권 분석 · {modeLabel}</h3>
        <span className="dash-card-badge">{modeLabel}</span>
      </header>
      <div className="dash-isochrone-layout dash-isochrone-layout--maponly">
        <table className="dash-table">
          {/* 가구 열은 늘 "—"였다 — 도달권 밴드에는 가구 수가 없다(백엔드
              IsochroneBand에 그 필드가 없고, 가구 추정치는 반경 단위로만
              나온다). 채울 수 없는 값을 열로 약속해 두면 데이터가 빠진 것처럼
              보인다. */}
          <thead><tr><th>{modeLabel} 도달</th><th>인구</th><th>{compCol}</th></tr></thead>
          <tbody>
            {iso.bands.map((b) => (
              <tr
                key={b.minutes}
                className={b.minutes === activeMinutes ? "dash-band-row--active" : ""}
                onClick={() => onBandClick(b.minutes)}
              >
                <td>{b.minutes}분 <span className="dash-band-dist">~{Math.round(b.max_distance_m)}m</span></td>
                <td>{formatDashNum(b.population_estimate)}</td>
                <td>{b.same_category_count}곳</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="dash-mode-toggles" role="group" aria-label="이동 수단">
        {MODE_ORDER.map((m) => (
          <button
            key={m}
            type="button"
            className={`dash-mode-btn${m === mode ? " dash-mode-btn--active" : ""}`}
            onClick={() => onModeChange(m)}
          >
            {ISO_MODE_LABELS[m] || m}
          </button>
        ))}
      </div>
      {(iso.warnings || []).map((w, i) => <p key={i} className="demand-meta dash-iso-warn">{w}</p>)}
      <p className="dash-footnote">※ 도로망 기준 도달권. <strong>지도 위 시간 슬라이더</strong>를 1분 단위로 드래그하면 원이 그 시간의 실제 도로망 반경으로 커지고 작아집니다. 표 행을 누르면 동일업종 목록 시간대가 바뀝니다. 배달·오토바이는 차량도로망, 자전거는 보행로망 기준입니다.</p>
    </section>
  );
}
