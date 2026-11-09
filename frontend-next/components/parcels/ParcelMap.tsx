"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { loadKakaoMaps } from "@/lib/kakao";
import { api, type MapParcel } from "@/lib/api";
import {
  GAP_BANDS,
  UNJUDGED,
  fillOpacity,
  gapBand,
  outerRings,
} from "@/lib/parcelMap";

/**
 * 동 안에서 필지를 눈으로 훑고 눌러 읽는 지도.
 *
 * 목록은 "상위 10곳"만 보여 준다. 그건 답을 주지만 **주변과 비교할 수단이
 * 없다** — 옆 필지가 왜 더 비싼지, 이 블록 전체가 그런 건지 이 땅만 그런 건지
 * 목록으로는 알 수 없다. 지도는 그 비교를 공짜로 준다.
 *
 * **판정 없는 필지도 회색으로 그린다.** 판정된 것만 그리면 지도에 구멍이 뚫리고
 * 사용자는 그게 "판정 못 함"인지 "땅이 아님"인지 구분할 수 없다. 여의도동 실측이
 * 폴리곤 529 대 판정 283이라 절반이 빈다.
 *
 * 지도를 움직이면 그 위치로 다시 부른다. 한 번에 전 서울을 내리지 않는 이유는
 * 5초 룰이다 — 89만 폴리곤을 GeoJSON으로 내릴 수 없어서 서버가 반경 600m로
 * 상한을 두고 있고, 넓게 보는 것은 벡터 타일이 선행이다.
 */

type Phase = "loading" | "ready" | "error";

/** 지도 배율 → 조회 반경(m). 서버 상한이 600m라 그 안에서만 움직인다. */
function radiusForLevel(level: number): number {
  if (level <= 2) return 150;
  if (level === 3) return 250;
  if (level === 4) return 400;
  return 600;
}

/** 필지가 잘 보이는 배율. 3이면 한 블록, 5면 동 절반쯤이다. */
const INITIAL_LEVEL = 4;

/** 지도를 움직일 때마다 부르지 않는다 — 관성 스크롤 한 번에 여러 번 걸린다. */
const REFETCH_DEBOUNCE_MS = 350;

/**
 * Kakao 객체는 전역 `window.kakao`로만 들어온다. 여기서 쓰는 것만 구조적으로
 * 좁혀 둔다 — SDK 전체 타입을 끌어오지 않고도 오타가 잡힌다.
 */
type KakaoMapHandle = {
  getCenter: () => { getLat: () => number; getLng: () => number };
  getLevel: () => number;
};

type KakaoPolygonHandle = {
  setMap: (map: unknown) => void;
  setOptions: (options: { strokeWeight?: number; strokeColor?: string }) => void;
};

interface Props {
  center: { lat: number; lng: number };
  /** 목록에서 고른 필지. 지도가 그 필지를 강조한다. */
  selectedPnu?: string | null;
  onSelect?: (parcel: MapParcel) => void;
}

