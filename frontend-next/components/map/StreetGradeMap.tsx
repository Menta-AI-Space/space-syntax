"use client";

import { useEffect, useRef, useState } from "react";
import { api, type StreetGradeResponse, type StreetSegment } from "@/lib/api";
import { withObjectParticle } from "@/lib/agentFormat";
import { loadKakaoMaps } from "@/lib/kakao";
import { rankLabel, tierOf, type Tier } from "@/lib/streetTier";

// 지도 분석 (2026-10-26, 5단계는 10-27) — 서울 길을 「사람이 지나가는 정도」 5단계로 칠한다.
// 값은 미리 계산해 둔 길 구조(NACH, depthmapX)의 서울 전역 백분위(top_pct)다. 단계는 백엔드
// grade(3단계)가 아니라 화면에서 top_pct로 나눈다 — 백엔드 배포 없이 바꿀 수 있게.
// 검증이 가르는 건 상위 10%(1·2단계)뿐이라, 아래 세 단계는 참고로만 읽게 범례에 적는다.

type KakaoMap = {
  getBounds: () => { getSouthWest: () => KakaoLatLng; getNorthEast: () => KakaoLatLng };
  setCenter: (latlng: unknown) => void;
  setLevel: (level: number) => void;
  getCenter: () => KakaoLatLng;
  getLevel: () => number;
  panBy: (dx: number, dy: number) => void;
  relayout: () => void;
};
type KakaoLatLng = { getLat: () => number; getLng: () => number };
type KakaoPolyline = { setMap: (map: KakaoMap | null) => void };

// 밝기가 단계마다 크게 달라 적록 색약에도 갈린다(진한 빨강 → 주황 → 노랑 → 하늘 → 회색).
const TIER_STYLE: Record<Tier, { color: string; weight: number; opacity: number; z: number }> = {
  1: { color: "#b2182b", weight: 6, opacity: 0.95, z: 5 },
  2: { color: "#f06c00", weight: 4.5, opacity: 0.95, z: 4 },
  3: { color: "#f5c000", weight: 3, opacity: 0.9, z: 3 },
  4: { color: "#6fa8dc", weight: 2, opacity: 0.8, z: 2 },
  5: { color: "#b4bac3", weight: 1.5, opacity: 0.55, z: 1 },
};

/** 손가락으로 누를 수 있는 폭(px) — 빨강(6px)보다 얇은 길(주황·노랑·하늘·회색)에 투명한 누름 선을 깐다. */
const HIT_WEIGHT = 14;

const LEGEND: { tier: Tier; label: string; note: string }[] = [
  { tier: 1, label: "손꼽히는 길", note: "서울 상위 2%" },
  { tier: 2, label: "목 좋은 길", note: "상위 2~10%" },
  { tier: 3, label: "꽤 지나가는 길", note: "상위 10~25%" },
  { tier: 4, label: "보통 길", note: "상위 25~50%" },
  { tier: 5, label: "적게 지나가는 길", note: "하위 50%" },
];

const TIER_NAME = Object.fromEntries(LEGEND.map((row) => [row.tier, row.label])) as Record<Tier, string>;

const TIER_SAYS: Record<Tier, string> = {
  1: "사람이 아주 많이 지나갈 길입니다. 지나가다 들르는 손님이 중요한 가게에 잘 맞아요.",
  2: "사람이 많이 지나갈 길입니다.",
  3: "꽤 지나가는 길입니다. 바로 옆 빨강·주황 길과 견줘 보세요.",
  4: "보통 수준입니다. 바로 옆 빨강·주황 길과 견줘 보세요.",
  5: "주변보다 덜 지나가는 길입니다. 찾아오는 손님이 많은 가게라면 괜찮을 수 있어요.",
};

const START = { lat: 37.5551, lng: 126.9369, level: 4 }; // 신촌역 2호선 (랩미팅 시연 예시와 같은 자리)
// 마지막으로 본 화면. 이 탭은 열려 있을 때만 마운트돼서, 「합정역」을 찾아 둘러보다 다른 탭에
// 다녀오면 지도가 신촌역으로 돌아가 다시 찾아야 했다 — 페이지를 새로 열기 전까지는 기억한다
// (2026-11-09 UX 자동 개선).
let lastView: { lat: number; lng: number; level: number } | null = null;
// 찾은 곳(검색어·핀 자리)도 같이 기억한다 — 화면만 돌아오고 핀·검색칸이 비어, 길 선만 가득한
// 화면에서 방금 찾은 「합정역」이 어디인지 다시 몰랐다 (2026-11-09 UX 자동 개선).
let lastPlace: { query: string; lat: number; lng: number } | null = null;

