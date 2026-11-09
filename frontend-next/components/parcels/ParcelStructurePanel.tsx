"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { loadKakaoMaps } from "@/lib/kakao";
import { api, type ParcelStructure, type ParcelStructureResponse } from "@/lib/api";
import { downloadParcelReport } from "@/lib/parcelReport";
import {
  GRADE_COLORS,
  GRADE_LEGEND,
  UNGRADED_COLOR,
  money,
  outerRings,
  topPercentText,
} from "@/lib/parcelGrade";

/**
 * 블록 하나의 필지를 길 구조 등급으로 칠해 보여 주는 화면.
 *
 * 목록만으로는 "왜 이 필지가 1등급인가"를 납득시킬 수 없다 — 옆 필지와 어떻게
 * 다른지, 어느 길에 붙었는지가 보여야 한다. 그래서 지도가 본체고 표가 곁이다.
 *
 * 화면이 지켜야 할 선(서비스 층 주석과 같은 이유):
 * - 금액을 단정하지 않는다. 총액을 사용자가 넣었을 때만 **배분** 금액을 낸다.
 * - 등급은 화면에 보이는 범위 안의 상대값이다. 그 말을 항상 붙인다.
 * - 접한 도로를 못 찾은 필지는 회색이고 "등급 없음"이다 — 0등급이 아니다.
 */

/** 이 배율보다 멀리 빼면 한 블록(최대 700m)이 화면에서 점만큼 작아져, 색을 칠해도
 *  보이지 않는다. 그때는 부르지 않고 "확대하세요"라고 말한다 — 빈 지도를 주면
 *  사용자는 기능이 고장 난 줄 안다(2026-09-30 소유자 실사용에서 실제로 그랬다). */
const MAX_LEVEL_FOR_PARCELS = 5;

/** 지도 배율 → 조회 반경(m). 서버가 800m를 상한으로 둔다. */
function radiusForLevel(level: number): number {
  if (level <= 2) return 150;
  if (level === 3) return 250;
  if (level === 4) return 400;
  return 700;
}

const INITIAL_LEVEL = 4;

/** 필지 경계를 적재해 둔 구. 다른 구로 가면 지도는 나오는데 아무것도 안 칠해져
 *  "고장 났다"로 읽힌다 — 어디가 되는지 화면이 먼저 말하고, 바로 갈 수 있게 한다.
 *  (2026-09-30: 소유자가 반포역=서초구에서 빈 지도를 만났다) */
/** 고른 필지를 브라우저에 남긴다 — 검토는 하루에 끝나지 않고, 새로고침 한 번에
 *  스무 개를 다시 찍게 만들면 아무도 쓰지 않는다. */
const PICK_STORAGE_KEY = "landi.parcelStructure.picked";

const COVERED_AREAS: { label: string; lat: number; lng: number }[] = [
  { label: "강남 논현", lat: 37.5172, lng: 127.0276 },
  { label: "강남 역삼", lat: 37.5006, lng: 127.0366 },
  { label: "서초 반포", lat: 37.5087, lng: 127.0110 },
  { label: "서초 교대", lat: 37.4934, lng: 127.0143 },
  { label: "마포 홍대", lat: 37.5563, lng: 126.9236 },
  { label: "성북 길음", lat: 37.6035, lng: 127.0255 },
  { label: "금천 가산", lat: 37.4784, lng: 126.8829 },
];
const REFETCH_DEBOUNCE_MS = 400;

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
}

