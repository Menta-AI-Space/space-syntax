"use client";

import { useMemo } from "react";
import { RampLegend } from "@/components/demo/PlanCanvas";
import type { buildFloorplanDemo, Space } from "@/lib/demoFloorplan";
import {
  HEATMAP_TERMS,
  TERMS,
  TERM_ORDER,
  rankText,
  type TermKey,
} from "@/lib/floorplanTerms";
import { readVga, type VgaResult } from "@/lib/floorplanVga";

/**
 * 평면 공간구조 분석 결과 — `/demo` 안의 세 번째 분석 종류.
 *
 * 원래 `/demo/floorplan`이라는 별도 화면이었다가 데모를 한 URL로 모으면서 여기로
 * 들어왔다(2026-08-24). 화면 껍데기는 토지·상권 결과와 같은 것을 쓴다(`.res-*`) —
 * 분석 종류가 바뀌었다고 프레임이 바뀌면 같은 서비스로 안 읽힌다. 도면은 오른쪽
 * 지도 자리에 그려지고(`app/demo/page.tsx`), 이 파일은 왼쪽 패널만 담당한다.
 *
 * 두 가지가 이 화면의 규칙이다:
 *
 * 1. **용어를 먼저 정하고 그 말만 쓴다.** 첫 탭이 네 가지 말을 쉬운 한국어로
 *    정의하고, 그다음부터 그 이름만 쓴다. '통합도'·'가시성 그래프' 같은 전문
 *    용어는 각 항목의 「전문 용어」 줄에만 적는다.
 * 2. **숫자를 지어내지 않는다.** 지표는 브라우저에서 실제로 계산한다
 *    (`lib/floorplanVga.ts`, 백엔드 `floorplan_vga.py`와 같은 정의). 평면 도형만
 *    예시이고, 그 위의 값은 그 도형에서 정말로 나온 값이다.
 */

export const FLOORPLAN_TABS = [
  { id: "f-terms", label: "쓰는 말" },
  { id: "f-plan", label: "이 평면" },
  ...HEATMAP_TERMS.map((k) => ({ id: `f-${k}` as const, label: TERMS[k].easy })),
  { id: "f-reading", label: "해석" },
] as const;

export type FloorplanTabId = (typeof FLOORPLAN_TABS)[number]["id"];

/** 첫 탭은 값이 아니라 **말**이다 — 용어를 모른 채로 본 숫자는 뜻이 없다. */
export const DEFAULT_FLOORPLAN_TAB: FloorplanTabId = "f-terms";

type Plan = ReturnType<typeof buildFloorplanDemo>;

/**
 * 상단 한 줄. **판정은 「퍼짐」 하나로만 한다** — 계산 경로가 내놓는 값 중에서
 * 이 평면 전체를 한 문장으로 말할 근거가 되는 것이 그것뿐이다. 문턱 2.5는 근거가
 * 있어서가 아니라 화면과 백엔드가 한 곳에서만 정하기 위해 고정한 값이다
 * (`lib/floorplanVga.ts`의 `readVga`와 같은 값).
 */
function oneLine(vga: VgaResult | null): string {
  if (!vga || !vga.cells.length) return "계산이 끝나면 여기에 적습니다.";
  const spread = vga.integrationMean > 0 ? vga.integrationMax / vga.integrationMean : 0;
  return spread >= 2.5
    ? `공간이 뚜렷하게 나뉜 평면입니다 (퍼짐 ${spread.toFixed(2)})`
    : `어느 자리에서 봐도 조건이 비슷한 평면입니다 (퍼짐 ${spread.toFixed(2)})`;
}

/** 탭이 히트맵 탭이면 그 지표를, 아니면 null — 도면에 무엇을 칠할지가 여기서 정해진다. */
export function paintOf(tab: FloorplanTabId): TermKey | null {
  const key = tab.slice(2) as TermKey;
  return (HEATMAP_TERMS as readonly string[]).includes(key) ? key : null;
}

