"use client";

import { useEffect, useRef, useState } from "react";
import { loadKakaoMaps } from "@/lib/kakao";
import {
  boundsForPoints,
  boundsForRadius,
  cssVar,
  formatDistanceM,
  isFiniteLatLng,
  normalizedSplit,
} from "@/lib/mapUtils";
import type { SiteScoreResponse } from "@/lib/api";
import { siteScoreWarnings } from "@/lib/siteScoreWarnings";

// 글로만 설명하면 "그래서 여기가 어디고, 뭐가 강한 거냐"가 안 잡힌다. 이미 응답에
// 실려 오는 값(좌표·연령/성비·서울 상위%)을 그림으로 보여 준다. 새 숫자를 만들지
// 않고 엔진이 낸 값만 그대로 그린다.
//
// 원칙: 어떤 지점이 와도 같은 코드로 그려져야 한다. 좌표가 없을 수도, 인구
// 데이터가 통째로 없을 수도, 연령대가 한쪽으로 쏠릴 수도, 반경이 100m일 수도
// 2km일 수도 있다. 그래서 축척·색·정규화는 전부 값에서 계산하고(lib/mapUtils.ts),
// 값이 없으면 조용히 숨기는 대신 "없다"고 적는다.
//
// 색·간격은 styles/tokens.css 토큰만 쓴다(레이아웃은 styles/visuals.css).

type Band = { band: string; count?: number; pct: number };
type Gender = { male_pct?: number | null; female_pct?: number | null };
type SeoulRank = {
  population_top_pct?: number | null;
  workers_top_pct?: number | null;
  youth_top_pct?: number | null;
};

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function clampPct(value: number): number {
  return Math.max(0, Math.min(100, value));
}

type MapPhase = "loading" | "ready" | "error";

