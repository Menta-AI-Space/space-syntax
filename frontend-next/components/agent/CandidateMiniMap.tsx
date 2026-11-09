"use client";

import { useEffect, useRef, useState } from "react";
import { loadKakaoMaps } from "@/lib/kakao";
import {
  assignOverlapSlots,
  boundsForPoints,
  boxSpanM,
  cssVar,
  formatDistanceM,
  haversineM,
  isFiniteLatLng,
  overlapThresholdM,
  type LatLngPoint,
} from "@/lib/mapUtils";
import type { AreaCandidate } from "@/lib/api";

// 결과가 주소 문자열만 있으면 "그래서 여기가 어디냐"를 알 수 없다. 순위 번호를
// 붙인 마커를 지도에 찍어 후보들의 위치 관계를 한눈에 보이게 한다.
// 분석 화면의 KakaoMap은 도로 폴리라인·지표 팔레트까지 얹은 무거운 컴포넌트라
// 여기서는 마커만 쓰는 가벼운 지도를 따로 둔다.
//
// 후보 개수·간격은 매번 다르다. 한 건물에 셋이 몰릴 수도, 서울 반대편까지
// 흩어질 수도, 한 곳뿐일 수도 있다. 축척·겹침 판정은 전부 좌표에서 계산한다.

/** 순위 배지 색 — tokens.css에 있는 색만 쓴다(새 팔레트를 만들지 않는다). */
const RANK_COLOR_TOKENS: [string, string][] = [
  ["--blue-50", "#2563eb"],
  ["--lime-37", "#059669"],
  ["--orange-39", "#d97706"],
  ["--purple-40", "#7c3aed"],
  ["--red-50", "#dc2626"],
];

/** 배지가 위로 쌓이는 최대 단수 — 더 쌓이면 화면 밖으로 나가 오히려 안 보인다. */
const MAX_MARKER_STACK = 4;

type MapPhase = "loading" | "ready" | "error";

interface RankedPoint extends LatLngPoint {
  rank: number;
  label: string;
}

/** 좌표가 있는 후보만, 순위를 붙여 뽑는다. */
function rankedPointsOf(candidates: AreaCandidate[]): RankedPoint[] {
  return candidates
    .map((c, index) => ({
      lat: c.lat as number,
      lng: c.lng as number,
      rank: index + 1,
      label: c.label || `후보 ${index + 1}`,
    }))
    // 좌표 없는 후보가 (0,0)으로 찍히면 지도가 아프리카 앞바다까지 늘어난다.
    .filter((p) => isFiniteLatLng(p));
}

/** 가장 멀리 떨어진 두 후보 사이 거리(m). 후보가 하나면 0. */
function maxPairDistanceM(points: LatLngPoint[]): number {
  let max = 0;
  for (let i = 0; i < points.length; i++) {
    for (let j = i + 1; j < points.length; j++) {
      max = Math.max(max, haversineM(points[i], points[j]));
    }
  }
  return max;
}