export default function FloorplanResult({
  plan,
  vga,
  tab,
  onTab,
  space,
  bodyRef,
}: {
  plan: Plan;
  vga: VgaResult | null;
  tab: FloorplanTabId;
  onTab: (t: FloorplanTabId) => void;
  space: Space | null;
  bodyRef: React.RefObject<HTMLDivElement | null>;
}) {
  return (
    <div className="res">
      <header className="res-head">
        <p className="res-eyebrow">
          {plan.fileName} · {plan.floorLabel} · 평면은 예시 · 값은 실제 계산
        </p>
        <h1 className="res-title">평면 공간구조 분석</h1>

        <div className="res-sum">
          <div>
            <span className="res-sk">벽</span>
            <span className="res-sv">{plan.walls.length}개</span>
          </div>
          <div>
            <span className="res-sk">문 · 창 · 출입구</span>
            <span className="res-sv">{plan.openings.length}개</span>
          </div>
          <div>
            <span className="res-sk">방</span>
            <span className="res-sv">{plan.spaces.length}개</span>
          </div>
          <div>
            <span className="res-sk">계산에 쓴 칸</span>
            <span className="res-sv">
              {vga ? vga.cellCount.toLocaleString("ko-KR") : "—"}
              <small> 칸</small>
            </span>
          </div>
          <div>
            <span className="res-sk">격자 한 칸</span>
            <span className="res-sv">{vga ? `${vga.cell} × ${vga.cell}` : "—"}</span>
          </div>
          <div>
            <span className="res-sk">계산 시간</span>
            <span className="res-sv">{vga ? `${vga.elapsedMs}ms` : "계산 중…"}</span>
          </div>
        </div>
        <p className="res-verdict">
          한 줄 판정 <b>{oneLine(vga)}</b>
        </p>
      </header>

      <nav className="res-tabs" role="tablist" aria-label="평면 분석 탭">
        {FLOORPLAN_TABS.map((t) => (
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
        {tab === "f-terms" ? (
          <TermsPane onGo={() => onTab("f-plan")} />
        ) : tab === "f-plan" ? (
          <PlanPane plan={plan} vga={vga} space={space} />
        ) : tab === "f-reading" ? (
          <ReadingPane vga={vga} />
        ) : (
          <MetricPane term={paintOf(tab) as TermKey} vga={vga} />
        )}
      </div>

      <footer className="res-foot">
        <div className="res-foot-grid">
          <span>
            도면 <b>DXF 벡터만</b>
          </span>
          <span>
            계산 <b>브라우저에서 직접</b>
          </span>
          <span>
            정의 <b>백엔드 floorplan_vga.py와 동일</b>
          </span>
        </div>
        <p>
          공간구조 계산 결과이며, <b>설계 판단이나 법규 적합성 판정이 아닙니다.</b> 최종
          판단은 건축사 및 관련 전문가의 확인이 필요합니다. 평면 도형은 예시입니다.
        </p>
      </footer>
    </div>
  );
}

/* ── 쓰는 말 — 값보다 먼저 온다 ─────────────────────────────────────────── */

function TermsPane({ onGo }: { onGo: () => void }) {
  return (
    <div className="fp-pane">
      <p className="fp-q">이 화면이 쓰는 말 네 가지</p>
      <p className="fp-note">
        아래 이름만 씁니다. <b>전문 용어는 각 항목의 「전문 용어」 줄에만</b> 적어 두었고,
        화면의 다른 곳에서는 쓰지 않습니다.
      </p>

      {TERM_ORDER.map((k) => {
        const t = TERMS[k];
        return (
          <article key={k} className={`fp-term${t.computed ? "" : " fp-term--off"}`}>
            <h4 className="fp-term-name">
              {t.easy}
              {!t.computed && <span className="fp-lv fp-lv--check">계산하지 않음</span>}
            </h4>
            <p className="fp-p">{t.what}</p>
            <ul className="fp-ev">
              <li>
                <span>값이 크면</span>
                <b>{t.high}</b>
              </li>
              <li>
                <span>값이 작으면</span>
                <b>{t.low}</b>
              </li>
              <li>
                <span>무엇에 쓰나</span>
                <b>{t.use}</b>
              </li>
              <li>
                <span>전문 용어</span>
                <b>
                  {t.jargon} · {t.en}
                </b>
              </li>
            </ul>
            <p className="fp-note">
              <b>오해하기 쉬운 것 —</b> {t.caution}
            </p>
          </article>
        );
      })}

      <p className="fp-note">
        「지나다니게 되는 자리」는 <b>계산하지 않습니다.</b> 지금 계산 경로가 내놓는 것은
        위 세 가지뿐입니다. 없는 값을 그럴듯한 숫자로 채우지 않기 위해 자리만 두고
        비워 둡니다.
      </p>

      <div className="fp-acts">
        <button type="button" className="fp-btn fp-btn--go" onClick={onGo}>
          이 평면 보기
        </button>
      </div>
    </div>
  );
}

/* ── 이 평면 — 무엇을 계산했는지 ────────────────────────────────────────── */

function PlanPane({
  plan,
  vga,
  space,
}: {
  plan: Plan;
  vga: VgaResult | null;
  space: Space | null;
}) {
  return (
    <div className="fp-pane">
      <p className="fp-q">무엇을 어떻게 계산했는가?</p>

      <h4 className="fp-h">계산 순서</h4>
      <ol className="fp-toc">
        <li>벽을 격자에 굽고, 문과 창이 있는 자리를 다시 뚫습니다.</li>
        <li>도면 바깥에서 물을 흘려 넣어 건물 밖을 걷어냅니다.</li>
        <li>남은 실내에서 두 칸 사이에 벽이 없으면 서로 「보인다」로 잇습니다.</li>
        <li>그 이어진 관계만으로 세 가지 값을 셉니다.</li>
      </ol>
      <p className="fp-note">
        <b>바깥을 걷어내는 2단계가 없으면</b> 사방이 트인 건물 밖 공터가 가장 잘 보이고
        가장 찾아오기 쉬운 자리로 나옵니다.
      </p>

      <ul className="fp-ev">
        <li>
          <span>격자 한 칸</span>
          <b>{vga ? `${vga.cell} × ${vga.cell}` : "—"}</b>
        </li>
        <li>
          <span>계산에 쓴 칸</span>
          <b>{vga ? `${vga.cellCount.toLocaleString("ko-KR")}칸` : "계산 중…"}</b>
        </li>
        <li>
          <span>걸린 시간</span>
          <b>{vga ? `${vga.elapsedMs}ms` : "—"}</b>
        </li>
      </ul>

      {space ? (
        <div className="fp-picked">
          <b>{space.name}</b>
          <span>
            {space.areaM2}㎡{space.note ? ` · ${space.note}` : ""}
          </span>
        </div>
      ) : (
        <p className="fp-note">도면에서 방을 누르면 이름과 면적이 여기에 뜹니다.</p>
      )}

      <p className="fp-note">
        평면 도형은 <b>예시</b>입니다. 그 위의 값은 이 도형에서 <b>실제로 계산한 것</b>이고,
        정의는 서비스 백엔드가 쓰는 것과 같습니다. 받는 파일은 <b>DXF 벡터 도면만</b>이고
        사진·스캔 이미지는 받지 않습니다.
      </p>
      <p className="fp-note">방 {plan.spaces.length}개가 이 도면에 이름을 가지고 있습니다.</p>
    </div>
  );
}

/* ── 지표 하나 ──────────────────────────────────────────────────────────── */

function MetricPane({ term, vga }: { term: TermKey; vga: VgaResult | null }) {
  const t = TERMS[term];

  const stats = useMemo(() => {
    if (!vga || !vga.cells.length) return null;
    const values = vga.cells.map((c) =>
      term === "connectivity" ? c.connectivity : term === "mean_depth" ? c.meanDepth : c.integration,
    );
    const sorted = [...values].sort((a, b) => a - b);
    const at = (q: number) => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * q))];
    const hiIdx = values.indexOf(Math.max(...values));
    const loIdx = values.indexOf(Math.min(...values));
    return {
      values,
      lo: sorted[0],
      hi: sorted[sorted.length - 1],
      mid: at(0.5),
      hiCell: vga.cells[hiIdx],
      loCell: vga.cells[loIdx],
    };
  }, [vga, term]);

  const digits = term === "connectivity" ? 0 : 2;

  return (
    <div className="fp-pane">
      <p className="fp-q">{t.easy}</p>
      <p className="fp-p">{t.what}</p>

      <RampLegend lowLabel={t.low} highLabel={t.high} />

      {!stats ? (
        <p className="fp-note">계산 중입니다…</p>
      ) : (
        <>
          <ul className="fp-ev">
            <li>
              <span>가장 높은 값</span>
              <b>{stats.hi.toFixed(digits)}</b>
            </li>
            <li>
              <span>가운데 값</span>
              <b>{stats.mid.toFixed(digits)}</b>
            </li>
            <li>
              <span>가장 낮은 값</span>
              <b>{stats.lo.toFixed(digits)}</b>
            </li>
            <li>
              <span>가장 높은 자리</span>
              <b>
                ({Math.round(stats.hiCell.x)}, {Math.round(stats.hiCell.y)})
              </b>
            </li>
            <li>
              <span>가장 낮은 자리</span>
              <b>
                ({Math.round(stats.loCell.x)}, {Math.round(stats.loCell.y)})
              </b>
            </li>
          </ul>
          <p className="fp-note">
            원시값은 <b>이 도면 안에서만</b> 뜻이 있습니다. 다른 건물의 같은 숫자와 비교할
            수 있는 값이 아니어서 순위로 읽습니다 — 예를 들어 가운데 값은 이 평면에서{" "}
            {rankText(stats.mid, stats.values)}입니다.
          </p>
        </>
      )}

      <h4 className="fp-h">무엇에 쓰나</h4>
      <p className="fp-p">{t.use}</p>

      <p className="fp-note">
        <b>오해하기 쉬운 것 —</b> {t.caution}
      </p>

      <ul className="fp-ev">
        <li>
          <span>전문 용어</span>
          <b>
            {t.jargon} · {t.en}
          </b>
        </li>
      </ul>
    </div>
  );
}

