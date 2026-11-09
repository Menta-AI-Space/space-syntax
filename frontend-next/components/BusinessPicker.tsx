"use client";

import { useEffect, useId, useRef, useState } from "react";
import { api, type UpjongSearchResult } from "@/lib/api";

export interface UpjongSelection {
  inds_lcls_cd?: string | null;
  inds_mcls_cd?: string | null;
  inds_scls_cd?: string | null;
  business_type?: string;
  label: string;
  breadcrumb: string;
}

function resultToSelection(result: UpjongSearchResult): UpjongSelection {
  return { inds_lcls_cd: result.inds_lcls_cd, inds_mcls_cd: result.inds_mcls_cd, inds_scls_cd: result.inds_scls_cd, business_type: "custom", label: result.label, breadcrumb: result.breadcrumb };
}

// 업종 입력창 하나 + 자동완성 드롭다운 (프리셋 버튼·카드 헤딩 없음 —
// NICE bizmap의 "분석업종" 입력과 같은 형태). 선택하면 입력창에 업종명이
// 남고, 다시 타이핑하면 재검색된다.
export function BusinessPicker({
  value,
  onChange,
  label,
  onConfirm,
}: {
  value: UpjongSelection | null;
  onChange: (value: UpjongSelection) => void;
  /** 이미 고른 업종이 입력칸에 그대로 있을 때 누른 Enter — 단계별 입력에서 다음 단계로 넘긴다.
   *  첫 Enter는 업종을 고르고(어느 분류로 잡혔는지 보이게), 다음 Enter가 넘어간다. */
  onConfirm?: () => void;
  /** 보이는 라벨을 이 컴포넌트가 직접 그린다. 바깥에서 `<label htmlFor>`로
   *  묶으면 화면에 여러 개가 떠 있을 때 엉뚱한 입력창에 걸린다(아래 참고). */
  label?: string;
}) {
  // 이 컴포넌트는 한 화면에 여러 번 그려진다(입지 분석 폼 + 새 분석/후보
  // 인테이크의 단계들). 고정 id를 쓰면 id가 5개 중복돼 라벨 클릭이 숨겨진
  // 첫 번째 입력창으로 가고, 나머지는 접근성 이름이 아예 없었다.
  const inputId = useId();
  const [query, setQuery] = useState(value?.label ?? "");
  const [dirty, setDirty] = useState(false);
  const [open, setOpen] = useState(false);
  const [results, setResults] = useState<UpjongSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [status, setStatus] = useState("");
  const searchSeqRef = useRef(0);
  // 검색 결과가 오기 전(200ms 대기·요청 중)에 누른 Enter. 휴대폰에선 「카페」 치고 바로 자판의
  // 이동을 누르는 게 보통인데, 그때 결과가 비어 있어 Enter가 버려지고 「목록에서 업종을 선택해야
  // 반영됩니다」만 남았다 — 결과가 오면 그 Enter를 마저 처리한다 (2026-11-07 UX 자동 개선, 390px).
  const pickOnResultsRef = useRef(false);

  // 입력창에 적힌 글자가 실제로 분석에 쓰이는 업종과 다른 상태. 목록에서
  // 고르기 전까지 선택은 바뀌지 않으므로, 여기서 알려 주지 않으면 "오마카세"라고
  // 적힌 화면에서 직전 업종(또는 일반 업종)으로 분석이 돌아간다.
  const typed = query.trim();
  const unconfirmed = dirty && typed.length > 0 && typed !== (value?.label ?? "");

  useEffect(() => {
    const q = query.trim();
    // Bump the sequence on EVERY query change so in-flight responses for an
    // older query can never overwrite newer results (the "typed 샐러드 but see
    // 카페 results" race).
    const seq = ++searchSeqRef.current;
    if (!dirty || q.length < 2) {
      pickOnResultsRef.current = false;
      setResults([]);
      setSearching(false);
      setStatus("");
      return;
    }
    setSearching(true);
    const timer = window.setTimeout(() => {
      api.upjongSearch(q, 30)
        .then((r) => {
          if (seq !== searchSeqRef.current) return;
          setSearching(false);
          setStatus("");
          if (pickOnResultsRef.current && r.length > 0) {
            pickLatestRef.current(r[0]);
            return;
          }
          pickOnResultsRef.current = false;
          setResults(r);
        })
        .catch((err) => {
          if (seq !== searchSeqRef.current) return;
          pickOnResultsRef.current = false;
          setResults([]);
          setSearching(false);
          setStatus(err instanceof Error ? err.message : "업종 검색 실패");
        });
    }, 200);
    return () => window.clearTimeout(timer);
  }, [query, dirty]);

  function pick(result: UpjongSearchResult) {
    pickOnResultsRef.current = false;
    onChange(resultToSelection(result));
    setQuery(result.label);
    setDirty(false);
    setOpen(false);
    setResults([]);
  }

  // 효과 안에서 부를 때 그 렌더의 onChange가 아니라 늘 지금 것을 쓰게 한다.
  const pickLatestRef = useRef(pick);
  pickLatestRef.current = pick;

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      // Enter는 "이걸로 하겠다"는 뜻이다. 첫 결과를 고르고 폼 제출은 막는다 —
      // 그대로 흘려보내면 방금 친 업종이 아니라 직전 선택으로 분석이 돈다.
      if (results.length > 0 && !searching) {
        e.preventDefault();
        pick(results[0]);
      } else if (onConfirm && value && !dirty && query.trim() === value.label) {
        // 2026-11-08 UX 자동 개선(390px): 「카페」 Enter로 고른 뒤 한 번 더 눌러도 무반응이라
        // 자판에 가린 「다음」 단추를 찾아야 했다.
        e.preventDefault();
        onConfirm();
      } else if (dirty && query.trim().length >= 2 && searching) {
        e.preventDefault();
        pickOnResultsRef.current = true;
      }
      return;
    }
    if (e.key === "Escape") {
      // 되돌리기: 입력창을 실제 선택값으로 맞춰 둘이 어긋난 상태를 없앤다.
      setQuery(value?.label ?? "");
      setDirty(false);
      setOpen(false);
    }
  }

  return (
    <div
      className="upjong-picker"
      onBlur={(e) => {
        // 목록 밖으로 초점이 나가면 닫는다. 글자는 지우지 않고 남겨 둔 채
        // 아래 안내로 "아직 반영 안 됨"을 계속 보여 준다.
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      {label && <label className="field-label" htmlFor={inputId}>{label}</label>}
      <input
        id={inputId}
        aria-label={label ?? "분석 업종"}
        className="text-input"
        value={query}
        onChange={(e) => { pickOnResultsRef.current = false; setQuery(e.target.value); setDirty(true); setOpen(true); }}
        onFocus={() => { if (dirty) setOpen(true); }}
        onKeyDown={handleKeyDown}
        enterKeyHint={onConfirm && value && !dirty ? "next" : undefined}
        placeholder="업종 입력 (예: 카페, 샐러드, 학원)"
      />
      {open && dirty && (searching || results.length > 0 || (query.trim().length >= 2 && !status)) && (
        <div className="upjong-search-results">
          {searching && <p className="upjong-search-hint">검색 중…</p>}
          {results.slice(0, 10).map((result) => (
            <button
              className="upjong-search-item"
              type="button"
              key={`${result.type}-${result.inds_mcls_cd}-${result.inds_scls_cd || ""}`}
              onClick={() => pick(result)}
            >
              <span className="upjong-search-label">{result.label}</span>
              <span className="upjong-search-breadcrumb">{result.breadcrumb}</span>
              {result.matched_alias && <span className="upjong-search-alias">“{result.matched_alias}” 관련</span>}
            </button>
          ))}
          {!searching && results.length === 0 && query.trim().length >= 2 && !status && (
            <p className="upjong-search-hint">검색 결과 없음</p>
          )}
        </div>
      )}
      {value && !dirty && <p className="site-panel-meta">{value.breadcrumb}</p>}
      {unconfirmed && !status && (
        <p className="site-panel-meta upjong-unconfirmed">
          {value
            ? `목록에서 업종을 선택해야 반영됩니다. 지금은 "${value.label}" 기준으로 분석합니다.`
            : "목록에서 업종을 선택해야 반영됩니다. 아직 업종이 지정되지 않았습니다."}
        </p>
      )}
      {status && <p className="status-banner status-banner--error">{status}</p>}
    </div>
  );
}
