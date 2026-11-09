"use client";

/**
 * 땅값 화면 — 왼쪽 결과, 오른쪽 지도.
 *
 * **결과가 입구이고 AI는 후속 창구다**(`docs/SPEC_LAND_VALUE_TABS_UI.md`).
 * 예전에는 필지를 누르면 판정이 `AgentChat` 스레드에 카드 한 장으로만 들어가서,
 * 무엇을 물어야 하는지 모르는 사람은 아무것도 못 봤다. 지금은 필지를 누르면
 * 왼쪽에 6탭 결과가 스스로 완결되고, 대화는 그 결과를 본 사람이 "왜 저평가야?"를
 * 묻는 자리로 남는다.
 *
 * ⚠️ **`AgentChat`은 DOM에 하나뿐이고 언마운트하지 않는다.** 탭마다 따로 두면
 * 스레드가 갈리고, 언마운트하면 탭을 옮길 때마다 대화가 날아간다. 탭 전환은
 * CSS 배치만 바꾼다.
 *
 * ⚠️ **「대화」 탭에서는 지도를 감춘다** — 읽는 동안 옆에서 계속 움직이는 것이
 * 시야에 남아 거슬린다(소유자 지적, 2026-08-24).
 */

import { useCallback, useState } from "react";
import { AgentChat } from "@/components/agent/AgentChat";
import { LandValueResult } from "@/components/landvalue/LandValueResult";
import { ParcelMap } from "@/components/parcels/ParcelMap";
import { api, type MapParcel } from "@/lib/api";
import type { StructuredAnalysis } from "@/lib/landAnalysis";

/** 데모 구역 — 여의도동. 폴리곤이 적재돼 있고 판정이 실측 검증된 곳이다. */
const DEMO_CENTER = { lat: 37.5202, lng: 126.9317 };

type Tab = "result" | "chat";

const TABS: { key: Tab; label: string }[] = [
  { key: "result", label: "분석 결과" },
  { key: "chat", label: "대화" },
];

export default function LandPage() {
  const [tab, setTab] = useState<Tab>("result");
  const [analysis, setAnalysis] = useState<StructuredAnalysis | null>(null);
  const [selectedPnu, setSelectedPnu] = useState<string | null>(null);
  const [loadError, setLoadError] = useState("");
  const [loading, setLoading] = useState(false);

  const onSelect = useCallback(async (parcel: MapParcel) => {
    setSelectedPnu(parcel.pnu);
    setLoadError("");
    setLoading(true);
    try {
      // 판정은 서버가 만든다. 지도가 들고 있는 값으로 화면에서 다시 조립하지
      // 않는다 — 그러면 규칙이 두 곳에 생기고 서술이 없다.
      setAnalysis(await api.parcelAnalysis(parcel.pnu));
    } catch {
      // 판정 자료가 없는 필지(404)와 통신 실패를 같은 문구로 말한다 — 사용자가
      // 할 수 있는 일이 같다(다른 필지를 눌러 본다).
      setAnalysis(null);
      setLoadError(
        `${parcel.address || parcel.pnu}의 판정 자료를 불러오지 못했습니다. 다른 필지를 눌러 보세요.`,
      );
    } finally {
      setLoading(false);
    }
  }, []);

  return (
    <div className={`land-page land-page--${tab}`}>
      {/* 앱 상단 탭과 같은 마크업·스타일 */}
      <nav className="app-tabs" role="tablist" aria-label="땅값 화면">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            className={`app-tab${tab === t.key ? " app-tab--active" : ""}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {loading && (
        <p className="status-banner status-banner--loading" role="status">
          필지 판정을 불러오는 중…
        </p>
      )}
      {loadError && (
        <p className="status-banner status-banner--error" role="alert">
          {loadError}
        </p>
      )}

      <div className="land-page__body">
        {/* 결과 패널 — 「대화」 탭에서는 감춘다(언마운트하지 않는다: 돌아올 때
            필지를 다시 누르게 하지 않으려고). */}
        <section
          className={`land-page__result${tab === "result" ? "" : " hidden"}`}
          aria-label="분석 결과"
        >
          {analysis ? (
            <LandValueResult analysis={analysis} />
          ) : (
            <p className="land-page__hint">
              오른쪽 지도에서 필지를 누르면 여기에 <b>토지가치 종합분석</b>이 나옵니다. 궁금한 점은
              「대화」 탭에서 이어서 물어볼 수 있습니다.
            </p>
          )}
        </section>

        <section
          className={`land-page__chat${tab === "chat" ? "" : " hidden"}`}
          aria-label="대화"
        >
          <AgentChat parcelAnalysis={analysis} />
        </section>

        {/* 지도는 결과 탭에서만 보인다. 언마운트하지 않는 이유는 돌아올 때마다
            필지를 처음부터 다시 받아 오기 때문이다. */}
        <section
          className={`land-page__map${tab === "result" ? "" : " hidden"}`}
          aria-label="지도"
        >
          <ParcelMap center={DEMO_CENTER} selectedPnu={selectedPnu} onSelect={onSelect} />
        </section>
      </div>
    </div>
  );
}
