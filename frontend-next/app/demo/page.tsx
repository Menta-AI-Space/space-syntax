"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import FloorplanResult, {
  DEFAULT_FLOORPLAN_TAB,
  paintOf,
  type FloorplanTabId,
} from "@/components/demo/FloorplanResult";
import PlanCanvas, { VIEW_H, VIEW_W } from "@/components/demo/PlanCanvas";
import { buildFloorplanDemo, type Space } from "@/lib/demoFloorplan";
import { computeVga, type VgaResult } from "@/lib/floorplanVga";
import { loadKakaoMaps } from "@/lib/kakao";
import {
  ANALYSIS_KINDS,
  DEMO_CENTER,
  DEMO_PLANS,
  DEMO_REGIONS,
  GAP_BANDS,
  INDUSTRIES,
  buildDemoSites,
  comparableMedian,
  gapBand,
  heroSite,
  percentileText,
  signedPct,
  signedPp,
  verdictOf,
  wonPerM2,
  type AnalysisKindId,
  type DemoSite,
  type PlanImpact,
} from "@/lib/demoParcels";
import {
  buildTradeAnalysis,
  cannibalLabel,
  eokwon,
  gradeNote,
  hourLabel,
  manwon,
  percentileLabel,
  vsSeoulPct,
  type TradeAnalysis,
} from "@/lib/demoTrade";
import { CHAT_INTRO, CHAT_TURNS, FOLLOW_UPS, type ChatBlock } from "@/lib/demoChat";

/**
 * LAND AI 데모 — 60초 안에 서비스가 어떻게 돌아가는지 보여준다.
 *
 * 흐름은 **분석지역 → 분석 종류 → 그 분석에 필요한 최소 입력 → 실행**이다.
 * 토지가치분석은 추가 입력이 없어 바로 실행으로 가고, 상권분석은 업종을,
 * 평면 공간구조 분석은 도면을 받는다.
 *
 * 데모는 이 한 URL 하나다. 평면 공간구조 분석은 `/demo/floorplan`이라는 별도
 * 화면이었는데 2026-08-24에 여기로 접었다 — 데모 링크를 두 개 들고 다니면 보는
 * 사람은 둘이 같은 서비스인지부터 의심한다. 결과 화면(`.res-*`)과 셋업을 그대로
 * 쓰고, 왼쪽 패널만 `components/demo/FloorplanResult.tsx`가 채운다.
 *
 * 결과는 AI 채팅이 아니라 **탭형 UI**다. 위에 요약이 항상 떠 있고, 그 아래 6개
 * 탭이 각각 하나의 질문에만 답한다. 지도에서 지점을 누르면 그 필지로 바뀐다.
 *
 * 숫자는 **예시 데이터**다(`lib/demoParcels.ts`). 지도 타일과 좌표만 실제다.
 */

const TABS = [
  { id: "overview", label: "종합" },
  { id: "future", label: "미래전망" },
  { id: "value", label: "가치평가" },
  { id: "hotspot", label: "유망입지" },
  { id: "place", label: "입지·도시계획" },
  { id: "compare", label: "주변비교" },
] as const;

type TabId = (typeof TABS)[number]["id"];

/** 「종합」이 아닌 첫 탭. 근거는 `docs/SPEC_LAND_VALUE_TABS_UI.md`. */
const DEFAULT_TAB: TabId = "future";

/**
 * 상권분석도 같은 규칙으로 묶는다 — **탭 하나가 질문 하나에 답한다.**
 *
 * 지금 서비스는 결과를 네 섹션(입지 진단 / 업종 분석 / 시세·유동인구 / 확장
 * 분석)으로 내는데, "확장 분석" 한 칸에 도달권·잠식·시너지·추세가 다 들어가 있고
 * 임대 타당성은 아예 다른 화면에 있다. 여섯으로 갈라 각 탭이 답하는 질문을
 * 화면 맨 위에 적는다.
 */
const TRADE_TABS = [
  { id: "t-overview", label: "종합" },
  { id: "t-site", label: "입지진단" },
  { id: "t-demand", label: "수요·도달권" },
  { id: "t-rival", label: "경쟁·잠식" },
  { id: "t-money", label: "매출·손익" },
  { id: "t-trend", label: "상권추세" },
] as const;

type TradeTabId = (typeof TRADE_TABS)[number]["id"];

/** 상권분석의 첫 탭도 「종합」이 아니다 — 상단 Summary와 겹친다. */
const DEFAULT_TRADE_TAB: TradeTabId = "t-site";

/**
 * 네 막이다.
 *
 * 1막 토지가치분석 — 탭 바를 왼쪽에서 오른쪽으로 걷되 요약인 「종합」으로 맺는다.
 * 2막 상권분석 — 같은 셋업으로 돌아가 종류만 바꾼다. 업종 단계가 여기서 생긴다.
 * 3막 평면 공간구조 — 같은 셋업, 입력만 도면이다. 지도 자리에 평면이 들어간다.
 * 4막 AI — 앞의 분석을 **이미 본 사람이 하는 후속 질문**을 보여 준다.
 */
const SCENES = [
  { key: "intro", dur: 8_000, label: "무엇을 하는가" },
  { key: "setup", dur: 10_000, label: "분석 설정" },
  { key: "future", dur: 12_000, label: "미래전망" },
  { key: "value", dur: 12_000, label: "가치평가 · 유망입지" },
  { key: "place", dur: 10_000, label: "입지 · 주변비교" },
  { key: "overview", dur: 8_000, label: "종합" },
  { key: "tradeSetup", dur: 9_000, label: "상권분석 설정" },
  { key: "tradeSite", dur: 12_000, label: "입지진단 · 수요" },
  { key: "tradeRival", dur: 12_000, label: "경쟁 · 매출" },
  { key: "tradeTrend", dur: 9_000, label: "상권추세 · 종합" },
  { key: "planSetup", dur: 9_000, label: "평면 분석 설정" },
  { key: "planTerms", dur: 11_000, label: "쓰는 말" },
  { key: "planMetrics", dur: 15_000, label: "얼마나 보이나 · 찾아오기" },
  { key: "planReading", dur: 10_000, label: "평면 해석" },
  { key: "chatTrade", dur: 18_000, label: "AI — 상권 후속 질문" },
  { key: "chatLand", dur: 20_000, label: "AI — 미래가치 후속 질문" },
] as const;

type Phase = "loading" | "ready" | "error";

type OverlayHandle = { setMap: (map: unknown) => void };

