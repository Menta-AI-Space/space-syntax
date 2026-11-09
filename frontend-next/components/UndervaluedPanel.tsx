"use client";

import { useEffect, useState } from "react";
import { api, type UndervaluedArea } from "@/lib/api";
import { UndervaluedParcelList } from "./UndervaluedParcelList";

/**
 * 구조 대비 가격이 낮은 동네 — **사실 서술** 화면.
 *
 * ⚠️ 2026-10-01에 「저평가 부지」에서 이름과 문구를 바꿨다. 이유는 검증 결과다:
 * 갭(목 − 가격)이 미래 상승을 맞힌다는 종전 보고는 **기저효과 착시**였고
 * (`DECISIONS_LAND_MODEL.md` §16 — 목을 빼고 「값 하위만」 고른 대조군이 갭보다
 * 성적이 높았다), 매출 수준을 층으로 고정하면 목과 미래 성장률의 상관이 0이다
 * (−0.007). §10·§26은 그래서 「저평가」·「뜰 자리」를 **제품 결론에서 내렸는데**
 * 이 화면만 옛 이름으로 남아 있었다.
 *
 * 그렇다고 지울 필요는 없다. **"구조는 상위인데 값은 아직 그만큼이 아니다"는
 * 관측된 사실**이고, 그 자체로 후보를 좁히는 데 쓸모가 있다. 다만 **"그래서
 * 오른다"는 말을 우리가 하지 않는다** — 판단은 보는 사람 몫으로 남긴다.
 * 감정평가사인 대표가 가액 의견을 공표하는 형태가 되는 것도 피한다(§10).
 *
 * 화면에서는 Choice·Integration을 "목"이라 부른다. `목이 좋다`는 좋은 자리를
 * 가리키는 한국어 관용어인데, 뜻이 정확히 겹친다 — 목은 "지나다닐 수밖에 없는
 * 자리"이고 Choice가 재는 것이 바로 그것이다. 학술 용어를 일상어로 갈아끼운 게
 * 아니라 원래 같은 것을 가리키는 말이라 설명이 필요 없다. 정식 용어는 표 아래
 * 각주에 남겨 둔다 — 투자자·공공기관도 보는 화면이라 근거가 있어야 한다.
 *
 * 상품이 파는 것은 저평가된 "땅"이지 동네가 아니다. 다만 판정 단위인 필지는
 * 서울 87만 개라 처음부터 늘어놓을 수 없고, 개별공시지가·토지특성 적재도 아직
 * 끝나지 않았다. 그래서 지금 화면은 **동네를 좁히는 데까지만** 정직하게 보여준다.
 * 필지 목록이 준비되면 동 카드를 눌러 그 안으로 내려가는 2단계를 붙인다.
 *
 * 여기서 "저평가된 동을 찾았다"고 말하지 않는 것이 중요하다 — 동 평균은 같은 동
 * 안 대로변과 이면도로의 차이를 지우므로, 동은 답이 아니라 후보 구역이다.
 */
