"use client";

import { SpeakButton } from "@/components/SpeakButton";
import { useCallback, useEffect, useRef, useState, useMemo } from "react";
import { loadKakaoMaps } from "@/lib/kakao";
import { inKorea, loadOsmMaps, type MapsApi } from "@/lib/osmMapEngine";
import "leaflet/dist/leaflet.css";
import {
  api,
  ApiClientError,
  type PlannedLot,
  type PlannedLotSide,
  type PlannedSiteExtractResponse,
  type PlannedSiteResponse,
} from "@/lib/api";
import { UNGRADED_COLOR, outerRings } from "@/lib/parcelGrade";

/**
 * 계획 단계 택지 — 아직 없는 길 위에서 **고른 획지와 옆 획지를 견준다**.
 *
 * 2026-10-24: 1~5등급 색칠을 걷어냈다. 등급은 도면을 어디까지 잘라 넣느냐에 따라
 * 흔들리고(운정 샘플에서 F10이 2→3→1등급), 「1등급」만으로는 왜 좋은지 모른다.
 * 대신 획지 하나를 고르면 ① 변(동·서·남·북)마다 어떤 길에 닿아 왜 좋은지/약한지,
 * ② 맞닿은·길 건너 획지와 견주면 어떤지를 보여 준다 — 입찰하는 사람의 질문은
 * 「이 땅이냐 옆 땅이냐」, 「입구를 어느 쪽에 내나」다.
 *
 * A안 화면(`ParcelStructurePanel`)은 지도를 움직이면 그 자리의 필지를 서버가
 * 알아서 읽어 온다. 여기는 그럴 수 없다 — 계획 도면은 우리 DB에 없고 사용자가
 * 들고 온다. 그래서 **계획안 파일을 받아** 그리고, 지도는 그 범위에 맞춘다.
 *
 * 받는 것은 **LH 공고에 붙어 오는 계획 도면 DXF**다(2026-10-22). 서버가 길·획지를
 * 꺼내 `{roads, lots}`로 돌려주면 그대로 등급 계산에 넣는다. 서버 요청 모양의 JSON도
 * 여전히 받는다. CAD에서 뽑은 **벡터 PDF**도 받는다(2026-11-09) — 서버가 도곽 좌표 숫자로
 * 위치를 맞춰 DXF와 같은 길로 읽는다. 스캔 PDF와 좌표 숫자가 없는 PDF는 서버가 할 일을 말하고 멈춘다.
 *
 * ⚠️ 좌표계는 사람이 확인한다. 5174와 5186은 **둘 다 한국 땅에 떨어진다** — 틀리면
 * 100km 어긋난 채 조용히 돌아간다. 그래서 후보마다 지명을 붙여 보여 주고, 「이 위치가
 * 맞습니다」를 누르기 전에는 계산을 막는다.
 */

type LayerRole = "road" | "lot" | "other";

// 샘플 셋. 운정은 실제 LH 도면(업무복합 F10 입찰 2026-10-28) — 바둑판 가상 지구는 「이게 무슨
// 도면이냐」가 먼저 나와서 바꿨다(2026-10-24). 두바이·미국은 2026-11-09(해커톤 01, 글로벌 대회)에
// 더했다 — 그 나라 공식 CAD 도면은 공개돼 있지 않아 OpenStreetMap의 **실제 도로망**으로 만든
// DXF다(`backend/tools/make_osm_plan_sample.py`, 블록 = 획지 대용, 좌표계 UTM). 한국 밖이라 주변
// 기존 도로·아파트·역 데이터 없이 도면 안 길만으로 계산한다(서버가 알아서 뺀다).
type SamplePlan = {
  id: "unjeong" | "dubai" | "us";
  label: string;
  path: string;
  fileName: string;
  crs: string;
  focus: string | null; // 계산이 끝나면 처음부터 골라 둘 획지. null이면 가장 좋은 상가 땅을 고른다.
  abroad: boolean;
  about: string; // 샘플 설명 한 문장
};
const SAMPLE_PLANS: SamplePlan[] = [
  {
    id: "unjeong",
    label: "샘플: 운정신도시 LH 도면",
    path: "/samples/planned-site-sample.dxf",
    fileName: "운정3지구_운정중앙역_획지결정도.dxf",
    crs: "EPSG:5174",
    focus: "F10",
    abroad: false,
    about: "파주 운정3지구 운정중앙역 일대 LH 획지결정도 한 장입니다. 계산하면 업무복합용지 F10(LH 입찰 2026-10-28)을 골라 옆 땅과 견줍니다.",
  },
  {
    id: "dubai",
    label: "샘플: 두바이 다운타운",
    path: "/samples/planned-site-dubai-downtown.dxf",
    fileName: "Dubai_Downtown_OSM.dxf",
    crs: "EPSG:32640",
    focus: null,
    abroad: true,
    about:
      "두바이 다운타운(부르즈 할리파·두바이몰 일대) 약 1.4km×1.1km의 실제 도로망입니다. 도로가 둘러싼 블록을 획지로 보고, 계산하면 가장 좋은 블록을 골라 둡니다. 자료: © OpenStreetMap contributors (ODbL).",
  },
  {
    id: "us",
    label: "샘플: 미국 셀러브레이션",
    path: "/samples/planned-site-us-celebration.dxf",
    fileName: "Celebration_FL_parcels.dxf",
    crs: "EPSG:32617",
    // 중심가(마켓 스트리트) 상업 필지. 「가장 좋은 땅」을 고르면 지구 북쪽 25ha 빈 상업 용지(C1)가
    // 잡혀 시연이 땅덩이 얘기가 된다 — 운정 F10처럼 보여 줄 땅을 정해 둔다(2026-11-09).
    focus: "C18",
    abroad: true,
    about:
      "미국 플로리다 셀러브레이션(디즈니가 1994년부터 계획해 지은 신도시) 중심부 약 2.6km×2.2km입니다. 길은 실제 도로망, 땅은 오시올라 카운티 감정평가청의 2025년 실제 필지입니다 — C 상업·업무, 초 학교, P 공원·공공은 필지 그대로, B 주거는 호실·단독주택이 수천 개라 길로 나뉜 블록으로 합쳤습니다. 자료: Osceola County Property Appraiser, © OpenStreetMap contributors (ODbL).",
  },
];
// 한국 밖 샘플의 블록 이름 머리글자 → 용도 (OSM landuse·leisure·amenity에서 읽은 것)
const ABROAD_LAND_USE: [string, string][] = [["C", "상업 블록"], ["B", "주거 블록"], ["P", "공원·녹지"], ["초", "학교"]];

// 운정3 샘플의 땅 기호 → LH 「파주운정3 지구단위지침서-2편(건축)_260729」(공고 BN-0008014 첨부 hwp)에 적힌 용지 이름.
// 2026-10-27 소유자: 「문이 뭐야? 이용 계획표를 보고 빨리 정확하게 넣어」. 근거 — 제1장 제3조⑥과 표Ⅱ-1·Ⅱ-2(F 용지 분류),
// 표Ⅱ-9(주복1~6 주상복합용지), 표Ⅱ-15(학교: 초·중·고), 표Ⅱ-18(문2 문화시설), 그림Ⅱ-6(A 공동주택용지).
// 기호는 지구마다 달라 다른 도면에는 붙이지 않는다. 「획지 5」는 원본 도면에 이름이 없어 확인 중.
const SAMPLE_LAND_USE: Record<string, string> = {
  "F1-1": "일반상업용지", "F1-2": "일반상업용지", "F2-1": "일반상업용지", "F2-2": "일반상업용지",
  F10: "업무복합용지",
  주복1: "주상복합용지", 주복2: "주상복합용지", 주복3: "주상복합용지",
  주복4: "주상복합용지", 주복5: "주상복합용지", 주복6: "주상복합용지",
  문2: "문화시설",
  초4: "학교·초등학교", 초5: "학교·초등학교", 중5: "학교·중학교", 고2: "학교·고등학교",
  A20: "공동주택용지", A21: "공동주택용지", A22: "공동주택용지",
  A44: "공동주택용지", A45: "공동주택용지", A46: "공동주택용지", A47: "공동주택용지",
};
// 같은 지침 표Ⅱ-1(건폐율·용적률·층수)과 표Ⅱ-2(변경, 용도분류표: 판매시설 △3·업무시설 △13). 값·입찰 판단은 쓰지 않는다.
const SAMPLE_ALLOWED: Record<string, string> = {
  F10:
    "판매시설 중 소매시장 허용(도매시장만 제외) · 오피스텔은 건축 연면적 50% 이내 · 건폐율 70% 이하 · 용적률 500% 이하 · 최저 5층",
};

// 지도 색은 하나의 뜻만 — 「손님이 찾아오기 쉬운 정도」(가장 좋은 변의 값)를 파랑→노랑→빨강으로.
// 2026-10-24: 「F10 대비 더 좋음/비슷/약함」으로 칠했더니 거의 다 한 색이라 「차이가 없다,
// 설명도 이해가 안 간다」(사용자). 평면도 결과처럼 연속 색으로 칠하고, 비교는 표에 글로 둔다.
// 「근처 학교·아파트」로 적는 거리 — 서버 NEARBY_M과 같게.
const NEARBY_LIMIT_M = 150;

