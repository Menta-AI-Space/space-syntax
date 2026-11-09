"use client";

import { useCallback, useRef, useState } from "react";

// 목적별 판정 요청 하나의 수명(요청 → 로딩 → 결과/실패 → 재시도)을 담는다.
// 다섯 목적이 같은 규칙을 쓰도록 한 곳에만 둔다: 마지막 요청만 화면에 반영하고
// (레이스 가드), 실패해도 같은 조건으로 그대로 다시 쏠 수 있어야 한다.
export interface AgentRun<Req, Res> {
  loading: boolean;
  error: string;
  result: Res | null;
  /** 마지막으로 보낸 요청 — 재시도와 결과 화면의 입력값 표시에 쓴다. */
  request: Req | null;
  run: (body: Req) => void;
  retry: () => void;
}

export function useAgentRun<Req, Res>(
  call: (body: Req) => Promise<Res>,
  fallbackError: string
): AgentRun<Req, Res> {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<Res | null>(null);
  const [request, setRequest] = useState<Req | null>(null);
  const seqRef = useRef(0);

  const run = useCallback((body: Req) => {
    const seq = ++seqRef.current;
    setRequest(body);
    setLoading(true);
    setError("");
    setResult(null);
    call(body).then(
      (value) => {
        if (seq !== seqRef.current) return;
        setResult(value);
        setLoading(false);
      },
      (err: unknown) => {
        if (seq !== seqRef.current) return;
        // 백엔드가 아직 없거나(404) 죽어도 화면은 오류 배너에서 멈춘다.
        setError(err instanceof Error ? err.message : fallbackError);
        setLoading(false);
      }
    );
  }, [call, fallbackError]);

  const retry = useCallback(() => {
    if (request !== null) run(request);
  }, [request, run]);

  return { loading, error, result, request, run, retry };
}
