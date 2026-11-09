"use client";

import { useEffect, useRef, useState } from "react";
import { searchKoreanAddresses, type AddressSearchItem } from "@/lib/address";
import { directionFrom, type TopStreet } from "@/lib/topStreets";
import { withObjectParticle } from "@/lib/agentFormat";

// 첫 화면 — 주소를 고르면 주변 길을 사람 흐름 색으로 칠하고 「목이 좋은 길」 3곳을
// 짚는다(2026-10-24 소유자 결정). AI 서술 없이 엔진 숫자만이라 2초 안팎이다.
// 계산·지도 그리기는 AppShell이 하고, 이 컴포넌트는 검색창과 결과 카드만 맡는다.

export interface StreetFinderSpot extends TopStreet {
  /** 역지오코딩한 주소. 오는 동안은 null. */
  address: string | null;
}

// 2026-10-26: 「순위에 넣은 길 269개 중 1번째」는 창업자가 읽어도 좋은 건지 알 수 없었다(소유자
// 지적). 한마디 판정 + 「주변 길 N개 중 M등」 + 어떤 가게에 맞는지를 낸다. 검증된 건 「길 구조가
// 좋은 상권일수록 지금 매출이 높다」(상권 5분위 16억 → 54억)까지라, 매출·임대료를 약속하는 말은
// 쓰지 않는다 — 길 구조와 임대료는 서로 거의 무관했다(상관 +0.04).
function spotVerdict(spot: StreetFinderSpot): { label: string; tip: string } {
  const share = spot.order && spot.total ? spot.order / spot.total : spot.topPct / 100;
  if (share <= 0.02) {
    return {
      label: "동네에서 손꼽히는 길",
      tip: "오가는 사람이 가장 많이 지나는 축이에요. 지나가다 들르는 손님이 중요한 가게(카페·편의점·분식·약국)에 잘 맞아요.",
    };
  }
  if (share <= 0.05) {
    return {
      label: "상위권 길",
      tip: "사람이 꾸준히 지나는 길이에요. 지나가다 들르는 가게도, 찾아오는 가게도 무난해요.",
    };
  }
  return {
    label: "괜찮은 길",
    tip: "큰 흐름에선 한 발 비켜 있어요. 손님이 일부러 찾아오는 가게(학원·병원·전문 매장)라면 검토할 만해요.",
  };
}