/** 분석 지점과 반경 — 주소만으로는 어디인지 감이 안 온다. */
function SiteMiniMap({ lat, lng, radiusM }: { lat: number; lng: number; radiusM: number }) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<{ relayout?: () => void } | null>(null);
  const [phase, setPhase] = useState<MapPhase>("loading");
  const [reason, setReason] = useState("");

  const hasPoint = isFiniteLatLng({ lat, lng });
  const radius = Number.isFinite(radiusM) && radiusM > 0 ? radiusM : 0;

  // 실패 화면에서는 지도 div가 사라진다. 그 상태로 다른 지점을 분석하면 ref가
  // 없어 effect가 아무것도 못 하고 계속 오류 화면에 갇힌다 — 입력이 바뀌면
  // 먼저 로딩으로 되돌려 지도 div를 다시 붙인다.
  const inputKey = `${lat},${lng},${radius}`;
  const [renderedKey, setRenderedKey] = useState(inputKey);
  if (renderedKey !== inputKey) {
    setRenderedKey(inputKey);
    setPhase("loading");
    setReason("");
  }

  useEffect(() => {
    // 좌표가 없는 경우는 렌더 시점에 이미 판정해 아래에서 대체 표시를 낸다.
    if (!hasPoint) return;
    const box = boxRef.current;
    if (!box) return;

    let cancelled = false;
    setPhase("loading");
    setReason("");

    loadKakaoMaps()
      .then(() => {
        if (cancelled || !boxRef.current) return;
        const kakao = window.kakao;
        const center = new kakao.maps.LatLng(lat, lng);
        const map = new kakao.maps.Map(boxRef.current, { center, level: 5 });
        mapRef.current = map;
        new kakao.maps.Marker({ map, position: center });

        // 분석 반경을 함께 그려 "이 안을 본 것"이 눈에 보이게 한다. 색은 토큰에서
        // 읽는다 — Kakao는 var()를 못 받지만 hex를 박으면 토큰과 어긋난다.
        if (radius > 0) {
          const color = cssVar("--blue-50", "#2563eb");
          new kakao.maps.Circle({
            map,
            center,
            radius,
            strokeWeight: 2,
            strokeColor: color,
            strokeOpacity: 0.85,
            strokeStyle: "dashed",
            fillColor: color,
            fillOpacity: 0.08,
            clickable: false,
          });
        }

        // level을 상수로 두면 반경 100m는 점처럼, 2km는 화면 밖으로 나간다.
        // 반경에서 사각형을 만들어 맞추면 어떤 반경이 와도 원이 화면에 들어온다.
        const radiusBox = boundsForRadius(lat, lng, radius);
        const fit = radiusBox
          ? boundsForPoints(
              [
                { lat: radiusBox.south, lng: radiusBox.west },
                { lat: radiusBox.north, lng: radiusBox.east },
              ],
              300,
            )
          : boundsForPoints([{ lat, lng }], 300);
        if (fit) {
          const bounds = new kakao.maps.LatLngBounds();
          bounds.extend(new kakao.maps.LatLng(fit.south, fit.west));
          bounds.extend(new kakao.maps.LatLng(fit.north, fit.east));
          map.setBounds(bounds, 12, 12, 12, 12);
        }

        // 카드가 접혀 있다 펼쳐지면 컨테이너 크기가 0인 채로 초기화될 수 있다.
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
      // Kakao 지도에는 destroy가 없다. 컨테이너를 비우지 않으면 좌표가 바뀔 때
      // 옛 지도 위에 새 지도가 겹쳐 쌓인다.
      box.innerHTML = "";
    };
  }, [lat, lng, radius, hasPoint]);

  // 창 크기·패널 폭이 바뀌면 Kakao는 스스로 다시 그리지 않는다(회색 띠가 남는다).
  useEffect(() => {
    if (phase !== "ready") return;
    const box = boxRef.current;
    if (!box || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => mapRef.current?.relayout?.());
    observer.observe(box);
    return () => observer.disconnect();
  }, [phase]);

  // 지도는 보조 수단이다. 못 띄웠다고 좌표·반경이라는 사실까지 사라지면 안 된다.
  // 좌표가 아예 없는 건 렌더 시점에 알 수 있으므로 로딩을 거치지 않고 바로 알린다
  // (effect로 판정하면 SSR·첫 페인트에서 헛돌이 스피너가 잠깐 보인다).
  if (!hasPoint || phase === "error") {
    return (
      <div className="viz-map-fallback">
        <strong>
          {hasPoint ? `위도 ${lat.toFixed(5)}, 경도 ${lng.toFixed(5)}` : "좌표를 받지 못했습니다"}
        </strong>
        {radius > 0 && <span>반경 {formatDistanceM(radius)} 안을 분석했습니다.</span>}
        <p className="viz-map-fallback-reason">
          {hasPoint ? reason : "분석 지점의 좌표가 응답에 없어 지도를 그릴 수 없습니다."}
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="viz-map-wrap">
        <div
          ref={boxRef}
          className="viz-map"
          role="img"
          aria-label={`분석 지점 지도. 위도 ${lat.toFixed(5)}, 경도 ${lng.toFixed(5)}${
            radius > 0 ? `, 반경 ${formatDistanceM(radius)}` : ""
          }`}
          aria-busy={phase === "loading"}
        />
        {phase === "loading" && <div className="viz-map-skeleton">지도를 불러오는 중…</div>}
      </div>
      {radius > 0 && (
        <p className="viz-legend">
          <span className="viz-legend-item">
            <span className="viz-legend-swatch" aria-hidden="true" />
            반경 {formatDistanceM(radius)} — 이 안을 분석했습니다
          </span>
        </p>
      )}
    </>
  );
}

/** 서울 상위 % 행 — 값이 온 지표만 남긴다(세 개 다 없을 수도 있다). */
function rankRowsOf(rank: SeoulRank | null): { label: string; pct: number }[] {
  if (!rank) return [];
  return (
    [
      { label: "거주인구", pct: num(rank.population_top_pct) },
      { label: "직장인", pct: num(rank.workers_top_pct) },
      { label: "청년(20대)", pct: num(rank.youth_top_pct) },
    ] as { label: string; pct: number | null }[]
  )
    .filter((r): r is { label: string; pct: number } => r.pct !== null)
    .map((r) => ({ ...r, pct: clampPct(r.pct) }));
}

/** 연령대 행 — 0%도 남기되 값이 아닌 것은 버린다. */
function bandRowsOf(bands: Band[]): { band: string; pct: number }[] {
  return bands
    .map((b) => ({ band: typeof b.band === "string" && b.band ? b.band : "미상", pct: num(b.pct) }))
    .filter((b): b is { band: string; pct: number } => b.pct !== null && b.pct >= 0);
}

/** 서울 상위 % — 숫자가 낮을수록 좋다. 게이지는 '좋을수록 길게' 채운다. */
function SeoulRankGauges({ rows }: { rows: { label: string; pct: number }[] }) {
  if (rows.length === 0) return null;

  return (
    <div className="viz-block">
      <strong className="viz-block-title">서울에서 어느 정도인가</strong>
      {/* 상위 %는 낮을수록 좋은 값이라 그대로 그리면 거꾸로 읽힌다 — 규칙을 적는다. */}
      <p className="viz-block-hint">막대가 길수록 서울 안에서 상위입니다.</p>
      <div className="viz-rows">
        {rows.map((r) => {
          // 상위 30% 안이면 '많은 편'으로 읽히게 색을 나눈다. 하위권도 회색으로
          // 흐려 두면 안 보인다 — 대비가 나오는 중립색을 쓴다.
          const color =
            r.pct <= 30 ? "var(--lime-37)" : r.pct <= 60 ? "var(--blue-50)" : "var(--label-neutral)";
          return (
            <div
              className="viz-row"
              key={r.label}
              role="img"
              aria-label={`${r.label} 서울 상위 ${Math.round(r.pct)}%`}
            >
              <span className="viz-row-label">{r.label}</span>
              <span className="viz-track">
                <span
                  className="viz-fill"
                  style={{ width: `${100 - r.pct}%`, ["--viz-fill-color" as string]: color }}
                />
              </span>
              <span className="viz-row-value">상위 {Math.round(r.pct)}%</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** 배후 인구 구성 — 어떤 손님이 있는 동네인지. */
function AgeGenderChart({
  rows,
  gender,
}: {
  rows: { band: string; pct: number }[];
  gender: Gender | null;
}) {
  const max = rows.reduce((m, b) => Math.max(m, b.pct), 0);
  const split = normalizedSplit(gender?.male_pct, gender?.female_pct);
  // 전부 0이면 막대가 다 비어 의미가 없다 — 데이터 없음으로 취급한다.
  const hasBands = rows.length > 0 && max > 0;

  if (!hasBands && !split) return null;

  // 대학가처럼 한 연령대로 쏠린 곳은 그 사실 자체가 결론이다. 가장 큰 칸을
  // 굵게 표시하고 문장으로도 한 번 말해 준다.
  const peakIndex = hasBands ? rows.reduce((best, b, i) => (b.pct > rows[best].pct ? i : best), 0) : -1;

  return (
    <div className="viz-block">
      <strong className="viz-block-title">배후 인구 구성</strong>

      {hasBands ? (
        <>
          <p className="viz-block-hint">
            가장 많은 연령대는 <strong>{rows[peakIndex].band}</strong>({rows[peakIndex].pct.toFixed(0)}%)
            입니다.
          </p>
          <div className="viz-rows">
            {rows.map((b, index) => (
              // 같은 이름의 밴드가 두 번 와도 키가 겹치지 않게 순서를 함께 쓴다.
              <div
                className={`viz-row${index === peakIndex ? " viz-row--peak" : ""}`}
                key={`${b.band}-${index}`}
                role="img"
                aria-label={`${b.band} ${b.pct.toFixed(0)}%`}
              >
                <span className="viz-row-label">{b.band}</span>
                <span className="viz-track">
                  <span
                    className="viz-fill"
                    // 가장 큰 칸 기준으로 정규화한다 — 전 연령이 10%대인 동네에서도
                    // 차이가 보인다. 실제 값은 오른쪽에 %로 그대로 적는다.
                    style={{
                      width: `${(b.pct / max) * 100}%`,
                      ["--viz-fill-color" as string]:
                        index === peakIndex ? "var(--blue-45)" : "var(--blue-50)",
                    }}
                  />
                </span>
                <span className="viz-row-value">{b.pct.toFixed(0)}%</span>
              </div>
            ))}
          </div>
        </>
      ) : (
        <p className="viz-note">연령대 구성 데이터가 이 지역에는 없습니다.</p>
      )}

      {split && (
        <div style={{ marginTop: "var(--space-3)" }}>
          <div
            className="viz-split"
            role="img"
            aria-label={`남성 ${split.a.toFixed(0)}%, 여성 ${split.b.toFixed(0)}%`}
          >
            {/* 백엔드가 반올림해 합이 99·101이어도 막대가 어긋나지 않게 100 기준으로
                되돌린 값을 쓴다. */}
            <span className="viz-split-seg" style={{ width: `${split.a}%`, background: "var(--blue-50)" }} />
            <span className="viz-split-seg" style={{ width: `${split.b}%`, background: "var(--purple-40)" }} />
          </div>
          <div className="viz-split-legend">
            <span>
              <span
                className="viz-legend-swatch viz-legend-swatch--solid"
                style={{ ["--viz-swatch-color" as string]: "var(--blue-50)" }}
                aria-hidden="true"
              />
              남 {split.a.toFixed(0)}%
            </span>
            <span>
              <span
                className="viz-legend-swatch viz-legend-swatch--solid"
                style={{ ["--viz-swatch-color" as string]: "var(--purple-40)" }}
                aria-hidden="true"
              />
              여 {split.b.toFixed(0)}%
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

export function AnalysisVisuals({ site }: { site: SiteScoreResponse }) {
  const demand = (site.demand ?? null) as
    | { age_bands?: Band[]; gender?: Gender | null; seoul_rank?: SeoulRank | null }
    | null;
  const bands = Array.isArray(demand?.age_bands) ? demand.age_bands : [];
  const gender = demand?.gender ?? null;

  const rankRows = rankRowsOf((demand?.seoul_rank ?? null) as SeoulRank | null);
  const bandRows = bandRowsOf(bands);
  // 두 블록이 모두 비면 카드 제목이 거짓말이 된다("어떤 사람들을 본 건지"인데
  // 사람 이야기가 없음). 인구 데이터가 없는 지점도 흔하므로 그 사실을 적는다.
  // 컴포넌트가 null을 돌려주는지로 판단하면 안 된다 — 엘리먼트는 항상 truthy다.
  const hasDemandVisual =
    rankRows.length > 0 ||
    bandRows.some((b) => b.pct > 0) ||
    normalizedSplit(gender?.male_pct, gender?.female_pct) !== null;
  const warnings = siteScoreWarnings(site);

  return (
    <div className="card">
      <h3>{hasDemandVisual ? "어디를, 어떤 사람들을 본 건지" : "어디를 본 건지"}</h3>
      <SiteMiniMap lat={site.lat} lng={site.lng} radiusM={site.radius_m} />
      <SeoulRankGauges rows={rankRows} />
      <AgeGenderChart rows={bandRows} gender={gender} />
      {!hasDemandVisual && (
        <p className="viz-note">이 지점에는 배후 인구 통계가 없어 지도만 표시합니다.</p>
      )}
      {warnings.length > 0 && (
        <ul className="warnings-banner">
          {warnings.map((w, i) => <li key={`${i}-${w}`}>{w}</li>)}
        </ul>
      )}
    </div>
  );
}