export default function DemoPage() {
  const sites = useMemo(() => buildDemoSites(), []);
  const hero = useMemo(() => heroSite(sites), [sites]);

  const boxRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<unknown>(null);
  const siteOverlaysRef = useRef<{ site: DemoSite; overlay: OverlayHandle; el: HTMLElement }[]>([]);
  const compareOverlaysRef = useRef<OverlayHandle[]>([]);
  const reachCirclesRef = useRef<OverlayHandle[]>([]);
  const stepTimersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  const bodyRef = useRef<HTMLDivElement | null>(null);

  const [phase, setPhase] = useState<Phase>("loading");
  const [reason, setReason] = useState("");

  const [sceneIdx, setSceneIdx] = useState(0);
  const [progress, setProgress] = useState(0);
  const [playing, setPlaying] = useState(true);

  // 셋업 상태 — 지역 → 종류 → (업종) → 실행.
  const [region, setRegion] = useState<string | null>(null);
  const [kind, setKind] = useState<AnalysisKindId | null>(null);
  const [industry, setIndustry] = useState<string | null>(null);
  const [ran, setRan] = useState(false);

  const [selected, setSelected] = useState<DemoSite | null>(null);
  // 처음 열리는 탭은 「미래전망」이다 — 상단 Summary가 이미 결론을 담고 있어서
  // 「종합」을 default로 두면 같은 요약을 두 번 읽는다.
  const [tab, setTab] = useState<TabId>(DEFAULT_TAB);
  const [tradeTab, setTradeTab] = useState<TradeTabId>(DEFAULT_TRADE_TAB);
  /** AI 장면에서 지금 보여 주는 후속 질문. */
  const [chatIdx, setChatIdx] = useState(0);

  // 평면 공간구조 분석. 도면 도형은 고정이고(예시), 그 위의 값만 계산한다.
  const plan = useMemo(() => buildFloorplanDemo(), []);
  const [planFile, setPlanFile] = useState<string | null>(null);
  const [planTab, setPlanTab] = useState<FloorplanTabId>(DEFAULT_FLOORPLAN_TAB);
  const [vga, setVga] = useState<VgaResult | null>(null);
  const [planSpace, setPlanSpace] = useState<Space | null>(null);

  const trade = useMemo(
    () => (region && industry ? buildTradeAnalysis(region, industry) : null),
    [region, industry],
  );

  const scene = SCENES[sceneIdx];
  const kindMeta = ANALYSIS_KINDS.find((k) => k.id === kind) ?? null;
  /** 평면 분석은 지도 자리를 도면이 쓴다 — 이 분석의 단위는 필지가 아니라 한 층이다. */
  const showPlan = ran && kind === "floorplan";
  const canRun = Boolean(
    region &&
      kind &&
      (!kindMeta?.needsIndustry || industry) &&
      (!kindMeta?.needsPlan || planFile),
  );

  // ── 평면 지표는 실행한 뒤에 한 번만 계산한다 ───────────────────────────
  // 배포된 브라우저에서 4.2초 걸린다(`docs/SPEC_SPACE_ANALYSIS_MODULES.md`).
  // 그동안 메인 스레드가 멈추므로, 도면을 먼저 띄운 다음 한 틱 미뤄 시작한다 —
  // 화면에는 이미 평면이 있고 패널이 "계산 중"이라고 말한다. 도형이 고정이라
  // 값도 하나뿐이어서, 종류를 오가며 다시 계산하지 않는다.
  useEffect(() => {
    if (!ran || kind !== "floorplan" || vga) return;
    const t = setTimeout(() => {
      setVga(computeVga(plan.walls, plan.openings, VIEW_W, VIEW_H));
    }, 0);
    return () => clearTimeout(t);
  }, [ran, kind, vga, plan]);

  const clearStepTimers = useCallback(() => {
    for (const t of stepTimersRef.current) clearTimeout(t);
    stepTimersRef.current = [];
  }, []);

  const later = useCallback((ms: number, fn: () => void) => {
    stepTimersRef.current.push(setTimeout(fn, ms));
  }, []);

  /** 사람이 손을 대면 시나리오를 멈춘다 — 화면이 저 혼자 넘어가면 안 된다. */
  const takeOver = useCallback(() => {
    setPlaying(false);
    clearStepTimers();
  }, [clearStepTimers]);

  const selectSite = useCallback((site: DemoSite) => {
    setSelected(site);
    setRan(true);
  }, []);
  const selectRef = useRef(selectSite);
  selectRef.current = selectSite;
  const takeOverRef = useRef(takeOver);
  takeOverRef.current = takeOver;

  // ── 지도를 한 번 만들고 지점 오버레이를 얹는다 ─────────────────────────
  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    let cancelled = false;

    loadKakaoMaps()
      .then(() => {
        if (cancelled || !boxRef.current) return;
        const kakao = window.kakao;
        const map = new kakao.maps.Map(boxRef.current, {
          center: new kakao.maps.LatLng(DEMO_CENTER.lat, DEMO_CENTER.lng),
          level: 4,
        });
        mapRef.current = map;

        siteOverlaysRef.current = sites.map((site) => {
          const el = document.createElement("button");
          el.type = "button";
          el.className = "pin";
          el.innerHTML =
            `<span class="pin__dot" style="background:${gapBand(site.gapPct).color}"></span>` +
            `<span class="pin__label">${site.jibun}</span>`;
          el.addEventListener("click", () => {
            takeOverRef.current();
            selectRef.current(site);
          });
          const overlay: OverlayHandle = new kakao.maps.CustomOverlay({
            position: new kakao.maps.LatLng(site.lat, site.lng),
            content: el,
            yAnchor: 1,
            zIndex: 4,
          });
          return { site, overlay, el };
        });

        setPhase("ready");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setPhase("error");
        setReason(err instanceof Error ? err.message : "지도를 불러오지 못했습니다.");
      });

    return () => {
      cancelled = true;
      siteOverlaysRef.current = [];
      compareOverlaysRef.current = [];
      mapRef.current = null;
      // Kakao 지도에는 destroy가 없다 — 비우지 않으면 지도가 겹쳐 쌓인다.
      box.innerHTML = "";
    };
  }, [sites]);

  // ── 실행 전에는 지점을 띄우지 않는다 — 그래야 [분석 실행]이 뜻을 갖는다 ──
  // 필지 지점은 토지가치분석의 것이다. 상권분석은 필지를 고르지 않으므로 띄우지
  // 않는다 — 안 쓰는 지점이 남아 있으면 누르라는 뜻으로 읽힌다.
  useEffect(() => {
    if (phase !== "ready") return;
    const map = mapRef.current;
    const show = ran && kind === "land";
    for (const { site, overlay, el } of siteOverlaysRef.current) {
      overlay.setMap(show ? map : null);
      el.classList.toggle("pin--on", selected?.id === site.id);
    }
  }, [ran, selected, phase, kind]);

  // ── 상권분석은 도달권을 지도에 그린다 — 이 분석의 단위가 반경이기 때문이다 ──
  useEffect(() => {
    if (phase !== "ready") return;
    const kakao = window.kakao;
    for (const c of reachCirclesRef.current) c.setMap(null);
    reachCirclesRef.current = [];
    if (!ran || kind !== "trade" || !trade) return;

    // 도보 5·10·15분 ≒ 350·700·1,100m. 반경을 원으로 그리는 것은 근사다 —
    // 실제 도달권은 가로망을 따라 찌그러진다. 화면에도 그렇게 적어 둔다.
    const radii = [350, 700, 1_100];
    reachCirclesRef.current = trade.reach.map((band, i) => {
      const circle: OverlayHandle = new kakao.maps.Circle({
        center: new kakao.maps.LatLng(DEMO_CENTER.lat, DEMO_CENTER.lng),
        radius: radii[i],
        strokeWeight: 1.5,
        strokeColor: "#2563eb",
        strokeOpacity: 0.55 - i * 0.12,
        strokeStyle: i === 0 ? "solid" : "shortdash",
        fillColor: "#2563eb",
        fillOpacity: 0.1 - i * 0.03,
        zIndex: 2 - i,
      });
      circle.setMap(mapRef.current);
      void band;
      return circle;
    });
  }, [ran, kind, trade, phase]);

  // ── 주변비교 탭에서만 비교 필지를 지도에 얹는다 ────────────────────────
  useEffect(() => {
    if (phase !== "ready") return;
    const kakao = window.kakao;
    for (const o of compareOverlaysRef.current) o.setMap(null);
    compareOverlaysRef.current = [];
    if (tab !== "compare" || !selected) return;

    compareOverlaysRef.current = selected.comparables.map((c) => {
      const el = document.createElement("span");
      el.className = "pin pin--peer";
      el.innerHTML = `<span class="pin__dot"></span><span class="pin__label">${c.name}</span>`;
      const overlay: OverlayHandle = new kakao.maps.CustomOverlay({
        position: new kakao.maps.LatLng(c.lat, c.lng),
        content: el,
        yAnchor: 1,
        zIndex: 3,
      });
      overlay.setMap(mapRef.current);
      return overlay;
    });
  }, [tab, selected, phase]);

  /** 장면에 들어갈 때 하는 일. 장면 안의 잔 동작은 타이머로 이어 붙인다. */
  const enterScene = useCallback(
    (idx: number) => {
      clearStepTimers();
      const key = SCENES[idx].key;

      if (key === "intro") {
        setRegion(null);
        setKind(null);
        setIndustry(null);
        setPlanFile(null);
        setRan(false);
        setSelected(null);
        setTab(DEFAULT_TAB);
        return;
      }
      if (key === "setup") {
        // 사람이 고르는 순서를 그대로 밟는다 — 지역, 종류, 실행.
        setIndustry(null);
        setPlanFile(null);
        setRan(false);
        setSelected(null);
        later(1_400, () => setRegion(DEMO_REGIONS[0].id));
        later(4_200, () => setKind("land"));
        later(7_800, () => {
          setRan(true);
          setSelected(hero);
          setTab(DEFAULT_TAB);
        });
        return;
      }
      if (key === "tradeSetup") {
        // 2막 — 같은 셋업으로 돌아가 종류만 바꾼다. 업종 단계가 여기서 생긴다.
        setRegion(DEMO_REGIONS[0].id);
        setKind(null);
        setIndustry(null);
        setPlanFile(null);
        setRan(false);
        setSelected(null);
        later(1_200, () => setKind("trade"));
        later(4_000, () => setIndustry("카페"));
        later(6_800, () => {
          setRan(true);
          setTradeTab(DEFAULT_TRADE_TAB);
        });
        return;
      }
      if (key === "planSetup") {
        // 3막 — 셋업은 그대로다. 세 번째 칸에서 받는 것이 업종이 아니라 도면이다.
        setRegion(DEMO_REGIONS[0].id);
        setKind(null);
        setIndustry(null);
        setPlanFile(null);
        setRan(false);
        setSelected(null);
        setPlanSpace(null);
        later(1_200, () => setKind("floorplan"));
        later(4_000, () => setPlanFile(DEMO_PLANS[0].id));
        later(6_800, () => {
          setRan(true);
          setPlanTab(DEFAULT_FLOORPLAN_TAB);
        });
        return;
      }
      if (key.startsWith("plan")) {
        // 장면 점으로 곧장 뛰어들어도 결과가 서 있어야 한다.
        setRegion(DEMO_REGIONS[0].id);
        setKind("floorplan");
        setIndustry(null);
        setSelected(null);
        setPlanFile(DEMO_PLANS[0].id);
        setRan(true);

        if (key === "planTerms") {
          setPlanTab("f-terms");
          later(6_000, () => setPlanTab("f-plan"));
          return;
        }
        if (key === "planMetrics") {
          setPlanTab("f-connectivity");
          later(5_000, () => setPlanTab("f-integration"));
          later(10_000, () => setPlanTab("f-mean_depth"));
          return;
        }
        setPlanTab("f-reading");
        return;
      }

      // 아래 장면은 장면 점(dot)으로 곧장 뛰어들 수 있으므로 결과 상태를 보장한다.
      setRegion(DEMO_REGIONS[0].id);
      setRan(true);

      const isTrade = key.startsWith("trade") || key === "chatTrade";
      if (isTrade) {
        setKind("trade");
        setIndustry("카페");
        setSelected(null);
      } else {
        setKind("land");
        setIndustry(null);
        setSelected(hero);
      }

      if (key === "future") {
        setTab("future");
        return;
      }
      if (key === "value") {
        setTab("value");
        later(6_000, () => setTab("hotspot"));
        return;
      }
      if (key === "place") {
        setTab("place");
        later(5_000, () => setTab("compare"));
        return;
      }
      if (key === "overview") {
        setTab("overview");
        return;
      }
      if (key === "tradeSite") {
        setTradeTab("t-site");
        later(6_000, () => setTradeTab("t-demand"));
        return;
      }
      if (key === "tradeRival") {
        setTradeTab("t-rival");
        later(6_000, () => setTradeTab("t-money"));
        return;
      }
      if (key === "tradeTrend") {
        setTradeTab("t-trend");
        later(5_000, () => setTradeTab("t-overview"));
        return;
      }
      // 3막 — 앞의 두 분석을 이미 본 사람이 하는 질문이다.
      if (key === "chatTrade") {
        setTradeTab("t-overview");
        setChatIdx(0);
        return;
      }
      if (key === "chatLand") {
        setTab("overview");
        setChatIdx(1);
      }
    },
    [clearStepTimers, hero, later],
  );

  // 첫 장면 진입.
  useEffect(() => {
    enterScene(0);
    // 최초 1회만 — 이후 전환은 아래 시계가 부른다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => () => clearStepTimers(), [clearStepTimers]);

  // ── 시계 ──────────────────────────────────────────────────────────────
  useEffect(() => {
    let raf = 0;
    let last: number | null = null;
    let elapsed = 0;

    const tick = (now: number) => {
      if (last === null) last = now;
      const dt = now - last;
      last = now;
      if (playing) {
        elapsed += dt;
        const dur = SCENES[sceneIdx].dur;
        if (elapsed >= dur) {
          elapsed = 0;
          const nextIdx = (sceneIdx + 1) % SCENES.length;
          setSceneIdx(nextIdx);
          enterScene(nextIdx);
        }
        setProgress(Math.min(100, (elapsed / dur) * 100));
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, sceneIdx, enterScene]);

  // 탭이 바뀌면 그 탭의 머리부터 보여준다.
  useEffect(() => {
    bodyRef.current?.scrollTo({ top: 0 });
  }, [tab, selected]);

  const go = useCallback(
    (idx: number) => {
      setSceneIdx(idx);
      enterScene(idx);
    },
    [enterScene],
  );

  return (
    <main className="demo">
      <div className="demo-bar">
        <i style={{ width: `${progress}%` }} />
      </div>

      <header className="demo-top">
        <span className="demo-brand">LAND AI</span>
        <span className="demo-badge">예시 데이터</span>

        <div className="demo-ctl">
          <button
            type="button"
            className="demo-play"
            onClick={() => setPlaying((v) => !v)}
            aria-label={playing ? "일시정지" : "재생"}
          >
            <svg width="16" height="16" viewBox="0 0 18 18" aria-hidden="true">
              <path fill="currentColor" d={playing ? "M4 3h3.2v12H4zM10.8 3H14v12h-3.2z" : "M5 3l10 6-10 6z"} />
            </svg>
          </button>
          <div className="demo-dots" role="tablist" aria-label="장면">
            {SCENES.map((s, i) => (
              <button
                key={s.key}
                type="button"
                role="tab"
                className="demo-dot"
                aria-current={i === sceneIdx}
                aria-label={s.label}
                title={s.label}
                onClick={() => go(i)}
              />
            ))}
          </div>
          <span className="demo-count">
            {sceneIdx + 1} / {SCENES.length}
          </span>
        </div>
      </header>

      <div className="demo-body">
        <section className="demo-panel" aria-label="분석">
          {/* 상권·평면 분석은 필지를 고르지 않는다 — 실행만 되면 결과다. */}
          {!ran || (kind === "land" && !selected) ? (
            <Setup
              region={region}
              kind={kind}
              industry={industry}
              planFile={planFile}
              canRun={canRun}
              needsIndustry={Boolean(kindMeta?.needsIndustry)}
              needsPlan={Boolean(kindMeta?.needsPlan)}
              onRegion={(id) => {
                takeOver();
                setRegion(id);
                setKind(null);
                setIndustry(null);
                setPlanFile(null);
              }}
              onKind={(id) => {
                takeOver();
                setKind(id);
                setIndustry(null);
                setPlanFile(null);
              }}
              onIndustry={(v) => {
                takeOver();
                setIndustry(v);
              }}
              onPlan={(v) => {
                takeOver();
                setPlanFile(v);
              }}
              onRun={() => {
                takeOver();
                setRan(true);
                if (kind === "trade") {
                  setTradeTab(DEFAULT_TRADE_TAB);
                } else if (kind === "floorplan") {
                  setPlanTab(DEFAULT_FLOORPLAN_TAB);
                } else {
                  setSelected(hero);
                  setTab(DEFAULT_TAB);
                }
              }}
            />
          ) : kind === "floorplan" ? (
            <FloorplanResult
              plan={plan}
              vga={vga}
              tab={planTab}
              onTab={(t) => {
                takeOver();
                setPlanTab(t);
              }}
              space={planSpace}
              bodyRef={bodyRef}
            />
          ) : kind === "trade" && trade ? (
            <TradeResult
              trade={trade}
              tab={tradeTab}
              onTab={(t) => {
                takeOver();
                setTradeTab(t);
              }}
              bodyRef={bodyRef}
            />
          ) : !selected ? (
            <div className="setup">
              <p className="demo-lead">분석을 실행하면 결과가 여기에 나옵니다.</p>
            </div>
          ) : (
            <Result
              site={selected}
              tab={tab}
              onTab={(t) => {
                takeOver();
                setTab(t);
              }}
              bodyRef={bodyRef}
            />
          )}
        </section>

        <section className="demo-map" aria-label={showPlan ? "도면" : "지도"}>
          {/*
            지도 컨테이너는 항상 붙어 있다 — 평면 분석에서 지웠다 다시 붙이면 Kakao
            지도를 처음부터 다시 만들어야 하고, 그 효과는 sites가 바뀔 때만 돈다.
            display:none으로 숨겨도 Kakao 지도는 다시 보일 때 크기를 잃는다. 그래서
            지도는 그대로 두고, 그 위에 불투명한 평면을 덮는다.
          */}
          <div ref={boxRef} className="demo-canvas" />
          {showPlan && (
            <div className="demo-plan">
              <PlanCanvas
                walls={plan.walls}
                openings={plan.openings}
                spaces={plan.spaces}
                vga={vga}
                paint={paintOf(planTab)}
                showExtremes={paintOf(planTab) !== null}
                onSpace={planTab === "f-plan" ? setPlanSpace : undefined}
              />
            </div>
          )}
          {phase === "loading" && !showPlan && <p className="demo-mapmsg">지도를 불러오는 중…</p>}
          {phase === "error" && !showPlan && (
            <div className="demo-mapmsg demo-mapmsg--err">
              <p>{reason}</p>
            </div>
          )}
          {ran && kind === "trade" && trade && (
            <div className="demo-legend">
              <h3>도보 도달권</h3>
              {trade.reach.map((b, i) => (
                <div key={b.minutes} className="demo-legend-row">
                  <i
                    aria-hidden="true"
                    style={{ background: `rgba(37, 99, 235, ${0.34 - i * 0.1})` }}
                  />
                  {b.minutes}분 · 배후 {b.population.toLocaleString("ko-KR")}명
                </div>
              ))}
              <p className="demo-micro">
                원은 근사다. 실제 도달권은 가로망을 따라 찌그러진다 — 탭의 숫자는 가로망
                기준이다.
              </p>
            </div>
          )}
          {showPlan && (
            <div className="demo-legend">
              <h3>{planTab === "f-plan" || planTab === "f-terms" ? "도면" : "칠한 값"}</h3>
              <p className="demo-micro">
                {planTab === "f-plan" || planTab === "f-terms"
                  ? "벽·문·창·방은 탭이 바뀌어도 그대로입니다. 바뀌는 것은 그 위에 칠하는 값뿐입니다."
                  : "옅은 곳이 낮고 진한 곳이 높습니다. 색은 이 도면 안에서의 순위이고, 다른 건물과 비교할 수 있는 값이 아닙니다."}
              </p>
            </div>
          )}
          {ran && kind === "land" && (
            <div className="demo-legend">
              <h3>모델 예측 − 실제 개별공시지가 (Gap %)</h3>
              {GAP_BANDS.map((b) => (
                <div key={b.label} className="demo-legend-row">
                  <i style={{ background: b.color }} aria-hidden="true" />
                  {b.label}
                </div>
              ))}
              <p className="demo-micro">
                양수 = 모델이 보는 값보다 실제 공시지가가 낮다. 시장 실거래가와의 비교가
                아니다.
              </p>
            </div>
          )}
          {!ran && phase === "ready" && (
            <p className="demo-maphint">
              {kind === "trade"
                ? "분석을 실행하면 이 지점의 도달권이 지도에 올라옵니다."
                : kind === "floorplan"
                  ? "분석을 실행하면 이 자리에 도면과 계산 결과가 올라옵니다."
                  : "분석을 실행하면 이 지역의 필지 판정이 지도에 올라옵니다."}
            </p>
          )}
        </section>

        {scene.key === "intro" && <IntroOverlay />}
        {scene.key.startsWith("chat") && <ChatOverlay turn={chatIdx} />}
      </div>
    </main>
  );
}

/* ── 셋업 — 분석지역 → 분석 종류 → 필요한 최소 입력 ────────────────────── */

function Setup({
  region,
  kind,
  industry,
  planFile,
  canRun,
  needsIndustry,
  needsPlan,
  onRegion,
  onKind,
  onIndustry,
  onPlan,
  onRun,
}: {
  region: string | null;
  kind: AnalysisKindId | null;
  industry: string | null;
  planFile: string | null;
  canRun: boolean;
  needsIndustry: boolean;
  needsPlan: boolean;
  onRegion: (id: string) => void;
  onKind: (id: AnalysisKindId) => void;
  onIndustry: (v: string) => void;
  onPlan: (v: string) => void;
  onRun: () => void;
}) {
  return (
    <div className="setup">
      <div className="setup-step">
        <p className="setup-k">
          <span className="setup-n">1</span> 분석지역
        </p>
        <div className="setup-opts">
          {DEMO_REGIONS.map((r) => (
            <button
              key={r.id}
              type="button"
              className={`setup-opt${region === r.id ? " setup-opt--on" : ""}`}
              onClick={() => onRegion(r.id)}
            >
              <b>{r.label}</b>
              <span>{r.detail}</span>
            </button>
          ))}
        </div>
      </div>

      <div className={`setup-step${region ? "" : " setup-step--off"}`}>
        <p className="setup-k">
          <span className="setup-n">2</span> 분석 종류
        </p>
        <div className="setup-opts">
          {ANALYSIS_KINDS.map((k) => (
            <button
              key={k.id}
              type="button"
              className={`setup-opt${kind === k.id ? " setup-opt--on" : ""}`}
              disabled={!region}
              onClick={() => onKind(k.id)}
            >
              <b>{k.label}</b>
              <span>{k.detail}</span>
            </button>
          ))}
        </div>
      </div>

      {/* 업종은 상권분석만 받는다. 토지가치분석에는 필요한 입력이 없어 바로 실행이다. */}
      {needsIndustry && (
        <div className="setup-step">
          <p className="setup-k">
            <span className="setup-n">3</span> 업종
          </p>
          <div className="setup-chips">
            {INDUSTRIES.map((v) => (
              <button
                key={v}
                type="button"
                className={`setup-chip${industry === v ? " setup-chip--on" : ""}`}
                onClick={() => onIndustry(v)}
              >
                {v}
              </button>
            ))}
          </div>
        </div>
      )}

      {/*
        도면은 평면 공간구조 분석만 받는다. 데모라 예시 도면 하나뿐이고, 이 칸이
        실제 서비스에서 업로드가 서는 자리다 — 받는 형식은 DXF 벡터뿐이다.
      */}
      {needsPlan && (
        <div className="setup-step">
          <p className="setup-k">
            <span className="setup-n">3</span> 도면
          </p>
          <div className="setup-opts">
            {DEMO_PLANS.map((p) => (
              <button
                key={p.id}
                type="button"
                className={`setup-opt${planFile === p.id ? " setup-opt--on" : ""}`}
                onClick={() => onPlan(p.id)}
              >
                <b>{p.label}</b>
                <span>{p.detail}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      <button type="button" className="setup-run" disabled={!canRun} onClick={onRun}>
        분석 실행
      </button>
      <p className="demo-micro">
        {kind === "land"
          ? "토지가치분석은 추가 입력이 없습니다 — 지역만 정하면 실행합니다."
          : kind === "trade"
            ? "상권분석은 무엇과 비교할지를 정해야 하므로 업종을 받습니다."
            : kind === "floorplan"
              ? "평면 공간구조 분석은 건물 한 층의 도면을 받습니다 — DXF 벡터 도면만이고 사진·스캔은 받지 않습니다."
              : "분석 종류에 따라 그다음 받는 입력이 달라집니다."}
      </p>
    </div>
  );
}

/* ── 상권분석 결과 — 상단 요약 + 6개 탭 ────────────────────────────────── */

function TradeResult({
  trade,
  tab,
  onTab,
  bodyRef,
}: {
  trade: TradeAnalysis;
  tab: TradeTabId;
  onTab: (t: TradeTabId) => void;
  bodyRef: React.RefObject<HTMLDivElement | null>;
}) {
  const vsSeoul = vsSeoulPct(trade.perStoreMonthlyMan, trade.seoulMedianPerStoreMan);

  return (
    <div className="res">
      <header className="res-head">
        <p className="res-eyebrow">
          {trade.spotLabel} · 업종 {trade.industry} · 예시 데이터
        </p>
        <h1 className="res-title">상권 종합분석</h1>

        <div className="res-sum">
          <div>
            <span className="res-sk">입지 종합점수</span>
            <span className="res-sv">
              {trade.totalScore}
              <small> / 100 · {trade.grade}</small>
            </span>
          </div>
          <div>
            <span className="res-sk">점포당 월 추정매출</span>
            <span className="res-sv">{manwon(trade.perStoreMonthlyMan)}</span>
          </div>
          <div>
            <span className="res-sk">서울 중앙값 대비</span>
            <span className="res-sv">{vsSeoul}%</span>
          </div>
          <div>
            <span className="res-sk">도보 5분 동일 업종</span>
            <span className="res-sv">{trade.sameCategoryCount}곳</span>
          </div>
          <div>
            <span className="res-sk">잠식 위험</span>
            <span className="res-sv">
              {trade.cannibalPct}%<small> · {trade.cannibalLabel}</small>
            </span>
          </div>
          <div>
            <span className="res-sk">손익분기 월매출</span>
            <span className="res-sv">{manwon(trade.breakEvenMonthlyMan)}</span>
          </div>
        </div>
        <p className="res-verdict">
          한 줄 판정 <b>{trade.verdict}</b>
        </p>
      </header>

      <nav className="res-tabs" role="tablist" aria-label="상권분석 탭">
        {TRADE_TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={`res-tab${tab === t.id ? " res-tab--on" : ""}`}
            onClick={() => onTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <div className="res-body" ref={bodyRef}>
        {tab === "t-overview" && <TradeOverview trade={trade} onTab={onTab} />}
        {tab === "t-site" && <TradeSite trade={trade} />}
        {tab === "t-demand" && <TradeDemand trade={trade} />}
        {tab === "t-rival" && <TradeRival trade={trade} />}
        {tab === "t-money" && <TradeMoney trade={trade} />}
        {tab === "t-trend" && <TradeTrend trade={trade} />}
      </div>

      <footer className="res-foot">
        <div className="res-foot-grid">
          <span>
            상권 데이터 <b>서울시 우리마을가게</b>
          </span>
          <span>
            매출 <b>카드매출 추정 (분기)</b>
          </span>
          <span>
            유동인구 <b>생활인구 (행정동)</b>
          </span>
          <span>
            가로망 <b>Space Syntax</b>
          </span>
          <span>
            도달권 <b>가로망 기준 도보</b>
          </span>
        </div>
        <p>
          <b>추정매출은 상권 단위 집계이고 특정 점포의 실적이 아닙니다.</b> 손익분기와 적정
          임대료는 화면에 적힌 가정 위의 계산이며 보장값이 아닙니다. 이 화면의 수치는 예시
          데이터입니다.
        </p>
      </footer>
    </div>
  );
}

/* ── 상권 탭 1. 종합 ────────────────────────────────────────────────────── */

function TradeOverview({
  trade,
  onTab,
}: {
  trade: TradeAnalysis;
  onTab: (t: TradeTabId) => void;
}) {
  const reach5 = trade.reach[0];
  const rows: { k: string; v: string; to: TradeTabId }[] = [
    { k: "입지 종합", v: `${trade.totalScore}점 · ${trade.grade}`, to: "t-site" },
    { k: "배후 인구 (5분)", v: `${reach5.population.toLocaleString("ko-KR")}명`, to: "t-demand" },
    { k: "동일 업종", v: `${trade.sameCategoryCount}곳`, to: "t-rival" },
    { k: "잠식 위험", v: `${trade.cannibalPct}% · ${trade.cannibalLabel}`, to: "t-rival" },
    { k: "점포당 월매출", v: manwon(trade.perStoreMonthlyMan), to: "t-money" },
    { k: "동일 업종 증감", v: signedPct(trade.categoryGrowthPct), to: "t-trend" },
  ];

  return (
    <div className="tabpane">
      <p className="tab-q">이 자리에서 이 업종을 하면 어떤가?</p>
      <ul className="ov-list">
        {rows.map((r) => (
          <li key={r.k}>
            <span className="ov-k">{r.k}</span>
            <span className="ov-v">{r.v}</span>
            <button type="button" className="ov-more" onClick={() => onTab(r.to)}>
              상세보기
            </button>
          </li>
        ))}
      </ul>
      <p className="tab-note">
        여섯 줄이 각각 아래 탭 하나에서 나온 결론입니다. <b>{trade.verdict}</b>
      </p>
    </div>
  );
}

/* ── 상권 탭 2. 입지진단 ────────────────────────────────────────────────── */

function TradeSite({ trade }: { trade: TradeAnalysis }) {
  return (
    <div className="tabpane">
      <p className="tab-q">이 자리는 무엇이 좋고 무엇이 약한가?</p>

      <div className="hot-score">
        <div>
          <span className="hot-k">입지 종합점수</span>
          <span className="hot-v">
            {trade.totalScore}
            <small> / 100</small>
          </span>
        </div>
        <div>
          <span className="hot-k">등급</span>
          <span className="hot-v">{trade.grade}</span>
        </div>
      </div>
      {gradeNote(trade.grade) && (
        <p className="tab-note">같은 지역 여섯 업종과 견주면 {gradeNote(trade.grade)}.</p>
      )}

      <h4 className="tab-h">항목별 진단</h4>
      <ul className="rank">
        {trade.subscores.map((s) => (
          <li key={s.label}>
            <span className="rank-label">{s.label}</span>
            {s.percentile != null && (
              <span className="rank-track">
                <i style={{ width: `${s.percentile}%` }} />
              </span>
            )}
            <span className="rank-val">{s.value}</span>
          </li>
        ))}
      </ul>
      <p className="tab-note">
        <b>경쟁 강도는 낮을수록 좋은 항목이 아니라 낮게 나온 항목입니다</b> — 막대가 짧다는
        것은 이 자리의 경쟁이 심하다는 뜻입니다. 종합점수는 항목의 단순 평균이 아니라 업종별
        가중치를 쓴 값입니다.
      </p>

      <h4 className="tab-h">공간 구조·교통</h4>
      <ul className="rank">
        {trade.spatial.map((s) => (
          <li key={s.label}>
            <span className="rank-label">{s.label}</span>
            {s.percentile != null && (
              <span className="rank-track">
                <i style={{ width: `${s.percentile}%` }} />
              </span>
            )}
            <span className="rank-val">{s.value}</span>
          </li>
        ))}
      </ul>
      <ul className="plan">
        <li>
          <span className="plan-k">지하철</span>
          <span className="plan-v">{trade.transitLabel}</span>
          <span className="imp imp--good">{trade.transitM}m</span>
        </li>
        <li>
          <span className="plan-k">버스</span>
          <span className="plan-v">300m 내 정류장</span>
          <span className="imp imp--good">{trade.busStops}개</span>
        </li>
      </ul>
    </div>
  );
}

/* ── 상권 탭 3. 수요·도달권 ─────────────────────────────────────────────── */

function TradeDemand({ trade }: { trade: TradeAnalysis }) {
  const max = Math.max(...trade.footfall.hourly.map((h) => Math.max(h.weekday, h.weekend)));

  return (
    <div className="tabpane">
      <p className="tab-q">손님이 어디서, 몇 시에 오는가?</p>

      <div className="cmp-scroll">
        <table className="cmp">
          <thead>
            <tr>
              <th>도보</th>
              <th>면적</th>
              <th>배후 인구</th>
              <th>전체 점포</th>
              <th>동일 업종</th>
            </tr>
          </thead>
          <tbody>
            {trade.reach.map((b) => (
              <tr key={b.minutes}>
                <td>{b.minutes}분</td>
                <td>{b.areaHa}ha</td>
                <td>{b.population.toLocaleString("ko-KR")}명</td>
                <td>{b.stores.toLocaleString("ko-KR")}곳</td>
                <td>{b.sameCategory}곳</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="tab-note">
        도달권은 반경이 아니라 <b>가로망을 따라 걸은 거리</b>입니다. 지도의 원은 눈대중용
        근사입니다.
      </p>

      <h4 className="tab-h">시간대 유동인구</h4>
      <div className="hrs" role="img" aria-label="시간대별 유동인구">
        {trade.footfall.hourly.map((h) => (
          <div key={h.hour} className="hrs-col" title={`${hourLabel(h.hour)}`}>
            <span className="hrs-bar" style={{ height: `${(h.weekday / max) * 100}%` }} />
            <span className="hrs-bar hrs-bar--end" style={{ height: `${(h.weekend / max) * 100}%` }} />
          </div>
        ))}
      </div>
      <div className="hrs-axis">
        <span>00</span>
        <span>06</span>
        <span>12</span>
        <span>18</span>
        <span>23</span>
      </div>
      <p className="tab-note">
        진한 막대가 주중, 옅은 막대가 주말입니다. 주중 정점은{" "}
        <b>{hourLabel(trade.footfall.peakHour)}</b>이고 유동의 주말 비중은{" "}
        {trade.footfall.weekendSharePct}%입니다. 이 상권의 유동인구는 서울{" "}
        {percentileLabel(trade.footfall.percentile)}입니다.
      </p>

      <h4 className="tab-h">연령 · 요일</h4>
      <div className="split2">
        <ul className="rank">
          {trade.footfall.ages.map((a) => (
            <li key={a.label}>
              <span className="rank-label">{a.label}</span>
              <span className="rank-track">
                <i style={{ width: `${a.pct * 1.8}%` }} />
              </span>
              <span className="rank-val">{a.pct}%</span>
            </li>
          ))}
        </ul>
        <ul className="rank">
          {trade.footfall.days.map((d) => (
            <li key={d.label}>
              <span className="rank-label">{d.label}</span>
              <span className="rank-track">
                <i style={{ width: `${d.pct * 1.8}%` }} />
              </span>
              <span className="rank-val">{d.pct}%</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/* ── 상권 탭 4. 경쟁·잠식 ───────────────────────────────────────────────── */

function TradeRival({ trade }: { trade: TradeAnalysis }) {
  const band = trade.cannibalPct >= 55 ? "imp imp--bad" : trade.cannibalPct >= 40 ? "imp imp--flat" : "imp imp--good";

  return (
    <div className="tabpane">
      <p className="tab-q">누가 같은 손님을 나눠 갖는가?</p>

      <div className="val-big">
        <div>
          <span className="val-k">도보 5분 동일 업종</span>
          <span className="val-v">{trade.sameCategoryCount}곳</span>
        </div>
        <div>
          <span className="val-k">잠식 위험</span>
          <span className="val-v">{trade.cannibalPct}%</span>
          <span className="val-arrow">→ {cannibalLabel(trade.cannibalPct)}</span>
        </div>
      </div>
      <p className="tab-note">
        잠식 위험은 <b>도달권이 서로 얼마나 겹치는가</b>입니다. 경쟁 점포 수가 아니라 겹치는
        면적의 비율이고, 구간(낮음·보통·주의·높음)으로만 읽습니다 — 41%와 46%에 다른 뜻을
        두지 마세요.
      </p>

      <h4 className="tab-h">가까운 경쟁 점포</h4>
      <ul className="plan">
        {trade.competitors.map((c) => (
          <li key={c.label}>
            <span className="plan-k">{c.distanceM}m</span>
            <span className="plan-v">{c.note}</span>
            <span className={c.kind === "동일 업종" ? band : "imp imp--flat"}>{c.kind}</span>
          </li>
        ))}
      </ul>
      <p className="tab-note">
        상호는 쓰지 않습니다 — 지어낸 평가를 실재하는 가게에 붙이지 않기 위해서입니다. 실제
        서비스에서는 상호·거리·업종이 그대로 나옵니다.
      </p>

      <h4 className="tab-h">같이 있으면 도움이 되는 업종</h4>
      <ul className="plan">
        {trade.synergy.map((s) => (
          <li key={s.label}>
            <span className="plan-k">{s.label}</span>
            <span className="plan-v">{s.count}곳</span>
            <span
              className={
                s.relation === "보완"
                  ? "imp imp--good"
                  : s.relation === "경쟁"
                    ? "imp imp--bad"
                    : "imp imp--flat"
              }
            >
              {s.relation}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ── 상권 탭 5. 매출·손익 ───────────────────────────────────────────────── */

function TradeMoney({ trade }: { trade: TradeAnalysis }) {
  const vs = vsSeoulPct(trade.perStoreMonthlyMan, trade.seoulMedianPerStoreMan);

  return (
    <div className="tabpane">
      <p className="tab-q">얼마를 벌어야 남는가?</p>

      {/* 범위를 숫자보다 먼저 — "38억"이 어느 경계 안의 얘기인지 없으면 숫자가 공중에 뜬다. */}
      <p className="tab-scope">
        기준 상권 <b>{trade.trdarName}</b> · 서울시 우리마을가게 상권 경계 ·{" "}
        {trade.trdarAreaHa}ha · 점포 {trade.trdarStores.toLocaleString("ko-KR")}곳 ·{" "}
        {trade.salesBasis}
      </p>

      <div className="fut-rows">
        <div className="fut-row fut-row--now">
          <span className="fut-k">상권 월 추정매출</span>
          <span className="fut-v">{eokwon(trade.trdarMonthlyEok)}</span>
          <span className="fut-d">전체 업종</span>
        </div>
        <div className="fut-row">
          <span className="fut-k">점포당 월 추정매출 (÷ {trade.trdarStores.toLocaleString("ko-KR")}곳)</span>
          <span className="fut-v">{manwon(trade.perStoreMonthlyMan)}</span>
          <span className="fut-d">{vs}%</span>
        </div>
        <div className="fut-row">
          <span className="fut-k">서울 점포당 중앙값</span>
          <span className="fut-v">{manwon(trade.seoulMedianPerStoreMan)}</span>
          <span className="fut-d">100%</span>
        </div>
        <div className="fut-row">
          <span className="fut-k">매출의 주말 비중</span>
          <span className="fut-v">{trade.weekendSalesPct}%</span>
          <span className="fut-d">—</span>
        </div>
      </div>
      <p className="tab-note">
        점포당 값은 <b>상권 매출을 점포 수로 나눈 평균</b>입니다. 새로 여는 가게의 값이
        아니고, 상위 점포와 하위 점포의 차이는 이 숫자에 담기지 않습니다.
      </p>

      <h4 className="tab-h">손익분기 — 가정 위의 계산</h4>
      <div className="hot-calc">
        <div>
          <span>적정 월 임대료 밴드</span>
          <b>
            {trade.rentLowMan.toLocaleString("ko-KR")}~
            {trade.rentHighMan.toLocaleString("ko-KR")}만원
          </b>
        </div>
        <div>
          <span>임대료 부담률 (추정매출 대비)</span>
          <b>{trade.rentBurdenPct}%</b>
        </div>
        <div>
          <span>가정 객단가</span>
          <b>{trade.ticketWon.toLocaleString("ko-KR")}원</b>
        </div>
        <div className="hot-calc__sum">
          <span>손익분기 월매출</span>
          <b>{manwon(trade.breakEvenMonthlyMan)}</b>
        </div>
      </div>
      <div className="val-big">
        <div>
          <span className="val-k">하루 매출</span>
          <span className="val-v">{trade.breakEvenDailyMan}만원</span>
          <span className="val-arrow">월 26일 기준</span>
        </div>
        <div>
          <span className="val-k">하루 객수</span>
          <span className="val-v">{trade.ordersPerDay}</span>
          <span className="val-arrow">객단가 {trade.ticketWon.toLocaleString("ko-KR")}원</span>
        </div>
      </div>
      <p className="tab-note">
        이 계산은 임대료·인건비·원가율 가정을 바꾸면 같이 움직입니다. 실제 서비스에서는 그
        가정을 직접 넣고 밴드가 어떻게 움직이는지 봅니다.
      </p>
    </div>
  );
}

/* ── 상권 탭 6. 상권추세 ────────────────────────────────────────────────── */

function TradeTrend({ trade }: { trade: TradeAnalysis }) {
  const vals = trade.trend.map((q) => q.sameCategory);
  const max = Math.max(...vals);
  const min = Math.min(...vals);
  const span = Math.max(1, max - min);
  const pts = trade.trend.map((q, i) => ({
    x: 6 + (i * 88) / (trade.trend.length - 1),
    y: 44 - ((q.sameCategory - min) / span) * 34,
    q,
  }));

  return (
    <div className="tabpane">
      <p className="tab-q">이 상권은 커지고 있는가, 줄고 있는가?</p>

      <div className="val-big">
        <div>
          <span className="val-k">동일 업종 점포 수</span>
          <span className="val-v">{signedPct(trade.categoryGrowthPct)}</span>
          <span className="val-arrow">최근 8분기</span>
        </div>
        <div>
          <span className="val-k">상권 전체 점포 수</span>
          <span className="val-v">{signedPct(trade.totalGrowthPct)}</span>
          <span className="val-arrow">최근 8분기</span>
        </div>
      </div>

      <svg className="fut-chart" viewBox="0 0 100 52" role="img" aria-label="동일 업종 점포 수 추이">
        <polyline
          points={pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ")}
          fill="none"
          stroke="var(--blue-50)"
          strokeWidth="1.4"
          strokeLinejoin="round"
        />
        {pts.map((p, i) => (
          <g key={p.q.quarter}>
            <circle cx={p.x} cy={p.y} r="1.6" fill="var(--blue-50)" />
            {i % 2 === 0 && (
              <text x={p.x} y="51" textAnchor="middle" className="fut-chart__t">
                {p.q.quarter}
              </text>
            )}
          </g>
        ))}
      </svg>
      <p className="tab-note">
        점포 수가 느는 것은 <b>수요가 늘었다는 신호이면서 동시에 경쟁이 는다는 신호</b>입니다.
        어느 쪽인지는 매출 탭의 점포당 값과 같이 봐야 갈립니다.
      </p>

      <h4 className="tab-h">거리별 신호</h4>
      <ul className="plan">
        {trade.streetSignals.map((s) => (
          <li key={s.name}>
            <span className="plan-k">{s.name}</span>
            <span className="plan-v">{s.detail}</span>
            <span className="imp imp--flat">{s.label}</span>
          </li>
        ))}
      </ul>

      <h4 className="tab-h">닮은 상권</h4>
      <div className="cmp-scroll">
        <table className="cmp">
          <thead>
            <tr>
              <th>상권</th>
              <th>유사도</th>
              <th>점포당 월매출</th>
              <th>점포 수</th>
            </tr>
          </thead>
          <tbody>
            <tr className="cmp--self">
              <td>{trade.regionLabel}</td>
              <td>—</td>
              <td>{manwon(trade.perStoreMonthlyMan)}</td>
              <td>{trade.reach[0].stores.toLocaleString("ko-KR")}곳</td>
            </tr>
            {trade.analogs.map((a) => (
              <tr key={a.name}>
                <td>{a.name}</td>
                <td>{a.similarity.toFixed(2)}</td>
                <td>{manwon(a.perStoreMonthlyMan)}</td>
                <td>{a.storeCount.toLocaleString("ko-KR")}곳</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="tab-note">
        유사도는 유동인구 구성·업종 구성·매출 구조를 함께 본 값입니다. 닮은 상권이 어떻게
        움직였는지가 이 상권의 다음 분기를 읽는 재료가 됩니다.
      </p>
    </div>
  );
}

/* ── AI 후속 질문 장면 ──────────────────────────────────────────────────── */

function ChatOverlay({ turn }: { turn: number }) {
  const t = CHAT_TURNS[Math.min(turn, CHAT_TURNS.length - 1)];
  const follows = FOLLOW_UPS[t.id] ?? [];

  return (
    <div className="demo-over" role="group" aria-label="AI 후속 질문">
      <div className="demo-over-in chat-in">
        <div className="chat-top">
          <div>
            <p className="demo-eyebrow">분석을 본 다음에 하는 질문</p>
            <h1>AI는 입구가 아니라 설명 창구입니다</h1>
          </div>
          <div className="chat-intro">
            {CHAT_INTRO.map((c) => (
              <div key={c.k}>
                <b>{c.k}</b>
                <span>{c.v}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="chat-thread">
          <div className="chat-q">
            <span className="chat-scope">{t.scope}</span>
            <p>{t.q}</p>
          </div>

          <div className="chat-a">
            <div className="chat-tools">
              {t.tools.map((tool) => (
                <span key={tool} className="chat-tool">
                  {tool}
                </span>
              ))}
              <span className="chat-elapsed">{t.elapsedLabel}</span>
            </div>

            {t.blocks.map((b, i) => (
              <ChatBlockView key={i} block={b} />
            ))}

            <div className="chat-src">
              {t.sources.map((s) => (
                <span key={s}>{s}</span>
              ))}
            </div>
          </div>

          <div className="chat-follow">
            {follows.map((f) => (
              <span key={f} className="chat-chip">
                {f}
              </span>
            ))}
          </div>
        </div>

        <p className="demo-micro">
          예시 대화입니다. 실제 서비스의 도구 루프는 5초 룰의 유일한 예외로, 속도 대신 깊이를
          택한 경로입니다.
        </p>
      </div>
    </div>
  );
}

/** `**굵게**` 한 겹만 푼다 — 답 텍스트에 그 이상은 넣지 않는다. */
function bold(text: string) {
  return text.split(/\*\*(.+?)\*\*/g).map((part, i) => (i % 2 ? <b key={i}>{part}</b> : part));
}

function ChatBlockView({ block }: { block: ChatBlock }) {
  if (block.kind === "p") return <p className="chat-p">{bold(block.text)}</p>;
  if (block.kind === "note") return <p className="chat-note">{bold(block.text)}</p>;
  if (block.kind === "ul") {
    return (
      <ul className="chat-ul">
        {block.items.map((it, i) => (
          <li key={i}>{bold(it)}</li>
        ))}
      </ul>
    );
  }
  return (
    <div className="cmp-scroll">
      <table className="cmp">
        <thead>
          <tr>
            {block.head.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {block.rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── 결과 — 상단 요약 + 6개 탭 ──────────────────────────────────────────── */

function Result({
  site,
  tab,
  onTab,
  bodyRef,
}: {
  site: DemoSite;
  tab: TabId;
  onTab: (t: TabId) => void;
  bodyRef: React.RefObject<HTMLDivElement | null>;
}) {
  const verdict = verdictOf(site.gapPct);
  const band = gapBand(site.gapPct);
  const y3 = site.future.find((f) => f.horizon === 3);

  return (
    <div className="res">
      <header className="res-head">
        <p className="res-eyebrow">
          서대문구 {site.jibun} · 예시 필지 · Peer {site.peerCount.toLocaleString("ko-KR")}필지
        </p>
        <h1 className="res-title">토지가치 종합분석</h1>

        <div className="res-sum">
          <div>
            <span className="res-sk">현재 개별공시지가</span>
            <span className="res-sv">{wonPerM2(site.actual)}</span>
          </div>
          <div>
            <span className="res-sk">모델 예측 공시지가</span>
            <span className="res-sv">{wonPerM2(site.predicted)}</span>
          </div>
          <div>
            <span className="res-sk">{verdict === "고평가 후보" ? "고평가" : "저평가"} Gap</span>
            <span className="res-sv" style={{ color: band.color }}>
              {signedPct(site.gapPct)}
            </span>
          </div>
          <div>
            <span className="res-sk">향후 3년 예상 상승률</span>
            <span className="res-sv">{signedPct(y3?.pct ?? 0)}</span>
          </div>
          <div>
            <span className="res-sk">유망입지 Percentile</span>
            <span className="res-sv">{percentileText(site.percentile)}</span>
          </div>
        </div>
        <p className="res-verdict">
          종합 판정 <b>{site.overall}</b>
        </p>
      </header>

      <nav className="res-tabs" role="tablist" aria-label="분석 탭">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={`res-tab${tab === t.id ? " res-tab--on" : ""}`}
            onClick={() => onTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <div className="res-body" ref={bodyRef}>
        {tab === "overview" && <TabOverview site={site} onTab={onTab} />}
        {tab === "value" && <TabValue site={site} />}
        {tab === "future" && <TabFuture site={site} />}
        {tab === "hotspot" && <TabHotspot site={site} />}
        {tab === "place" && <TabPlace site={site} />}
        {tab === "compare" && <TabCompare site={site} />}
      </div>

      <footer className="res-foot">
        <div className="res-foot-grid">
          <span>
            데이터 기준일 <b>2026.01.01</b>
          </span>
          <span>
            공시지가 <b>2026 개별공시지가</b>
          </span>
          <span>
            모델 후보 <b>LightGBM · CatBoost</b> (validation으로 선택)
          </span>
          <span>
            검증 <b>Spatial CV × Temporal CV</b>
          </span>
          <span>
            누락 데이터 <b>지구단위계획 일부</b>
          </span>
        </div>
        <p>
          <b>
            이 분석의 저평가·고평가는 실거래 시장가격 대비 평가가 아니라 모델 예측 공시지가와
            실제 개별공시지가의 차이입니다.
          </b>{" "}
          예측값은 확정된 값이 아니라 지금 조건이 이어질 때의 전망이며, 인과 추정이 아닙니다.
          이 화면의 수치는 예시 데이터입니다.
        </p>
      </footer>
    </div>
  );
}

/* ── 탭 1. 종합 ─────────────────────────────────────────────────────────── */

function TabOverview({ site, onTab }: { site: DemoSite; onTab: (t: TabId) => void }) {
  const y3 = site.future.find((f) => f.horizon === 3);
  const integration = site.spatial.find((s) => s.label === "Integration");
  const plan = site.plans.find((p) => p.impact !== "중립");

  const rows: { k: string; v: string; to: TabId }[] = [
    { k: "3년 전망", v: signedPct(y3?.pct ?? 0), to: "future" },
    { k: verdictOf(site.gapPct) === "고평가 후보" ? "고평가" : "저평가", v: signedPct(site.gapPct), to: "value" },
    { k: "유망입지", v: percentileText(site.percentile), to: "hotspot" },
    { k: "Space Syntax", v: integration?.value ?? "—", to: "place" },
    { k: "개발계획", v: plan ? plan.impact : "중립", to: "place" },
    { k: "비교군 중앙값", v: wonPerM2(comparableMedian(site)), to: "compare" },
  ];

  return (
    <div className="tabpane">
      <p className="tab-q">이 땅을 한 줄로 보면 어떤가?</p>
      <ul className="ov-list">
        {rows.map((r) => (
          <li key={r.k}>
            <span className="ov-k">{r.k}</span>
            <span className="ov-v">{r.v}</span>
            <button type="button" className="ov-more" onClick={() => onTab(r.to)}>
              상세보기
            </button>
          </li>
        ))}
      </ul>
      <p className="tab-note">
        각 줄은 아래 탭 하나에서 나온 결론입니다. 숫자만 보고 판단하지 말고 근거 탭을 함께
        보세요.
      </p>
    </div>
  );
}

/* ── 탭 3. 가치평가 ─────────────────────────────────────────────────────── */

function TabValue({ site }: { site: DemoSite }) {
  const verdict = verdictOf(site.gapPct);
  const band = gapBand(site.gapPct);

  return (
    <div className="tabpane">
      <p className="tab-q">지금 이 땅은 저평가인가, 고평가인가?</p>

      <div className="val-big">
        <div>
          <span className="val-k">실제 개별공시지가</span>
          <span className="val-v">{wonPerM2(site.actual)}</span>
        </div>
        <div>
          <span className="val-k">모델 예측 공시지가</span>
          <span className="val-v">{wonPerM2(site.predicted)}</span>
        </div>
        <div className="val-gap" style={{ borderColor: band.color }}>
          <span className="val-k">Gap</span>
          <span className="val-v" style={{ color: band.color }}>
            {signedPct(site.gapPct)}
          </span>
          <span className="val-arrow">→ 상대적 {verdict}</span>
        </div>
      </div>

      <p className="tab-note">
        Residual (모델 − 실제) {site.residual >= 0 ? "+" : "−"}
        {wonPerM2(Math.abs(site.residual))} ·{" "}
        {verdict === "저평가 후보"
          ? "모델 예측값이 실제 공시지가보다 높습니다."
          : verdict === "고평가 후보"
            ? "모델 예측값이 실제 공시지가보다 낮습니다. 모델이 보지 못하는 요인(조망·학군·개발 기대)으로 값이 높을 수 있습니다."
            : "모델 예측값과 실제 공시지가가 거의 같습니다."}
      </p>

      <h4 className="tab-h">
        {verdict === "고평가 후보" ? "고평가" : "저평가"}에 영향을 준 요인
      </h4>
      <ul className="drv">
        {site.valueUp.map((d) => (
          <li key={d.label} className="drv--up">
            <span className="drv-mark" aria-hidden="true">
              +
            </span>
            <span className="drv-label">{d.label}</span>
            <span className="drv-arrow">{d.strength === 2 ? "↑↑" : "↑"}</span>
          </li>
        ))}
        {site.valueDown.map((d) => (
          <li key={d.label} className="drv--down">
            <span className="drv-mark" aria-hidden="true">
              −
            </span>
            <span className="drv-label">{d.label}</span>
            <span className="drv-arrow">{d.strength === 2 ? "↓↓" : "↓"}</span>
          </li>
        ))}
      </ul>
      <p className="tab-note">
        SHAP 기여를 방향과 크기로만 보여줍니다. 기여값 자체는 모델·투입 순서에 따라 달라지므로
        순위로 읽으세요.
      </p>
    </div>
  );
}

/* ── 탭 2. 미래전망 ─────────────────────────────────────────────────────── */

function TabFuture({ site }: { site: DemoSite }) {
  const max = Math.max(...site.future.map((f) => f.price));
  const min = Math.min(...site.future.map((f) => f.price));
  const span = Math.max(1, max - min);
  const pts = site.future.map((f, i) => ({
    x: 8 + (i * 84) / (site.future.length - 1),
    y: 44 - ((f.price - min) / span) * 34,
    f,
  }));

  return (
    <div className="tabpane">
      <p className="tab-q">앞으로 가격이 어떻게 될 가능성이 있는가?</p>

      <div className="fut-rows">
        {site.future.map((f) => (
          <div key={f.horizon} className={f.horizon === 0 ? "fut-row fut-row--now" : "fut-row"}>
            <span className="fut-k">{f.horizon === 0 ? "현재" : `${f.horizon}년 예상`}</span>
            <span className="fut-v">{wonPerM2(f.price)}</span>
            <span className="fut-d">{f.horizon === 0 ? `${f.year}년` : signedPct(f.pct)}</span>
          </div>
        ))}
      </div>

      <svg className="fut-chart" viewBox="0 0 100 52" role="img" aria-label="공시지가 전망 추이">
        <polyline
          points={pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ")}
          fill="none"
          stroke="var(--blue-50)"
          strokeWidth="1.4"
          strokeLinejoin="round"
        />
        {pts.map((p) => (
          <g key={p.f.horizon}>
            <circle cx={p.x} cy={p.y} r="1.9" fill="var(--blue-50)" />
            <text x={p.x} y="51" textAnchor="middle" className="fut-chart__t">
              {p.f.year}
            </text>
          </g>
        ))}
      </svg>

      <h4 className="tab-h">왜 오를 것으로 보는가</h4>
      <ul className="rank">
        {site.futureDrivers.map((d) => (
          <li key={d.label}>
            <span className="rank-label">{d.label}</span>
            {d.percentile != null && (
              <span className="rank-track">
                <i style={{ width: `${d.percentile}%` }} />
              </span>
            )}
            <span className="rank-val">{d.value}</span>
          </li>
        ))}
      </ul>
      <p className="tab-note">{site.futureNarrative}</p>
    </div>
  );
}

/* ── 탭 4. 유망입지 ─────────────────────────────────────────────────────── */

function TabHotspot({ site }: { site: DemoSite }) {
  return (
    <div className="tabpane">
      <p className="tab-q">이 자리가 앞으로 뜰 자리인가?</p>

      <div className="hot-score">
        <div>
          <span className="hot-k">유망입지 Score</span>
          <span className="hot-v">
            {site.hotspotScore}
            <small> / 100</small>
          </span>
        </div>
        <div>
          <span className="hot-k">해당 권역</span>
          <span className="hot-v">{percentileText(site.percentile)}</span>
        </div>
      </div>

      <div className="hot-calc">
        <div>
          <span>향후 3년 예상 상승률</span>
          <b>{signedPct(site.growth3y)}</b>
        </div>
        <div>
          <span>유사 필지 예상 상승률</span>
          <b>{signedPct(site.peerGrowth3y)}</b>
        </div>
        <div className="hot-calc__sum">
          <span>예상 초과성장</span>
          <b>{signedPp(site.excessGrowthPp)}</b>
        </div>
      </div>
      <p className="tab-note">
        가격이 많이 오르는 땅이 아니라 <b>같은 지역·비슷한 조건보다 더 오르는 땅</b>을 찾는
        지표입니다. 그래서 미래전망 탭과 따로 둡니다.
      </p>

      <h4 className="tab-h">유망입지 요인</h4>
      <ul className="rank">
        {site.hotspotFactors.map((f) => (
          <li key={f.label}>
            <span className="rank-label">{f.label}</span>
            {f.percentile != null && (
              <span className="rank-track">
                <i style={{ width: `${f.percentile}%` }} />
              </span>
            )}
            <span className="rank-val">{f.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ── 탭 5. 입지·도시계획 ────────────────────────────────────────────────── */

const IMPACT_CLASS: Record<PlanImpact, string> = {
  "매우 긍정적": "imp imp--vgood",
  긍정적: "imp imp--good",
  중립: "imp imp--flat",
  부정적: "imp imp--bad",
};

function TabPlace({ site }: { site: DemoSite }) {
  return (
    <div className="tabpane">
      <p className="tab-q">이 자리는 공간적으로 어떤 자리이고, 계획은 어떻게 걸려 있는가?</p>

      <h4 className="tab-h">입지·공간</h4>
      <ul className="rank">
        {site.spatial.map((s) => (
          <li key={s.label}>
            <span className="rank-label">{s.label}</span>
            {s.percentile != null && (
              <span className="rank-track">
                <i style={{ width: `${s.percentile}%` }} />
              </span>
            )}
            <span className="rank-val">{s.value}</span>
          </li>
        ))}
      </ul>
      <p className="tab-note">
        Space Syntax(Integration·Choice)를 따로 떼어 두지 않습니다 — 여러 입지 지표 중
        하나이고, 기여도는 ablation으로 검증합니다.
      </p>

      <h4 className="tab-h">도시계획·개발</h4>
      <ul className="plan">
        {site.plans.map((p) => (
          <li key={p.label}>
            <span className="plan-k">{p.label}</span>
            <span className="plan-v">{p.value}</span>
            <span className={IMPACT_CLASS[p.impact]}>{p.impact}</span>
          </li>
        ))}
      </ul>
      <p className="tab-note">
        나열이 아니라 <b>가치에 어느 방향으로 작용하는지</b>를 함께 표시합니다. 지구단위계획
        일부는 아직 적재되지 않아 비어 있을 수 있습니다.
      </p>
    </div>
  );
}

/* ── 탭 6. 주변비교 ─────────────────────────────────────────────────────── */

function TabCompare({ site }: { site: DemoSite }) {
  const median = comparableMedian(site);

  return (
    <div className="tabpane">
      <p className="tab-q">비슷한 땅들은 얼마에 매겨져 있는가?</p>

      <div className="cmp-scroll">
        <table className="cmp">
          <thead>
            <tr>
              <th>필지</th>
              <th>거리</th>
              <th>용도지역</th>
              <th>면적</th>
              <th>도로조건</th>
              <th>공시지가</th>
              <th>유사도</th>
            </tr>
          </thead>
          <tbody>
            <tr className="cmp--self">
              <td>대상 필지</td>
              <td>—</td>
              <td>{site.zoning}</td>
              <td>{site.areaM2.toLocaleString("ko-KR")}㎡</td>
              <td>{site.roadFrontage}</td>
              <td>{wonPerM2(site.actual)}</td>
              <td>—</td>
            </tr>
            {site.comparables.map((c) => (
              <tr key={c.name}>
                <td>{c.name}</td>
                <td>{c.distanceM}m</td>
                <td>{c.zoning}</td>
                <td>{c.areaM2.toLocaleString("ko-KR")}㎡</td>
                <td>{c.road}</td>
                <td>{wonPerM2(c.price)}</td>
                <td>{c.similarity.toFixed(2)}</td>
              </tr>
            ))}
            <tr className="cmp--median">
              <td>유사필지 중앙값</td>
              <td colSpan={4} />
              <td>{wonPerM2(median)}</td>
              <td>—</td>
            </tr>
          </tbody>
        </table>
      </div>

      <p className="tab-note">
        비교 필지는 지도에도 함께 표시됩니다. 대상 필지가 중앙값보다{" "}
        <b>{signedPct(((site.actual - median) / median) * 100)}</b> 위치에 있습니다 —{" "}
        {"“왜 저평가라고 보는가”"}에 대한 가장 직접적인 근거입니다.
      </p>
    </div>
  );
}

/* ── 인트로 ─────────────────────────────────────────────────────────────── */

function IntroOverlay() {
  return (
    <div className="demo-over" role="group" aria-label="LAND AI가 답하는 두 질문">
      <div className="demo-over-in">
        <h1>
          LAND AI는 토지의 미래 가격을 예측하고,
          <br />
          현재 저평가된 토지를 알려줍니다.
        </h1>
        <div className="demo-two">
          <div>
            <span className="demo-num">1</span>
            <h2>저평가 · 고평가 분석</h2>
            <p>
              토지특성, 용도지역, 교통, 상권, 도시계획, 건축·도시분석에서 활용되는 공간구조
              지표(Space Syntax) 등을 바탕으로 토지가 어느정도 저평가, 혹은 고평가 되어있는지
              보여줍니다.
            </p>
          </div>
          <div>
            <span className="demo-num">2</span>
            <h2>미래 가치 · 유망입지 예측</h2>
            <p>
              과거에 실제로 가격이 상승한 토지의 특성과 변화 패턴을 학습한 머신러닝을
              기반으로 1·3·5년 후 공시지가 변화를 예측합니다.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
