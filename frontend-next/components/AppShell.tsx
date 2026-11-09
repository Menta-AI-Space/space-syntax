"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useLang } from "@/lib/i18n";
import { api, ApiClientError, type AnalyzeResponse, type DecideRequest, type DecideResponse, type DecisionGoal, type FranchiseBrand, type RadiusM, type SimulateResponse, type SiteScoreResponse } from "@/lib/api";
import { GoalPicker } from "@/components/agent/GoalPicker";
import { AgentChat, type AgentResultLocation } from "@/components/agent/AgentChat";
import { CandidateIntake } from "@/components/agent/CandidateIntake";
import { VerdictReport } from "@/components/agent/VerdictReport";
import { GoalIntake, type GoalIntakeValues } from "@/components/agent/GoalIntake";
import { AREA_INTAKE, DIAGNOSE_INTAKE, LEASE_INTAKE, PURCHASE_INTAKE } from "@/components/agent/intakeConfigs";
import { LeaseVerdictReport } from "@/components/agent/LeaseVerdictReport";
import { PurchaseVerdictReport } from "@/components/agent/PurchaseVerdictReport";
import { DiagnoseReport } from "@/components/agent/DiagnoseReport";
import { AreaSearchReport } from "@/components/agent/AreaSearchReport";
import { useAgentRun } from "@/components/agent/useAgentRun";
import { searchKoreanAddresses, reverseGeocodeLatLng, type AddressSearchItem } from "@/lib/address";
import { BusinessPicker, type UpjongSelection } from "@/components/BusinessPicker";
import { AdaptiveDashboard } from "@/components/AdaptiveDashboard";
import { FranchiseBrandCard } from "@/components/FranchiseBrandCard";
import { AreaInsightsSection, fmtManwon } from "@/components/AreaInsightsSection";
import type { AreaInsightsResponse } from "@/lib/api";
import { KakaoMap, MapLegend, type RoadClickInfo, type WatchlistMapMarker, type CandidateMapMarker } from "@/components/map/KakaoMap";
import { ReachSlider } from "@/components/map/ReachSlider";
import { StreetGradeMap } from "@/components/map/StreetGradeMap";
import { StreetFinder, type StreetFinderSpot } from "@/components/map/StreetFinder";
import { pickTopStreets } from "@/lib/topStreets";
import { SiteScorePanel } from "@/components/SiteScorePanel";
import { SimulationPanel } from "@/components/SimulationPanel";
import { DashboardPanel } from "@/components/dashboard/DashboardPanel";
import { WatchlistAddBar } from "@/components/WatchlistAddBar";
import { CandidateCompare, CANDIDATE_LABELS } from "@/components/CandidateCompare";
import { ProfitabilityPanel } from "@/components/profitability/ProfitabilityPanel";
import { profileIdForUpjong } from "@/lib/adaptive-profile";
import { loadWatchlistFromStorage, getSiteAnalysisWatchlist, type LastAnalyzedLocation, type WatchlistItem } from "@/lib/watchlist";
import { FloorplanAnalyzer } from "@/components/floorplan/FloorplanAnalyzer";
import { ParcelStructurePanel } from "@/components/parcels/ParcelStructurePanel";
import { PlannedSitePanel } from "@/components/parcels/PlannedSitePanel";
import { UndervaluedPanel } from "@/components/UndervaluedPanel";

interface PickedLocation {
  address: string;
  display: string;
  lat: number;
  lng: number;
}

// 2026-08-01: "새 분석"과 "입지 분석"을 하나로 합쳤다(docs/agent/GOALS.md 5번).
// "새 분석"이라는 이름이 무엇을 하는 탭인지 안 와닿는다는 지적이었고, 실제로
// 두 탭 모두 "이 자리가 어떤가"를 묻는 같은 일이었다 — 하나는 자연어로,
// 하나는 폼과 지도로. 이제 한 화면에서 위는 자연어 질의, 아래는 지도다.
type AppTab =
  | "analyze"
  | "streetMap"
  | "map"
  | "undervalued"
  | "profitability"
  | "simulation"
  | "floorplan"
  | "plannedSite"
  | "parcelStructure"
  | "watchlist";

// 2026-08-22: 상단에 "가는 곳"만 남긴다. 종전엔 탭이 다섯이었는데 그중 "입지
// 분석"은 눌러도 화면이 그대로였다 — 그게 곧 아래 질의창이라 문이 둘이었다.
// 기준은 **장소인가 질문인가**다. 구조 대비 저가·도면 분석은 둘러보고 작업하는
// 화면이라 탭에 두고, "이 자리 어때?"는 질문이라 질의창이 맡는다.
// 2026-10-22: 될 것만 남긴다(소유자 결정). 「지도 분석」은 이제 지도 위 질의창
// (LLM 분석)이고, 나머지는 「도면 분석」 하나 — 그 안에서 건물 평면도와
// 신도시 계획 도면(옛 「계획 택지」 탭)을 고른다.
// 상단에서 뺀 화면 — 폼으로 하던 지도 분석(map), 구조 대비 저가(「저평가」
// 결론을 내렸으므로, DECISIONS §10·§26), 필지 등급(맨 위 한 칸만 갈린다),
// 관심목록 — 은 패널을 지우지 않고 문만 닫았다. 다시 열 때 여기 한 줄을 넣는다.
// 2026-10-23: 도면을 다시 두 탭으로 나눈다(소유자 결정, 랩미팅 시연용). 10-22에
// 합친 이유는 「도면 분석」·「계획 택지」 이름이 같은 일처럼 보여서였다 — 이름에
// 무슨 도면인지(건축 평면도 / 신도시 계획도면)를 박아 그 문제를 푼다. 「도면만
// 넣으면 둘 다 된다」가 메뉴에서 바로 보여야 한다. ``short``는 좁은 화면용.
// 2026-10-26: 「지도 분석」은 이제 서울 길을 목 좋은 길 3단계로 칠한 지도
// (StreetGradeMap)이고, 지도 위 질의창은 「AI 입지분석」으로 이름을 바꿨다
// (소유자 결정 — 앱 출시용 지도 분석, 질의창은 랩미팅 시연용으로 유지).
// 2026-11-09: 도면 둘을 앞으로, 「AI 입지분석」은 맨 뒤로, 「지도 분석」(StreetGradeMap)은 문을 닫았다
// (소유자 결정). 화면 언어는 영어가 기본이고 `?lang=ko`면 한국어 — lib/i18n.tsx. 첫 탭이 곧 첫 화면이다.
const PRIMARY_TABS: { id: AppTab; label: string; short: string; labelEn: string; shortEn: string }[] = [
  { id: "floorplan", label: "건축 평면도 분석", short: "평면도", labelEn: "Floor Plan Analysis", shortEn: "Floor plan" },
  { id: "plannedSite", label: "신도시 계획도면 분석", short: "계획도면", labelEn: "New Town Plan Analysis", shortEn: "Town plan" },
  { id: "analyze", label: "AI 입지분석", short: "AI 입지", labelEn: "AI Site Analysis", shortEn: "AI site" },
];
const HOME_TAB: AppTab = PRIMARY_TABS[0].id;

type SheetSize = "peek" | "half" | "full";
const SHEET_ORDER: SheetSize[] = ["peek", "half", "full"];
// 창 폭 하한 — 이보다 좁으면 보고서 카드의 표가 깨진다.
const CHAT_MIN_W = 360;