export function CandidateMiniMap({
  candidates,
  center,
}: {
  candidates: AreaCandidate[];
  center?: { lat: number; lng: number } | null;
}) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<{ relayout?: () => void } | null>(null);
  const [phase, setPhase] = useState<MapPhase>("loading");
  const [reason, setReason] = useState("");

  const points = rankedPointsOf(candidates);
  const total = candidates.length;
  const shown = points.length;
  const spread = maxPairDistanceM(points);
  // points는 매 렌더 새 배열이라 좌표 문자열로 비교한다.
  const pointsKey = points.map((p) => `${p.lat},${p.lng}`).join("|");

  // 실패 화면에서는 지도 div가 사라진다. 그 상태로 새 결과가 오면 ref가 없어
  // effect가 아무것도 못 하고 영영 오류 화면에 갇힌다 — 입력이 바뀌면 먼저
  // 로딩으로 되돌려 지도 div를 다시 붙인다.
  const [renderedKey, setRenderedKey] = useState(pointsKey);
  if (renderedKey !== pointsKey) {
    setRenderedKey(pointsKey);
    setPhase("loading");
    setReason("");
  }

  useEffect(() => {
    if (points.length === 0) return;
    const box = boxRef.current;
    if (!box) return;

    let cancelled = false;
    setPhase("loading");
    setReason("");

    loadKakaoMaps()
      .then(() => {
        if (cancelled || !boxRef.current) return;
        const kakao = window.kakao;
        const first = points[0];
        const map = new kakao.maps.Map(boxRef.current, {
          center: new kakao.maps.LatLng(center?.lat ?? first.lat, center?.lng ?? first.lng),
          level: 5,
        });
        mapRef.current = map;

        // 후보가 하나뿐이면 최소 폭(400m)이 보장돼 최대 배율로 튀지 않고,
        // 멀리 흩어져 있으면 사각형이 그만큼 커져 전부 들어온다.
        const fit = boundsForPoints(points, 400);
        const spanM = fit ? boxSpanM(fit) : 0;

        // 겹침 기준은 지도 축척에서 나온다 — 한 동네에 몰렸을 때와 서울 전역에
        // 흩어졌을 때 "화면에서 겹친다"의 미터 값이 다르기 때문이다.
        const viewportPx = boxRef.current.clientWidth || 320;
        const slots = assignOverlapSlots(points, overlapThresholdM(spanM, viewportPx, 46));

        points.forEach((point, index) => {
          const [token, fallback] = RANK_COLOR_TOKENS[(point.rank - 1) % RANK_COLOR_TOKENS.length];
          const el = document.createElement("div");
          el.className = "viz-rank-marker";
          el.style.setProperty("--marker-color", cssVar(token, fallback));
          el.style.setProperty("--marker-stack", String(Math.min(slots[index], MAX_MARKER_STACK)));
          // textContent로 넣어 라벨에 무엇이 들어와도 HTML로 해석되지 않게 한다.
          el.textContent = `${point.rank}위`;
          el.title = point.label;
          el.setAttribute("aria-label", `${point.rank}위 ${point.label}`);
          new kakao.maps.CustomOverlay({
            map,
            position: new kakao.maps.LatLng(point.lat, point.lng),
            content: el,
            yAnchor: 1,
            xAnchor: 0.5,
            // 앞 순위가 위에 오게 — 겹쳤을 때 1위가 가려지지 않는다.
            zIndex: Math.max(1, points.length - index),
          });
        });

        if (fit) {
          const bounds = new kakao.maps.LatLngBounds();
          bounds.extend(new kakao.maps.LatLng(fit.south, fit.west));
          bounds.extend(new kakao.maps.LatLng(fit.north, fit.east));
          // 배지는 지점 위쪽으로 뻗으므로 위 여백을 더 준다. 여백이 없으면
          // 가장자리 후보의 배지가 지도 밖으로 잘린다.
          map.setBounds(bounds, 56, 24, 16, 24);
        }

        requestAnimationFrame(() => mapRef.current?.relayout?.());
        setPhase("ready");
      })
      .catch((err) => {
        if (cancelled) return;
        setPhase("error");
        setReason(err instanceof Error ? err.message : "지도를 불러오지 못했습니다.");
      });

    return () => {
      cancelled = true;
      mapRef.current = null;
      // Kakao 지도에는 destroy가 없다 — 비우지 않으면 결과가 바뀔 때마다 지도가
      // 겹쳐 쌓인다.
      box.innerHTML = "";
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pointsKey, center?.lat, center?.lng]);

  useEffect(() => {
    if (phase !== "ready") return;
    const box = boxRef.current;
    if (!box || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => mapRef.current?.relayout?.());
    observer.observe(box);
    return () => observer.disconnect();
  }, [phase]);

  if (total === 0) return null;

  // 좌표가 하나도 없으면 지도를 그릴 수 없다. 그렇다고 아무 말 없이 사라지면
  // "지도가 있다가 없어졌네"로 읽힌다 — 왜 없는지 한 줄로 밝힌다.
  if (shown === 0) {
    return (
      <div className="card">
        <h3>후보 위치</h3>
        <p className="viz-note">후보 주소의 좌표를 얻지 못해 지도를 표시할 수 없습니다.</p>
      </div>
    );
  }

  if (phase === "error") {
    return (
      <div className="card">
        <h3>후보 위치</h3>
        <div className="viz-map-fallback">
          <strong>지도를 띄우지 못했지만 순위와 위치는 아래와 같습니다.</strong>
          <ol className="viz-fallback-list">
            {points.map((p) => (
              <li key={`${p.rank}-${p.lat},${p.lng}`}>
                {p.rank}위 {p.label} ({p.lat.toFixed(4)}, {p.lng.toFixed(4)})
              </li>
            ))}
          </ol>
          <p className="viz-map-fallback-reason">{reason}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <h3>후보 위치</h3>
      <div className="viz-map-wrap">
        <div
          ref={boxRef}
          className="viz-map viz-map--tall"
          role="img"
          aria-label={`후보 ${shown}곳의 위치 지도`}
          aria-busy={phase === "loading"}
        />
        {phase === "loading" && <div className="viz-map-skeleton">지도를 불러오는 중…</div>}
      </div>

      <p className="viz-legend">
        <span className="viz-legend-item">숫자 배지는 아래 카드의 순위와 같습니다.</span>
        {spread > 0 && (
          // 후보가 서로 멀면 지도가 확 줌아웃돼 "다 붙어 있네"로 오해하기 쉽다.
          // 실제 거리를 적어 축척을 읽을 수 있게 한다.
          <span className="viz-legend-item">
            가장 먼 두 후보 사이 약 {formatDistanceM(spread)}
          </span>
        )}
      </p>

      {shown < total && (
        <p className="viz-note">
          후보 {total}곳 가운데 {shown}곳만 좌표가 있어 지도에 표시했습니다.
        </p>
      )}
    </div>
  );
}