/* ── 해석 ───────────────────────────────────────────────────────────────── */

function ReadingPane({ vga }: { vga: VgaResult | null }) {
  const reading = useMemo(() => (vga ? readVga(vga) : null), [vga]);

  return (
    <div className="fp-pane">
      <p className="fp-q">이 값들이 이 평면에서 뜻하는 것</p>

      {!reading ? (
        <p className="fp-note">계산 중입니다…</p>
      ) : (
        <>
          <p className="fp-p fp-p--ai">{reading.text}</p>

          <h4 className="fp-h">근거</h4>
          <ul className="fp-ev">
            {reading.basis.map((b) => (
              <li key={b.k}>
                <span>{b.k}</span>
                <b>{b.v}</b>
              </li>
            ))}
          </ul>
          <p className="fp-note">
            「퍼짐」은 가장 높은 값을 평균으로 나눈 것입니다. <b>2.5를 넘으면</b> 공간이
            뚜렷하게 나뉜 것으로 읽습니다 — 그 문턱은 근거가 있어서가 아니라 화면과 백엔드가
            한 곳에서만 정하기 위해 고정한 값입니다.
          </p>
        </>
      )}

      <h4 className="fp-h">이 분석이 못 보는 것</h4>
      <ul className="fp-ul">
        <li>가구 배치. 책상과 칸막이는 도면의 벽이 아니지만 시야를 막습니다.</li>
        <li>문 폭과 문의 여닫힘. 열린 문과 닫힌 문을 구분하지 않습니다.</li>
        <li>층 사이의 이동. 계단과 엘리베이터를 그냥 방으로 봅니다.</li>
        <li>사람의 목적. 어디로 가려 하는지는 이 계산에 들어 있지 않습니다.</li>
        <li>「지나다니게 되는 자리」는 계산하지 않습니다.</li>
      </ul>
    </div>
  );
}