// 무지개(보라→빨강) — 사용자 요청 2026-10-24.
// 2026-10-26 사용자: 「빨강일수록 높고 파랑일수록 낮다」 — 보라로 시작하면 말과 어긋나 파랑부터.
const RAMP = ["#2563eb", "#06b6d4", "#22c55e", "#facc15", "#f97316", "#dc2626"];

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** 0(낮음)~1(높음) → 보라·파랑·하늘·초록·노랑·주황·빨강 사이 색. */
function rampColor(t: number): string {
  const x = Math.min(1, Math.max(0, t)) * (RAMP.length - 1);
  const i = Math.min(RAMP.length - 2, Math.floor(x));
  const [a, b] = [hexToRgb(RAMP[i]), hexToRgb(RAMP[i + 1])];
  const f = x - i;
  return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * f)).join(",")})`;
}

const VERDICT = {
  better: { label: "더 좋음", color: "#ea580c" },
  similar: { label: "비슷", color: "#eab308" },
  weaker: { label: "약함", color: "#38bdf8" },
} as const;
type Verdict = keyof typeof VERDICT;
// 가장 좋은 변끼리 이만큼(백분위 점) 넘게 벌어져야 「더 좋음/약함」으로 부른다.
const VERDICT_GAP = 5;

// 변 색 — 그 변이 닿은 길의 세기.
/** 이 도면의 상가 땅 점수 순서(0~1). 땅 색과 변 색이 같은 잣대를 쓰게 한 곳에 둔다 —
 *  땅은 빨강인데 그 땅의 변은 주황(절대 점수)으로 나와 엇갈려 보였다(2026-10-26 사용자). */
const COUNT_WORD: Record<number, string> = { 1: "한", 2: "두", 3: "세", 4: "네" };

function makeRank(lots: PlannedLot[]): (v: number) => number {
  const values = lots
    .filter((l) => l.kind === "candidate")
    .map((l) => l.best_side_score)
    .filter((v): v is number => v != null)
    .sort((x, y) => x - y);
  return (v: number) => {
    const below = values.filter((x) => x < v).length;
    const same = values.filter((x) => x === v).length;
    return values.length > 1 ? Math.min(1, (below + Math.max(0, same - 1) / 2) / (values.length - 1)) : 0.5;
  };
}

// 2026-10-26 사용자: 빨강은 「입구 내기 좋은 곳」이 아니라 「사람이 많이 지나다닐 길」로 읽어야 한다.
function sideStrength(rank: number): { label: string; color: string } {
  const color = rampColor(rank);
  if (rank >= 2 / 3) return { label: "유동인구 높음(예상)", color };
  if (rank >= 1 / 3) return { label: "보통", color };
  return { label: "유동인구 낮음(예상)", color };
}

function verdictOf(picked: PlannedLot, other: PlannedLot): Verdict {
  const gap = (other.best_side_score ?? 0) - (picked.best_side_score ?? 0);
  if (gap > VERDICT_GAP) return "better";
  if (gap < -VERDICT_GAP) return "weaker";
  return "similar";
}

/** 도로 등급(학습과 같은 OSM 표, 도면 길은 폭으로 환산) → 쉬운 말. */
function roadClassText(rank: number): string {
  if (rank >= 5) return "큰길(대로)";
  if (rank >= 4) return "중간 길(중로)";
  if (rank >= 3) return "작은 길(소로)";
  return "좁은 길";
}

/** 상가 후보끼리 「가게가 들어설 가능성」 등수. 같은 블록의 땅은 같은 점수다. */
function planRanks(lots: PlannedLot[]): Map<string, { rank: number; total: number; t: number }> {
  const cand = lots.filter((l) => l.kind === "candidate" && l.plan).sort((a, b) => b.plan!.score - a.plan!.score);
  const out = new Map<string, { rank: number; total: number; t: number }>();
  cand.forEach((l) => {
    const rank = cand.findIndex((o) => o.plan!.score === l.plan!.score) + 1;
    out.set(l.pnu, { rank, total: cand.length, t: cand.length > 1 ? 1 - (rank - 1) / (cand.length - 1) : 0.5 });
  });
  return out;
}

/** 백분위(0~100, 높을수록 좋음) → 「상위 20%」, 5% 단위로 올림.
 *  2026-10-27 소유자: 「길 100개 중 19등 안」보다 「상위 20%」가 입찰하는 사람에게 바로 읽힌다. */
function topText(pct: number): string {
  // 2026-11-07 UX 루프: 아래쪽 절반도 「상위」로 말하니 학교·아파트 땅에서 「상위 90%」「상위 100%」가
  // 찍혔다 — 「100%」는 사실상 맨 뒤라 뜻이 거꾸로 읽힌다. 아래쪽 절반은 「하위」로 뒤집는다
  // (lib/parcelMap.ts percentileText, 지도 탭 범례 「하위 50%」와 같은 방식). 5% 단위는 그대로.
  const top = Math.max(5, Math.ceil((100 - pct) / 5) * 5);
  if (top <= 50) return `상위 ${top}%`;
  return `하위 ${Math.max(5, Math.ceil(pct / 5) * 5)}%`;
}

type Tier = "high" | "mid" | "low";
/** 변 색·「유동인구 높음/보통/낮음」과 같은 셋 나눔(상가 땅끼리 견준 순서 0~1). */
function tierOf(rank: number): Tier {
  return rank >= 2 / 3 ? "high" : rank >= 1 / 3 ? "mid" : "low";
}

/** 받침 있나 — 「F2-2는」·「주복1은」. 숫자는 읽는 소리로(1 일·2 이·3 삼…). 모르면 null. */
function hasBatchim(word: string): boolean | null {
  const ch = word.trim().slice(-1);
  if (/[가-힣]/.test(ch)) return (ch.charCodeAt(0) - 0xac00) % 28 !== 0;
  if (/\d/.test(ch)) return "013678".includes(ch);
  return null;
}

function josa(word: string, withB: string, withoutB: string): string {
  const b = hasBatchim(word);
  return word + (b == null ? `${withB}(${withoutB})` : b ? withB : withoutB);
}

/** 정면 — 2026-10-27 소유자: 결론(어느 쪽이 정면, 그래서 정문·간판을 어디에)을 먼저, 숫자는 쉬운 말로 뒤에.
 *  학교·아파트 땅(commercial=false)은 위에 「상가를 낼 수 없어」라 적어 놓고 아래에서 「1층 상가를 길게
 *  놓을 수 있으니 간판은 이쪽」·「업무동 출입구」라고 해 서로 어긋났다 — 상가·간판 말을 빼고 출입구로만
 *  말한다 (2026-11-07 UX 자동 개선, 운정 샘플 초4·A20). */
function frontText(lot: PlannedLot, tier: Tier, commercial = true): string {
  const s = lot.sides[0];
  const head =
    lot.sides.length >= 2
      ? `${s.direction}쪽이 이 땅의 정면입니다 — ${COUNT_WORD[lot.sides.length] ?? lot.sides.length} 면 중 앞길이 가장 좋은 쪽이고, 지구 안 길 중 ${topText(s.score)}입니다(지나가는 사람과 찾아오기 쉬움을 함께 본 길 모양 예상).`
      : `길에 닿은 면이 ${s.direction}쪽 하나라 이쪽이 정면입니다 — 지구 안 길 중 ${topText(s.score)}입니다(지나가는 사람과 찾아오기 쉬움을 함께 본 길 모양 예상).`;
  const tail = !commercial
    ? ` 길에 ${Math.round(s.length_m)}m 맞닿아 있어 정문은 이쪽이 좋습니다.`
    : s.length_m >= 100
      ? ` 길에 ${Math.round(s.length_m)}m 맞닿아 1층 상가를 길을 따라 길게 놓을 수 있으니, 정문·간판은 이쪽이 좋습니다.`
      : " 정문·간판은 이쪽이 좋습니다.";
  // 정면 칸 머리표는 「상가 땅끼리 견준 순서」라, 이 땅에선 가장 좋은 쪽인데도 「유동인구 낮음」이 붙어
  // 「가장 좋은 쪽·상위 30%」 문장과 어긋나 보였다 — 견준 상대가 다르다는 걸 한 문장으로 밝힌다
  // (2026-11-06 UX 자동 개선, 운정 샘플 F10: 남쪽 정면 상위 30% · 머리표 「유동인구 낮음」).
  const versus =
    tier === "low"
      ? " 다만 다른 상가 땅들의 정면과 견주면 앞길이 약한 편이라 「유동인구 낮음」으로 표시했습니다."
      : tier === "mid"
        ? " 다른 상가 땅들의 정면과 견주면 중간이라 「보통」으로 표시했습니다."
        : "";
  return head + tail + (s.footway ? " 보행로에 닿아 걸어오는 손님도 이쪽으로 들어옵니다." : "") + versus;
}

/** 정면이 아닌 변 — 어디에 쓰면 좋은지로. 지나가는 사람만 보면 정면보다 많은 변은 따로 짚는다. */
function otherSideText(side: PlannedLotSide, tier: Tier, front: PlannedLotSide, commercial = true): string {
  const d = `${side.direction}쪽`;
  if (!commercial) {
    const use =
      tier === "high"
        ? `${d}도 앞길이 좋은 쪽(지구 안 길 중 ${topText(side.score)}) — 두 번째 출입구 자리로 좋습니다.`
        : tier === "mid"
          ? `${d}은 보통(지구 안 길 중 ${topText(side.score)}) — 보조 출입구로 쓸 만합니다.`
          : `${d}은 지나가는 사람이 적을 쪽(뒤쪽) — 주차·차량 출입구에 맞습니다.`;
    return use + (side.footway ? " 보행로에 닿아 걸어서 드나드는 쪽이기도 합니다." : "");
  }
  const base =
    tier === "high"
      ? `${d}도 앞길이 좋은 쪽(지구 안 길 중 ${topText(side.score)}) — 두 번째 출입구나 모퉁이 간판 자리로 좋습니다.`
      : tier === "mid"
        ? `${d}은 보통(지구 안 길 중 ${topText(side.score)}) — 보조 출입구나 작은 1층 가게 자리로 쓸 만합니다.`
        : `${d}은 지나가는 사람이 적을 쪽(뒤쪽) — 주차 출입구·짐 싣고 내리는 곳·업무동 출입구에 맞습니다.`;
  const passBy =
    tier !== "low" && side.choice_pct - front.choice_pct > 5
      ? ` 지나가는 사람만 보면 정면보다 많을 쪽(${topText(side.choice_pct)})이라, 지나가다 들르는 가게를 이쪽에 두는 것도 방법입니다.`
      : "";
  return base + passBy + (side.footway ? " 보행로에 닿아 걸어오는 손님이 들어오는 쪽이기도 합니다." : "");
}

/** 주 손님 — 실제로 계산한 요인(근처 아파트·학교, 역 거리, 800m 안 아파트 땅, 앞길 순서)에서만 문장을 만든다. */
function customerOf(lot: PlannedLot, frontTier: Tier | null): { core: string; extra: string; short: string } {
  const housing = lot.nearby.filter((n) => n.kind === "housing");
  const school = lot.nearby.some((n) => n.kind === "school");
  const f = lot.plan?.features;
  const stationM = f && f.station_known && (f.station_m ?? 3000) < 3000 ? f.station_m : null;
  const res800 = f ? (f.res_area_800 ?? 0) / 10000 : 0;
  const who: string[] = [];
  if (frontTier === "high") who.push("지나가는 사람");
  if (stationM != null && stationM <= 500) who.push("역 이용객");
  if (school) who.push("등하교 학생·학부모");
  let core: string;
  if (housing.length) {
    const ids = housing
      .slice(0, 2)
      .map((n) => n.lot_id)
      .join("·");
    core =
      `${NEARBY_LIMIT_M}m 안에 아파트(${ids})가 있어, 걸어오는 단지 주민이 주 손님인 땅입니다.` +
      (who.length ? ` ${josa(who.join("·"), "이", "가")} 그다음 손님입니다.` : "");
  } else {
    const list = who.length ? who.join("·") : "일부러 찾아오는 손님";
    core = `${NEARBY_LIMIT_M}m 안에 아파트가 없어, 동네 단지 주민보다 ${josa(list, "이", "가")} 주 손님인 땅입니다.`;
  }
  const extra = [
    stationM != null ? `가장 가까운 역까지는 약 ${Math.round(stationM).toLocaleString()}m(직선)입니다.` : "",
    !housing.length && res800 >= 10
      ? `800m 안에는 아파트 땅이 ${res800.toFixed(0)}ha 있어 조금 걸어오는 주민 손님도 기대할 수 있습니다.`
      : "",
  ]
    .filter(Boolean)
    .join(" ");
  const shortWho = [...(housing.length ? ["옆 아파트 주민"] : []), ...who];
  return { core, extra, short: shortWho.length ? shortWho.join("·") : "일부러 찾아오는 손님" };
}

/** 앞길 한마디 — 요약 문장 앞머리. */
/** 요약 문단 — 첫 문장만 굵게. 2026-11-07: 문단 통째 굵은 글씨라 390px에서 7줄이 다 굵어 읽기 무거웠다
 *  (첫 문장 = 순위·결론, 나머지 = 이유). 소리로 듣기는 원문 그대로 쓴다. */
function SummaryText({ text: full, lead }: { text: string; lead?: string }) {
  // 2026-11-07 UX 루프: 바로 위 제목이 「F10 (업무복합용지) — 정면은 어디…」인데 요약도
  // 「F10 (업무복합용지) — 상가 땅 12곳 중 10등…」으로 같은 이름을 되풀이해 390px에서 한 줄을 먹었다.
  // 화면에서만 앞머리 이름을 떼고, 소리로 듣기는 원문(이름 포함) 그대로.
  const text = lead && full.startsWith(lead) ? full.slice(lead.length) : full;
  const cut = text.indexOf(". ");
  if (cut < 0) return <p className="plan-summary"><strong>{text}</strong></p>;
  return (
    <p className="plan-summary">
      <strong>{text.slice(0, cut + 1)}</strong> {text.slice(cut + 2)}
    </p>
  );
}

function frontPhrase(tier: Tier | null, hasHousing: boolean): string {
  if (tier == null) return "길에 닿은 면이 없고,";
  if (tier === "high") return hasHousing ? "앞길도 좋고," : "앞길은 좋지만";
  return tier === "mid" ? "앞길은 보통이고," : "앞길은 조용한 편이고,";
}

/** 변 경계선(LineString/MultiLineString) → 카카오 경로들. */
function edgePaths(edge: PlannedLotSide["edge"]): [number, number][][] {
  if (!edge) return [];
  if (edge.type === "LineString") return [edge.coordinates as [number, number][]];
  if (edge.type === "MultiLineString") return edge.coordinates as [number, number][][];
  return [];
}

const KIND_LABEL = { school: "학교", housing: "주거", public: "공공·녹지" } as const;
// 근처 학교·아파트가 옆 상가에 주는 뜻 — 사용자 2026-10-24: 「학교가 옆 상가에 영향을 준다」.
const KIND_MEANING = {
  school: "등하교 시간에 학생·학부모가 지나감",
  housing: "단지 주민이 걸어서 오는 이웃 손님",
  public: "공원·문화·공공시설을 찾는 사람이 오가는 곳",
} as const;

interface PlanFile {
  roads: { coordinates: number[][]; road_type?: string; name?: string; width_m?: number | null }[];
  lots: { lot_id: string; geometry: { type: string; coordinates: unknown } }[];
}

type KakaoMapHandle = {
  setBounds: (bounds: unknown) => void;
  relayout: () => void;
  destroy?: () => void;
};

type KakaoShapeHandle = {
  setMap: (map: unknown) => void;
  setOptions: (options: { strokeWeight?: number; strokeColor?: string }) => void;
};

/** 계획안의 모든 좌표에서 지도 범위를 만든다 — 사용자가 찾아 들어가지 않게. */
function planBounds(plan: PlanFile): { minLat: number; maxLat: number; minLng: number; maxLng: number } | null {
  const points: [number, number][] = [];
  for (const road of plan.roads) {
    for (const [lng, lat] of road.coordinates) points.push([lng, lat]);
  }
  for (const lot of plan.lots) {
    for (const ring of outerRings(lot.geometry)) points.push(...ring);
  }
  if (!points.length) return null;
  const lats = points.map((p) => p[1]);
  const lngs = points.map((p) => p[0]);
  return {
    minLat: Math.min(...lats), maxLat: Math.max(...lats),
    minLng: Math.min(...lngs), maxLng: Math.max(...lngs),
  };
}

export function PlannedSitePanel({
  onOpenInFloorplan,
}: {
  /** 건물 평면도를 이 탭에 잘못 올렸을 때 그 파일을 평면도 탭으로 넘긴다. */
  onOpenInFloorplan?: (file: File) => void;
} = {}) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<KakaoMapHandle | null>(null);
  // 그리기 코드가 쓰는 지도 API — 한국 도면이면 카카오 `window.kakao.maps`, 밖이면 OSM/Leaflet 흉내(lib/osmMapEngine).
  const mapsRef = useRef<MapsApi | null>(null);
  const shapesRef = useRef<{ id: string; shape: KakaoShapeHandle }[]>([]);

  const [mapReady, setMapReady] = useState(false);
  // 지도(카카오 SDK)를 못 불러오면 오류 문장만 「길 구조 계산」 버튼 아래에 뜨고 다시 부를 길이
  // 새로고침뿐이었다 — 지도 자리 바로 밑에 「다시 불러오기」를 둔다. 누를 때마다 1씩 올려
  // 아래 효과를 다시 돈다 (2026-11-07 UX 자동 개선, 지도 탭·첫 화면과 같은 동작).
  const [mapFailed, setMapFailed] = useState<string | null>(null);
  const [mapAttempt, setMapAttempt] = useState(0);
  const [plan, setPlan] = useState<PlanFile | null>(null);
  const [planName, setPlanName] = useState("");
  const [result, setResult] = useState<PlannedSiteResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  // 파일·샘플을 읽다 난 오류는 파일 자리 바로 아래에 보인다 — 「길 구조 계산」 버튼 아래에 뜨면
  // 휴대폰에서 파일을 고른 자리에서 안 보였다 (2026-11-06 UX 자동 개선). 계산 오류는 버튼 아래 그대로.
  // 레이어를 골라 다시 읽다 난 오류는 「이 레이어로 다시 읽기」 바로 아래에 보인다 — 파일 칸 밑에만 뜨면
  // 휴대폰에서 그 단추(약 720px)를 누른 사람에겐 화면 밖이라 아무 일도 안 일어난 것처럼 보였다(2026-11-08).
  const [errorAt, setErrorAt] = useState<"file" | "layers" | "calc">("calc");
  function fileError(message: string, at: "file" | "layers" = "file") {
    setErrorAt(at);
    setError(message);
    setRetry(null);
  }
  // 서버 쪽 실패(5xx·연결 끊김·시간 초과)면 같은 일을 다시 할 「다시 시도」를 단다 — 배포 중 503이면
  // 빨간 문장만 남아 휴대폰에선 파일 창을 다시 열어 같은 도면을 찾아 골라야 했다. 도면 탓 400은
  // 다시 보내도 같으니 달지 않는다 (2026-11-08 UX 자동 개선, 평면도 441388a와 같은 규칙).
  const [retry, setRetry] = useState<(() => void) | null>(null);
  function retryIfServer(err: unknown, again: () => void) {
    const serverSide = !(err instanceof ApiClientError) || err.status >= 500;
    setRetry(serverSide ? () => again : null);
  }
  function errorBanner(alert?: boolean) {
    return (
      <div className="status-banner status-banner--error floorplan-error-row" role={alert ? "alert" : undefined}>
        <span>{error}</span>
        {retry && !extracting && !running && (
          <button type="button" className="map-error__retry" onClick={() => retry()}>
            다시 시도
          </button>
        )}
      </div>
    );
  }
  const [pickedId, setPickedId] = useState<string | null>(null);
  const [focusId, setFocusId] = useState<string | null>(null);
  // 샘플은 자리를 안다 — 좌표계 후보(당진·서해·봉화…)를 보이면 「샘플이 여러 개」로 읽히고,
  // 누르면 운정 도면이 그리로 옮겨져 엉터리로 계산된다(2026-10-24 사용자). 샘플일 땐 숨긴다.
  const [sample, setSample] = useState<SamplePlan | null>(null);
  const isSample = sample !== null;
  /** 「문2 (문화시설)」 — 샘플만. 원본에 이름이 없는 땅은 「용도 확인 중」. 한국 밖 샘플은 머리글자로. */
  const landUseOf = (id: string): string | null => {
    if (!sample) return null;
    if (!sample.abroad) return SAMPLE_LAND_USE[id] ?? "용도 확인 중";
    return ABROAD_LAND_USE.find(([prefix]) => id.startsWith(prefix))?.[1] ?? null;
  };
  const nameOf = (id: string): string => {
    const u = landUseOf(id);
    return u ? `${id} (${u})` : id;
  };
  /** 여러 땅 기호 + 조사 — 조사는 마지막 땅 기호의 받침으로.
   *  2026-11-06: 한 문장 안 목록은 기호만 쓴다. 용도까지 붙이면 「(F2-2 (일반상업용지)·주복6
   *  (주상복합용지)…)」처럼 괄호가 겹치고 조사가 「(문화시설)는」으로 붙어 390px에서 다섯 줄이 됐다.
   *  용도는 바로 아래 비교 카드마다 나온다. */
  const namesWith = (ids: string[], withB: string, withoutB: string): string => {
    const b = hasBatchim(ids[ids.length - 1] ?? "");
    return ids.join("·") + (b == null ? `${withB}(${withoutB})` : b ? withB : withoutB);
  };
  // 2026-10-24 검증: 도면 길만 넣으면 도면 끝에 길이 뻗은 남쪽 학교·아파트가 1·2위로 뜨고 F10 옆
  // 간선(도면 밖)이 빠진다. 주변 실제 도로를 넣으면 역 둘레 문2·F10·F1-2·F2-2가 1~4위 — 기본을 켠다.
  const [withExisting, setWithExisting] = useState(true);
  // 2026-10-27: 땅 색 기본은 「가게가 들어설 가능성」(신도시 10곳 학습, 맞게 고르는 비율 약 84%) —
  // 「앞길 유동인구」(길 모양만, 약 68%)는 고를 수 있게 남긴다.
  const [colorBy, setColorBy] = useState<"plan" | "flow">("plan");
  // DXF로 들어온 경우 — 좌표계를 바꾸거나 레이어를 고치면 같은 파일을 다시 올린다.
  const [dxfFile, setDxfFile] = useState<File | null>(null);
  const [extracted, setExtracted] = useState<PlannedSiteExtractResponse | null>(null);
  const [extracting, setExtracting] = useState(false);
  const [crsConfirmed, setCrsConfirmed] = useState(false);
  const [regionNames, setRegionNames] = useState<Record<string, string>>({});
  const [layerRoles, setLayerRoles] = useState<Record<string, LayerRole>>({});
  const [showLayers, setShowLayers] = useState(false);
  const needsCrsCheck = !!extracted && extracted.crs_candidates.length > 1 && !crsConfirmed;

  // ── 지도 한 번 만들기 ─────────────────────────────────────────────────
  // 2026-11-09(해커톤 01): 도면이 한국 밖(두바이·미국 샘플)이면 카카오 대신 OSM 타일(Leaflet)로 —
  // 카카오는 한국 밖 타일이 없어 흰 바탕만 떴다. 엔진이 바뀌면 지도를 새로 만들고 다시 그린다.
  const planCentre = useMemo(() => {
    const b = plan ? planBounds(plan) : null;
    return b ? { lat: (b.minLat + b.maxLat) / 2, lng: (b.minLng + b.maxLng) / 2 } : null;
  }, [plan]);
  const engine: "kakao" | "osm" = planCentre && !inKorea(planCentre.lat, planCentre.lng) ? "osm" : "kakao";
  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    let cancelled = false;
    setMapReady(false);
    setMapFailed(null);
    const load: Promise<MapsApi> =
      engine === "osm" ? loadOsmMaps() : loadKakaoMaps().then(() => window.kakao.maps as MapsApi);
    load
      .then((maps) => {
        if (cancelled || !boxRef.current) return;
        mapsRef.current = maps;
        const centre = planCentreRef.current ?? { lat: 37.716, lng: 126.727 }; // 기본은 샘플 자리(운정중앙역)
        mapRef.current = new maps.Map(boxRef.current, {
          center: new maps.LatLng(centre.lat, centre.lng),
          level: 6,
        });
        setMapReady(true);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error(err);
        setMapFailed("지도를 불러오지 못했습니다 — 인터넷 연결을 확인하고 다시 불러와 주세요.");
      });
    return () => {
      cancelled = true;
      mapRef.current?.destroy?.();
      mapRef.current = null;
      mapsRef.current = null;
      shapesRef.current = [];
      box.innerHTML = "";
    };
  }, [mapAttempt, engine]);
  const planCentreRef = useRef(planCentre);
  planCentreRef.current = planCentre;

  const lotsById = new Map((result?.lots || []).map((lot) => [lot.pnu, lot]));
  const picked = pickedId ? lotsById.get(pickedId) ?? null : null;
  const sideRank = makeRank(result?.lots || []);
  const planRankMap = planRanks(result?.lots || []);
  const pickedPlan = picked ? planRankMap.get(picked.pnu) : undefined;
  const pickedTier = picked?.sides[0] ? tierOf(sideRank(picked.sides[0].score)) : null;
  const pickedHasHousing = !!picked?.nearby.some((x) => x.kind === "housing");
  const pickedSummary = (() => {
    if (!picked || picked.kind !== "candidate") return "";
    const c = customerOf(picked, pickedTier);
    const head = pickedPlan
      ? `${nameOf(picked.pnu)} — 상가 땅 ${pickedPlan.total}곳 중 ${pickedPlan.rank}등(가게 들어설 가능성).`
      : `${nameOf(picked.pnu)} —`;
    return `${head} ${frontPhrase(pickedTier, pickedHasHousing)} ${c.core}${c.extra ? ` ${c.extra}` : ""} (예상)`;
  })();
  /** 옆 땅 판정 — 화면의 주 점수(가게 들어설 가능성 순위)로. 2026-10-27 소유자: F2-2가 2등인데 앞길만 견줘
   *  「약함」으로 나와 「2등인데 왜 약함?」으로 읽혔다. 앞길 비교는 작은 글씨로 남긴다. */
  const compareOf = (me: PlannedLot, other: PlannedLot): { label: string; color: string; sub: string } => {
    const flow = verdictOf(me, other);
    const flowText =
      flow === "better"
        ? `앞길은 ${me.pnu}보다 붐빌 쪽`
        : flow === "weaker"
          ? `앞길은 ${me.pnu}보다 조용한 쪽`
          : `앞길은 ${josa(me.pnu, "과", "와")} 비슷`;
    const a = planRankMap.get(me.pnu);
    const b = planRankMap.get(other.pnu);
    if (!a || !b) return { label: `앞길 ${VERDICT[flow].label}`, color: VERDICT[flow].color, sub: `${flowText} (예상)` };
    const v: Verdict = b.rank < a.rank ? "better" : b.rank > a.rank ? "weaker" : "similar";
    // 칸 이름이 이미 「F10과 비교」라 「F10보다 유리」는 같은 이름을 한 칸에 세 번 말했다
    // (칸 이름·결론·작은 글씨). 결론에서 이름을 뺀다 (2026-11-08 UX 자동 개선).
    const label = v === "better" ? "더 유리" : v === "weaker" ? "더 불리" : "비슷";
    return { label, color: VERDICT[v].color, sub: `${b.rank}등 vs ${a.rank}등 · ${flowText} (예상)` };
  };
  const compareSummary = (() => {
    if (!picked || !pickedPlan) return "";
    const others = picked.neighbors.flatMap((n) => {
      const r = planRankMap.get(n.lot_id);
      return r ? [{ id: n.lot_id, rank: r.rank }] : [];
    });
    if (!others.length) return "";
    const higher = others.filter((x) => x.rank < pickedPlan.rank).map((x) => x.id);
    const lower = others.filter((x) => x.rank > pickedPlan.rank).map((x) => x.id);
    const parts: string[] = [];
    if (higher.length) parts.push(`${namesWith(higher, "은", "는")} 가게 들어설 가능성이 ${picked.pnu}보다 높고`);
    if (lower.length) parts.push(`${namesWith(lower, "은", "는")} 낮습니다`);
    const all = (ids: string[]) => ids.length === others.length;
    let head = `${nameOf(picked.pnu)} 옆 상가 땅 ${others.length}곳 중 ${parts.join(", ")}.`;
    if (!lower.length) head = head.replace(/높고\.$/, "높습니다.");
    if (all(higher))
      head = `${nameOf(picked.pnu)} 옆 상가 땅 ${others.length}곳(${higher.join("·")})은 모두 가게 들어설 가능성이 ${picked.pnu}보다 높습니다.`;
    if (all(lower))
      head = `${nameOf(picked.pnu)} 옆 상가 땅 ${others.length}곳(${lower.join("·")})은 모두 가게 들어설 가능성이 ${picked.pnu}보다 낮습니다.`;
    if (!parts.length) head = `${picked.pnu} 옆 상가 땅 ${others.length}곳은 모두 ${josa(picked.pnu, "과", "와")} 같은 순위입니다.`;
    // 같은 순위 옆 땅이 높다·낮다 어느 쪽에도 안 들어가 문장에서 말없이 빠졌다 — 「3곳 중」이라
    // 해 놓고 둘만 말해 셋째가 어디 갔는지 알 수 없었다 (2026-11-07 UX 자동 개선).
    const same = others.filter((x) => x.rank === pickedPlan.rank).map((x) => x.id);
    if (parts.length && same.length)
      head += ` ${namesWith(same, "은", "는")} ${josa(picked.pnu, "과", "와")} 같은 순위입니다.`;
    const tail =
      `${josa(picked.pnu, "은", "는")} ${frontPhrase(pickedTier, pickedHasHousing)}` +
      `${pickedHasHousing ? "" : ` ${NEARBY_LIMIT_M}m 안에 아파트가 없어`} 주 손님은 ${customerOf(picked, pickedTier).short}입니다`;
    return `${head} ${tail} (예상).`;
  })();
  const planCell = (lot: PlannedLot) => {
    const r = planRankMap.get(lot.pnu);
    if (!lot.plan || !r) return "—";
    return (
      <>
        <strong style={{ color: rampColor(r.t) }}>
          {r.total}곳 중 {r.rank}등
        </strong>
        <br />
        <small>비슷한 블록 가게 약 {Math.round(lot.plan.expected_stores)}곳</small>
      </>
    );
  };

  /** 지도를 범위에 맞춘다. 지도는 숨겨진 탭 안에서 만들어져 처음엔 크기를 0으로 알고, 범례가
   *  끼어들면 자리도 바뀐다 — 그대로 맞추면 한반도 전체나 엉뚱한 곳이 잡혔다(2026-10-24 사용자).
   *  그래서 맞추려던 범위를 기억해 두고, 지도 상자 크기가 바뀔 때마다 다시 재고 다시 맞춘다. */
  const targetRef = useRef<ReturnType<typeof planBounds>>(null);
  // 계산이 끝나도 휴대폰에선 결과(획지 고르기·지도)가 접힌 아래에서 시작해 「눌렀는데 아무 일도
  // 없다」로 읽혔다 — 결과가 새로 오면 그 자리가 화면 밖일 때만 내려 준다 (2026-11-06 UX 자동 개선, 390px).
  const pickRef = useRef<HTMLLabelElement | null>(null);
  const runBtnRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    const el = pickRef.current;
    if (!result || !el || typeof window === "undefined") return;
    const top = el.getBoundingClientRect().top;
    if (top < 0 || top > window.innerHeight * 0.6) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [result]);
  // 「옆 땅과 견주면」에서 다른 땅을 누르면 지도·정면 설명은 1,000~2,800px 위에서 바뀌는데 화면은 표에 머물러
  // 「바꿔 봅니다」가 무엇을 바꿨는지 안 보였다 — 표에서 고른 때만 그 땅 설명 카드로 올라간다(지도·select로
  // 고를 땐 그대로). 표 자리를 지키고 「위로 가기」 링크만 두는 안은 한 번 더 눌러야 해 버렸다
  // (2026-11-06 UX 자동 개선, 390·1280).
  const detailRef = useRef<HTMLDivElement | null>(null);
  const jumpToDetailRef = useRef(false);
  useEffect(() => {
    if (!jumpToDetailRef.current) return;
    jumpToDetailRef.current = false;
    detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [pickedId]);
  const applyTarget = useCallback(() => {
    const map = mapRef.current;
    const bounds = targetRef.current;
    const box = boxRef.current;
    const maps = mapsRef.current;
    if (!map || !maps || !bounds || !box || !box.offsetWidth || !box.offsetHeight) return;
    map.relayout();
    map.setBounds(
      new maps.LatLngBounds(
        new maps.LatLng(bounds.minLat, bounds.minLng),
        new maps.LatLng(bounds.maxLat, bounds.maxLng),
      ),
    );
  }, []);
  const fitTo = useCallback(
    (bounds: ReturnType<typeof planBounds>) => {
      targetRef.current = bounds;
      requestAnimationFrame(applyTarget);
    },
    [applyTarget],
  );
  useEffect(() => {
    const box = boxRef.current;
    if (!mapReady || !box || typeof ResizeObserver === "undefined") return;
    let last = "";
    const observer = new ResizeObserver(() => {
      const size = `${box.offsetWidth}x${box.offsetHeight}`;
      if (size === last) return;
      last = size;
      applyTarget();
    });
    observer.observe(box);
    return () => observer.disconnect();
  }, [mapReady, applyTarget]);

  // 획지를 고를 때마다 다시 그리지만 지도 범위는 도면이 바뀔 때만 맞춘다 — 누를 때마다 튀지 않게.
  useEffect(() => {
    if (mapReady && plan) fitTo(planBounds(plan));
  }, [mapReady, plan, fitTo]);

  // 획지를 고르면 그 땅과 이웃이 꽉 차게 — 지구 전체로 보면 변 색이 안 보인다.
  useEffect(() => {
    if (!mapReady || !plan || !pickedId || !result) return;
    const me = result.lots.find((l) => l.pnu === pickedId);
    if (!me) return;
    const ids = new Set([pickedId, ...me.neighbors.map((n) => n.lot_id)]);
    fitTo(planBounds({ roads: [], lots: plan.lots.filter((l) => ids.has(l.lot_id)) }));
  }, [mapReady, plan, result, pickedId, fitTo]);

  const draw = useCallback(() => {
    const map = mapRef.current;
    const maps = mapsRef.current;
    if (!map || !maps || !plan) return;
    // 아래 그리기 코드는 카카오 API 모양 그대로 — 한국 밖이면 같은 모양의 OSM/Leaflet 흉내가 들어온다.
    const kakao = { maps };

    for (const { shape } of shapesRef.current) shape.setMap(null);
    shapesRef.current = [];

    // 2026-11-06 소유자: 「블록 단위가 아니라 길이 더 구체적으로 보여야 하고, 블록 안 길마다
    // 좋은지 나쁜지 나와야 한다」. 계산이 끝났으면 길 구간마다 사람 흐름 값으로 칠한다(땅 색과 같은
    // 파랑→빨강). 땅은 그 위에서 옅게 — 길이 주인공이다. 계산 전에는 종전대로 회색 도면 길.
    const scoredRoads = result?.roads ?? [];
    for (const road of scoredRoads) {
      const line = new kakao.maps.Polyline({
        path: road.coordinates.map(([lng, lat]) => new kakao.maps.LatLng(lat, lng)),
        strokeWeight: road.planned ? 5 : 3,
        strokeColor: rampColor(Math.max(0, Math.min(1, road.score / 100))),
        strokeOpacity: road.planned ? 0.95 : 0.55,
        zIndex: 6,
      });
      line.setMap(map);
      shapesRef.current.push({ id: "road-scored", shape: line });
    }
    // 계획 도로 — 획지 아래 깔리면 안 보이므로 굵게, 위에 그린다.
    for (const road of scoredRoads.length ? [] : plan.roads) {
      const line = new kakao.maps.Polyline({
        path: road.coordinates.map(([lng, lat]) => new kakao.maps.LatLng(lat, lng)),
        // 2026-10-24: 진한 6px였더니 지도가 「대부분 검정」으로 보였다(사용자). 길은 배경이다.
        strokeWeight: road.road_type === "footway" ? 2 : 3,
        strokeColor: "#94a3b8",
        strokeOpacity: 0.7,
        zIndex: 5,
      });
      line.setMap(map);
      shapesRef.current.push({ id: `road-${road.name ?? ""}`, shape: line });
    }

    const scored = new Map((result?.lots || []).map((lot) => [lot.pnu, lot]));
    const me = pickedId ? scored.get(pickedId) : undefined;
    // 색은 점수가 아니라 이 도면 안 **순서**로 — 점수는 80~97에 몰려 있어 그대로 칠하면 거의 다
    // 빨강이었다. 순서로 펴야 파랑~빨강이 고르게 나와 차이가 눈에 보인다.
    // 순서는 상가를 낼 수 있는 땅끼리만 — 학교·아파트 땅은 회색으로 자리만 보인다.
    const rankOf = makeRank(result?.lots || []);
    const ranks = planRanks(result?.lots || []);
    const usePlan = colorBy === "plan" && ranks.size > 0;
    const noHover = typeof window.matchMedia === "function" && window.matchMedia("(hover: none)").matches;
    const neighbourIds = new Set((me?.neighbors ?? []).map((n) => n.lot_id));
    for (const lot of plan.lots) {
      const info = scored.get(lot.lot_id);
      const isMe = lot.lot_id === pickedId;
      const value = info?.kind === "candidate" ? info.best_side_score : null;
      const planT = ranks.get(lot.lot_id)?.t;
      const color = usePlan
        ? planT != null
          ? rampColor(planT)
          : UNGRADED_COLOR
        : value != null
          ? rampColor(rankOf(value))
          : UNGRADED_COLOR;
      // 상가를 못 내는 땅(학교·주거·공공·녹지)은 회색으로 자리만 — 순위·색에서 뺀다(2026-11-06 소유자:
      // 「상권 분석이니까 상업용지 이외의 것은 회색으로」). 대신 이름은 늘 보여서 손님이 어디서
      // 오는지(아파트·학교) 읽히게 한다.
      const greyed = !!info && info.kind !== "candidate";
      const graded = usePlan ? planT != null : value != null;
      for (const ring of outerRings(lot.geometry)) {
        const poly = new kakao.maps.Polygon({
          path: ring.map(([lng, lat]) => new kakao.maps.LatLng(lat, lng)),
          strokeWeight: 1,
          strokeColor: greyed ? "#cbd5e1" : "#ffffff",
          fillColor: greyed ? "#cbd5e1" : color,
          // 길을 칠했으면 땅은 옅게 — 길 색이 묻히지 않게.
          fillOpacity: greyed ? 0.45 : graded ? (scoredRoads.length ? 0.5 : 0.72) : info ? 0.35 : 0.2,
          zIndex: isMe ? 3 : 2,
        });
        poly.setMap(map);
        shapesRef.current.push({ id: lot.lot_id, shape: poly });
        if (info) {
          kakao.maps.event.addListener(poly, "click", () => setPickedId(lot.lot_id));
        }
        // 땅 이름 툴팁 — 「문이 뭐야?」(2026-10-27 소유자). 샘플이면 용지 이름까지.
        const centre = ring.reduce((acc, [lng, lat]) => [acc[0] + lng / ring.length, acc[1] + lat / ring.length], [0, 0]);
        const tip = new kakao.maps.CustomOverlay({
          position: new kakao.maps.LatLng(centre[1], centre[0]),
          content: `<div class="plan-lot-tip">${nameOf(lot.lot_id)}</div>`,
          yAnchor: 1.2,
          zIndex: 12,
        });
        shapesRef.current.push({ id: `tip-${lot.lot_id}`, shape: tip });
        if (greyed) {
          // 회색 땅 이름표는 늘 — 「아파트 A21」「학교 초5」처럼 무엇인지 보이게.
          const label = new kakao.maps.CustomOverlay({
            position: new kakao.maps.LatLng(centre[1], centre[0]),
            content: `<div class="plan-lot-label">${KIND_LABEL[info!.kind as keyof typeof KIND_LABEL]} ${nameOf(lot.lot_id)}</div>`,
            yAnchor: 0.5,
            zIndex: 4,
          });
          label.setMap(map);
          shapesRef.current.push({ id: `label-${lot.lot_id}`, shape: label });
        } else if (noHover && (isMe || neighbourIds.has(lot.lot_id))) {
          // 휴대폰엔 마우스 올리기가 없어 상가 땅 이름이 하나도 안 보였다 — 회색 땅(못 고르는 땅)
          // 이름만 보이고 「옆 땅과 견주면」의 F2-2·주복6이 지도 어디인지 몰랐다. 터치 화면에선
          // 고른 땅과 견주는 옆 땅 이름을 늘 띄운다(2026-11-09 UX 자동 개선). 나머지는 눌러 고르면 뜬다.
          tip.setMap(map);
        } else {
          kakao.maps.event.addListener(poly, "mouseover", () => tip.setMap(map));
          kakao.maps.event.addListener(poly, "mouseout", () => tip.setMap(null));
        }
        // 고른 땅 테두리는 따로 선으로 — 다각형 테두리는 길 선(zIndex 5) 아래 깔려 안 보였다(2026-10-26 사용자).
        if (isMe) {
          const outline = new kakao.maps.Polyline({
            path: [...ring, ring[0]].map(([lng, lat]) => new kakao.maps.LatLng(lat, lng)),
            strokeWeight: 12,
            strokeColor: "#000000",
            strokeOpacity: 1,
            zIndex: 7,
          });
          outline.setMap(map);
          shapesRef.current.push({ id: `outline-${lot.lot_id}`, shape: outline });
        }
      }
    }

    // 고른 획지의 변 — 그 변이 닿은 길의 세기로 굵게 칠한다. 길보다 위에.
    for (const side of me?.sides || []) {
      for (const path of edgePaths(side.edge)) {
        const line = new kakao.maps.Polyline({
          path: path.map(([lng, lat]) => new kakao.maps.LatLng(lat, lng)),
          // 검은 테두리(12px) 가운데에 색 줄 — 테두리도 변 색도 같이 보이게
          strokeWeight: 6,
          strokeColor: sideStrength(rankOf(side.score)).color,
          strokeOpacity: 1,
          zIndex: 8,
        });
        line.setMap(map);
        shapesRef.current.push({ id: `side-${side.direction}`, shape: line });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- nameOf는 sample만 본다
  }, [plan, result, pickedId, colorBy, sample]);

  useEffect(() => {
    if (mapReady) draw();
  }, [mapReady, draw]);

  // 좌표계 후보마다 「어느 동네에 떨어지나」를 붙인다. 위경도 숫자만으로는 사람이
  // 맞는지 판단할 수 없다 — "세종시 연기면"과 "전북 완주군"이면 바로 안다.
  useEffect(() => {
    if (!mapReady || engine !== "kakao" || !extracted?.crs_candidates.length) return;
    const Geocoder = window.kakao?.maps?.services?.Geocoder;
    if (!Geocoder) return;
    const geocoder = new Geocoder();
    let cancelled = false;
    for (const c of extracted.crs_candidates) {
      geocoder.coord2RegionCode(c.lng, c.lat, (rows: { region_type: string; address_name: string }[], status: string) => {
        if (cancelled || status !== window.kakao.maps.services.Status.OK || !rows?.length) return;
        const name = (rows.find((r) => r.region_type === "H") ?? rows[0]).address_name;
        setRegionNames((prev) => ({ ...prev, [c.epsg]: name }));
      });
    }
    return () => {
      cancelled = true;
    };
  }, [mapReady, engine, extracted]);

  function applyPlan(next: PlanFile, name: string) {
    // 샘플 표시는 새 도면을 실제로 읽었을 때만 내린다. 파일을 고르는 순간 내리면 사진·DWG처럼
    // 거절된 파일 하나로 샘플 화면이 「직접 올린 도면」으로 바뀌어, 샘플엔 숨기는 좌표계 후보
    // (누르면 운정 도면이 엉뚱한 곳으로 옮겨짐)·레이어 고치기가 나타나고 획지 용도 이름이
    // 사라졌다 (2026-11-08 UX 자동 개선). loadSample은 이 뒤에 다시 true로 켠다.
    setSample(null);
    setPlan(next);
    setPlanName(name);
    setResult(null);
    setPickedId(null);
    setFocusId(null);
    setError("");
  }

  function loadPlan(text: string, name: string) {
    try {
      const parsed = JSON.parse(text) as PlanFile;
      if (!Array.isArray(parsed.roads) || !Array.isArray(parsed.lots)) {
        throw new Error("roads와 lots가 있어야 합니다.");
      }
      setDxfFile(null);
      setExtracted(null);
      applyPlan(parsed, name);
    } catch (err) {
      // JSON.parse의 영어 오류(「Unexpected token …」)를 그대로 보이지 않는다 (2026-11-06 UX 자동 개선).
      const detail = err instanceof SyntaxError ? "JSON 형식이 아닙니다." : err instanceof Error ? err.message : "";
      fileError(`계획안 파일을 읽지 못했습니다${detail ? ` — ${detail}` : "."}`);
    }
  }

  /** DXF를 서버에 보내 길·획지를 꺼낸다. 좌표계·레이어를 바꿔 다시 읽을 때도 이 길이다. */
  async function runExtract(
    file: File,
    opts?: { crs?: string; roles?: Record<string, LayerRole>; errorAt?: "layers" },
  ): Promise<boolean> {
    setExtracting(true);
    setError("");
    setRetry(null);
    try {
      const roles = opts?.roles;
      const response = await api.plannedSiteExtract(file, {
        crs: opts?.crs,
        roadLayers: roles ? Object.keys(roles).filter((n) => roles[n] === "road") : undefined,
        lotLayers: roles ? Object.keys(roles).filter((n) => roles[n] === "lot") : undefined,
      });
      setDxfFile(file);
      setExtracted(response);
      if (!opts?.crs) {
        setCrsConfirmed(false);
        setRegionNames({});
      }
      if (!roles) {
        setLayerRoles(Object.fromEntries(response.layers.map((l) => [l.name, l.guess])));
      }
      applyPlan({ roads: response.roads, lots: response.lots }, file.name);
      if (!response.roads.length || !response.lots.length) setShowLayers(true);
      return true;
    } catch (err) {
      fileError(err instanceof Error ? err.message : "도면을 읽지 못했습니다.", opts?.errorAt ?? "file");
      retryIfServer(err, () => void runExtract(file, opts));
      return false;
    } finally {
      setExtracting(false);
    }
  }

  async function handleFile(file: File | null) {
    if (!file) return;
    if (/\.(dxf|pdf)$/i.test(file.name)) {
      await runExtract(file);
    } else if (/\.dwg$/i.test(file.name)) {
      fileError("DWG는 아직 못 받습니다. CAD에서 「다른 이름으로 저장 → DXF」로 바꿔 올려 주세요.");
    } else if (/\.json$/i.test(file.name)) {
      loadPlan(await file.text(), file.name);
    } else {
      // 사진·PDF를 고르면 JSON으로 읽으려다 「Unexpected token …」 영어 오류가 났다 — 휴대폰
      // 파일 고르기는 accept를 무시하고 사진첩을 먼저 연다 (2026-11-06 UX 자동 개선, 390px).
      fileError("계획 도면 DXF 또는 CAD에서 뽑은 PDF만 읽습니다. 사진·이미지는 못 읽으니 공고에 붙은 도면 파일을 올려 주세요.");
    }
  }

  async function loadSample(which: SamplePlan) {
    setExtracting(true);
    try {
      const res = await fetch(which.path);
      if (!res.ok) throw new Error();
      const blob = await res.blob();
      // 샘플은 자리를 우리가 안다(운정중앙역 5174, 두바이 UTM 40N, 플로리다 UTM 17N) — 위치 확인을
      // 묻지 않고 바로 계산할 수 있게.
      setRegionNames({});
      const ok = await runExtract(new File([blob], which.fileName, { type: "application/dxf" }), {
        crs: which.crs,
      });
      if (!ok) {
        // 샘플을 다시 시도하면 샘플 표시·F10 고르기까지 다시 해야 한다 — runExtract만 다시 부르면
        // 「직접 올린 도면」처럼 위치 확인을 묻는다.
        setRetry((prev) => (prev ? () => void loadSample(which) : null));
        return;
      }
      setCrsConfirmed(true);
      setFocusId(which.focus);
      setSample(which);
    } catch {
      setExtracting(false);
      fileError("샘플 도면을 불러오지 못했습니다. 잠시 뒤 다시 시도해 주세요.");
      setRetry(() => () => void loadSample(which));
    }
  }

  function chooseCrs(epsg: string) {
    if (!dxfFile || epsg === extracted?.crs) return;
    setCrsConfirmed(false);
    void runExtract(dxfFile, { crs: epsg, roles: layerRoles });
  }

  async function run() {
    if (!plan) return;
    setRunning(true);
    setError("");
    setRetry(null);
    try {
      const bounds = planBounds(plan);
      const response = await api.plannedSite({
        roads: plan.roads.map((r) => ({
          coordinates: r.coordinates,
          road_type: r.road_type === "footway" ? "footway" : "residential",
          name: r.name ?? "",
          width_m: r.width_m ?? null,
        })),
        lots: plan.lots,
        // 2026-10-22: 총액 배분을 내지 않는다(소유자 결정). 총액을 나눈 몫이라도
        // 「이 획지 ○억」으로 찍혀 나가면 땅값을 매긴 것으로 읽힌다 — 이 화면과
        // 보고서는 등급과 「지구 안 상위 몇 %」만 말한다.
        total_amount_manwon: null,
        // 한국 밖 샘플은 주변 데이터가 없어 끈다(서버도 한국 밖이면 알아서 뺀다).
        include_existing_roads: sample ? !sample.abroad : withExisting,
        centre_lat: bounds ? (bounds.minLat + bounds.maxLat) / 2 : null,
        centre_lng: bounds ? (bounds.minLng + bounds.maxLng) / 2 : null,
      });
      setResult(response);
      // 샘플은 F10을 골라 둔다. 직접 올린 도면은 사람이 지도에서 누른다.
      // 고를 땅을 안 정한 샘플(두바이·미국)은 상가 후보 가운데 가장 좋은 땅을 골라 둔다 — 빈 화면으로
      // 「어디를 누르라는 건지」가 먼저 나오지 않게.
      if (focusId && response.lots.some((l) => l.pnu === focusId)) {
        setPickedId(focusId);
      } else if (sample && !focusId) {
        const best = [...response.lots]
          .filter((l) => l.kind === "candidate")
          .sort((a, b) => (b.percentile ?? 0) - (a.percentile ?? 0))[0];
        setPickedId(best?.pnu ?? null);
      } else {
        setPickedId(null);
      }
    } catch (err) {
      setErrorAt("calc");
      setError(err instanceof Error ? err.message : "분석에 실패했습니다.");
      retryIfServer(err, () => void run());
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="floorplan-panel">
      <div className="card">
        <h2>신도시 계획도면 — 이 땅 vs 옆 땅</h2>
        <p className="floorplan-help-text">
          아직 지어지지 않은 길 위에서 <strong>고른 획지와 옆 획지를 견줍니다.</strong> LH 공고에 붙어 오는{" "}
          <strong>계획 도면(DXF·PDF)</strong>을 올리면 계획 도로와 획지를 찾아, 땅의 변마다 어떤 길에 닿아 왜 좋은지 보여
          줍니다 — <strong>지적도에 없는 땅도 됩니다.</strong> 한국 밖 도면도 받습니다(좌표계를 UTM 등으로
          지정하면 도면 안 길만으로 계산).
        </p>

        {/* 브라우저 기본 「Choose File / No file chosen」은 무엇을 올리는지 말하지 않고 휴대폰에서
            작다 — 건축 평면도 탭과 같은 올리기 자리로 (2026-11-06 UX 자동 개선, 390px). */}
        <label className={`floorplan-dropzone${extracting ? " floorplan-dropzone--busy" : ""}`}>
          <input
            type="file"
            accept=".dxf,.pdf,application/pdf,.json,application/json"
            onChange={(e) => {
              void handleFile(e.target.files?.[0] || null);
              // 같은 파일을 다시 골라도 onChange가 오게 비운다(잘못 고른 뒤 고쳐 저장한 경우).
              e.target.value = "";
            }}
            className="floorplan-file-input"
          />
          <span className="floorplan-dropzone-title">{plan && !isSample ? planName : "계획 도면 DXF·PDF 파일 선택"}</span>
          <span className="floorplan-dropzone-sub">
            {plan && !isSample ? (
              "다른 파일을 고르려면 다시 누르세요"
            ) : (
              // 휴대폰엔 끌어다 놓기가 없다 — 평면도 탭과 같이 터치 화면에선 숨긴다 (2026-11-06).
              <>
                눌러서 고르세요<span className="floorplan-desktop-hint"> · 끌어다 놓아도 됩니다</span> · .dxf · .pdf
              </>
            )}
          </span>
        </label>
        {error && errorAt === "file" && errorBanner()}
        <div className="floorplan-sample-row">
          {SAMPLE_PLANS.map((s) => (
            <button key={s.id} type="button" className="secondary-button" onClick={() => void loadSample(s)} disabled={extracting}>
              {s.label}
            </button>
          ))}
          {plan && (
            <span className="floorplan-line-count">
              {planName} · 길 {plan.roads.length}개 · 획지 {plan.lots.length}곳
            </span>
          )}
        </div>
        {/* 도면·샘플을 읽은 뒤에도 남아, 390px에선 파일 이름과 「이렇게 읽었습니다」 사이를 두 줄
            차지했다 — 아직 아무 도면도 안 읽었을 때만 보인다. DWG를 고르면 위 오류 문장이 같은 말을
            한다 (2026-11-08 UX 자동 개선). */}
        {!plan && (
          <p className="floorplan-scale-hint">
            스캔한 PDF와 DWG는 못 읽습니다.
          </p>
        )}
        {extracting && <div className="status-banner status-banner--loading">도면에서 계획 도로와 획지를 찾는 중입니다…</div>}

        {sample && extracted && <p className="floorplan-help-text">{sample.about}</p>}

        {extracted && !isSample && (
          <>
            {/* 좌표계 후보가 여럿이면 바로 아래 「지도 위치가 맞는지 확인해 주세요」 상자가 같은 말을
                쉬운 말로 한다 — 「좌표계를 EPSG:5174로 보고 37.7164N…」 메모는 겹치고 용어만 어려워
                뺀다 (2026-11-08 UX 자동 개선). */}
            {(() => {
              const notes =
                extracted.crs_candidates.length > 1
                  ? extracted.notes.filter((note) => !note.startsWith("좌표계를 "))
                  : extracted.notes;
              // 「선 1개를 뺐습니다」「9조각을 이어 붙여」 같은 과정 설명은 사람이 할 일이 없는데 화면을
              // 여섯 줄 차지했다 — 할 일이 있는 말만 펴 두고 나머지는 접는다 (2026-11-09 소유자 「너무 기계적」).
              const todo = notes.filter((note) => /주세요|못했|못 찾|건물 평면도라면/.test(note));
              const how = notes.filter((note) => !todo.includes(note));
              return (
                <>
                  {!!todo.length && (
                    <ul className="auto-notes-banner">
                      {todo.map((note) => (
                        <li key={note}>{note}</li>
                      ))}
                      {/* 건물 평면도를 이 탭에 올리면 글만 있어 탭을 옮겨 같은 파일을 다시 골라야 했다 —
                          고른 파일을 그대로 평면도 탭으로 넘긴다 (2026-11-08 UX 자동 개선). */}
                      {onOpenInFloorplan && dxfFile && notes.some((note) => note.includes("건물 평면도라면")) && (
                        <li className="plan-note-action">
                          <button type="button" className="secondary-button" onClick={() => onOpenInFloorplan(dxfFile)}>
                            이 도면을 「건축 평면도 분석」에서 열기
                          </button>
                        </li>
                      )}
                    </ul>
                  )}
                  {!!how.length && (
                    <details className="plan-note-how">
                      <summary>어떻게 읽었는지 보기</summary>
                      <ul>
                        {how.map((note) => (
                          <li key={note}>{note}</li>
                        ))}
                      </ul>
                    </details>
                  )}
                </>
              );
            })()}

            {extracted.crs_candidates.length > 1 && (
              <div className={`plan-crs-check${crsConfirmed ? " plan-crs-check--ok" : ""}`}>
                <strong>{crsConfirmed ? "위치 확인됨" : "지도 위치가 맞는지 확인해 주세요"}</strong>
                <p className="floorplan-scale-hint">지도에서 도면이 제자리에 놓였는지 보고 골라 주세요.</p>
                <div className="plan-crs-options" role="radiogroup" aria-label="좌표계 후보">
                  {extracted.crs_candidates.map((c) => (
                    <label key={c.epsg} className="plan-crs-option">
                      <input
                        type="radio"
                        name="plan-crs"
                        checked={extracted.crs === c.epsg}
                        disabled={extracting}
                        onChange={() => chooseCrs(c.epsg)}
                      />
                      <span>
                        {regionNames[c.epsg] ?? `${c.lat.toFixed(3)}N, ${c.lng.toFixed(3)}E`}
                      </span>
                    </label>
                  ))}
                </div>
                {!crsConfirmed && (
                  <button
                    type="button"
                    className="primary-button"
                    disabled={extracting}
                    onClick={() => setCrsConfirmed(true)}
                  >
                    이 위치가 맞습니다
                  </button>
                )}
              </div>
            )}

            <div className="floorplan-result-header">
              <span className="floorplan-scale-hint">
                레이어 {extracted.layers.length}개 중 길{" "}
                {extracted.layers.filter((l) => l.guess === "road").length}개 · 획지{" "}
                {extracted.layers.filter((l) => l.guess === "lot").length}개로 읽었습니다.
              </span>
              <button type="button" className="secondary-button" onClick={() => setShowLayers((v) => !v)}>
                {showLayers ? "접기" : "레이어 고치기"}
              </button>
            </div>
            {showLayers && (
              <>
                <div className="floorplan-room-table-wrap">
                  <table className="floorplan-room-table floorplan-layer-table">
                    <thead>
                      <tr>
                        <th>레이어</th>
                        <th>선</th>
                        <th>역할</th>
                      </tr>
                    </thead>
                    <tbody>
                      {extracted.layers.map((layer) => (
                        <tr key={layer.name}>
                          <td>{layer.name}</td>
                          <td>{layer.entity_count.toLocaleString()}</td>
                          <td>
                            <select
                              className="floorplan-layer-select"
                              aria-label={`${layer.name} 레이어 역할`}
                              value={layerRoles[layer.name] ?? "other"}
                              onChange={(e) =>
                                setLayerRoles((prev) => ({ ...prev, [layer.name]: e.target.value as LayerRole }))
                              }
                            >
                              <option value="road">계획 도로</option>
                              <option value="lot">획지선</option>
                              <option value="other">안 씀</option>
                            </select>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={extracting || !dxfFile}
                  onClick={() => dxfFile && runExtract(dxfFile, { crs: extracted.crs || undefined, roles: layerRoles, errorAt: "layers" })}
                >
                  이 레이어로 다시 읽기
                </button>
                {error && errorAt === "layers" && errorBanner(true)}
              </>
            )}
          </>
        )}

        {!isSample && (
          <div className="floorplan-scale-row">
            <label className="floorplan-excluded-toggle">
              <input
                type="checkbox"
                checked={withExisting}
                onChange={(e) => setWithExisting(e.target.checked)}
              />
              주변 기존 도로도 함께 계산 (권장 — 끄면 도면 가장자리 땅이 실제보다 높거나 낮게 나옵니다)
            </label>
          </div>
        )}

        <button
          ref={runBtnRef}
          type="button"
          className="primary-button floorplan-analyze-btn"
          onClick={run}
          disabled={!plan || !plan.roads.length || !plan.lots.length || running || extracting || needsCrsCheck}
          title={needsCrsCheck ? "먼저 지도 위치를 확인해 주세요" : undefined}
        >
          {/* 처음 들어오면 도면이 없어 단추가 흐리게 잠겨 있는데 왜 안 눌리는지 말이 없었다 — 할 일을
              단추 글자로 말한다(위치 확인과 같은 방식) (2026-11-07 UX 자동 개선, 390px). */}
          {running
            ? "계산 중…"
            : needsCrsCheck
              ? "위의 「이 위치가 맞습니다」를 먼저 눌러 주세요"
              : !plan && !extracting
                ? "위에서 도면이나 샘플을 먼저 골라 주세요"
                : plan && !extracting && (!plan.roads.length || !plan.lots.length)
                  ? // 도로나 획지를 못 찾으면 단추가 「길 구조 계산하고…」인 채 잠기고, 왜 잠겼는지는 지도
                    // (약 480px) 아래 상태 문장에만 있어 휴대폰에선 단추 옆에서 안 보였다 — 빠진 것과 할 일을
                    // 단추 글자로 말한다 (2026-11-08 UX 자동 개선, 390px).
                    `${!plan.roads.length && !plan.lots.length ? "길·획지를" : !plan.roads.length ? "계획 도로를" : "획지를"} 못 찾아 잠김 — 위 「레이어 고치기」에서 골라 주세요`
                  : "길 구조 계산하고 획지 견주기"}
        </button>
        {error && errorAt === "calc" && errorBanner()}

        {result && (
          <label className="plan-pick" ref={pickRef}>
            볼 획지
            <select value={pickedId ?? ""} onChange={(e) => setPickedId(e.target.value || null)}>
              {/* 「…여기서 고르세요」는 390px 칸(글자 자리 약 212px)에 234px라 「고르서」로 잘렸다 —
                  직접 올린 도면은 계산 뒤 아무 땅도 안 골라 이 문구가 처음 보인다 (2026-11-08 UX 자동 개선). */}
              <option value="">지도에서 누르거나 고르세요</option>
              {/* 상가 땅과 학교·주거·공공 땅이 한 목록에 섞여 「고2 (학교·고등학교) · 14,824㎡ (학교 — 비교
                  대상 아님)」처럼 꼬리말이 반복되고, 견줄 땅을 고르려면 긴 목록을 훑어야 했다 — 두 묶음으로
                  나누고 꼬리말은 묶음 이름으로 옮긴다 (2026-11-07 UX 자동 개선, 390px). */}
              {(
                [
                  ["견줄 상가 땅", result.lots.filter((lot) => lot.kind === "candidate")],
                  ["견주지 않는 땅 (학교·주거·공공)", result.lots.filter((lot) => lot.kind !== "candidate")],
                ] as const
              ).map(([groupLabel, lots]) =>
                lots.length ? (
                  <optgroup key={groupLabel} label={groupLabel}>
                    {[...lots]
                      .sort((a, b) => a.pnu.localeCompare(b.pnu, "ko", { numeric: true }))
                      .map((lot) => (
                        <option key={lot.pnu} value={lot.pnu}>
                          {nameOf(lot.pnu)} · {lot.area_sqm.toLocaleString(undefined, { maximumFractionDigits: 0 })}㎡
                        </option>
                      ))}
                  </optgroup>
                ) : null,
              )}
            </select>
          </label>
        )}

        <div className="ps-map-wrap">
          {/* 2026-10-24: 지도 아래 작은 범례로는 「색이 무슨 뜻인지 안 나온다」(사용자) — 지도 위에 말로 적는다. */}
          {picked && (
            <div className="plan-legend" aria-label="색 읽는 법">
              <div className="plan-legend-group">
                <span className="plan-legend-title" style={{ flexBasis: "auto" }}>
                  {colorBy === "plan" && planRankMap.size
                    ? "땅 색 = 가게가 들어설 가능성(예상) — 빨강일수록 높고 파랑일수록 낮다 (상가 땅끼리 견줌)"
                    : "땅 색 = 앞길 유동인구 정도(예상) — 빨강일수록 높고 파랑일수록 낮다 (상가 땅끼리 견줌)"}
                </span>
                {!!planRankMap.size && (
                  <span className="plan-legend-item" role="group" aria-label="땅 색 기준">
                    <button
                      type="button"
                      className={colorBy === "plan" ? "plan-color-toggle is-active" : "plan-color-toggle"}
                      aria-pressed={colorBy === "plan"}
                      onClick={() => setColorBy("plan")}
                    >
                      가게 들어설 가능성
                    </button>
                    <button
                      type="button"
                      className={colorBy === "flow" ? "plan-color-toggle is-active" : "plan-color-toggle"}
                      aria-pressed={colorBy === "flow"}
                      onClick={() => setColorBy("flow")}
                    >
                      앞길 유동인구
                    </button>
                  </span>
                )}
                <span className="plan-legend-item">
                  낮음
                  <i
                    style={{ width: 160, height: 14, background: `linear-gradient(90deg, ${RAMP.join(", ")})` }}
                    aria-hidden
                  />
                  높음
                </span>
                {(result?.roads?.length ?? 0) > 0 && (
                  <span className="plan-legend-item">길 색 = 사람이 지나가기 쉬운 정도 — 같은 색 막대 (굵은 선이 도면의 계획 길)</span>
                )}
                <span className="plan-legend-item">
                  <i style={{ background: "#cbd5e1" }} aria-hidden />
                  회색 = 상가를 못 내는 땅(주거·학교·공원·공공) — 순위에서 뺌
                </span>
              </div>
              <div className="plan-legend-group">
                <span className="plan-legend-item">
                  <i style={{ background: "transparent", border: "3px solid #000" }} aria-hidden />
                  {/* 글과 굵은 이름을 한 덩어리로 — 따로 flex 칸이 되면 휴대폰에서 세 줄로 꺾였다 (2026-11-06 UX 자동 개선). */}
                  <span>
                    검은 테두리 = 지금 보는 땅 <b>{nameOf(picked.pnu)}</b>
                  </span>
                </span>
                <span className="plan-legend-item">
                  <i className="plan-legend-line" style={{ background: `linear-gradient(90deg, ${RAMP.join(", ")})` }} aria-hidden />
                  {picked.pnu} 블록 {COUNT_WORD[picked.sides.length] ?? picked.sides.length} 면 길의 유동인구 정도(예상) — 빨강일수록 높고
                  파랑일수록 낮다
                </span>
              </div>
            </div>
          )}
          <div ref={boxRef} className="pm-canvas" aria-label="계획 택지 지도" />
          {mapFailed && (
            <div className="status-banner status-banner--error map-error__body" role="alert">
              <p>{mapFailed}</p>
              <button
                type="button"
                className="map-error__retry"
                onClick={() => {
                  setMapFailed(null);
                  setMapAttempt((n) => n + 1);
                }}
              >
                다시 불러오기
              </button>
            </div>
          )}
          <p className="pm-status" role="status">
            {!plan
              ? "계획 도면(DXF·PDF)을 올리거나 샘플(운정·두바이·미국)을 불러오세요."
              : !result
                ? // 위치 확인 전엔 계산 버튼이 잠겨 있다 — 여기서 「계산을 누르세요」라 하면 버튼 문구와 어긋난다
                  // (2026-11-07 UX 자동 개선). 이 지도가 바로 「지도에서 위치 확인」의 그 지도다.
                  needsCrsCheck
                  ? "도면을 이 지도에 올려 두었습니다 — 자리가 맞으면 아래 단추를, 틀리면 위에서 다른 후보를 골라 주세요."
                  : plan && (!plan.roads.length || !plan.lots.length)
                    ? // 획지만 찾고 길을 못 찾았는데도 「길·획지를 못 찾았다」고 해, 위 「획지 1곳」과
                      // 어긋났다 — 빠진 쪽만 말한다 (2026-11-08 UX 자동 개선).
                      `도면에서 ${
                        !plan.roads.length && !plan.lots.length ? "길·획지를" : !plan.roads.length ? "계획 도로를" : "획지를"
                      } 아직 못 찾았습니다 — 위 「레이어 고치기」 표에서 레이어를 골라 다시 읽어 주세요.`
                    : "「길 구조 계산하고 획지 견주기」를 누르세요."
                : picked
                  ? `${picked.pnu} · 옆 획지 ${picked.neighbors.length}곳을 견줍니다 — 다른 획지를 누르면 바뀝니다.`
                  : "지도에서 획지를 누르면 그 땅의 변과 옆 땅 비교가 나옵니다." +
                    (result.summary.landlocked_count ? ` · 길에 안 닿은 곳 ${result.summary.landlocked_count}곳` : "")}
          </p>
          {/* 휴대폰에선 위치 확인 상자(「이 위치가 맞습니다」)와 이 지도가 약 300~800px 떨어져, 지도를 보며
              확인할 수 없었다 — 지도 바로 밑에 같은 단추를 두고, 누르면 계산 버튼으로 올린다
              (2026-11-07 UX 자동 개선, 390px). */}
          {needsCrsCheck && (
            <button
              type="button"
              className="primary-button plan-crs-confirm-below"
              disabled={extracting}
              onClick={() => {
                setCrsConfirmed(true);
                requestAnimationFrame(() =>
                  runBtnRef.current?.scrollIntoView({ behavior: "smooth", block: "center" }),
                );
              }}
            >
              이 위치가 맞습니다
            </button>
          )}
        </div>
      </div>

      {picked && (
        <div className="card" ref={detailRef} style={{ scrollMarginTop: 12 }}>
          <div className="floorplan-result-header">
            {/* 학교·공공·주거 땅은 바로 아래에 「상가를 낼 수 없어」라 적는데 제목이 「손님은 누구」를 물어
                가게 손님을 말해 줄 것처럼 읽혔다 — 상가 땅일 때만 손님을 묻는다 (2026-11-08 UX 자동 개선, 초4·문2). */}
            <h2>
              {nameOf(picked.pnu)} — 정면은 어디{picked.kind === "candidate" ? ", 손님은 누구" : ""}
            </h2>
            <span className="floorplan-scale-hint">{picked.area_sqm.toLocaleString(undefined, { maximumFractionDigits: 0 })}㎡</span>
          </div>
          {picked.kind !== "candidate" && (
            <p className="floorplan-help-text">
              <strong>{KIND_LABEL[picked.kind]} 부지</strong>라 상가를 낼 수 없어 순위·비교에서는 뺐습니다. 아래는
              참고로 둔 길 정보이고, 옆 상가 땅에는 「근처 {KIND_LABEL[picked.kind]}」로 나옵니다.
            </p>
          )}
          {pickedSummary && <SummaryText text={pickedSummary} lead={`${nameOf(picked.pnu)} — `} />}
          {pickedSummary && <SpeakButton text={[pickedSummary, compareSummary].filter(Boolean).join(" ")} />}
          {sample?.id === "unjeong" && SAMPLE_ALLOWED[picked.pnu] && (
            <p className="floorplan-help-text">
              <strong>허용 용도</strong>(LH 지구단위계획 시행지침): {SAMPLE_ALLOWED[picked.pnu]}
            </p>
          )}
          {!picked.sides.length ? (
            <p className="floorplan-help-text">
              계획 도로에 닿은 변이 없습니다 — 맹지이거나 도면에서 앞길이 빠졌는지 확인해 주세요.
            </p>
          ) : (
            <>
              <ul className="plan-sides">
                {picked.sides.map((side, i) => {
                  const strength = sideStrength(sideRank(side.score));
                  return (
                    <li key={side.direction} className="plan-side">
                      <div className="plan-side-head">
                        <i style={{ background: strength.color }} aria-hidden />
                        <strong>{i === 0 ? `${side.direction}쪽 — 정면` : `${side.direction}쪽`}</strong>
                        <span className="plan-side-badge" style={{ color: strength.color }}>
                          {strength.label}
                        </span>
                      </div>
                      <p className="plan-side-text">
                        {i === 0
                          ? frontText(picked, tierOf(sideRank(side.score)), picked.kind === "candidate")
                          : otherSideText(side, tierOf(sideRank(side.score)), picked.sides[0], picked.kind === "candidate")}
                      </p>
                      <small className="floorplan-scale-hint">
                        지나가는 사람 {topText(side.choice_pct)} · 동네 어디서나 찾아오기 쉬움 {topText(side.integration_pct)} · 길에{" "}
                        {side.length_m.toFixed(0)}m 닿음 (이 도면의 길끼리 견줌, 예상)
                      </small>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
          {picked.kind === "candidate" && picked.plan && pickedPlan && (
            <div className="plan-side" style={{ marginTop: 10 }}>
              <div className="plan-side-head">
                <strong>가게가 들어설 가능성 (예상)</strong>
                <span className="plan-side-badge" style={{ color: rampColor(pickedPlan.t) }}>
                  상가 땅 {pickedPlan.total}곳 중 {pickedPlan.rank}등
                </span>
              </div>
              <p className="floorplan-help-text" style={{ margin: "6px 0" }}>
                이미 지어진 신도시 10곳의 블록과 견주면 이 땅의 블록은 <strong>상위 {picked.plan.top_pct}%</strong> 점수입니다.
                {/* 2026-11-07 UX 자동 개선: 바로 위 「13곳 중 11등」과 「상위 30%」가 붙어 모순처럼 읽혔다
                    (운정 F10). 기준이 다르다(이 지구 상가 땅끼리 vs 지어진 신도시 블록 전체)는 걸 한 마디로. */}
                {pickedPlan.rank > pickedPlan.total / 2 && picked.plan.top_pct <= 50
                  ? " 이 지구에 점수가 더 높은 상가 땅이 몰려 있어 지구 안 순위는 뒤쪽입니다."
                  : pickedPlan.rank <= pickedPlan.total / 2 && picked.plan.top_pct > 50
                    ? " 지구 안에서는 앞쪽이지만, 지어진 신도시 전체로 보면 높은 점수는 아닙니다."
                    : ""}{" "}
                그 점수대 블록 100곳 중 <strong>{Math.round(picked.plan.commercial_share * 100)}곳</strong>은 가게가 10곳 넘게
                들어선 상가 블록이었습니다(이 점수면 보통 가게 {Math.round(picked.plan.expected_stores)}곳 안팎 — 지어진 신도시
                블록은 절반 넘게 가게가 0곳).
              </p>
              <ul>
                <li>
                  블록 넓이 {Math.round(picked.plan.block_area_m2).toLocaleString()}㎡ — 큰 블록일수록 가게가 많이 들어섰습니다
                </li>
                <li>둘레에서 가장 큰 길: {roadClassText(picked.plan.features.hw_max ?? 3)}</li>
                <li>
                  800m 안 아파트 땅 {((picked.plan.features.res_area_800 ?? 0) / 10000).toFixed(1)}ha
                  {picked.plan.features.res_block ? " · 이 블록 안에도 아파트 땅이 있음" : ""}
                </li>
                <li>
                  가장 가까운 역까지{" "}
                  {!picked.plan.features.station_known
                    ? "알 수 없음 (역을 불러오지 못해 역 거리 없이 계산)"
                    : (picked.plan.features.station_m ?? 3000) >= 3000
                      ? "3km 넘음"
                      : `약 ${Math.round(picked.plan.features.station_m).toLocaleString()}m (직선)`}
                </li>
              </ul>
              <small className="floorplan-scale-hint">
                같은 블록 안 땅은 같은 점수입니다. 처음 보는 신도시에서 가게가 많이 들어선 블록을 맞게 고른 비율 약 84%.
              </small>
            </div>
          )}
          {picked.kind === "candidate" && (
            <div className="plan-side" style={{ marginTop: 10 }}>
              {/* 2026-11-07 UX 자동 개선: 제목이 「근처 학교·아파트」인데 목록엔 공공·녹지(문2 문화시설)도
                  나와 F10에서 「아파트가 없다」는 위 문장과 함께 어긋나 읽혔다 — 실제로 담는 세 종류를 다 적는다. */}
              <div className="plan-side-head">
                <strong>근처 학교·아파트·공공시설 ({NEARBY_LIMIT_M}m 안)</strong>
              </div>
              {picked.nearby.length ? (
                <ul>
                  {picked.nearby.map((n) => (
                    <li key={n.lot_id}>
                      {/* 이름에 이미 괄호(「문2 (문화시설)」)가 있어 거리 괄호가 겹쳐 「(문화시설) (45m)」로
                          읽혔다 — 거리는 가운뎃점 뒤에 둔다 (2026-11-07 UX 자동 개선). */}
                      {KIND_LABEL[n.kind]} {nameOf(n.lot_id)} · {n.distance_m < 3 ? "맞닿음" : `${n.distance_m.toFixed(0)}m`} —{" "}
                      {/* 390px에서 「공원·문화 / ·공공시설」처럼 가운뎃점 앞에서 꺾여 다음 줄이 「·」로
                          시작했다 — 점 양옆에 단어 잇개(U+2060)를 넣어 낱말 묶음째 넘긴다
                          (2026-11-08 UX 자동 개선). */}
                      {KIND_MEANING[n.kind].replace(/·/g, "⁠·⁠")}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="floorplan-scale-hint" style={{ margin: "6px 0 0" }}>
                  없습니다 — 손님은 길을 지나는 사람과 역·간선 쪽에서 옵니다.
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {picked && !!picked.neighbors.length && (
        <div className="card">
          <h2>옆 땅과 견주면</h2>
          {compareSummary && <SummaryText text={compareSummary} />}
          <p className="floorplan-help-text">
            상가를 낼 수 있는 옆 땅만 견줍니다. 견주는 기준은 「가게가 들어설 가능성」 순위이고, 앞길 비교는 그 아래 작은 글씨로
            둡니다. 옆 땅을 누르면 그 땅으로 바꾸고 위의 설명으로 올라갑니다.
          </p>
          {/* 휴대폰(390px)에선 다섯 칸 표가 한 칸 3~4글자 폭으로 눌려 머리글이 「가게 들어 설 가 능성」처럼
              글자 단위로 꺾이고 안쪽 스크롤까지 겹쳐 읽을 수 없었다 — 640px 이하에선 땅마다 카드로 쌓고
              칸 이름을 각 값 앞에 붙인다(styles/floorplan.css .plan-compare-wrap) (2026-11-06 UX 자동 개선). */}
          <div className="floorplan-room-table-wrap plan-compare-wrap">
            <table className="floorplan-room-table plan-compare-table">
              <thead>
                <tr>
                  <th>땅</th>
                  <th>가게 들어설 가능성 (예상)</th>
                  <th>정면·앞길 (예상)</th>
                  <th>주 손님 (예상)</th>
                  <th>{josa(picked.pnu, "과", "와")} 비교</th>
                </tr>
              </thead>
              <tbody>
                {[picked, ...picked.neighbors.map((n) => lotsById.get(n.lot_id))].map((lot, i) => {
                  if (!lot) return null;
                  const n = i === 0 ? null : picked.neighbors[i - 1];
                  const best = lot.sides[0];
                  const tier = best ? tierOf(sideRank(best.score)) : null;
                  const cmp = n ? compareOf(picked, lot) : null;
                  return (
                    <tr
                      key={lot.pnu}
                      className={n ? "plan-neighbor-row" : "plan-compare-picked"}
                      onClick={
                        n
                          ? () => {
                              jumpToDetailRef.current = true;
                              setPickedId(lot.pnu);
                            }
                          : undefined
                      }
                    >
                      <td data-label="땅">
                        <strong>{nameOf(lot.pnu)}</strong>
                        <br />
                        <small>
                          {n ? `${n.relation}${n.relation === "길 건너" ? ` ${n.distance_m.toFixed(0)}m` : ""}` : "고른 땅"} ·{" "}
                          {lot.area_sqm.toLocaleString(undefined, { maximumFractionDigits: 0 })}㎡
                        </small>
                      </td>
                      <td data-label="가게 들어설 가능성 (예상)"><div>{planCell(lot)}</div></td>
                      <td data-label="정면·앞길 (예상)"><div>
                        {best ? (
                          <>
                            <strong>{best.direction}쪽 정면</strong> · {sideStrength(sideRank(best.score)).label}
                            <br />
                            <small>
                              지구 안 길 중 {topText(best.score)} · 길에 닿은 면 {lot.sides.length}개
                            </small>
                          </>
                        ) : (
                          "길에 안 닿음"
                        )}
                      </div></td>
                      <td data-label="주 손님 (예상)"><div>{customerOf(lot, tier).short}</div></td>
                      <td data-label={`${josa(picked.pnu, "과", "와")} 비교`}><div>
                        {cmp ? (
                          <>
                            <strong style={{ color: cmp.color }}>{cmp.label}</strong>
                            <br />
                            <small>{cmp.sub}</small>
                          </>
                        ) : (
                          "—"
                        )}
                      </div></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!!(result?.warnings.length || result?.caveats.length) && (
        <div className="card">
          <h2>이 숫자를 어디까지 믿을지</h2>
          {/* 2026-10-26 사용자: 「간단히 다 맞는 건 아니다」를 먼저 — 아래 목록만으로는 신뢰를 잘못 읽는다. */}
          <p className="floorplan-help-text" style={{ fontWeight: 700 }}>
            한 줄로: 이 결과가 다 맞는 건 아닙니다. 길 모양으로 본 예상이니 참고로만 보고, 현장·분양 정보와 함께 판단하세요.
          </p>
          {/* 2026-11-08 UX 자동 개선: 이번 계산에서 실제로 빠진 것(warnings — 기존 도로·역을 못 불러옴)과
              늘 붙는 설명(caveats — 「길 모양만 보고 낸 예상」 등)이 한 주황 목록에 섞여, 390에서 6줄이
              모두 경고처럼 보이고 진짜 빠진 것이 묻혔다. 빠진 것만 주황으로 먼저, 설명은 보통 글씨로. */}
          {!!result?.warnings.length && (
            <>
              <p className="plan-caveats-lead">이번 계산에서 빠진 것</p>
              <ul className="warnings-banner plan-caveats">
                {result.warnings.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </>
          )}
          {!!result?.caveats.length && (
            <ul className="plan-caveats">
              {result.caveats.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