export function AppShell() {
  const { lang, setLang, t } = useLang();
  const [activeTab, setActiveTab] = useState<AppTab>(HOME_TAB);
  const [floorplanHandoff, setFloorplanHandoff] = useState<{ file: File; id: number } | null>(null);
  const [address, setAddress] = useState("신촌역");
  const [pickedLocation, setPickedLocation] = useState<PickedLocation | null>(null);
  const [addressResults, setAddressResults] = useState<AddressSearchItem[]>([]);
  const [addressStatus, setAddressStatus] = useState("");
  const [radius, setRadius] = useState<RadiusM>(500);
  // 지도 색 지표와 길 색칠은 이제 화면에서 바꿀 수 없다 — 이 둘을 켜던
  // "길 구조로 보는 미래 가치" 패널을 첫 화면에서 뺐다(2026-08-24). state 로
  // 두면 setter 가 죽은 코드로 남으므로 상수로 고정한다. 다시 조절하게 할 때
  // useState 로 되돌린다.
  const metric = "choice";
  const roadLayer = true;
  const [upjong, setUpjong] = useState<UpjongSelection | null>(null);
  const [brand, setBrand] = useState("");
  // 브랜드 자동완성 — 입력 중일 때만(dirty) 드롭다운을 연다. 결과가 비면
  // (테이블 미적재로 API가 빈 배열을 주는 동안 포함) 드롭다운도 없다.
  const [brandResults, setBrandResults] = useState<FranchiseBrand[]>([]);
  const [brandDirty, setBrandDirty] = useState(false);
  // 마지막으로 "상권 분석하기"를 실행한 시점의 브랜드 스냅샷 — 잠식 분석
  // 요청에 쓰인다 (타이핑마다 대시보드 전체가 재요청되지 않도록 분리).
  const [analyzedBrand, setAnalyzedBrand] = useState("");
  const [analysisData, setAnalysisData] = useState<AnalyzeResponse | null>(null);
  const [siteScore, setSiteScore] = useState<SiteScoreResponse | null>(null);
  const [priorities, setPriorities] = useState<string[]>([]);
  // 우선순위별 강도(1.0~3.0). 기본 1.8 = 종전 부스트와 동일 — 슬라이더를 안
  // 만지면 결과가 어제와 같아야 한다.
  const [priorityBoosts, setPriorityBoosts] = useState<Record<string, number>>({});
  const [personalizing, setPersonalizing] = useState(false);
  const [closedOsmIds, setClosedOsmIds] = useState<number[]>([]);
  const [simulationResult, setSimulationResult] = useState<SimulateResponse | null>(null);
  const [siteLoading, setSiteLoading] = useState(false);
  const [siteError, setSiteError] = useState("");
  const [status, setStatus] = useState<{ tone: "idle" | "loading" | "success" | "error"; message: string }>({ tone: "idle", message: "" });
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [lastAnalyzedLocation, setLastAnalyzedLocation] = useState<LastAnalyzedLocation | null>(null);
  const [roadInfo, setRoadInfo] = useState<RoadClickInfo | null>(null);
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<string[]>([]);
  const [focusBoundsTargets, setFocusBoundsTargets] = useState<{ lat: number; lng: number }[] | null>(null);
  // 에이전트 대화가 진행 중인가 — 시작되면 목적 픽커·구분선을 접는다.
  // 분석이 떠 있는데 그 아래 "무엇을 결정하려고 하시나요?"가 계속 서 있으면
  // 화면이 두 제품처럼 보인다(사용자 피드백).
  // 새분석 첫 화면의 자연어 에이전트(AgentChat)가 분석한 위치 — 지도에 핀으로.
  const [agentPin, setAgentPin] = useState<AgentResultLocation | null>(null);
  // 2026-10-22: 질의창을 지도 위에 띄운다(C안). 휴대폰에서는 아래에서 올라오는
  // 창이고 높이가 세 단계다 — 입력칸만(peek) → 반(half) → 전체(full). 위는 늘
  // 지도라서, 답을 읽으면서 그 자리가 어디인지 같이 본다. 넓은 화면에서는
  // 오른쪽에 뜬 창이고 왼쪽 가장자리를 끌어 폭을 바꾼다(chatWidth).
  const [sheet, setSheet] = useState<SheetSize>("peek");
  const [chatWidth, setChatWidth] = useState(440);
  const sheetDragRef = useRef<{ y: number } | null>(null);
  // 2026-10-24: 첫 화면은 「주소 → 길 색칠 → 목 좋은 길 3곳」(StreetFinder)이고, AI
  // 질의창은 「AI에게 물어보기」를 눌렀을 때만 연다(소유자 결정). AI 답은 판단할
  // 것이 많고 깊은 분석까지 20초 넘게 걸려, 처음 보는 사람에게 먼저 보이는 것은
  // 엔진 숫자만으로 2초 안에 나오는 길 순위로 한다.
  const [chatOpen, setChatOpen] = useState(false);
  const [streetLoading, setStreetLoading] = useState(false);
  const [streetError, setStreetError] = useState("");
  // 같은 곳을 다시 보내도 같은 답인 실패(4xx — 「지금은 서울 지역만 분석할 수 있어요」)엔
  // 「다시 분석하기」를 내지 않는다 (2026-11-09 UX 자동 개선).
  const [streetRetryable, setStreetRetryable] = useState(true);
  const [streetPlace, setStreetPlace] = useState<string | null>(null);
  // 첫 화면에서 고른 장소를 상담 입력창에 잇는다(§9-2).
  const [chatSeed, setChatSeed] = useState<{ place: string; nonce: number; midChat?: boolean } | null>(null);
  const [streetSpots, setStreetSpots] = useState<StreetFinderSpot[]>([]);
  const streetRequestRef = useRef(0);
  const analysisRequestRef = useRef(0);
  const siteRequestRef = useRef(0);
  const personalFitRequestRef = useRef(0);
  const addressRequestRef = useRef(0);
  const mapClickRequestRef = useRef(0);
  const brandSearchSeqRef = useRef(0);

  // 브랜드 자동완성 검색 — BusinessPicker와 같은 debounce + 시퀀스 레이스 가드.
  useEffect(() => {
    const q = brand.trim();
    const seq = ++brandSearchSeqRef.current;
    if (!brandDirty || q.length < 1) {
      setBrandResults([]);
      return;
    }
    const timer = window.setTimeout(() => {
      api.franchiseBrands(q, 10)
        .then((res) => { if (seq === brandSearchSeqRef.current) setBrandResults(res.brands || []); })
        .catch(() => { if (seq === brandSearchSeqRef.current) setBrandResults([]); });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [brand, brandDirty]);

  // Ported from `loadWatchlistFromStorage` in frontend/js/watchlist.js, called
  // once on mount (client-only) so the initial SSR/hydration render matches
  // (both start from an empty list, then this effect fills in the real one).
  useEffect(() => {
    setWatchlist(loadWatchlistFromStorage());
  }, []);

  const statusClass = useMemo(() => status.tone === "idle" ? "" : `status-banner status-banner--${status.tone}`, [status.tone]);
  const industryProfileId = useMemo(() => profileIdForUpjong(upjong), [upjong]);
  const simulationCenter = pickedLocation || analysisData?.center;
  const profitabilityLocation = useMemo(() => {
    if (pickedLocation) return { lat: pickedLocation.lat, lng: pickedLocation.lng, label: pickedLocation.display };
    if (siteScore) return { lat: siteScore.lat, lng: siteScore.lng, label: lastAnalyzedLocation?.address };
    if (analysisData?.center) return { lat: analysisData.center.lat, lng: analysisData.center.lng, label: undefined };
    return null;
  }, [pickedLocation, siteScore, analysisData, lastAnalyzedLocation]);

  // Ported from `syncWatchlistMapMarkers`/`syncCandidateMarkers` in
  // frontend/js/map.js + frontend/js/candidate_compare.js: the 입지 분석 tab
  // shows plain star markers for saved watchlist items; the 관심목록·후보지
  // 비교 tab shows the same locations with selected candidates badged A-E and
  // everything else dimmed. Both tabs share the single KakaoMap instance
  // (see the UI-reorganization note in MIGRATION_CHECKLIST.md), so which set
  // renders is keyed off `activeTab` instead of reparenting the map DOM node.
  const siteAnalysisWatchlist = useMemo(() => getSiteAnalysisWatchlist(watchlist), [watchlist]);
  const watchlistMarkers: WatchlistMapMarker[] = useMemo(
    () => (activeTab === "analyze" ? siteAnalysisWatchlist.map((w) => ({ id: w.id, lat: w.lat, lng: w.lng, address: w.address })) : []),
    [activeTab, siteAnalysisWatchlist]
  );
  const candidateMarkers: CandidateMapMarker[] = useMemo(
    () => {
      if (activeTab === "watchlist") {
        return siteAnalysisWatchlist.map((w) => {
          const selIdx = selectedCandidateIds.indexOf(w.id);
          const selected = selIdx >= 0;
          return { id: w.id, lat: w.lat, lng: w.lng, address: w.address, label: selected ? CANDIDATE_LABELS[selIdx] : "★", dimmed: !selected };
        });
      }
      // 첫 화면: 질의창이 열려 있으면 질의가 분석한 위치에 별 핀, 아니면 목 좋은 길
      // ①②③.
      if (activeTab === "analyze" && chatOpen && agentPin) {
        return [{ id: "agent-pin", lat: agentPin.lat, lng: agentPin.lng, address: agentPin.address, label: "★" }];
      }
      if (activeTab === "analyze" && !chatOpen) {
        return streetSpots.map((s) => ({ id: `street-${s.rank}`, lat: s.lat, lng: s.lng, address: s.address ?? "", label: String(s.rank) }));
      }
      return [];
    },
    [activeTab, siteAnalysisWatchlist, selectedCandidateIds, agentPin, chatOpen, streetSpots]
  );

  // --- 결정 에이전트 탭 (첫 화면) ---
  // 목적 선택 → 입력 → 판정문의 3단계를 목적 5개가 그대로 공유한다. 화면은
  // (단계, 목적) 두 값으로만 결정되고, 모든 입력·결과 화면은 마운트를 유지한
  // 채 CSS로만 숨긴다 — 「조건 수정」으로 돌아오면 적어둔 값이 남아 있다.
  // 기존 전문가 탭들의 상태와는 공유하는 것이 없다.
  const [decideStep, setDecideStep] = useState<"goal" | "intake" | "verdict">("goal");
  const [decideGoal, setDecideGoal] = useState<DecisionGoal>("compare_candidates");
  const [decideResult, setDecideResult] = useState<DecideResponse | null>(null);
  const [decideLoading, setDecideLoading] = useState(false);
  const [decideError, setDecideError] = useState("");
  // 마지막 요청 본문 — 실패했을 때 같은 조건으로 재시도하기 위해 보관한다.
  const [decideRequestBody, setDecideRequestBody] = useState<DecideRequest | null>(null);
  const decideRequestRef = useRef(0);

  // 첫 화면 검색 — 주변 길을 칠하고 목 좋은 길 3곳을 고른다. 주소는 뒤에서 채운다
  // (역지오코딩이 늦어도 번호와 순위는 먼저 보인다).
  async function runStreetFinder(item: AddressSearchItem) {
    const requestId = ++streetRequestRef.current;
    setStreetLoading(true);
    setStreetError("");
    setStreetRetryable(true);
    setStreetSpots([]);
    setStreetPlace(item.placeName || item.address);
    try {
      const result = await api.analyze({ address: item.geocodeAddress, lat: item.lat, lng: item.lng, radius_m: 500, mode: "walk" });
      if (requestId !== streetRequestRef.current) return;
      setAnalysisData(result);
      setClosedOsmIds([]);
      setSimulationResult(null);
      const picks = pickTopStreets(result);
      setStreetSpots(picks.map((p) => ({ ...p, address: null })));
      // 반경 원이 다 보이게 지도를 맞춘다 — 동서남북 끝 네 점(500m ≈ 위도 0.0045°).
      const { lat, lng } = result.center;
      const dLat = result.radius_m / 111_320;
      const dLng = dLat / Math.cos((lat * Math.PI) / 180);
      setFocusBoundsTargets([
        { lat: lat + dLat, lng }, { lat: lat - dLat, lng },
        { lat, lng: lng + dLng }, { lat, lng: lng - dLng },
      ]);
      if (!picks.length) {
        setStreetRetryable(false);
        setStreetError("이 주변에서 순위를 매길 만한 길을 찾지 못했어요.");
      }
      picks.forEach((p) => {
        void reverseGeocodeLatLng(p.lat, p.lng).then((addr) => {
          if (requestId !== streetRequestRef.current) return;
          setStreetSpots((prev) => prev.map((s) => (s.rank === p.rank ? { ...s, address: addr || `${p.kind} ${p.rank}` } : s)));
        });
      });
    } catch (err) {
      if (requestId === streetRequestRef.current) {
        const status = err instanceof ApiClientError ? err.status : 0;
        setStreetRetryable(!(status >= 400 && status < 500 && status !== 408 && status !== 429));
        setStreetError(err instanceof Error ? err.message : "길 분석에 실패했어요.");
      }
    } finally {
      if (requestId === streetRequestRef.current) setStreetLoading(false);
    }
  }

  // 창은 사용자가 내리기 전엔 저절로 내려가지 않는다 — 올리기만 한다.
  function raiseSheet(to: SheetSize) {
    setSheet((cur) => (SHEET_ORDER.indexOf(cur) < SHEET_ORDER.indexOf(to) ? to : cur));
  }

  // 단계별 입력·판정 화면은 길어서 반쯤 올린 창에서는 읽히지 않는다.
  useEffect(() => {
    if (decideStep !== "goal") setSheet("full");
  }, [decideStep]);

  // 손잡이: 톡 누르면 한 단계 올리고(맨 위면 반으로), 끌면 끈 방향으로 한 단계.
  function onGrabUp(e: React.PointerEvent) {
    const start = sheetDragRef.current;
    sheetDragRef.current = null;
    if (!start) return;
    const dy = e.clientY - start.y;
    setSheet((cur) => {
      const i = SHEET_ORDER.indexOf(cur);
      if (Math.abs(dy) < 8) return cur === "full" ? "half" : SHEET_ORDER[i + 1];
      if (dy < 0) return SHEET_ORDER[Math.min(i + 1, 2)];
      return SHEET_ORDER[Math.max(i - 1, 0)];
    });
  }

  function onResizeMove(e: React.PointerEvent) {
    if (!(e.buttons & 1)) return;
    const max = Math.min(960, window.innerWidth * 0.7);
    setChatWidth(Math.round(Math.min(max, Math.max(CHAT_MIN_W, window.innerWidth - e.clientX - 16))));
  }

  async function runDecide(body: DecideRequest) {
    const requestId = ++decideRequestRef.current;
    setDecideRequestBody(body);
    setDecideStep("verdict");
    setDecideLoading(true);
    setDecideError("");
    setDecideResult(null);
    try {
      const result = await api.decide(body);
      if (requestId !== decideRequestRef.current) return;
      setDecideResult(result);
    } catch (err) {
      if (requestId !== decideRequestRef.current) return;
      setDecideError(err instanceof Error ? err.message : "비교 결과를 가져오지 못했습니다.");
    } finally {
      if (requestId === decideRequestRef.current) setDecideLoading(false);
    }
  }

  // 나머지 네 목적은 요청 수명이 모두 같아서 훅 하나로 처리한다
  // (마지막 요청만 반영 + 같은 조건으로 재시도).
  const leaseRun = useAgentRun(api.leaseVerdict, "계약 판정을 가져오지 못했습니다.");
  const purchaseRun = useAgentRun(api.purchaseVerdict, "매입 판정을 가져오지 못했습니다.");
  const diagnoseRun = useAgentRun(api.diagnose, "진단 결과를 가져오지 못했습니다.");
  const areaRun = useAgentRun(api.areaSearch, "추천 자리를 가져오지 못했습니다.");

  function upjongCodes(values: GoalIntakeValues) {
    return {
      inds_lcls_cd: values.upjong?.inds_lcls_cd ?? null,
      inds_mcls_cd: values.upjong?.inds_mcls_cd ?? null,
      inds_scls_cd: values.upjong?.inds_scls_cd ?? null,
    };
  }

  function submitLease(values: GoalIntakeValues) {
    if (values.lat === null || values.lng === null) return;
    setDecideStep("verdict");
    leaseRun.run({
      lat: values.lat,
      lng: values.lng,
      ...upjongCodes(values),
      radius_m: 500,
      monthly_rent_man: values.numbers.monthly_rent_man ?? 0,
      maintenance_man: values.numbers.maintenance_man,
      deposit_man: values.numbers.deposit_man,
      key_money_man: values.numbers.key_money_man,
      area_pyeong: values.numbers.area_pyeong,
      floor: values.numbers.floor,
    });
  }

  function submitPurchase(values: GoalIntakeValues) {
    if (values.lat === null || values.lng === null) return;
    const principal = values.numbers.loan_principal_manwon;
    setDecideStep("verdict");
    purchaseRun.run({
      lat: values.lat,
      lng: values.lng,
      ...upjongCodes(values),
      radius_m: 500,
      price_manwon: values.numbers.price_manwon ?? 0,
      area_sqm: values.numbers.area_sqm ?? 0,
      floor: values.numbers.floor,
      // 대출을 안 받으면 통째로 빼서 보낸다 (0원 대출로 계산되지 않도록).
      loan: principal !== null && principal > 0
        ? {
            principal_manwon: principal,
            annual_rate: values.numbers.loan_annual_rate,
            term_years: values.numbers.loan_term_years,
            interest_only: Boolean(values.flags.loan_interest_only),
          }
        : null,
    });
  }

  function submitDiagnose(values: GoalIntakeValues) {
    if (values.lat === null || values.lng === null) return;
    setDecideStep("verdict");
    diagnoseRun.run({
      lat: values.lat,
      lng: values.lng,
      ...upjongCodes(values),
      radius_m: 500,
      current_monthly_sales_man: values.numbers.current_monthly_sales_man ?? 0,
      monthly_rent_man: values.numbers.monthly_rent_man,
    });
  }

  function submitAreaSearch(values: GoalIntakeValues) {
    const query = values.query || values.label;
    if (!query) return;
    setDecideStep("verdict");
    areaRun.run({
      region_query: query,
      // 자동완성에서 정확히 고른 경우에만 좌표가 붙는다 (동네 이름만 적어도 된다).
      lat: values.lat,
      lng: values.lng,
      ...upjongCodes(values),
      budget_max_rent_man: values.numbers.budget_max_rent_man,
      priority: values.priority,
    });
  }

  async function runSiteScoreAt(lat: number, lng: number, addressForRequest?: string) {
    const requestId = ++siteRequestRef.current;
    const requestBrand = brand.trim();
    setSiteLoading(true);
    setSiteError("");
    try {
      const result = await api.siteScore({
        lat,
        lng,
        address: addressForRequest || address,
        radius_m: radius,
        inds_lcls_cd: upjong?.inds_lcls_cd || null,
        inds_mcls_cd: upjong?.inds_mcls_cd || null,
        inds_scls_cd: upjong?.inds_scls_cd || null,
        business_type: upjong?.business_type || "general",
        brand: requestBrand || null,
        priorities,
        include_ai_explanation: false,
      });
      if (requestId === siteRequestRef.current) {
        setSiteScore(result);
        setAnalyzedBrand(requestBrand);
        setLastAnalyzedLocation({ address: addressForRequest || address, lat, lng });
      }
    } catch (err) {
      if (requestId === siteRequestRef.current) {
        setSiteError(err instanceof Error ? err.message : "입지 진단 요청에 실패했습니다.");
      }
    } finally {
      if (requestId === siteRequestRef.current) setSiteLoading(false);
    }
  }

  async function handlePrioritiesChange(next: string[], boosts?: Record<string, number>) {
    setPriorities(next);
    const nextBoosts = boosts ?? priorityBoosts;
    if (boosts) setPriorityBoosts(boosts);
    const current = siteScore;
    if (!current) return;
    const requestId = ++personalFitRequestRef.current;
    setPersonalizing(true);
    try {
      const fit = await api.personalFit({
        base_score: current.total_score,
        subscores: current.subscores,
        base_weights: current.personalization_weights || {},
        priorities: next,
        priority_boosts: nextBoosts,
        extra_factor_scores: current.personalization_factors || {},
      });
      if (requestId === personalFitRequestRef.current) {
        setSiteScore((value) => value ? { ...value, personal_fit: next.length ? fit : null } : value);
      }
    } catch (err) {
      if (requestId === personalFitRequestRef.current) {
        setSiteError(err instanceof Error ? err.message : "맞춤 점수 계산에 실패했습니다.");
      }
    } finally {
      if (requestId === personalFitRequestRef.current) setPersonalizing(false);
    }
  }

  // 왼쪽 패널 하나가 입력(form)과 결과(results)를 오간다 — 분석이 실행되면
  // 결과로 전환되고, 「조건 수정」으로 되돌아온다. 오른쪽 결과 패널은 없다.
  const [analyzeView, setAnalyzeView] = useState<"form" | "results">("form");
  // 결과는 긴 스크롤 대신 번호 섹션 탭으로 나눠 보여준다 (bizmap식).
  // 모든 섹션은 마운트를 유지하고 CSS로만 숨긴다 — fetch 상태 보존.
  type ResultsSection = "score" | "industry" | "insights" | "dashboard";
  const [resultsSection, setResultsSection] = useState<ResultsSection>("score");
  const RESULTS_SECTIONS: Array<[ResultsSection, string]> = [
    ["score", "1. 입지 진단"],
    ["industry", "2. 업종 분석"],
    ["insights", "3. 시세·유동인구"],
    ["dashboard", "4. 확장 분석"],
  ];

  // 동네 시세·유동인구 — AppShell이 한 번만 불러 상단 요약 타일과
  // AreaInsightsSection 양쪽에 공유한다.
  const [areaInsights, setAreaInsights] = useState<AreaInsightsResponse | null>(null);
  const [areaInsightsLoading, setAreaInsightsLoading] = useState(false);
  const areaInsightsReqRef = useRef(0);

  useEffect(() => {
    const lat = siteScore?.lat;
    const lng = siteScore?.lng;
    if (lat === undefined || lng === undefined) {
      setAreaInsights(null);
      return;
    }
    const requestId = ++areaInsightsReqRef.current;
    setAreaInsightsLoading(true);
    api.areaInsights({ lat, lng, radius_m: radius })
      .then((d) => { if (requestId === areaInsightsReqRef.current) setAreaInsights(d); })
      .catch(() => { if (requestId === areaInsightsReqRef.current) setAreaInsights(null); })
      .finally(() => { if (requestId === areaInsightsReqRef.current) setAreaInsightsLoading(false); });
  }, [siteScore?.lat, siteScore?.lng, radius]);

  // 요약 타일용 파생값 — 핵심 고객층(최다 연령대 + 우세 성별)·시세·피크.
  const insightsBreakdown = areaInsights?.footfall_breakdown ?? null;
  const summaryTopAge = insightsBreakdown?.ages?.length
    ? [...insightsBreakdown.ages].sort((a, b) => b.value - a.value)[0].label
    : null;
  const summaryCoreCustomer = summaryTopAge
    ? `${summaryTopAge} ${(insightsBreakdown!.female_pct ?? 0) >= (insightsBreakdown!.male_pct ?? 0) ? "여성" : "남성"}`
    : null;
  const summaryAptPyeong = areaInsights?.apartment?.median_per_pyeong_manwon ?? null;
  const summaryPeakHour = areaInsights?.footfall?.weekday_peak_hour ?? null;

  // 최근 검색 (NICE bizmap식) — 주소 입력에 포커스하면 드롭다운으로 보여주고,
  // 항목 선택/전체삭제를 지원한다. localStorage에 최대 5개 유지.
  type RecentSearch = { display: string; address: string; lat?: number; lng?: number };
  const RECENT_KEY = "ss_recent_searches_v1";
  const [recentSearches, setRecentSearches] = useState<RecentSearch[]>([]);
  const [addressFocused, setAddressFocused] = useState(false);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(RECENT_KEY);
      // JSON.parse가 성공해도 배열이라는 보장은 없다 — 저장값이 객체나 숫자로
      // 손상돼 있으면 뒤에서 .map()이 터진다. 관심목록 로더는 이미 이 검사를
      // 하고 있어서 여기만 빠져 있었다.
      const parsed = raw ? JSON.parse(raw) : null;
      if (Array.isArray(parsed)) setRecentSearches(parsed);
    } catch { /* 손상된 저장값은 무시 */ }
  }, []);

  function persistRecent(next: RecentSearch[]) {
    setRecentSearches(next);
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(next)); } catch { /* quota 등 무시 */ }
  }

  function saveRecentSearch(item: RecentSearch) {
    persistRecent([item, ...recentSearches.filter((r) => r.display !== item.display)].slice(0, 5));
  }

  function removeRecentSearch(display: string) {
    persistRecent(recentSearches.filter((r) => r.display !== display));
  }

  function pickRecentSearch(item: RecentSearch) {
    setAddress(item.display);
    if (item.lat !== undefined && item.lng !== undefined) {
      setPickedLocation({ address: item.address, display: item.display, lat: item.lat, lng: item.lng });
    }
    setAddressResults([]);
    setAddressFocused(false);
  }

  async function runAnalysis() {
    const trimmed = address.trim();
    if (!trimmed) { setStatus({ tone: "error", message: "분석할 주소를 입력하세요." }); return; }
    const requestId = ++analysisRequestRef.current;
    setStatus({ tone: "loading", message: "도로망을 불러와 입지를 분석하고 있어요…" });
    setSiteScore(null);
    setSiteError("");
    try {
      const locationMatches = pickedLocation && (trimmed === pickedLocation.display || trimmed.includes(pickedLocation.address) || pickedLocation.display.includes(trimmed));
      const result = await api.analyze(locationMatches ? { address: pickedLocation.address, lat: pickedLocation.lat, lng: pickedLocation.lng, radius_m: radius, mode: "walk" } : { address: trimmed, radius_m: radius, mode: "walk" });
      if (requestId !== analysisRequestRef.current) return;
      setAnalysisData(result);
      setClosedOsmIds([]);
      setSimulationResult(null);
      setStatus({ tone: "success", message: `${result.segment_count.toLocaleString()}개 도로 구간을 분석했습니다.` });
      setAnalyzeView("results");
      await runSiteScoreAt(result.center.lat, result.center.lng, locationMatches ? pickedLocation.address : trimmed);
    } catch (err) {
      if (requestId === analysisRequestRef.current) {
        setStatus({ tone: "error", message: err instanceof Error ? err.message : "분석 요청에 실패했습니다." });
      }
    }
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    await runAnalysis();
  }

  async function handleAddressChange(value: string) {
    const requestId = ++addressRequestRef.current;
    setAddress(value);
    setPickedLocation(null);
    setClosedOsmIds([]);
    setSimulationResult(null);
    const q = value.trim();
    if (q.length < 2) { setAddressResults([]); return; }
    try {
      setAddressStatus("");
      const results = await searchKoreanAddresses(q, 8);
      if (requestId === addressRequestRef.current) setAddressResults(results);
    } catch (err) {
      if (requestId === addressRequestRef.current) {
        setAddressResults([]);
        setAddressStatus(err instanceof Error ? err.message : "주소 검색 실패");
      }
    }
  }

  function pickAddress(item: AddressSearchItem) {
    addressRequestRef.current += 1;
    setPickedLocation({ address: item.geocodeAddress, display: item.address, lat: item.lat, lng: item.lng });
    setAddress(item.address);
    setAddressResults([]);
    saveRecentSearch({ display: item.address, address: item.geocodeAddress, lat: item.lat, lng: item.lng });
  }

  // Ported from `handleMapPositionClick` in frontend/js/map.js +
  // `updateMapClickInfo`/`reverseGeocodeLatLng` in frontend/js/api.js:
  // clicking an empty spot on the map picks that lat/lng (the marker itself
  // is placed by KakaoMap's own click handler), shows a raw-coordinate label
  // immediately, then swaps in the reverse-geocoded address once resolved,
  // and triggers the same site-score flow the address form uses.
  // 2026-11-06: AI 입지분석 탭에서 지도를 누르면 그 자리를 고른 것으로 본다(소유자 지적 —
  // 주소를 다 치기 어려우니 지도에서 골라 물을 수 있어야 한다). 종전엔 지도 분석 탭의
  // 분석을 숨은 사이드바로 돌려서 화면에 아무 일도 안 일어났다.
  // - 상담이 닫혀 있으면: 검색으로 고른 것과 똑같이 「목 좋은 길 3곳」을 찾는다.
  // - 상담이 열려 있으면: 그 자리에 별표를 꽂고, 입력창이 비어 있으면 그 주소로 질문 틀을 채운다.
  async function pickPointOnAiTab(lat: number, lng: number) {
    const fallback = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
    let label = fallback;
    try {
      label = (await reverseGeocodeLatLng(lat, lng)) || fallback;
    } catch {
      /* 주소를 못 찾으면 좌표로 묻는다 — 상담은 좌표도 받는다. */
    }
    if (chatOpen) {
      setAgentPin({ lat, lng, address: label });
      setChatSeed((cur) => ({ place: label, nonce: (cur?.nonce ?? 0) + 1, midChat: true }));
      return;
    }
    void runStreetFinder({ address: label, geocodeAddress: label, placeName: label, lat, lng, source: "address" });
  }

  async function handleMapClick(lat: number, lng: number) {
    if (activeTab === "analyze") {
      void pickPointOnAiTab(lat, lng);
      return;
    }
    const requestId = ++mapClickRequestRef.current;
    const fallback = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
    setPickedLocation({ address: fallback, display: `클릭 위치 · 주소 확인 중…`, lat, lng });
    setAnalyzeView("results");
    setRoadInfo(null);
    setClosedOsmIds([]);
    setSimulationResult(null);
    let resolvedAddress = fallback;
    try {
      const geocoded = await reverseGeocodeLatLng(lat, lng);
      if (requestId !== mapClickRequestRef.current) return;
      resolvedAddress = geocoded || fallback;
      setPickedLocation({ address: resolvedAddress, display: resolvedAddress, lat, lng });
    } catch {
      if (requestId !== mapClickRequestRef.current) return;
      setPickedLocation({ address: fallback, display: fallback, lat, lng });
    }
    await runSiteScoreAt(lat, lng, resolvedAddress);
  }

  // Ported from `renderSegments`'s polyline click handler in
  // frontend/js/map.js: clicking an analyzed road segment shows its info
  // (non-blocking here, instead of Vanilla's `alert(...)`).
  function handleRoadClick(info: RoadClickInfo) {
    setRoadInfo(info);
  }

  function handleRadiusChange(nextRadius: RadiusM) {
    setRadius(nextRadius);
    setSimulationResult(null);
  }

  return (
    <div className="app-shell">
      {/* 상단 바 하나 — 로고와 탭이 같은 줄 (별도 브랜드 헤더 없음). */}
      <header className="app-topbar">
        {/* 로고가 홈이다 — 질의창으로 돌아오는 길. 종전엔 "입지 분석" 탭이 그
            역할이었는데, 탭 이름으로는 그게 홈인지 알 수 없었다. */}
        <button
          type="button"
          className="app-home"
          onClick={() => setActiveTab(HOME_TAB)}
          aria-label={t("처음 화면으로", "Home")}
          aria-current={activeTab === HOME_TAB ? "page" : undefined}
        >
          {/* 건물 마크. 종전엔 "S"(Space Syntax) 글자였는데 제품명에서 그 이름을
              뺐으므로 글자도 함께 뺀다 — 남겨 두면 어디서 온 S인지 알 수 없다. */}
          <span className="app-header-logo" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 21V7.5L11 4v17" />
              <path d="M11 10.5h6.5a1.5 1.5 0 0 1 1.5 1.5V21" />
              <path d="M2.5 21h19" />
              <path d="M7 10.5v0M7 14v0M7 17.5v0M14.5 14v0M14.5 17.5v0" />
            </svg>
          </span>
          {/* 2026-10-24 제품명 LANDI → 「목좋은곳」(소유자 결정). 장사·부동산에서 누구나 쓰는
              「목이 좋다」를 그대로 이름으로 — 화면이 통합도를 이미 「목」이라 부른다. */}
          <span className="app-home-name">목좋은곳</span>
        </button>
        <nav className="app-tabs" role="tablist" aria-label={t("분석 모드", "Analysis modes")}>
          {PRIMARY_TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              id={`app-tab-${tab.id}`}
              className={`app-tab${activeTab === tab.id ? " app-tab--active" : ""}`}
              aria-selected={activeTab === tab.id}
              aria-controls={`tab-panel-${tab.id}`}
              // 640px 이하에선 .app-tab-full이 숨고 짧은 이름은 aria-hidden이라 탭 이름이 비어
              // 화면 낭독기가 「탭」만 읽었다 (2026-11-08 UX 자동 개선) — 이름을 버튼에 박는다.
              aria-label={t(tab.label, tab.labelEn)}
              onClick={() => setActiveTab(tab.id)}
            >
              <span className="app-tab-full">{t(tab.label, tab.labelEn)}</span>
              <span className="app-tab-short" aria-hidden="true">{t(tab.short, tab.shortEn)}</span>
            </button>
          ))}
        </nav>
        <div className="app-lang" role="group" aria-label={t("화면 언어", "Language")}>
          {(["en", "ko"] as const).map((code) => (
            <button
              key={code}
              type="button"
              className={`app-lang__btn${lang === code ? " app-lang__btn--on" : ""}`}
              aria-pressed={lang === code}
              lang={code}
              aria-label={code === "en" ? "English" : "한국어"}
              onClick={() => setLang(code)}
            >
              {code === "en" ? "EN" : "KO"}
            </button>
          ))}
        </div>
      </header>

      {/* Tab panels stay mounted (hidden via CSS) rather than unmounting on
          switch, so the Kakao map instance and all fetched analysis/dashboard
          state survive tab changes instead of re-fetching from scratch. */}
      {/* 결정 에이전트 — 기본 진입 탭. 3단계 모두 마운트를 유지하고 CSS로만
          숨겨서(다른 탭·뷰와 같은 패턴) 「조건 수정」으로 돌아왔을 때 입력한
          후보지가 그대로 남아 있게 한다. */}
      {/* 2026-10-22: 질의창과 지도 분석 탭이 지도 하나를 같이 쓴다. 종전엔 지도가
          지도 분석 탭 안에만 있어서, 질의창이 찾은 자리를 보려면 탭을 옮겨야
          했다. 이제 지도가 바닥에 깔리고 질의창(.map-chat)이나 분석 폼
          (.app-sidebar--floating)이 그 위에 뜬다. */}
      <div
        className={`app-mapframe app-mapframe--${activeTab === "map" ? "map" : chatOpen ? `chat app-mapframe--sheet-${sheet}` : `finder${streetSpots.length || streetLoading ? " app-mapframe--finder-results" : ""}`}${activeTab === "analyze" || activeTab === "map" ? "" : " hidden"}`}
        style={{ "--chat-w": `${chatWidth}px` } as React.CSSProperties}
      >
      {activeTab === "analyze" && !chatOpen && (
        <StreetFinder
          loading={streetLoading}
          error={streetError}
          retryable={streetRetryable}
          placeLabel={streetPlace}
          segmentCount={analysisData?.segment_count ?? null}
          center={streetSpots.length ? analysisData?.center ?? null : null}
          spots={streetSpots}
          onPick={(item) => { void runStreetFinder(item); }}
          onFocusSpot={(s) => setFocusBoundsTargets([{ lat: s.lat, lng: s.lng }])}
          onOpenChat={() => {
            setChatOpen(true);
            setSheet("half");
            if (streetPlace) setChatSeed((cur) => ({ place: streetPlace, nonce: (cur?.nonce ?? 0) + 1 }));
          }}
        />
      )}
      <div
        id="tab-panel-analyze"
        role="tabpanel"
        aria-labelledby="app-tab-analyze"
        className={`map-chat map-chat--${sheet}${activeTab === "analyze" && chatOpen ? "" : " hidden"}`}
        onFocusCapture={(e) => { if (e.target instanceof HTMLTextAreaElement) raiseSheet("half"); }}
      >
        {/* 질의창을 닫고 첫 화면(길 순위)으로 — 대화는 마운트를 유지해 그대로 남는다. */}
        <button type="button" className="map-chat__close" onClick={() => setChatOpen(false)}>
          ← 지도로
        </button>
        {/* 넓은 화면: 왼쪽 가장자리를 끌어 폭을 바꾼다. */}
        <div
          className="map-chat__resize"
          aria-hidden="true"
          onPointerDown={(e) => e.currentTarget.setPointerCapture(e.pointerId)}
          onPointerMove={onResizeMove}
        />
        {/* 휴대폰: 손잡이를 누르거나 끌어 창 높이를 바꾼다. */}
        <button
          type="button"
          className="map-chat__grab"
          aria-label={sheet === "full" ? "창 내리기" : "창 올리기"}
          onPointerDown={(e) => { sheetDragRef.current = { y: e.clientY }; e.currentTarget.setPointerCapture(e.pointerId); }}
          onPointerUp={onGrabUp}
        >
          <span aria-hidden="true" />
        </button>
        <div className="tab-page tab-page--decide">
          <div className={`decide-stage decide-stage--chat${decideStep === "goal" ? "" : " hidden"}`}>
            <AgentChat
              seed={chatSeed}
              /* 2026-11-06: 질문을 보내면 창을 끝까지 올린다. 반 창(55%)은 머리·입력창을
                 빼면 답이 두세 줄만 보였다(사용자 테스트 §9-2). 지도는 손잡이를 내리거나
                 답 안의 지도로 본다. */
              onAsk={() => raiseSheet("full")}
              onResult={(loc) => {
                setAgentPin(loc);
                if (loc) raiseSheet("full");
                setFocusBoundsTargets(loc ? [{ lat: loc.lat, lng: loc.lng }] : null);
              }}
              /* 목적 카드 다섯 장은 늘 접어 둔다. 펼쳐 두면 질의창과 나란히 서서
                 "어느 쪽으로 시작해야 하나"를 사용자가 먼저 정해야 했다 — 정답이
                 없는 질문이다. 단계별로 하고 싶은 사람만 연다.
                 질의창 안(빈 화면 전용)에 넣는다 — 밖에 두면 대화가 시작된 뒤
                 하단 고정 입력창 아래에 남아 답변이 그 밑으로 내려간 것처럼 보인다. */
              emptyExtra={
                /* 펼치면 창을 끝까지 올린다 — 반 창에선 카드 다섯 장이 아래로 밀려 첫 장 머리만
                   보이고 입력창까지 가려졌다(390, 2026-11-07 UX 자동 개선). */
                <details
                  className="decide-goal-collapse"
                  onToggle={(e) => {
                    if (e.currentTarget.open) raiseSheet("full");
                  }}
                >
                  <summary>단계별로 하나씩 정하고 싶다면 여기를 여세요</summary>
                  <GoalPicker onPick={(goal) => { setDecideGoal(goal); setDecideStep("intake"); }} />
                </details>
              }
            />
          </div>

          {/* 입력 — 목적별 화면 다섯 개가 모두 마운트된 채로 하나만 보인다. */}
          <div className={`decide-stage decide-stage--scroll${decideStep === "intake" ? "" : " hidden"}`}>
            <div className={decideGoal === "compare_candidates" ? "" : "hidden"}>
              <CandidateIntake onBack={() => setDecideStep("goal")} onSubmit={(body) => { void runDecide(body); }} />
            </div>
            <div className={decideGoal === "evaluate_listing" ? "" : "hidden"}>
              <GoalIntake config={LEASE_INTAKE} onBack={() => setDecideStep("goal")} onSubmit={submitLease} />
            </div>
            <div className={decideGoal === "evaluate_purchase" ? "" : "hidden"}>
              <GoalIntake config={PURCHASE_INTAKE} onBack={() => setDecideStep("goal")} onSubmit={submitPurchase} />
            </div>
            <div className={decideGoal === "diagnose_store" ? "" : "hidden"}>
              <GoalIntake config={DIAGNOSE_INTAKE} onBack={() => setDecideStep("goal")} onSubmit={submitDiagnose} />
            </div>
            <div className={decideGoal === "find_site" ? "" : "hidden"}>
              <GoalIntake config={AREA_INTAKE} onBack={() => setDecideStep("goal")} onSubmit={submitAreaSearch} />
            </div>
          </div>

          {/* 결과 — 「조건 수정」은 같은 목적의 입력 화면으로 되돌아간다. */}
          <div className={`decide-stage decide-stage--scroll${decideStep === "verdict" ? "" : " hidden"}`}>
            <div className={decideGoal === "compare_candidates" ? "" : "hidden"}>
              <VerdictReport
                loading={decideLoading}
                error={decideError}
                result={decideResult}
                onRetry={() => { if (decideRequestBody) void runDecide(decideRequestBody); }}
                onRestart={() => setDecideStep("intake")}
              />
            </div>
            <div className={decideGoal === "evaluate_listing" ? "" : "hidden"}>
              <LeaseVerdictReport
                loading={leaseRun.loading}
                error={leaseRun.error}
                result={leaseRun.result}
                request={leaseRun.request}
                onRetry={leaseRun.retry}
                onRestart={() => setDecideStep("intake")}
              />
            </div>
            <div className={decideGoal === "evaluate_purchase" ? "" : "hidden"}>
              <PurchaseVerdictReport
                loading={purchaseRun.loading}
                error={purchaseRun.error}
                result={purchaseRun.result}
                request={purchaseRun.request}
                onRetry={purchaseRun.retry}
                onRestart={() => setDecideStep("intake")}
              />
            </div>
            <div className={decideGoal === "diagnose_store" ? "" : "hidden"}>
              <DiagnoseReport
                loading={diagnoseRun.loading}
                error={diagnoseRun.error}
                result={diagnoseRun.result}
                request={diagnoseRun.request}
                onRetry={diagnoseRun.retry}
                onRestart={() => setDecideStep("intake")}
              />
            </div>
            <div className={decideGoal === "find_site" ? "" : "hidden"}>
              <AreaSearchReport
                loading={areaRun.loading}
                error={areaRun.error}
                result={areaRun.result}
                onRetry={areaRun.retry}
                onRestart={() => setDecideStep("intake")}
              />
            </div>
          </div>
        </div>
      </div>

          {/* 지도 분석 탭 — 왼쪽 패널 하나가 입력(form)과 결과(results)를 전환.
              두 뷰 모두 마운트를 유지하고 CSS로만 숨겨서 대시보드 fetch·입력
              상태가 전환 때마다 날아가지 않게 한다. */}
          <aside
            id="tab-panel-map"
            role="tabpanel"
            aria-labelledby="app-tab-map"
            className={`app-sidebar app-sidebar--floating${activeTab === "map" ? "" : " hidden"}`}
          >
            <div className={analyzeView === "form" ? "" : "hidden"}>
            <form className="analyze-form" onSubmit={(event) => { void handleSubmit(event); }}>
              {/* 분석 지역 — 주소 입력 또는 지도 클릭 (같은 "위치 선택"의 두 방식). */}
              <section className="analyze-step">
                <label className="field-label" htmlFor="address">분석 지역</label>
                <div className="address-autocomplete-wrap">
                  <div className="search-input-wrap">
                    <input
                      id="address"
                      className="text-input"
                      value={address}
                      aria-label="분석 주소"
                      onChange={(e) => void handleAddressChange(e.target.value)}
                      onFocus={() => setAddressFocused(true)}
                      onBlur={() => window.setTimeout(() => setAddressFocused(false), 150)}
                      placeholder="행정동, 지하철역, 상호명 입력"
                    />
                    {address && (
                      <button type="button" className="search-input-clear" aria-label="지우기" onClick={() => { void handleAddressChange(""); }}>✕</button>
                    )}
                  </div>
                  {addressResults.length > 0 ? (
                    <ul className="address-autocomplete-results">
                      {addressResults.map((item) => <li key={`${item.lat}-${item.lng}-${item.address}`}><button type="button" className="address-autocomplete-item" onClick={() => pickAddress(item)}>{item.source === "place" ? "가게" : "주소"} · {item.address}</button></li>)}
                    </ul>
                  ) : (addressFocused && recentSearches.length > 0) ? (
                    <div className="address-autocomplete-results recent-search-panel">
                      <div className="recent-search-head">
                        <span>최근 검색</span>
                        <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => persistRecent([])}>전체삭제</button>
                      </div>
                      <ul className="recent-search-list">
                        {recentSearches.map((item) => (
                          <li key={item.display}>
                            <button type="button" className="recent-search-item" onMouseDown={(e) => e.preventDefault()} onClick={() => pickRecentSearch(item)}>{item.display}</button>
                            <button type="button" className="recent-search-remove" aria-label="삭제" onMouseDown={(e) => e.preventDefault()} onClick={() => removeRecentSearch(item.display)}>✕</button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </div>
                {addressStatus && <p className="status-banner status-banner--error">{addressStatus}</p>}
              </section>

              {/* 분석 업종 — 검색창 하나 (프리셋 버튼 없음). */}
              <section className="analyze-step">
                <BusinessPicker value={upjong} onChange={setUpjong} label="분석 업종" />
              </section>

              {/* 프랜차이즈 브랜드 — 론칭할 브랜드가 있으면 자기잠식·브랜드
                  분석에 쓰인다 (AdaptiveDashboard의 brand와 같은 상태). */}
              <section className="analyze-step">
                <label className="field-label" htmlFor="franchise-brand">
                  프랜차이즈 브랜드 <span className="sim-optional">(선택 — 론칭할 브랜드가 있다면)</span>
                </label>
                <div className="address-autocomplete-wrap">
                  <input
                    id="franchise-brand"
                    className="text-input"
                    value={brand}
                    maxLength={40}
                    autoComplete="off"
                    onChange={(e) => { setBrand(e.target.value); setBrandDirty(true); }}
                    onBlur={() => window.setTimeout(() => setBrandDirty(false), 150)}
                    placeholder="예: 메가커피, 컴포즈커피"
                  />
                  {brandDirty && brandResults.length > 0 && (
                    <ul className="address-autocomplete-results">
                      {brandResults.map((b) => (
                        <li key={`${b.brand_nm}-${b.yr ?? ""}`}>
                          <button
                            type="button"
                            className="address-autocomplete-item"
                            onMouseDown={(e) => e.preventDefault()}
                            onClick={() => { setBrand(b.brand_nm); setBrandDirty(false); setBrandResults([]); }}
                          >
                            {b.brand_nm}
                            {b.industry_mid ? ` · ${b.industry_mid}` : ""}
                            {b.frcs_cnt != null ? ` (가맹점 ${b.frcs_cnt.toLocaleString()}곳)` : ""}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </section>

              {/* 세부 옵션은 접어 둔다 — 기본 흐름은 지역·업종·실행 세 가지뿐. */}
              <details className="analyze-advanced">
                <summary>분석 옵션</summary>
                <fieldset className="form-fieldset">
                  <legend>반경</legend>
                  <div className="radio-row">
                    <label className="radio-label"><input type="radio" checked={radius === 500} onChange={() => handleRadiusChange(500)} /> 500m</label>
                    <label className="radio-label"><input type="radio" checked={radius === 1000} onChange={() => handleRadiusChange(1000)} /> 1km</label>
                  </div>
                </fieldset>
              </details>

              {/* 2026-08-24: "길 구조로 보는 미래 가치" 패널을 첫 화면에서 뺐다
                  (소유자 요청). 그 패널이 지도 색 지표(metric)와 길 색칠 토글
                  (roadLayer)을 켜는 유일한 자리였으므로, 지금은 둘 다 아래
                  기본값으로 고정된다 — 지도는 계속 칠해지지만 사용자가 지표를
                  바꾸거나 색칠을 끌 수는 없다. 어디에 다시 걸지는 별도로 정한다. */}
              <button className="primary-button analyze-run-button" type="button" onClick={() => { void runAnalysis(); }}>상권 분석하기</button>
            </form>
            {status.tone !== "idle" && <div className={statusClass} role="status">{status.message}</div>}
            </div>
            <div className={analyzeView === "results" ? "panel-results" : "hidden"}>
              {/* NICE bizmap식 블루 타이틀 바 — "위치 · 업종" + 조건 수정. */}
              <div className="panel-results-head">
                <span className="panel-results-head-title">
                  {lastAnalyzedLocation?.address ?? "분석 결과"}
                  {upjong ? ` · ${upjong.label}` : ""}
                </span>
                <button type="button" className="panel-results-head-btn" onClick={() => setAnalyzeView("form")}>조건 수정</button>
              </div>
              {analysisData?.warnings && analysisData.warnings.length > 0 && (
                <div className="warnings-banner">
                  {analysisData.warnings.map((w, i) => <div key={i}>{w}</div>)}
                </div>
              )}
              {/* 한눈 요약 타일 — 어떤 정보가 있는지 스크롤 없이 보여주고,
                  누르면 해당 섹션 탭으로 이동한다. */}
              <div className="summary-tiles">
                <button type="button" className="summary-tile" onClick={() => setResultsSection("score")}>
                  <span>입지 점수</span>
                  <strong>{siteScore ? `${Math.round(siteScore.total_score)}점 · ${siteScore.grade}` : siteLoading ? "계산 중…" : "—"}</strong>
                </button>
                <button type="button" className="summary-tile" onClick={() => setResultsSection("insights")}>
                  <span>핵심 고객층</span>
                  <strong>{summaryCoreCustomer ?? (areaInsightsLoading ? "계산 중…" : "—")}</strong>
                </button>
                <button type="button" className="summary-tile" onClick={() => setResultsSection("insights")}>
                  <span>아파트 시세</span>
                  <strong>{summaryAptPyeong != null ? `평당 ${fmtManwon(summaryAptPyeong)}` : areaInsightsLoading ? "계산 중…" : "—"}</strong>
                </button>
                <button type="button" className="summary-tile" onClick={() => setResultsSection("insights")}>
                  <span>유동 피크</span>
                  <strong>{summaryPeakHour != null ? `주중 ${summaryPeakHour}시` : areaInsightsLoading ? "계산 중…" : "—"}</strong>
                </button>
              </div>

              {/* 번호 섹션 탭 — 스크롤 대신 탭으로 원하는 분석에 바로 진입. */}
              <nav className="panel-section-tabs" role="tablist" aria-label="분석 결과 섹션">
                {RESULTS_SECTIONS.map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    role="tab"
                    aria-selected={resultsSection === id}
                    className={`panel-section-tab${resultsSection === id ? " panel-section-tab--active" : ""}`}
                    onClick={() => setResultsSection(id)}
                  >
                    {label}
                  </button>
                ))}
              </nav>
              {status.tone !== "idle" && <div className={statusClass} role="status">{status.message}</div>}
              <div className={resultsSection === "score" ? "panel-section" : "hidden"}>
                <WatchlistAddBar
                  lastAnalyzedLocation={lastAnalyzedLocation}
                  watchlist={watchlist}
                  onWatchlistChange={setWatchlist}
                />
                <SiteScorePanel
                  result={siteScore}
                  loading={siteLoading}
                  error={siteError}
                  priorities={priorities}
                  priorityBoosts={priorityBoosts}
                  onBoostsChange={(b) => { void handlePrioritiesChange(priorities, b); }}
                  personalizing={personalizing}
                  onPrioritiesChange={(next) => { void handlePrioritiesChange(next); }}
                />
              </div>
              <div className={resultsSection === "industry" ? "panel-section" : "hidden"}>
                {upjong ? (
                  <>
                    <AdaptiveDashboard industryId={industryProfileId} siteScore={siteScore} brand={brand} onBrandChange={setBrand} />
                    {brand.trim() !== "" && <FranchiseBrandCard brand={brand.trim()} />}
                  </>
                ) : (
                  // 업종 미선택이면 기본(카페) 프로파일을 침묵 표시하는 대신
                  // 무엇을 해야 하는지 바로 알려준다 — "텅 빈 화면" 방지.
                  <div className="card">
                    <h3>업종 분석</h3>
                    <p className="site-panel-placeholder">분석 업종을 선택하면 업종별 수요·경쟁·상권 매출 분석이 여기에 표시됩니다.</p>
                    <button type="button" className="sim-btn" onClick={() => setAnalyzeView("form")}>← 조건 수정에서 업종 선택</button>
                  </div>
                )}
              </div>
              <div className={resultsSection === "insights" ? "panel-section" : "hidden"}>
                <AreaInsightsSection data={areaInsights} loading={areaInsightsLoading} />
              </div>
              <div className={resultsSection === "dashboard" ? "panel-section" : "hidden"}>
                <DashboardPanel lat={siteScore?.lat ?? null} lng={siteScore?.lng ?? null} radiusM={radius} upjong={upjong} siteScore={siteScore} brand={analyzedBrand || null} />
              </div>
            </div>
          </aside>
          {/* 지도를 전체 배경으로 꽉 채운다 (풀블리드). 범례·안내·도로정보는 지도 위 오버레이. */}
          <div className="map-stage">
            <KakaoMap
              metric={metric}
              mode="walk"
              analysisData={analysisData}
              simulationData={simulationResult}
              selectedClosedOsmIds={closedOsmIds}
              showRoadLayer={roadLayer}
              onMapClick={handleMapClick}
              onRoadClick={handleRoadClick}
              watchlistMarkers={watchlistMarkers}
              candidateMarkers={candidateMarkers}
              focusBoundsTargets={focusBoundsTargets}
            />
            <ReachSlider />
            {/* 범례는 오른쪽 위로 올렸다 — 지도 아래에 가로로 길게 눕혀 두면
                남산 일대처럼 아래쪽 지형을 가린다. 세로로 짧게 세운다. */}
            {/* 2026-11-07: 주소를 넣기 전엔 칠한 길도 번호도 없는데 범례가 「큰 주황 번호가 목 좋은 길 순위」를 설명하며
                휴대폰 지도 오른쪽 위를 가렸다 — 길을 칠한 뒤에만 띄운다 (UX 자동 개선, 390px). */}
            {(analysisData || simulationResult) && (
              <div className="map-overlay-topright">
                <MapLegend metric={metric} simulationActive={Boolean(simulationResult)} simple={activeTab === "analyze"} />
              </div>
            )}
            <div className="map-overlay-bottom">
              {roadInfo && (
                <div className="road-info-banner" role="status">
                  <button type="button" className="road-info-close" aria-label="닫기" onClick={() => setRoadInfo(null)}>×</button>
                  <p><strong>OSM ID</strong> {roadInfo.osmId}</p>
                  <p><strong>도로 유형</strong> {roadInfo.highway}</p>
                  <p><strong>길이</strong> {roadInfo.lengthM} m</p>
                  <p><strong>{roadInfo.metricLabel}</strong> {roadInfo.metricValue}</p>
                </div>
              )}
            </div>
          </div>
      </div>

      {/* 2026-08-24: 상단바의 "더보기" 메뉴를 없앴다(소유자 요청). 이 패널과
          아래 시뮬레이션 패널로 들어가는 문이 그 메뉴뿐이었으므로, 지금은
          상단바에서 직접 열 수 없다. 마운트는 유지한다 — 투자 수익성 분석은
          질의창의 profitability 도구 결과로도 나오고, 패널을 지우면 기능이
          함께 사라진다. 어디에 다시 걸지는 별도로 정한다. */}
      {/* 지도 분석 — 목 좋은 길 3단계. 지도를 숨긴 채 띄우면 카카오 지도가 크기를
          0으로 잡으므로, 탭이 열려 있을 때만 마운트한다(다시 열면 화면 범위만 새로 받는다). */}
      <div
        id="tab-panel-streetMap"
        role="tabpanel"
        aria-labelledby="app-tab-streetMap"
        className={`app-tab-panel${activeTab === "streetMap" ? "" : " hidden"}`}
      >
        {activeTab === "streetMap" && <StreetGradeMap />}
      </div>

      <div
        id="tab-panel-profitability"
        role="tabpanel"
        className={`app-tab-panel${activeTab === "profitability" ? "" : " hidden"}`}
      >
        <div className="tab-page tab-page--profitability">
          <ProfitabilityPanel location={profitabilityLocation} />
        </div>
      </div>

      <div
        id="tab-panel-simulation"
        role="tabpanel"
        className={`app-tab-panel${activeTab === "simulation" ? "" : " hidden"}`}
      >
        <div className="tab-page tab-page--simulation">
          <SimulationPanel
            center={simulationCenter}
            radius={radius}
            closedOsmIds={closedOsmIds}
            result={simulationResult}
            onClosedOsmIdsChange={setClosedOsmIds}
            onResult={setSimulationResult}
          />
        </div>
      </div>

      <div
        id="tab-panel-undervalued"
        role="tabpanel"
        aria-labelledby="app-tab-undervalued"
        className={`app-tab-panel${activeTab === "undervalued" ? "" : " hidden"}`}
      >
        <div className="tab-page tab-page--undervalued">
          <UndervaluedPanel />
        </div>
      </div>

      <div
        id="tab-panel-floorplan"
        role="tabpanel"
        aria-labelledby="app-tab-floorplan"
        className={`app-tab-panel${activeTab === "floorplan" ? "" : " hidden"}`}
      >
        <div className="tab-page tab-page--floorplan">
          <FloorplanAnalyzer handoff={floorplanHandoff} />
        </div>
      </div>

      <div
        id="tab-panel-plannedSite"
        role="tabpanel"
        aria-labelledby="app-tab-plannedSite"
        className={`app-tab-panel${activeTab === "plannedSite" ? "" : " hidden"}`}
      >
        <div className="tab-page tab-page--floorplan">
          {/* 지도를 만드는 비용이 있어 탭을 열었을 때만 붙인다. */}
          {activeTab === "plannedSite" && (
            <PlannedSitePanel
              onOpenInFloorplan={(file) => {
                setFloorplanHandoff((prev) => ({ file, id: (prev?.id ?? 0) + 1 }));
                setActiveTab("floorplan");
              }}
            />
          )}
        </div>
      </div>

      <div
        id="tab-panel-parcelStructure"
        role="tabpanel"
        aria-labelledby="app-tab-parcelStructure"
        className={`app-tab-panel${activeTab === "parcelStructure" ? "" : " hidden"}`}
      >
        <div className="tab-page tab-page--floorplan">
          {/* 지도를 만드는 비용이 있어 탭을 열었을 때만 붙인다. */}
          {activeTab === "parcelStructure" && (
            <ParcelStructurePanel center={{ lat: 37.5172, lng: 127.0276 }} />
          )}
        </div>
      </div>


      <div
        id="tab-panel-watchlist"
        role="tabpanel"
        aria-labelledby="app-tab-watchlist"
        className={`app-tab-panel${activeTab === "watchlist" ? "" : " hidden"}`}
      >
        <div className="tab-page tab-page--watchlist">
          <CandidateCompare
            watchlist={watchlist}
            onWatchlistChange={setWatchlist}
            radius={radius}
            upjong={upjong}
            selectedIds={selectedCandidateIds}
            onSelectedIdsChange={setSelectedCandidateIds}
            onFocusCandidates={setFocusBoundsTargets}
          />
        </div>
      </div>
      {/* 빌드 시각 — 옛 화면이 캐시로 남아 있는지 한눈에 가르는 표식(2026-11-09). 번역하지 않는다. */}
      <p className="app-build-stamp notranslate" translate="no">
        build {process.env.NEXT_PUBLIC_BUILD_STAMP ?? "dev"}
      </p>
    </div>
  );
}