export function ParcelMap({ center, selectedPnu = null, onSelect }: Props) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<KakaoMapHandle | null>(null);
  const polygonsRef = useRef<{ pnu: string; poly: KakaoPolygonHandle }[]>([]);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 콜백이 매 렌더 새로 와도 지도를 다시 만들지 않게 최신 것만 들고 있는다.
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;

  const [phase, setPhase] = useState<Phase>("loading");
  const [reason, setReason] = useState("");
  const [parcels, setParcels] = useState<MapParcel[]>([]);
  const [judged, setJudged] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [fetching, setFetching] = useState(false);

  /** 지금 보이는 위치의 필지를 받아 온다. 실패는 지도를 죽이지 않는다. */
  const fetchHere = useCallback(async (map: KakaoMapHandle) => {
    const c = map.getCenter();
    const radius = radiusForLevel(map.getLevel());
    setFetching(true);
    try {
      const res = await api.mapParcels(c.getLng(), c.getLat(), radius);
      setParcels(res.parcels);
      setJudged(res.judged_count);
      setTruncated(res.truncated);
      setReason("");
    } catch (err) {
      // 지도는 그대로 두고 직전 필지를 남긴다 — 화면이 통째로 사라지는 것보다
      // 낫고, 다시 움직이면 재시도된다.
      setReason(err instanceof Error ? err.message : "필지를 불러오지 못했습니다.");
    } finally {
      setFetching(false);
    }
  }, []);

  // ── 지도를 한 번 만든다 ────────────────────────────────────────────────
  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    let cancelled = false;

    loadKakaoMaps()
      .then(() => {
        if (cancelled || !boxRef.current) return;
        const kakao = window.kakao;
        const map = new kakao.maps.Map(boxRef.current, {
          center: new kakao.maps.LatLng(center.lat, center.lng),
          level: INITIAL_LEVEL,
        });
        mapRef.current = map;
        setPhase("ready");

        kakao.maps.event.addListener(map, "idle", () => {
          if (timerRef.current) clearTimeout(timerRef.current);
          timerRef.current = setTimeout(() => fetchHere(map), REFETCH_DEBOUNCE_MS);
        });
        void fetchHere(map);
      })
      .catch((err) => {
        if (cancelled) return;
        setPhase("error");
        setReason(err instanceof Error ? err.message : "지도를 불러오지 못했습니다.");
      });

    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
      mapRef.current = null;
      polygonsRef.current = [];
      // Kakao 지도에는 destroy가 없다 — 비우지 않으면 지도가 겹쳐 쌓인다.
      box.innerHTML = "";
    };
    // center는 동을 바꿀 때만 달라진다. 지도를 움직일 때마다 다시 만들지 않는다.
  }, [center.lat, center.lng, fetchHere]);

  // ── 필지가 바뀌면 다시 그린다 ──────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map || phase !== "ready") return;
    const kakao = window.kakao;

    for (const { poly } of polygonsRef.current) poly.setMap(null);
    polygonsRef.current = [];

    for (const parcel of parcels) {
      const band = gapBand(parcel.gap_score);
      for (const ring of outerRings(parcel.geometry)) {
        const poly = new kakao.maps.Polygon({
          path: ring.map(([lng, lat]) => new kakao.maps.LatLng(lat, lng)),
          strokeWeight: 1,
          strokeColor: "#94a3b8",
          strokeOpacity: 0.7,
          fillColor: band.color,
          fillOpacity: fillOpacity(parcel.gap_score),
          zIndex: parcel.gap_score == null ? 1 : 2,
        });
        poly.setMap(map);
        polygonsRef.current.push({ pnu: parcel.pnu, poly });

        kakao.maps.event.addListener(poly, "click", () => {
          selectRef.current?.(parcel);
        });
        // 커서만으로도 "누를 수 있다"가 전달된다. 필지가 수백 개라 개별
        // 툴팁·마커를 달면 화면이 덮인다.
        kakao.maps.event.addListener(poly, "mouseover", () => {
          poly.setOptions({ strokeWeight: 3, strokeColor: "#0f172a" });
        });
        kakao.maps.event.addListener(poly, "mouseout", () => {
          poly.setOptions({ strokeWeight: 1, strokeColor: "#94a3b8" });
        });
      }
    }
  }, [parcels, phase]);

  // ── 선택된 필지를 굵게 ────────────────────────────────────────────────
  useEffect(() => {
    for (const { pnu, poly } of polygonsRef.current) {
      const on = pnu === selectedPnu;
      poly.setOptions({
        strokeWeight: on ? 4 : 1,
        strokeColor: on ? "#0f172a" : "#94a3b8",
      });
    }
  }, [selectedPnu, parcels]);

  if (phase === "error") {
    return (
      <div className="pm-fallback">
        <p>{reason || "지도를 불러오지 못했습니다."}</p>
      </div>
    );
  }

  const unjudged = parcels.length - judged;

  return (
    <div className="pm">
      <div ref={boxRef} className="pm-canvas" aria-label="필지 지도" />
      <div className="pm-legend">
        {GAP_BANDS.map((b) => (
          <span key={b.label} className="pm-legend-item">
            <i style={{ background: b.color }} aria-hidden />
            {b.label}
          </span>
        ))}
        <span className="pm-legend-item">
          <i style={{ background: UNJUDGED.color, opacity: 0.5 }} aria-hidden />
          {UNJUDGED.label}
        </span>
      </div>
      <p className="pm-status" role="status">
        {fetching
          ? "이 위치의 필지를 불러오는 중…"
          : parcels.length === 0
            ? "이 위치는 필지 경계를 아직 적재하지 않았습니다. 지도를 움직여 보세요."
            : `보이는 필지 ${parcels.length}곳 · 판정 ${judged}곳${
                unjudged > 0 ? ` · 회색 ${unjudged}곳은 판정 없음` : ""
              }${truncated ? " · 상한에 걸려 바깥쪽은 잘렸습니다" : ""}`}
      </p>
      {reason && <p className="pm-status pm-status--warn">{reason}</p>}
    </div>
  );
}