const NOTE = "매출 차이로 확인된 건 빨강·주황(서울 상위 10%)까지입니다 — 노랑·하늘·회색끼리는 차이가 크지 않아요.";

/** 응답마다 새 객체라 id 없이 등급·좌표로 같은 길인지 본다. */
function sameSegment(a: StreetSegment, b: StreetSegment): boolean {
  if (a.top_pct !== b.top_pct || a.path.length !== b.path.length) return false;
  return a.path.every(([lat, lng], i) => lat === b.path[i][0] && lng === b.path[i][1]);
}

export function StreetGradeMap() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<KakaoMap | null>(null);
  const linesRef = useRef<KakaoPolyline[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const refreshRef = useRef<(() => void) | null>(null);
  const highlightRef = useRef<KakaoPolyline | null>(null);
  const placeMarkerRef = useRef<KakaoPolyline | null>(null);
  const cardRef = useRef<HTMLElement | null>(null);
  const [data, setData] = useState<StreetGradeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<StreetSegment | null>(null);
  const [legendFolded, setLegendFolded] = useState(false);
  const [query, setQuery] = useState(() => lastPlace?.query ?? "");
  const [searchMsg, setSearchMsg] = useState<string | null>(null);
  const [movedTo, setMovedTo] = useState<string | null>(null);
  // 지도(카카오 SDK) 자체를 못 불러온 경우 — 다시 시도할 때마다 1씩 올려 아래 효과를 다시 돈다.
  const [mapFailed, setMapFailed] = useState(false);
  const [mapAttempt, setMapAttempt] = useState(0);

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const draw = (segments: StreetSegment[]) => {
      const map = mapRef.current;
      const kakao = window.kakao;
      linesRef.current.forEach((line) => line.setMap(null));
      linesRef.current = [];
      if (!map) return;
      // 회색부터 그려 진한 길이 위로 오게 한다.
      const ordered = [...segments].sort((a, b) => b.top_pct - a.top_pct);
      for (const seg of ordered) {
        const style = TIER_STYLE[tierOf(seg.top_pct)];
        const line: KakaoPolyline = new kakao.maps.Polyline({
          map,
          path: seg.path.map(([lat, lng]) => new kakao.maps.LatLng(lat, lng)),
          strokeColor: style.color,
          strokeWeight: style.weight,
          strokeOpacity: style.opacity,
          zIndex: style.z,
        });
        kakao.maps.event.addListener(line, "click", () => setPicked(seg));
        linesRef.current.push(line);
        // 회색(1.5px)·하늘(2px) 길은 손가락으로 거의 못 눌렀다 — 얇은 길 밑에 눈에 안 보이는
        // 굵은 선(HIT_WEIGHT)을 맨 아래(zIndex 0)에 깔아 누르는 폭만 넓힌다. 맨 아래라 보이는
        // 길을 정확히 누르면 그 길이 먼저 잡힌다 (2026-11-06 UX 자동 개선, 390px).
        if (style.weight < 5) {
          const hit: KakaoPolyline = new kakao.maps.Polyline({
            map,
            path: seg.path.map(([lat, lng]) => new kakao.maps.LatLng(lat, lng)),
            strokeColor: "#000000",
            strokeWeight: HIT_WEIGHT,
            strokeOpacity: 0.01,
            zIndex: 0,
          });
          kakao.maps.event.addListener(hit, "click", () => setPicked(seg));
          linesRef.current.push(hit);
        }
      }
    };

    const refresh = () => {
      const map = mapRef.current;
      if (!map) return;
      const center = map.getCenter();
      lastView = { lat: center.getLat(), lng: center.getLng(), level: map.getLevel() };
      const bounds = map.getBounds();
      const sw = bounds.getSouthWest();
      const ne = bounds.getNorthEast();
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setLoading(true);
      setError(null);
      api
        .streetGrades({ west: sw.getLng(), south: sw.getLat(), east: ne.getLng(), north: ne.getLat() }, controller.signal)
        .then((res) => {
          if (disposed || controller.signal.aborted) return;
          setData(res);
          draw(res.segments);
          // 지도를 옮겨 누른 길이 새 화면에서 빠지면 설명을 닫는다 — 안 그러면 화면에 없는
          // 길의 등급이 카드에 남아 지금 보이는 길 설명으로 읽혔다 (2026-11-06 UX 자동 개선).
          setPicked((prev) => (prev && !res.segments.some((seg) => sameSegment(seg, prev)) ? null : prev));
        })
        .catch((err: unknown) => {
          if (disposed || controller.signal.aborted) return;
          setError(err instanceof Error ? err.message : "길 데이터를 불러오지 못했습니다");
        })
        .finally(() => {
          if (!disposed && !controller.signal.aborted) setLoading(false);
        });
    };
    refreshRef.current = refresh;

    setMapFailed(false);
    loadKakaoMaps()
      .then(() => {
        if (disposed || !containerRef.current) return;
        const kakao = window.kakao;
        const map: KakaoMap = new kakao.maps.Map(containerRef.current, {
          center: new kakao.maps.LatLng((lastView ?? START).lat, (lastView ?? START).lng),
          level: (lastView ?? START).level,
        });
        mapRef.current = map;
        if (lastPlace) {
          placeMarkerRef.current?.setMap(null);
          placeMarkerRef.current = new kakao.maps.Marker({
            map,
            position: new kakao.maps.LatLng(lastPlace.lat, lastPlace.lng),
            zIndex: 10,
          });
        }
        // 지도를 끄는 동안 요청이 쏟아지지 않게, 멈춘 뒤 0.3초에 한 번만 부른다.
        kakao.maps.event.addListener(map, "idle", () => {
          if (timer) clearTimeout(timer);
          timer = setTimeout(refresh, 300);
        });
        // 검색으로 옮긴 뒤 손으로 지도를 끌어도 「「○○」 주변으로 옮겼습니다」가 남아 지금 보는
        // 곳이 그 장소인 것처럼 읽혔다 — 끌기 시작하면 지운다. 검색이 부르는 setCenter·setLevel은
        // dragstart를 내지 않아 검색 직후 문장은 남는다 (2026-11-06 UX 자동 개선).
        kakao.maps.event.addListener(map, "dragstart", () => setMovedTo(null));
        refresh();
      })
      .catch((err: unknown) => {
        if (disposed) return;
        // 개발용 문장(「NEXT_PUBLIC_KAKAO_JS_KEY와 localhost:3000 도메인 설정을 확인하세요」)이
        // 그대로 사용자에게 떴고, 다시 부를 버튼도 없어 새로고침밖에 없었다 — 사용자 말로 바꾸고
        // 「다시 불러오기」로 지도부터 다시 부른다 (2026-11-07 UX 자동 개선). 원문은 콘솔에 남긴다.
        console.error(err);
        setMapFailed(true);
        setError("지도를 불러오지 못했습니다 — 인터넷 연결을 확인하고 다시 불러와 주세요.");
      });

    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      abortRef.current?.abort();
      linesRef.current.forEach((line) => line.setMap(null));
      linesRef.current = [];
    };
  }, [mapAttempt]);

  // 길을 눌러도 지도 위엔 아무 표시가 없어, 여러 길이 겹친 곳에서 카드 설명이 어느 길
  // 얘기인지 알 수 없었다 — 누른 길 밑에 굵은 검은 테두리를 깐다. 테두리는 맨 아래(zIndex 0)라
  // 길 색은 그대로 보이고 양옆으로만 드러난다 (2026-11-06 UX 자동 개선).
  useEffect(() => {
    highlightRef.current?.setMap(null);
    highlightRef.current = null;
    const map = mapRef.current;
    if (!picked || !map) return;
    const kakao = window.kakao;
    highlightRef.current = new kakao.maps.Polyline({
      map,
      path: picked.path.map(([lat, lng]) => new kakao.maps.LatLng(lat, lng)),
      strokeColor: "#111111",
      strokeWeight: TIER_STYLE[tierOf(picked.top_pct)].weight + 6,
      strokeOpacity: 0.85,
      zIndex: 0,
    });
  }, [picked]);

  useEffect(
    () => () => {
      highlightRef.current?.setMap(null);
      placeMarkerRef.current?.setMap(null);
    },
    [],
  );

  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const search = (event: React.FormEvent) => {
    event.preventDefault();
    const q = query.trim();
    const map = mapRef.current;
    // 빈 칸으로 「이동」을 누르면 아무 반응이 없어 고장처럼 보였다 — 입력칸에 초점을 줘
    // 휴대폰 자판을 올리고 무엇을 넣을지 말한다 (2026-11-07 UX 자동 개선, 390px).
    if (!q) {
      searchInputRef.current?.focus();
      setMovedTo(null);
      setSearchMsg("찾을 역이나 동네 이름을 넣어 주세요");
      return;
    }
    // 지도를 못 불러온 채 「이동」을 누르면 아무 반응이 없었다 — 지도부터 다시 부르라고 말한다
    // (2026-11-07 UX 자동 개선).
    if (!map) {
      setMovedTo(null);
      setSearchMsg("지도를 아직 불러오지 못해 옮길 수 없습니다 — 아래 「다시 불러오기」를 눌러 주세요");
      return;
    }
    const kakao = window.kakao;
    new kakao.maps.services.Places().keywordSearch(
      q,
      (
        result: { y: string; x: string; place_name: string; address_name?: string; road_address_name?: string }[],
        status: string,
      ) => {
        // 검색 자체가 실패(회선 끊김 등, Status.ERROR)해도 「「합정역」을 찾지 못했습니다 — 역·동
        // 이름으로 다시 찾아 보세요」라 맞는 이름을 틀린 것처럼 고쳐 치게 했다 — 실패는 따로 말하고
        // 같은 이름으로 다시 누르게 한다 (2026-11-07 UX 자동 개선).
        if (status === kakao.maps.services.Status.ERROR) {
          setSearchMsg("장소 검색이 잠시 안 됩니다 — 인터넷 연결을 확인하고 「이동」을 다시 눌러 주세요");
          setMovedTo(null);
          return;
        }
        if (status !== kakao.maps.services.Status.OK || !result.length) {
          // 받침 없는 말(「이대」)에도 「을」이 붙던 것을 조사에 맞춘다 (2026-11-06 UX 자동 개선).
          setSearchMsg(`${withObjectParticle(`「${q}」`)} 찾지 못했습니다 — 역·동 이름으로 다시 찾아 보세요`);
          setMovedTo(null);
          return;
        }
        // 검색어와 다른 곳(첫 결과)으로 옮겨도 말이 없어 어디인지 알 길이 없었다 — 옮긴 곳 이름을
        // 한 줄 남긴다. 다시 치거나 다른 곳을 찾으면 바뀐다 (2026-11-06 UX 자동 개선).
        setSearchMsg(null);
        // 휴대폰에선 찾은 뒤에도 입력칸에 초점이 남아 자판이 지도 아래 절반을 덮고, 옮긴 곳을 보려면
        // 자판을 손으로 내려야 했다 — 찾으면 초점을 놓아 자판을 내린다. 못 찾았을 땐 고쳐 치도록
        // 그대로 둔다 (2026-11-06 UX 자동 개선, 390px).
        searchInputRef.current?.blur();
        // 첫 결과 하나만 받아 옮겼다 — 「스타벅스」「시청역」처럼 같은 이름이 여러 곳이면 서울 밖으로
        // 가서 「칠한 길이 없습니다」만 보였다. 이 지도는 서울 길만 칠하므로 서울 결과를 먼저 고른다
        // (없으면 종전대로 첫 결과) (2026-11-08 UX 자동 개선).
        const inSeoul = (r: (typeof result)[number]) =>
          /^서울/.test(r.address_name ?? "") || /^서울/.test(r.road_address_name ?? "");
        const hit = result.find(inSeoul) ?? result[0];
        setMovedTo(hit.place_name || q);
        setPicked(null);
        map.setLevel(4);
        const at = new kakao.maps.LatLng(Number(hit.y), Number(hit.x));
        map.setCenter(at);
        // 찾은 곳으로 옮겨도 지도 위엔 아무 표시가 없어, 길 선만 가득한 화면에서 「합정역」이
        // 어디인지 알 수 없었다 — 찾은 자리에 핀을 꽂는다(다음 검색 때 옮긴다). 휴대폰에선 아래
        // 카드가 지도 3분의 1을 덮어 지도 한가운데(찾은 곳)가 카드 윗선에 걸렸다 — 카드 위로 보이는
        // 부분의 가운데에 오게 카드 높이 절반만큼 올린다 (2026-11-08 UX 자동 개선, 390px).
        placeMarkerRef.current?.setMap(null);
        placeMarkerRef.current = new kakao.maps.Marker({ map, position: at, zIndex: 10 });
        lastPlace = { query: q, lat: Number(hit.y), lng: Number(hit.x) };
        const card = cardRef.current;
        if (card && window.matchMedia("(max-width: 640px)").matches) {
          map.panBy(0, Math.round((card.offsetHeight + 12) / 2));
        }
      },
      { size: 15 },
    );
  };

  // 휴대폰에선 범례가 카드의 대부분(약 110px)이라 카드가 지도 아래 3분의 1을 덮었다 —
  // 색을 익힌 뒤엔 접어 지도를 넓게 볼 수 있게 한다. 데스크톱엔 단추가 없다
  // (2026-11-07 UX 자동 개선). 새 줄을 만들지 않게 상태 문장 줄 오른쪽에 두고,
  // 불러오기 실패 땐 「다시 불러오기」 옆에 둔다 — 혼자 한 줄을 차지했다.
  const legendToggle = (
    <button
      type="button"
      className="street-map__legend-toggle"
      aria-expanded={!legendFolded}
      onClick={() => setLegendFolded((v) => !v)}
    >
      {legendFolded ? "길 색 설명 펴기" : "길 색 설명 접기"}
    </button>
  );

  return (
    <div className="street-map">
      <div ref={containerRef} className="street-map__canvas" />
      <section ref={cardRef} className={`street-map__card${picked ? " street-map__card--picked" : ""}`} aria-label="지도 분석 범례">
        <h2 className="street-map__title">목 좋은 길</h2>
        <p className="street-map__lead">사람이 오가며 지나가기 쉬운 길을 길 구조로 찾았습니다.</p>
        <form className="street-map__search" onSubmit={search}>
          <input
            ref={searchInputRef}
            type="search"
            enterKeyHint="search"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setMovedTo(null);
              // 실패 문장이 고쳐 치는 동안에도 남아 새 검색어가 또 실패한 것처럼 읽혔다
              // (2026-11-06 UX 자동 개선). 옮긴 곳 문장과 같이 치는 순간 지운다.
              setSearchMsg(null);
            }}
            placeholder="역·동네 이름 (예: 신촌역)"
            aria-label="장소 검색"
          />
          <button type="submit">이동</button>
        </form>
        {searchMsg && <p className="street-map__msg" role="status">{searchMsg}</p>}
        {movedTo && !searchMsg && (
          <p className="street-map__msg street-map__msg--ok" role="status">
            「{movedTo}」 주변으로 옮겼습니다
          </p>
        )}
        {/* 누른 길 설명은 검색창 바로 아래 — 휴대폰에선 카드가 화면 아래 45%로 잘려
            범례 밑에 두면 스크롤해야 보였다 (2026-11-06 UX 자동 개선). */}
        {picked && (
          <div className="street-map__picked" role="status">
            <button type="button" aria-label="닫기" onClick={() => setPicked(null)}>×</button>
            {/* 순위가 늘 빨간 굵은 글씨라 「하위 2%」 회색 길도 범례의 빨강(손꼽히는 길)처럼 읽혔고,
                휴대폰에서 범례를 접으면 이 길이 몇 단계인지 알 길이 없었다 — 범례와 같은 색 막대와
                단계 이름을 앞에 두고 순위는 검은 글씨로 (2026-11-07 UX 자동 개선, 390px). */}
            <p className="street-map__picked-head">
              <span
                className="street-map__picked-swatch"
                aria-hidden="true"
                style={{ background: TIER_STYLE[tierOf(picked.top_pct)].color }}
              />
              <span>
                <strong>{TIER_NAME[tierOf(picked.top_pct)]}</strong> · 서울 길 구간 가운데{" "}
                <strong className="street-map__picked-rank">{rankLabel(picked.top_pct)}</strong>
              </span>
            </p>
            <p className="street-map__picked-sub">
              {TIER_SAYS[tierOf(picked.top_pct)]}
            </p>
          </div>
        )}
        {/* 지도(카카오 SDK)를 못 불러오면 빈 화면 아래에 칠하지도 않은 길 색 다섯 줄이 펴진 채
            오류 문장을 밀어냈다(390에서 카드 300px) — 지도가 없을 땐 범례와 접기 단추를 뺀다
            (2026-11-08 UX 자동 개선). */}
        {!mapFailed && (
        <ul className={`street-map__legend${legendFolded ? " street-map__legend--folded" : ""}`}>
          {/* 멀리서 볼 땐 빨강·주황만 칠하는데 범례는 다섯 단계를 똑같이 보여, 노랑·하늘·회색 길이
              안 보이는 게 고장처럼 읽혔다 — 지금 안 칠한 단계는 흐리게 둔다 (2026-11-07 UX 자동 개선). */}
          {LEGEND.map((row) => (
            <li
              key={row.tier}
              className={data?.overview && row.tier > 2 ? "street-map__legend-row--off" : undefined}
              title={data?.overview && row.tier > 2 ? "확대하면 보입니다" : undefined}
            >
              <span
                className="street-map__swatch"
                style={{
                  background: TIER_STYLE[row.tier].color,
                  opacity: Math.max(TIER_STYLE[row.tier].opacity, 0.5),
                  height: TIER_STYLE[row.tier].weight,
                }}
              />
              <strong>{row.label}</strong>
              <span>{row.note}</span>
            </li>
          ))}
        </ul>
        )}
        {!mapFailed && <p className="street-map__note street-map__note--top">{NOTE}</p>}
        {/* 불러오기에 실패하면 오류 문장만 작게 뜨고 다시 부를 길이 지도를 끄는 것뿐이었다 —
            휴대폰 회선이 끊겼다 이어질 때 특히 막혔다. 다시 불러오기 버튼을 붙인다
            (2026-11-06 UX 자동 개선). 지도(SDK)를 못 불러온 경우엔 지도부터 다시 부른다(2026-11-07). */}
        {error && (
          <div className="street-map__error" role="alert">
            <p>{error}</p>
            {(mapFailed || mapRef.current) && (
              <button
                type="button"
                onClick={() => {
                  if (mapFailed) {
                    setError(null);
                    setMapAttempt((n) => n + 1);
                  } else refreshRef.current?.();
                }}
              >
                다시 불러오기
              </button>
            )}
            {!mapFailed && legendToggle}
          </div>
        )}
        {!error && (
        <div className="street-map__status-row">
          <p className="street-map__status" aria-live="polite">
            {loading
                ? "길을 불러오는 중…"
                : data && data.segments.length === 0
                  ? // 서울 밖(또는 길 없는 공원·강)으로 옮기면 「이 화면의 길 0개」만 떠 고장인지
                    // 알 수 없었다 — 데이터가 서울 길뿐이라는 것과 할 일을 말한다 (2026-11-06 UX 자동 개선).
                    "이 화면에는 칠한 길이 없습니다. 지금은 서울 길만 칠합니다 — 서울 안으로 옮기거나 위에서 역·동네 이름을 찾아 보세요."
                  : data?.overview
                  ? "멀리서 보는 중이라 빨강·주황(상위 10%) 길만 표시합니다. 확대하면 나머지 길도 나옵니다."
                  : data
                    ? // 길을 누르면 설명이 뜬다는 말이 어디에도 없어(휴대폰은 제목·안내문도 숨김)
                      // 처음 온 사람은 색만 보고 끝났다 — 아직 안 눌렀을 때만 한 마디 붙인다
                      // (2026-11-07 UX 자동 개선, 390px).
                      `이 화면의 길 ${data.segments.length.toLocaleString()}개${picked ? "" : " · 길을 누르면 설명이 나옵니다"}`
                    : "지도를 불러오는 중…"}
          </p>
          {legendToggle}
        </div>
        )}
        {/* 휴대폰에선 위 매출 확인 범위 문장을 여기로 접는다 — 카드가 화면 아래 45%를
            덮어 지도가 위 절반만 보였다 (2026-11-06 UX 자동 개선). */}
        {/* 2026-11-07 UX 루프: 짧은 휴대폰(390×667)이나 길을 누른 뒤엔 카드 높이가 꽉 차,
            펼친 설명이 카드 아래로 숨어 아무 일도 안 일어난 듯 보였다 — 펼 때 카드 안에서만
            설명 끝까지 굴린다(창은 굴리지 않는다). */}
        <details
          className="street-map__caveats"
          onToggle={(e) => {
            const el = e.currentTarget;
            if (!el.open) return;
            const card = el.closest<HTMLElement>(".street-map__card");
            if (!card) return;
            const hidden = el.getBoundingClientRect().bottom - card.getBoundingClientRect().bottom;
            if (hidden > 0) card.scrollBy({ top: hidden + 8, behavior: "smooth" });
          }}
        >
          <summary>이 지도를 읽는 법</summary>
          <p className="street-map__note street-map__note--folded">{NOTE}</p>
          {data && (
            <ul>
              {data.caveats.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          )}
        </details>
      </section>
    </div>
  );
}
