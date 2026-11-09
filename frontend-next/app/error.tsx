"use client";

import { useEffect } from "react";

/**
 * 렌더 중 예외가 나면 Next가 화면 전체를 흰 페이지로 갈아치운다. 이 앱은
 * 외부 SDK(Kakao 지도)와 선택 필드가 많은 백엔드 응답, localStorage에 남은
 * 옛 저장값을 함께 그리기 때문에 한 컴포넌트의 예외로 분석 결과 전체를
 * 잃기 쉽다. 최소한 무슨 일이 났는지 알리고 다시 시도할 길은 남긴다.
 *
 * 이 경계는 app/layout.tsx 아래 렌더에만 걸린다 — layout 자체가 터지는 경우는
 * global-error가 필요하지만, layout은 폰트 링크와 body뿐이라 위험이 낮다.
 */
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // 사용자에게는 안 보이지만 개발자 콘솔에는 원문을 남긴다 — 화면에 스택을
    // 그대로 뿌리면 읽을 수도 없고 내부 사정만 노출된다.
    console.error("렌더 중 오류:", error);
  }, [error]);

  return (
    <main className="card" style={{ margin: "var(--space-6, 24px) auto", maxWidth: 560 }}>
      <h2>화면을 그리는 중 문제가 생겼습니다</h2>
      <p className="status-banner status-banner--error">
        분석 결과를 표시하지 못했습니다. 다시 시도해도 같은 화면이 나오면 다른
        위치나 업종으로 분석해 보세요.
      </p>
      {error.digest && (
        <p className="agent-step-hint">문제 코드: {error.digest}</p>
      )}
      <button type="button" className="primary-button" onClick={reset}>
        다시 시도
      </button>
    </main>
  );
}
