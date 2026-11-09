"use client";

import { useEffect, useRef, useState } from "react";
import { api, type ClinicSubject, type DashboardResponse, type RadiusM, type RealCompetitionBundleResponse, type SiteScoreResponse, type TravelMode } from "@/lib/api";
import type { UpjongSelection } from "@/components/BusinessPicker";
import { IsochroneSection } from "./IsochroneSection";
import { CannibalizationSection } from "./CannibalizationSection";
import { TerritorySection } from "./TerritorySection";
import { SynergySection } from "./SynergySection";
import { TrendsSection } from "./TrendsSection";
import { ChatSection } from "./ChatSection";
import { LeaseAnalysisSection } from "./LeaseAnalysisSection";
import {
  AcademyClusterSection,
  AcademySubjectPrompt,
  ClinicExtrasForm,
  ClinicSubjectPicker,
  CompetitionSection,
  ModelFitSection,
  type ClinicExtras,
} from "./RealCompetitionSection";
import { COMPETITION_MINUTES_BY_MODE, isAcademyUpjong, isMedicalUpjong, supportsRealCompetition } from "./leaseUtils";

const DEFAULT_ISO_MINUTES = [5, 10, 15];

interface DashboardPanelProps {
  lat: number | null;
  lng: number | null;
  radiusM: RadiusM;
  upjong: UpjongSelection | null;
  siteScore: SiteScoreResponse | null;
  /** 분석 시점의 프랜차이즈 브랜드명 — 잠식 분석에서 자사(자기잠식) 분리에 쓰인다. */
  brand?: string | null;
}

function upjongPayload(upjong: UpjongSelection | null) {
  return {
    inds_lcls_cd: upjong?.inds_lcls_cd || null,
    inds_mcls_cd: upjong?.inds_mcls_cd || null,
    inds_scls_cd: upjong?.inds_scls_cd || null,
    business_type: upjong?.business_type || "general",
  };
}

const DEFAULT_CLINIC_EXTRAS: ClinicExtras = { has_parking: false, has_elevator: false, has_pharmacy_nearby: false, floor_level: null };