export function StreetFinder({
  loading,
  error,
  retryable = true,
  placeLabel,
  center,
  spots,
  onPick,
  onFocusSpot,
  onOpenChat,
}: {
  loading: boolean;
  error: string;
  /** false면 「다시 분석하기」를 숨긴다 — 다시 보내도 같은 답인 실패(서울 밖 등). */
  retryable?: boolean;
  placeLabel: string | null;
  segmentCount: number | null;
  /** 분석 중심 — 각 길을 「○○에서 북동쪽 180m」로 부른다. */
  center: { lat: number; lng: number } | null;
  spots: StreetFinderSpot[];
  onPick: (item: AddressSearchItem) => void;
  onFocusSpot: (spot: StreetFinderSpot) => void;
  onOpenChat: () => void;
}) {
  // AI 질의창을 열었다 닫으면 이 컴포넌트가 다시 붙는다 — 고른 장소 이름을 되살린다.
  const [text, setText] = useState(placeLabel ?? "");
  const [results, setResults] = useState<AddressSearchItem[]>([]);
  // 찾은 곳이 없을 때의 검색어. 전에는 목록이 안 뜨고 Enter도 아무 일 없어, 오타인지
  // 기다리는 중인지 알 길이 없었다 (2026-11-06 UX 자동 개선, 첫 화면 잘못된 입력).
  const [noMatch, setNoMatch] = useState<string | null>(null);
  const seqRef = useRef(0);
  // 길 분석이 실패하면 「잠시 후 다시 시도해 주세요」만 뜨고, 다시 하려면 주소를 지우고 다시 쳐서
  // 목록에서 또 골라야 했다(입력칸에 고른 이름이 그대로라 목록이 안 열린다) — 마지막으로 고른 곳을
  // 기억해 오류 옆 「다시 분석하기」로 한 번에 다시 부른다 (2026-11-06 UX 자동 개선, 390px).
  const lastPickRef = useRef<AddressSearchItem | null>(null);
  // 목록에서 고르면 입력칸에 그 이름을 넣는데, 그게 다시 검색을 불러 목록이 도로 열렸다.
  const pickedTextRef = useRef<string | null>(placeLabel ?? null);
  // 설명을 펼친 번호. 새로 검색해 목록이 바뀌면 1번으로 돌아간다.
  const [pickedRank, setPickedRank] = useState<number | null>(null);
  useEffect(() => setPickedRank(null), [spots]);
  const picked = spots.find((s) => s.rank === pickedRank) ?? spots[0] ?? null;

  useEffect(() => {
    const q = text.trim();
    const seq = ++seqRef.current;
    setNoMatch(null);
    if (pickedTextRef.current === text) {
      setResults([]);
      return;
    }
    pickedTextRef.current = null;
    if (q.length < 2) {
      setResults([]);
      return;
    }
    const timer = window.setTimeout(() => {
      searchKoreanAddresses(q, 6)
        .then((r) => {
          if (seq !== seqRef.current) return;
          setResults(r);
          setNoMatch(r.length ? null : q);
        })
        .catch(() => { if (seq === seqRef.current) setResults([]); });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [text]);

  const inputRef = useRef<HTMLInputElement>(null);
  function pick(item: AddressSearchItem) {
    seqRef.current += 1;
    // 2026-11-07 UX 자동 개선: Enter로 고르면 입력칸에 초점이 남아 휴대폰 자판이 그대로 떠
    // 아래 「목 좋은 길 3곳」·실패 안내 시트를 덮었다(지도 탭 검색은 이미 blur). 고르면 자판을 내린다.
    inputRef.current?.blur();
    const label = item.placeName || item.address;
    pickedTextRef.current = label;
    setText(label);
    setResults([]);
    lastPickRef.current = item;
    onPick(item);
  }

  return (
    <div className="street-finder">
      <div className="street-finder__search">
        <input
          ref={inputRef}
          className="street-finder__input"
          enterKeyHint="search"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && results[0]) pick(results[0]); }}
          placeholder="주소나 역 이름 — 예: 신촌역 2호선"
          aria-label="분석할 주소"
          autoComplete="off"
        />
        {results.length > 0 && (
          <ul className="street-finder__results">
            {results.map((item) => (
              <li key={`${item.lat}-${item.lng}-${item.address}`}>
                <button type="button" onClick={() => pick(item)}>
                  {item.placeName ? <strong>{item.placeName}</strong> : null}
                  <span>{item.address}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {noMatch && (
          <p className="street-finder__results street-finder__nomatch" role="status">
            {withObjectParticle(`「${noMatch}」`)} 찾지 못했어요 — 역·동 이름이나 도로명 주소로 다시 넣어 보세요.
          </p>
        )}
      </div>

      <section className="street-finder__card" aria-live="polite">
        {loading ? (
          <p className="street-finder__hint">주변 길을 분석하는 중이에요…</p>
        ) : error ? (
          <div className="street-finder__error-row" role="alert">
            <p className="street-finder__error">{error}</p>
            {retryable && lastPickRef.current && (
              <button
                type="button"
                className="street-finder__retry"
                onClick={() => { if (lastPickRef.current) onPick(lastPickRef.current); }}
              >
                다시 분석하기
              </button>
            )}
          </div>
        ) : spots.length > 0 ? (
          <>
            <h2 className="street-finder__title">목이 좋은 길 {spots.length}곳</h2>
            <p className="street-finder__sub">
              {placeLabel} 반경 500m 안에서 사람이 많이 지나가고 모이기 쉬운 순
            </p>
            {/* 2026-10-26: 세 곳을 카드로 늘어놓으면 휴대폰·좁은 창에서 카드가 화면 절반을
                먹어 지도가 안 보였다(소유자 지적). 번호만 한 줄로 두고, 누른 번호의 설명
                하나만 그 아래에 보인다 — 처음엔 1번. */}
            <div className="street-finder__chips" role="tablist" aria-label="목이 좋은 길">
              {spots.map((s) => (
                <button
                  key={s.rank}
                  type="button"
                  role="tab"
                  aria-selected={s.rank === picked?.rank}
                  className={`street-finder__chip${s.rank === picked?.rank ? " street-finder__chip--on" : ""}`}
                  onClick={() => { setPickedRank(s.rank); onFocusSpot(s); }}
                >
                  <span className="street-finder__badge">{s.rank}</span>
                  {/* 번호마다 다른 걸 보여 준다 — 「상위 1%」는 1·2번이 똑같이 나왔다. */}
                  <span className="street-finder__chip-label">{center ? directionFrom(center, s) : `${s.order ?? s.rank}번째`}</span>
                </button>
              ))}
            </div>
            {picked && (
              <div className="street-finder__detail" role="tabpanel">
                {/* 크게는 「검색한 곳에서 어느 쪽 몇 m」 — 지번은 큰 땅에 붙은 길 둘이
                    같은 번지로 나와 구분이 안 됐다. 주소는 작게 붙인다. */}
                <strong>
                  {center ? `${placeLabel ? `${placeLabel}에서 ` : ""}${directionFrom(center, picked)}` : picked.address ?? "주소 찾는 중…"}
                </strong>
                {(() => {
                  const verdict = spotVerdict(picked);
                  return (
                    <>
                      <span className="street-finder__verdict">
                        <b>{verdict.label}</b> · {picked.kind}
                        {picked.order && picked.total ? (
                          <>
                            {" · "}
                            <span className="street-finder__rank">
                              주변 길 {picked.total.toLocaleString()}개 중 {picked.order}등
                            </span>
                          </>
                        ) : null}
                      </span>
                      <span className="street-finder__tip">{verdict.tip}</span>
                    </>
                  );
                })()}
                {center && <span className="street-finder__addr">{picked.address ?? "주소 찾는 중…"}</span>}
              </div>
            )}
            <p className="street-finder__note">
              길 모양으로 본 판단이에요 — 임대료·공실·경쟁 가게는 계약 전에 따로 확인하세요.
            </p>
          </>
        ) : (
          <p className="street-finder__hint">
            주소를 넣으면 주변 길을 사람 흐름 색으로 칠하고 목이 좋은 길 3곳을 짚어 드려요.
          </p>
        )}
        <button type="button" className="street-finder__ai" onClick={onOpenChat}>
          AI에게 물어보기 — 이 자리에 이 업종 내도 될까?
        </button>
      </section>
    </div>
  );
}