export function UndervaluedPanel() {
  const [areas, setAreas] = useState<UndervaluedArea[]>([]);
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  // 한 번에 하나만 편다 — 여러 동을 동시에 펼치면 목록이 길어져 순위가 안 읽힌다.
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    api
      .undervaluedAreas(20)
      .then((res) => {
        if (cancelled) return;
        setAreas(res.areas);
        setNote(res.note);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // 배치를 아직 안 돌렸으면 표가 비어 있다 — 고장과 구분해서 말한다.
        setError(err instanceof Error ? err.message : "목록을 불러오지 못했습니다.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="uv-panel">
      <header className="uv-head">
        <p className="uv-eyebrow">후보 구역 좁히기 · 사실 서술</p>
        <h2 className="uv-title">구조 대비 가격이 낮은 동네</h2>
        <p className="uv-def">
          <strong>목</strong> — 다른 데를 오갈 때 자연히 지나게 되는 자리.
          일부러 찾아오지 않아도 사람이 지나갑니다.
        </p>
        <p className="uv-sub">
          서울 426개 동을 전수 계산해 <strong>목 백분위 − 가격 백분위</strong> 순으로
          정렬했습니다. 지금 이 동네의 <strong>길 구조 순위와 가격 순위가 얼마나
          벌어져 있는지</strong>를 보여주는 것이고, 그 이상은 아닙니다.
        </p>
        <p className="uv-warn">
          ⚠️ <strong>이것은 “오른다”는 뜻이 아닙니다.</strong> 저희 검증에서 이 차이로
          미래 상승을 맞히려 해 봤지만 무작위보다 낫지 않았습니다 — 값이 싼 곳이 나중에
          퍼센트로 많이 오르는 것은 구조 때문이 아니라 낮은 데서 시작해서입니다.
          투자 판단의 근거로 쓰지 마시고, <strong>현장을 볼 후보를 좁히는 데</strong>만
          쓰십시오. 감정평가가 아닙니다.
        </p>
      </header>

      {loading && <p className="uv-status">계산해 둔 순위를 불러오는 중…</p>}

      {error && (
        <div className="uv-empty">
          <p className="uv-empty-title">목록을 불러오지 못했습니다</p>
          <p className="uv-empty-body">{error}</p>
        </div>
      )}

      {!loading && !error && areas.length === 0 && (
        <div className="uv-empty">
          <p className="uv-empty-title">아직 계산된 순위가 없습니다</p>
          <p className="uv-empty-body">
            배치(<code>compute_undervalued_areas</code>)를 한 번 돌리면 채워집니다.
          </p>
        </div>
      )}

      {areas.length > 0 && (
        <>
          <ol className="uv-list">
            {areas.map((a, i) => {
              const open = expanded === a.adm_cd;
              return (
                <li key={a.adm_cd} className="uv-item">
                  <button
                    type="button"
                    className="uv-row uv-row--button"
                    aria-expanded={open}
                    onClick={() => setExpanded(open ? null : a.adm_cd)}
                  >
                    <span className="uv-rank">{i + 1}</span>
                    <span className="uv-name">{a.adm_nm}</span>
                    <span className="uv-metric uv-metric--structure">
                      <em>목</em>
                      {Math.round(a.structure_percentile)}
                    </span>
                    <span className="uv-metric">
                      <em>가격</em>
                      {a.price_percentile == null ? "—" : Math.round(a.price_percentile)}
                    </span>
                    <span className="uv-gap">
                      {a.gap_score > 0 ? "+" : ""}
                      {Math.round(a.gap_score)}
                    </span>
                    <span className="uv-caret" aria-hidden="true">
                      {open ? "▾" : "▸"}
                    </span>
                  </button>
                  {open && <UndervaluedParcelList admCd={a.adm_cd} />}
                </li>
              );
            })}
          </ol>

          <p className="uv-note">
            “목”은 <strong>길 구조</strong>(공간구문론)의 Choice · Integration을 합친
            값입니다 — 다른 곳들을 오갈 때 이 길을 지나게 되는 정도와, 어디서든 닿기
            쉬운 정도.
          </p>
          <p className="uv-note">{note}</p>
          <p className="uv-note">
            <strong>검증된 것과 안 된 것</strong> — 길 구조가 좋은 상권은 <strong>지금
            매출</strong>이 높습니다(서울 1,648개 상권, 구조 5분위 매출 3.3배, 순환성
            없음). 반면 <strong>미래 상승</strong>은 맞히지 못했습니다(상관 −0.007).
            이 표는 앞쪽 사실에 기대고 뒤쪽은 말하지 않습니다.
          </p>
          <p className="uv-next">
            동을 누르면 그 안에서 <strong>필지 단위</strong>로 내려갑니다 — 동은 답이
            아니라 후보 구역입니다. 같은 동 안에서도 대로변과 이면도로는 목이 다릅니다.
          </p>
        </>
      )}
    </div>
  );
}