// Ported from `fetchDashboard`/`appendDashboardSections` in frontend/js/dashboard.js.
// Fetches the `/api/dashboard` bundle (isochrone + cannibalization + trends +
// territory) whenever the analyzed location/radius/upjong changes, then wires
// mode/competitor-minutes changes to the same partial-refresh endpoints
// (`/api/isochrone`, `/api/cannibalization`) the Vanilla app uses. The
// `/api/real-competition` bundle (competition/academy_cluster/model_fit/synergy)
// is fetched separately, mirroring `loadRealCompetitionCards`/
// `refreshRealCompetitionReach` — see the reach-mode/minutes state below.
export function DashboardPanel({ lat, lng, radiusM, upjong, siteScore, brand }: DashboardPanelProps) {
  const [mode, setMode] = useState<TravelMode>("walk");
  const [competitorMinutes, setCompetitorMinutes] = useState(5);
  // Which isochrone bands are requested/drawn (user-adjustable via the minute
  // chips in IsochroneSection; 3-minute fine-grained band is opt-in).
  const [isoMinutes, setIsoMinutes] = useState<number[]>(DEFAULT_ISO_MINUTES);
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [isoRefreshing, setIsoRefreshing] = useState(false);
  const [cannRefreshing, setCannRefreshing] = useState(false);
  const [isoExpansionDone, setIsoExpansionDone] = useState(false);
  const [territoryLoading, setTerritoryLoading] = useState(false);
  const [territoryError, setTerritoryError] = useState("");

  const [reachMode, setReachMode] = useState("walk");
  const [reachMinutes, setReachMinutes] = useState(5);
  const [areaPyeong, setAreaPyeong] = useState<number | null>(null);
  const [clinicExtras, setClinicExtras] = useState<ClinicExtras>(DEFAULT_CLINIC_EXTRAS);
  const [targetModel, setTargetModel] = useState<string | null>(null);
  const [clinicSubjectCodes, setClinicSubjectCodes] = useState<string[] | null>(null);
  const [clinicSubjects, setClinicSubjects] = useState<ClinicSubject[]>([]);
  const [clinicSubjectsLoading, setClinicSubjectsLoading] = useState(false);
  const [clinicSubjectsError, setClinicSubjectsError] = useState("");
  const [competitionBundle, setCompetitionBundle] = useState<RealCompetitionBundleResponse | null>(null);
  const [competitionLoading, setCompetitionLoading] = useState(false);
  const [competitionError, setCompetitionError] = useState("");

  const brandName = brand?.trim() || null;
  const requestVersionRef = useRef(0);
  const isoExpansionVersionRef = useRef(0);
  const territoryVersionRef = useRef(0);
  const competitionVersionRef = useRef(0);
  const dashboardReady = data !== null;
  const secondaryReady = dashboardReady && isoExpansionDone;

  useEffect(() => {
    if (lat == null || lng == null) {
      requestVersionRef.current += 1;
      setData(null);
      setLoading(false);
      return;
    }
    const version = ++requestVersionRef.current;
    setMode("walk");
    setCompetitorMinutes(5);
    setIsoMinutes(DEFAULT_ISO_MINUTES);
    setLoading(true);
    setError("");
    setData(null);
    setIsoExpansionDone(false);

    const payload = { lat, lng, radius_m: radiusM, ...upjongPayload(upjong), brand: brandName, mode: "walk" as TravelMode, minutes: [5], competitor_minutes: 5, include_territory: false };

    api.dashboard(payload)
      .then((result) => { if (version === requestVersionRef.current) setData(result); })
      .catch((err) => { if (version === requestVersionRef.current) setError(err instanceof Error ? err.message : "대시보드 확장 데이터 로드 실패"); })
      .finally(() => { if (version === requestVersionRef.current) setLoading(false); });
  }, [lat, lng, radiusM, upjong, brandName]);

  useEffect(() => {
    if (lat == null || lng == null || !dashboardReady) {
      isoExpansionVersionRef.current += 1;
      setIsoRefreshing(false);
      return;
    }
    const version = ++isoExpansionVersionRef.current;
    setIsoRefreshing(true);
    api.isochrone({ lat, lng, radius_m: radiusM, ...upjongPayload(upjong), mode: "walk", minutes: DEFAULT_ISO_MINUTES })
      .then((isochrone) => {
        if (version === isoExpansionVersionRef.current) {
          setData((current) => current ? { ...current, isochrone } : current);
        }
      })
      .catch(() => undefined)
      .finally(() => {
        if (version === isoExpansionVersionRef.current) {
          setIsoRefreshing(false);
          setIsoExpansionDone(true);
        }
      });
  }, [lat, lng, radiusM, upjong, dashboardReady]);

  // Delivery territory needs a much wider road network than the core cards.
  // Load it only after the useful dashboard is visible so it can no longer
  // cause the whole panel to time out.
  useEffect(() => {
    if (lat == null || lng == null || !secondaryReady) {
      territoryVersionRef.current += 1;
      setTerritoryLoading(false);
      setTerritoryError("");
      return;
    }
    const version = ++territoryVersionRef.current;
    setTerritoryLoading(true);
    setTerritoryError("");
    api.territory({ lat, lng, radius_m: radiusM, ...upjongPayload(upjong) })
      .then((territory) => {
        if (version === territoryVersionRef.current) {
          setData((current) => current ? { ...current, territory } : current);
        }
      })
      .catch((err) => {
        if (version === territoryVersionRef.current) setTerritoryError(err instanceof Error ? err.message : "배달 권역 분석 실패");
      })
      .finally(() => {
        if (version === territoryVersionRef.current) setTerritoryLoading(false);
      });
  }, [lat, lng, radiusM, upjong, secondaryReady]);

  // 진료과목(clinic specialty) list is nationwide (not location-dependent), so
  // it's fetched once on mount rather than per-analysis, unlike everything
  // else in this component.
  useEffect(() => {
    let cancelled = false;
    setClinicSubjectsLoading(true);
    api.clinicSubjects()
      .then((res) => {
        if (cancelled) return;
        setClinicSubjects(res.subjects || []);
        if (res.warnings && res.warnings.length > 0) setClinicSubjectsError(res.warnings[0]);
      })
      .catch((err) => {
        if (cancelled) return;
        setClinicSubjectsError(err instanceof Error ? err.message : "진료과목 목록을 불러오지 못했습니다.");
      })
      .finally(() => {
        if (!cancelled) setClinicSubjectsLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  // Reset real-competition reach state whenever the analyzed location/upjong
  // changes (matches the `data.synergy = null` reset previously done inline).
  useEffect(() => {
    setReachMode("walk");
    setReachMinutes(5);
    setAreaPyeong(null);
    setClinicExtras(DEFAULT_CLINIC_EXTRAS);
    setClinicSubjectCodes(null);
    setTargetModel(null);
    setCompetitionBundle(null);
    setCompetitionError("");
  }, [lat, lng, upjong]);

  // Ported from `loadRealCompetitionCards`/`refreshRealCompetitionReach`.
  // Skipped entirely when the selected upjong doesn't support real
  // competition, and — for academy upjongs — until a subject is chosen
  // (matches Vanilla's `isAcademyUpjongSelected() && !ctx.upjong?.target_model` gate).
  useEffect(() => {
    if (lat == null || lng == null || !secondaryReady || !supportsRealCompetition(upjong)) {
      setCompetitionBundle(null);
      return;
    }
    if (isAcademyUpjong(upjong) && !targetModel) {
      setCompetitionBundle(null);
      return;
    }
    const version = ++competitionVersionRef.current;
    setCompetitionLoading(true);
    setCompetitionError("");
    api.realCompetition({
      lat,
      lng,
      radius_m: radiusM,
      ...upjongPayload(upjong),
      mode: reachMode as TravelMode,
      competitor_minutes: reachMinutes,
      area_pyeong: areaPyeong,
      target_model: targetModel,
      has_parking: clinicExtras.has_parking,
      has_elevator: clinicExtras.has_elevator,
      has_pharmacy_nearby: clinicExtras.has_pharmacy_nearby,
      floor_level: clinicExtras.floor_level,
      subject_codes: clinicSubjectCodes,
    })
      .then((bundle) => { if (version === competitionVersionRef.current) setCompetitionBundle(bundle); })
      .catch((err) => { if (version === competitionVersionRef.current) setCompetitionError(err instanceof Error ? err.message : "시너지 분석 실패"); })
      .finally(() => { if (version === competitionVersionRef.current) setCompetitionLoading(false); });
  }, [lat, lng, radiusM, upjong, reachMode, reachMinutes, areaPyeong, clinicExtras, targetModel, clinicSubjectCodes, secondaryReady]);

  async function handleModeChange(nextMode: string) {
    if (!data || lat == null || lng == null || nextMode === mode) return;
    const version = ++requestVersionRef.current;
    setMode(nextMode as TravelMode);
    setIsoRefreshing(true);
    setCannRefreshing(true);
    try {
      // Drop selections the new mode doesn't allow (e.g. moto 20/25 -> walk).
      const allowed = COMPETITION_MINUTES_BY_MODE[nextMode] || COMPETITION_MINUTES_BY_MODE.walk;
      const kept = isoMinutes.filter((m) => allowed.includes(m));
      const bandMinutes = kept.length ? kept : DEFAULT_ISO_MINUTES;
      setIsoMinutes(bandMinutes);
      const isoPayload = { lat, lng, radius_m: radiusM, ...upjongPayload(upjong), brand: brandName, mode: nextMode as TravelMode, minutes: bandMinutes };
      const iso = await api.isochrone(isoPayload);
      if (version !== requestVersionRef.current) return;
      setData((d) => (d ? { ...d, isochrone: iso } : d));
      setIsoRefreshing(false);

      const cannPayload = { ...isoPayload, competitor_minutes: competitorMinutes };
      const cann = await api.cannibalization(cannPayload);
      if (version !== requestVersionRef.current) return;
      setData((d) => (d ? { ...d, cannibalization: cann } : d));
    } catch (err) {
      if (version === requestVersionRef.current) setError(err instanceof Error ? err.message : "모드 변경 실패");
    } finally {
      if (version === requestVersionRef.current) {
        setIsoRefreshing(false);
        setCannRefreshing(false);
      }
    }
  }

  async function handleMinutesChange(minutes: number) {
    if (!data || lat == null || lng == null || minutes === competitorMinutes) return;
    const version = ++requestVersionRef.current;
    setCompetitorMinutes(minutes);
    setCannRefreshing(true);
    try {
      const payload = { lat, lng, radius_m: radiusM, ...upjongPayload(upjong), brand: brandName, mode, minutes: isoMinutes, competitor_minutes: minutes };
      const cann = await api.cannibalization(payload);
      if (version !== requestVersionRef.current) return;
      setData((d) => (d ? { ...d, cannibalization: cann } : d));
    } catch (err) {
      if (version === requestVersionRef.current) setError(err instanceof Error ? err.message : "동일업종 목록 갱신 실패");
    } finally {
      if (version === requestVersionRef.current) setCannRefreshing(false);
    }
  }

  async function handleIsoMinutesToggle(minute: number) {
    if (!data || lat == null || lng == null) return;
    const next = isoMinutes.includes(minute)
      ? isoMinutes.filter((m) => m !== minute)
      : [...isoMinutes, minute].sort((a, b) => a - b);
    if (next.length === 0) return; // keep at least one band selected
    const version = ++requestVersionRef.current;
    setIsoMinutes(next);

    // 도보 시간을 3분·5분·15분으로 바꿔도 경쟁 분석 결과가 똑같았다. 시간이
    // 도달권(isoMinutes)·잠식(competitorMinutes)·시너지(reachMinutes) 세 곳에
    // 따로 있어서, 지도만 다시 그리고 경쟁 쪽은 옛 기준 그대로였기 때문이다.
    // 지도에 그린 가장 넓은 도달권을 경쟁 분석 기준으로 맞춰 하나로 움직이게 한다.
    const reach = Math.max(...next);
    setReachMinutes(reach); // 시너지(realCompetition)는 useEffect가 알아서 다시 부른다
    const needCann = reach !== competitorMinutes;
    if (needCann) setCompetitorMinutes(reach);

    setIsoRefreshing(true);
    if (needCann) setCannRefreshing(true);
    try {
      const iso = await api.isochrone({ lat, lng, radius_m: radiusM, ...upjongPayload(upjong), mode, minutes: next });
      if (version !== requestVersionRef.current) return;
      setData((d) => (d ? { ...d, isochrone: iso } : d));
      setIsoRefreshing(false);

      if (needCann) {
        const cann = await api.cannibalization({
          lat, lng, radius_m: radiusM, ...upjongPayload(upjong), brand: brandName,
          mode, minutes: next, competitor_minutes: reach,
        });
        if (version !== requestVersionRef.current) return;
        setData((d) => (d ? { ...d, cannibalization: cann } : d));
      }
    } catch (err) {
      if (version === requestVersionRef.current) setError(err instanceof Error ? err.message : "도달권 시간대 갱신 실패");
    } finally {
      if (version === requestVersionRef.current) {
        setIsoRefreshing(false);
        setCannRefreshing(false);
      }
    }
  }

  function handleReachChange(nextMode: string, nextMinutes: number) {
    setReachMode(nextMode);
    setReachMinutes(nextMinutes);
  }

  if (lat == null || lng == null) {
    return <div className="card"><h2>대시보드 분석</h2><p className="site-panel-placeholder">분석 실행 또는 지도 클릭 후 도달권·잠식·권역·시너지·상권 추세 등 확장 분석이 표시됩니다.</p></div>;
  }
  if (loading) {
    return <div className="card"><h2>대시보드 분석</h2><p>대시보드 확장 데이터 로드 중...</p></div>;
  }
  if (error) {
    return <div className="card"><h2>대시보드 분석</h2><p className="status-banner status-banner--error">대시보드 확장 데이터 로드 실패: {error}</p></div>;
  }
  if (!data) return null;

  const academySelected = isAcademyUpjong(upjong);
  const medicalSelected = isMedicalUpjong(upjong);
  const showRealCompetition = supportsRealCompetition(upjong);
  const awaitingAcademySubject = academySelected && !targetModel;

  return (
    <div className="dash-analytics">
      <IsochroneSection
        iso={data.isochrone}
        categoryLabel={data.trends?.category_label}
        lat={lat}
        lng={lng}
        activeMinutes={competitorMinutes}
        selectedMinutes={isoMinutes}
        loading={isoRefreshing}
        onModeChange={(m) => void handleModeChange(m)}
        onMinutesToggle={(m) => void handleIsoMinutesToggle(m)}
        onBandClick={(m) => void handleMinutesChange(m)}
      />

      {medicalSelected && <ClinicExtrasForm value={clinicExtras} onChange={setClinicExtras} />}
      {medicalSelected && (
        <ClinicSubjectPicker
          options={clinicSubjects}
          value={clinicSubjectCodes}
          onChange={setClinicSubjectCodes}
          loading={clinicSubjectsLoading}
          error={clinicSubjectsError}
        />
      )}

      {showRealCompetition && academySelected && (
        <AcademySubjectPrompt value={targetModel} onChange={setTargetModel} />
      )}

      {showRealCompetition && !awaitingAcademySubject && competitionLoading && (
        <section className="dash-card dash-card--real-comp"><p className="demand-meta">실질 경쟁 분석 중... (최대 수 분 소요)</p></section>
      )}
      {showRealCompetition && !awaitingAcademySubject && !competitionLoading && competitionError && (
        <section className="dash-card dash-card--real-comp"><p className="demand-meta">실질 경쟁 로드 실패: {competitionError}</p></section>
      )}
      {showRealCompetition && competitionBundle?.academy_cluster && (
        <AcademyClusterSection cluster={competitionBundle.academy_cluster} mode={reachMode} minutes={reachMinutes} onReachChange={handleReachChange} />
      )}
      {showRealCompetition && !competitionBundle?.academy_cluster && competitionBundle?.competition && (
        <CompetitionSection
          competition={competitionBundle.competition}
          modelFit={competitionBundle.model_fit}
          mode={reachMode}
          minutes={reachMinutes}
          onReachChange={handleReachChange}
          warnings={competitionBundle.warnings}
        />
      )}
      <SynergySection synergy={competitionBundle?.synergy ?? null} loading={showRealCompetition && !awaitingAcademySubject && competitionLoading} error={competitionError} />
      {competitionBundle?.model_fit && <ModelFitSection modelFit={competitionBundle.model_fit} />}

      <LeaseAnalysisSection lat={lat} lng={lng} radiusM={radiusM} upjong={upjong} subjectCodes={clinicSubjectCodes} />
      <CannibalizationSection cann={data.cannibalization} loading={cannRefreshing} onMinutesChange={(m) => void handleMinutesChange(m)} />
      {territoryLoading && <section className="dash-card dash-card--loading"><p className="demand-meta">배달 권역 분석은 뒤에서 계속 계산 중입니다. 다른 결과는 먼저 확인할 수 있습니다.</p></section>}
      {territoryError && <section className="dash-card"><p className="demand-meta">배달 권역 분석 실패: {territoryError}</p></section>}
      <TerritorySection territory={data.territory ?? null} lat={lat} lng={lng} />
      <TrendsSection trends={data.trends} />
      <ChatSection trends={data.trends} siteScore={siteScore} lat={lat} lng={lng} businessType={upjong?.business_type} />
    </div>
  );
}