export function ParcelStructurePanel({ center }: Props) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<KakaoMapHandle | null>(null);
  const polygonsRef = useRef<{ pnu: string; poly: KakaoPolygonHandle }[]>([]);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 총액이 바뀔 때마다 지도를 다시 부르지 않도록 최신 값만 들고 있는다.
  const amountRef = useRef<number | null>(null);
  // 폴리곤 클릭 핸들러는 한 번만 붙으므로, 모드는 ref로 읽어야 최신이 잡힌다.
  const pickingRef = useRef(false);

  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [reason, setReason] = useState("");
  const [data, setData] = useState<ParcelStructureResponse | null>(null);
  const [fetching, setFetching] = useState(false);
  const [tooFarOut, setTooFarOut] = useState(false);
  // 사업지로 고른 필지. 비어 있으면 화면에 보이는 블록 전체가 대상이다.
  const [picked, setPicked] = useState<string[]>([]);
  const [picking, setPicking] = useState(false);
  const pickedRef = useRef<string[]>([]);
  pickedRef.current = picked;
  const [selected, setSelected] = useState<ParcelStructure | null>(null);
  const [amountText, setAmountText] = useState("");

  /** 고른 필지만 다시 채점한다. 지도 위치와 무관하므로 대상이 흔들리지 않는다. */
  const fetchPicked = useCallback(async (pnus: string[]) => {
    if (!pnus.length) return;
    setFetching(true);
    try {
      const res = await api.parcelStructure({
        lat: 0, lng: 0, pnus, total_amount_manwon: amountRef.current,
      });
      setData(res);
      setReason("");
    } catch (err) {
      setReason(err instanceof Error ? err.message : "고른 필지를 채점하지 못했습니다.");
    } finally {
      setFetching(false);
    }
  }, []);

  const fetchHere = useCallback(async (map: KakaoMapHandle) => {
    if (pickedRef.current.length) return; // 대상을 고른 뒤에는 지도가 대상을 바꾸지 않는다
    if (map.getLevel() > MAX_LEVEL_FOR_PARCELS) {
      setTooFarOut(true);
      return;
    }
    setTooFarOut(false);
    const c = map.getCenter();
    setFetching(true);
    try {
      const res = await api.parcelStructure({
        lat: c.getLat(),
        lng: c.getLng(),
        radius_m: radiusForLevel(map.getLevel()),
        total_amount_manwon: amountRef.current,
      });
      setData(res);
      setReason("");
    } catch (err) {
      const message = err instanceof Error ? err.message : "필지를 불러오지 못했습니다.";
      // 404는 장애가 아니라 "여긴 아직 데이터가 없다"다. 같은 말투로 내면
      // 사용자는 기능이 깨진 줄 안다.
      setReason(
        message.includes("필지 데이터가 없습니다")
          ? "이 지역은 필지 경계를 아직 적재하지 않았습니다 — 위 버튼으로 강남·금천으로 옮겨 보세요."
          : message,
      );
      setData(null);
    } finally {
      setFetching(false);
    }
  }, []);

  useEffect(() => {
    pickingRef.current = picking;
  }, [picking]);

  // 저장해 둔 선택을 한 번 복원한다 — 검토는 하루에 끝나지 않는다.
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(PICK_STORAGE_KEY);
      const saved = raw ? (JSON.parse(raw) as string[]) : [];
      if (Array.isArray(saved) && saved.length) {
        // ref를 **동기적으로** 채운다. 지도 생성 직후의 첫 조회가 이 effect보다
        // 먼저 끝나면, 상태만 세팅해서는 반경 결과가 고른 결과를 덮어쓴다.
        pickedRef.current = saved;
        setPicked(saved);
        void fetchPicked(saved);
      }
    } catch {
      /* 저장소가 막힌 환경 */
    }
  }, [fetchPicked]);

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
      box.innerHTML = "";
    };
  }, [center.lat, center.lng, fetchHere]);

  // 필지가 바뀌면 다시 칠한다.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || phase !== "ready" || !data) return;
    const kakao = window.kakao;

    for (const { poly } of polygonsRef.current) poly.setMap(null);
    polygonsRef.current = [];

    for (const parcel of data.parcels) {
      if (!parcel.geometry) continue;
      const color = parcel.grade ? GRADE_COLORS[parcel.grade] : UNGRADED_COLOR;
      for (const ring of outerRings(parcel.geometry)) {
        const poly = new kakao.maps.Polygon({
          path: ring.map(([lng, lat]) => new kakao.maps.LatLng(lat, lng)),
          strokeWeight: 1,
          strokeColor: "#94a3b8",
          strokeOpacity: 0.7,
          fillColor: color,
          fillOpacity: parcel.grade ? 0.55 : 0.25,
          zIndex: parcel.grade ? 2 : 1,
        });
        poly.setMap(map);
        polygonsRef.current.push({ pnu: parcel.pnu, poly });
        kakao.maps.event.addListener(poly, "click", () => {
          if (pickingRef.current) togglePick(parcel.pnu);
          else setSelected(parcel);
        });
        kakao.maps.event.addListener(poly, "mouseover", () => {
          poly.setOptions({ strokeWeight: 3, strokeColor: "#0f172a" });
        });
        kakao.maps.event.addListener(poly, "mouseout", () => {
          poly.setOptions({ strokeWeight: 1, strokeColor: "#94a3b8" });
        });
      }
    }
  }, [data, phase]);

  useEffect(() => {
    for (const { pnu, poly } of polygonsRef.current) {
      const isSelected = pnu === selected?.pnu;
      const isPicked = picked.includes(pnu);
      poly.setOptions({
        strokeWeight: isSelected ? 4 : isPicked ? 3 : 1,
        strokeColor: isSelected ? "#0f172a" : isPicked ? "#7c3aed" : "#94a3b8",
      });
    }
  }, [selected, picked, data]);

  function goTo(lat: number, lng: number) {
    const map = mapRef.current as unknown as {
      setCenter: (p: unknown) => void;
      setLevel: (n: number) => void;
    } | null;
    if (!map) return;
    map.setLevel(INITIAL_LEVEL);
    map.setCenter(new window.kakao.maps.LatLng(lat, lng));
  }

  function saveReport() {
    if (!data) return;
    downloadParcelReport(
      {
        title: "필지별 길 구조 등급",
        scopeNote: picked.length
          ? `고른 ${picked.length}필지`
          : "지도에 보이던 블록 전체 (대상을 고르지 않음)",
        summary: data.summary,
        parcels: data.parcels,
        caveats: data.caveats,
        totalManwon: amountRef.current,
      },
      picked.length ? "필지등급_선택" : "필지등급_블록",
    );
  }

  function applyPicks() {
    if (picked.length) void fetchPicked(picked);
  }

  function togglePick(pnu: string) {
    setPicked((prev) => {
      const next = prev.includes(pnu) ? prev.filter((p) => p !== pnu) : [...prev, pnu];
      try {
        window.localStorage.setItem(PICK_STORAGE_KEY, JSON.stringify(next));
      } catch {
        // 시크릿 창 등에서 저장이 막혀도 고르는 것 자체는 계속돼야 한다.
      }
      return next;
    });
  }

  function clearPicks() {
    setPicked([]);
    try {
      window.localStorage.removeItem(PICK_STORAGE_KEY);
    } catch {
      /* 저장소가 막힌 환경 */
    }
    if (mapRef.current) void fetchHere(mapRef.current);
  }

  function applyAmount() {
    const value = Number(amountText.replace(/,/g, ""));
    amountRef.current = Number.isFinite(value) && value > 0 ? value : null;
    if (picked.length) void fetchPicked(picked);
    else if (mapRef.current) void fetchHere(mapRef.current);
  }

  if (phase === "error") {
    return (
      <div className="card">
        <p>{reason || "지도를 불러오지 못했습니다."}</p>
      </div>
    );
  }

  const summary = data?.summary;
  const ranked = (data?.parcels || [])
    .filter((p) => p.grade)
    .sort((a, b) => (b.percentile ?? 0) - (a.percentile ?? 0));

  return (
    <div className="floorplan-panel">
      <div className="card">
        <h2>필지별 길 구조 등급</h2>
        <p className="floorplan-help-text">
          지도를 움직이면 그 블록의 필지를 길 구조로 줄 세웁니다. 등급은 <strong>지금 보이는
          범위 안에서의 순위</strong>이고, 감정평가가 아닙니다.
        </p>

        <div className="floorplan-scale-row">
          <span>대상</span>
          <button
            type="button"
            className={`secondary-button${picking ? " floorplan-toolbar-btn--active" : ""}`}
            onClick={() => setPicking((v) => !v)}
          >
            {picking ? "고르는 중 — 필지를 누르세요" : "사업지 필지 고르기"}
          </button>
          {data && (
            <button type="button" className="secondary-button" onClick={saveReport}>
              보고서 내려받기
            </button>
          )}
          {picked.length > 0 && (
            <>
              <span className="floorplan-line-count">고른 필지 {picked.length}곳</span>
              <button type="button" className="secondary-button" onClick={applyPicks} disabled={fetching}>
                고른 것만 등급 매기기
              </button>
              <button type="button" className="secondary-button" onClick={clearPicks}>
                선택 해제
              </button>
            </>
          )}
          <span className="floorplan-scale-hint">
            {picked.length
              ? "고른 필지끼리만 순위를 매깁니다. 지도를 옮겨도 대상이 바뀌지 않아 보고서에 쓸 수 있습니다."
              : "아무것도 안 고르면 화면에 보이는 블록 전체가 대상입니다 — 지도를 옮기면 대상도 바뀝니다."}
          </span>
        </div>

        <div className="floorplan-scale-row">
          <span>필지 경계가 있는 곳</span>
          {COVERED_AREAS.map((area) => (
            <button
              key={area.label}
              type="button"
              className="secondary-button"
              onClick={() => goTo(area.lat, area.lng)}
            >
              {area.label}
            </button>
          ))}
          <span className="floorplan-scale-hint">
            서울 25개 구 전역에 필지 경계가 있습니다. 서울 밖에서는 지도만 보이고 색이
            칠해지지 않으니, 아래 버튼으로 옮겨 보세요.
          </span>
        </div>

        <label className="floorplan-scale-row">
          <span>이 블록 총액 (선택)</span>
          <input
            type="text"
            inputMode="numeric"
            value={amountText}
            onChange={(e) => setAmountText(e.target.value)}
            onBlur={applyAmount}
            onKeyDown={(e) => e.key === "Enter" && applyAmount()}
            placeholder="예: 5000000"
            className="floorplan-scale-input"
          />
          <span>만원</span>
          <span className="floorplan-scale-hint">
            <strong>안 넣으셔도 됩니다.</strong> 여러 필지를 묶어 팔거나 살 때 총액을 등급대로
            나눠야 할 일이 있으면, 그 금액을 넣어 보세요 — 필지마다 얼마씩인지 계산해 줍니다.
            (감정평가액이 아니라 <em>나누는 비율</em>입니다.)
          </span>
        </label>

        <div className="ps-map-wrap">
          <div ref={boxRef} className="pm-canvas" aria-label="필지 구조 등급 지도" />
          <div className="pm-legend">
            {GRADE_LEGEND.map((g) => (
              <span key={g} className="pm-legend-item">
                <i style={{ background: GRADE_COLORS[g] }} aria-hidden />
                {g}등급
              </span>
            ))}
            <span className="pm-legend-item">
              <i style={{ background: UNGRADED_COLOR, opacity: 0.5 }} aria-hidden />
              등급 없음(접한 도로 못 찾음)
            </span>
          </div>
          <p className="pm-status" role="status">
            {tooFarOut
              ? "지도를 더 확대해 주세요 — 한 번에 한 블록씩 계산합니다. 건물이 하나씩 보일 만큼 들어가면 색이 칠해집니다."
              : fetching
              ? "이 블록의 필지를 살펴보는 중…"
              : summary
                ? `필지 ${summary.parcel_count}곳 · 등급 ${summary.graded_count}곳 · 코너 ${summary.corner_count}곳` +
                  (summary.landlocked_count ? ` · 등급 없음 ${summary.landlocked_count}곳` : "") +
                  (summary.structure_spread ? ` · 상·하위 10% 차이 ${summary.structure_spread}배` : "")
                : "지도를 움직여 보세요."}
          </p>
          {reason && <p className="pm-status pm-status--warn">{reason}</p>}
        </div>
      </div>

      {selected && (
        <div className="card">
          <div className="floorplan-result-header">
            <h2>{selected.pnu}</h2>
            <button type="button" className="secondary-button" onClick={() => setSelected(null)}>
              닫기
            </button>
          </div>
          <dl className="floorplan-summary-grid">
            <div>
              <dt>면적</dt>
              <dd>{selected.area_sqm.toFixed(0)}㎡</dd>
            </div>
            <div>
              <dt>등급</dt>
              <dd style={{ color: selected.grade ? GRADE_COLORS[selected.grade] : undefined }}>
                {selected.grade ? `${selected.grade}등급` : "없음"}
              </dd>
            </div>
            {selected.percentile != null && (
              <div>
                <dt>이 범위에서</dt>
                <dd>{topPercentText(selected.percentile)}</dd>
              </div>
            )}
            <div>
              <dt title="접한 도로 구간 수 — 많으면 코너">접한 길</dt>
              <dd>{selected.frontage_count}곳</dd>
            </div>
            {selected.road_distance_m != null && (
              <div>
                <dt>가장 가까운 길</dt>
                <dd>{selected.road_distance_m.toFixed(1)}m</dd>
              </div>
            )}
            {selected.allocation_manwon != null && (
              <div>
                <dt>배분 금액</dt>
                <dd>{money(selected.allocation_manwon)}원</dd>
              </div>
            )}
          </dl>
          {!selected.grade && (
            <p className="floorplan-scale-hint">
              20m 안에서 접한 도로를 찾지 못했습니다. 맹지이거나 도로 데이터가 닿지 않는
              자리입니다 — <strong>나쁜 자리라는 뜻이 아니라 모른다는 뜻</strong>입니다.
            </p>
          )}
        </div>
      )}

      {!!ranked.length && (
        <div className="card">
          <h2>등급 순</h2>
          <div className="floorplan-room-table-wrap">
            <table className="floorplan-room-table">
              <thead>
                <tr>
                  <th>순위</th>
                  <th>지번코드</th>
                  <th>면적</th>
                  <th>등급</th>
                  <th>접한 길</th>
                  {ranked.some((p) => p.allocation_manwon != null) && <th>배분</th>}
                </tr>
              </thead>
              <tbody>
                {ranked.slice(0, 50).map((parcel, i) => (
                  <tr
                    key={parcel.pnu}
                    className={selected?.pnu === parcel.pnu ? "floorplan-room-row--active" : undefined}
                    onMouseEnter={() => setSelected(parcel)}
                  >
                    <td>{i + 1}</td>
                    <td>{parcel.pnu.slice(-8)}</td>
                    <td>{parcel.area_sqm.toFixed(0)}㎡</td>
                    <td style={{ color: GRADE_COLORS[parcel.grade as number] }}>{parcel.grade}등급</td>
                    <td>{parcel.frontage_count}</td>
                    {ranked.some((p) => p.allocation_manwon != null) && (
                      <td>{parcel.allocation_manwon != null ? money(parcel.allocation_manwon) : "—"}</td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {ranked.length > 50 && (
            <p className="floorplan-scale-hint">상위 50곳만 표로 보여줍니다. 나머지는 지도에서 확인하세요.</p>
          )}
        </div>
      )}

      {!!data?.caveats?.length && (
        <div className="card">
          <h2>이 숫자를 어디까지 믿을지</h2>
          <ul className="warnings-banner">
            {data.caveats.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
