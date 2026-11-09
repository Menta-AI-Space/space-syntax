"use client";

import {
  REACH_MODE_LABELS,
  formatDistance as formatRadius,
  interpolatedReachRadiusM,
  setReachMinute,
  useReachState,
} from "@/lib/reachCircle";

/**
 * 지도 위 세로 슬라이더 — 도달권 시간을 1분 단위로 조절한다.
 * 드래그하면 KakaoMap의 도달권 원이 그 시간의 실측 도로망 반경으로
 * 즉시 커지고 작아진다(reachCircle 스토어 구독). 도달권 컨텍스트가
 * 없으면(분석 전·비도달권 탭) 아무것도 그리지 않는다.
 */
export function ReachSlider() {
  const reach = useReachState();
  if (!reach) return null;

  const { mode, minute, minMinute, maxMinute, bandMinutes, distancesM } = reach;
  const radius = interpolatedReachRadiusM(mode, minute, distancesM, bandMinutes);
  const modeLabel = REACH_MODE_LABELS[mode] || mode;

  return (
    <div className="reach-slider" role="group" aria-label="도달권 시간 조절">
      <div className="reach-slider__readout">
        <span className="reach-slider__mode">{modeLabel}</span>
        <strong className="reach-slider__minute">{minute}분</strong>
        <span className="reach-slider__radius">반경 {formatRadius(radius)}</span>
      </div>
      <div className="reach-slider__track-wrap">
        <span className="reach-slider__cap reach-slider__cap--top">{maxMinute}분</span>
        <input
          className="reach-slider__input"
          type="range"
          min={minMinute}
          max={maxMinute}
          step={1}
          value={minute}
          aria-label={`도달권 시간 ${minute}분`}
          aria-valuetext={`${minute}분, 반경 약 ${formatRadius(radius)}`}
          onChange={(e) => setReachMinute(Number(e.target.value))}
          list="reach-slider-bands"
        />
        <datalist id="reach-slider-bands">
          {bandMinutes.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
        <span className="reach-slider__cap reach-slider__cap--bottom">{minMinute}분</span>
      </div>
    </div>
  );
}
