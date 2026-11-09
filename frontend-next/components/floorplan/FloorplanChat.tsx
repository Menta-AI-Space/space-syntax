"use client";

// 도면 상담(2026-11-09) — 분석 결과를 두고 일반인이 되묻고, AI가 쉬운 말로 답한다.
// 사실은 전부 분석 응답(카드·방·규칙 요약·AI 해석)에 있고 서버가 그것만 모델에 준다.
// 답이 카드 하나를 가리키면(card) 부모가 그 카드 구역을 도면에 칠한다 — 글만으로는
// 어디인지 모른다(SPEC_FLOORPLAN_TABS_UI 「하단 고정 AI 채팅」 요구).
// 라우터는 stateless라 분석 응답과 지금까지의 대화를 매번 함께 보낸다.

import { useEffect, useRef, useState } from "react";

import { SpeakButton } from "@/components/SpeakButton";
import { api, ApiClientError, type FloorplanAnalyzeResponse } from "@/lib/api";
import { buildChatMessages, cardToIssueIndex, suggestedQuestions, type FloorplanChatLine } from "@/lib/floorplanChat";
import { stopSpeaking } from "@/lib/speech";
import { useSpeechInput } from "@/lib/useSpeechInput";

interface Props {
  result: FloorplanAnalyzeResponse;
  /** 화면에 보이는 AI 해석(도착했으면) — 모델이 같은 말을 되풀이하지 않게 함께 보낸다. */
  narrative: string | null;
  /** 답이 가리키는 카드를 도면에 보여 달라는 요청(카드 목록 인덱스, 0부터). */
  onShowCard: (index: number) => void;
}

export function FloorplanChat({ result, narrative, onShowCard }: Props) {
  const [lines, setLines] = useState<FloorplanChatLine[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const listRef = useRef<HTMLDivElement>(null);
  const issues = result.issues || [];
  // 말로 묻기(2026-11-09 소유자 「붙여」) — AI 질의창과 같은 브라우저 음성 인식. 들은 말은 바로 보내지 않고
  // 입력칸에 넣는다(useSpeechInput 주석: 잘못 들은 걸 고칠 기회). 「68번 매장」 같은 숫자는 자주 틀린다.
  const speech = useSpeechInput((text) => setInput((prev) => (prev.trim() ? `${prev.trim()} ${text}` : text)));
  function startListening() {
    // 읽어 주던 답은 멈춘다 — 스피커 소리를 마이크가 받아 적는다.
    stopSpeaking();
    speech.start();
  }
  const suggestions = suggestedQuestions(issues, result.summary);

  // 새 답이 오면 목록 끝으로 — 질문이 길어지면 답이 접힌 영역 아래에 있다.
  useEffect(() => {
    const el = listRef.current;
    if (el && lines.length) el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  async function ask(question: string) {
    const q = question.trim();
    if (!q || loading) return;
    setError("");
    setInput("");
    const next: FloorplanChatLine[] = [...lines, { role: "user", content: q }];
    setLines(next);
    setLoading(true);
    try {
      const answer = await api.floorplanChat({
        summary: result.summary,
        issues,
        rooms: result.rooms || [],
        rule_text: result.explanation.text,
        narrative,
        messages: buildChatMessages(lines, q),
      });
      const index = cardToIssueIndex(answer.card, issues.length);
      setLines([...next, { role: "assistant", content: answer.text, card: index == null ? null : index + 1, source: answer.source }]);
      if (index != null) onShowCard(index);
    } catch (e) {
      // 404는 서버에 상담 경로가 아직 없는 것(배포 전) — 「Not Found」 한 단어로는 사용자가 무슨 뜻인지 모른다.
      setError(
        e instanceof ApiClientError && e.status === 404
          ? "상담 기능이 아직 서버에 올라가지 않았습니다. 잠시 뒤 다시 시도해 주세요."
          : e instanceof Error
            ? e.message
            : "답을 받지 못했습니다.",
      );
      // 실패한 질문은 입력칸에 되돌려 둔다 — 다시 치게 하지 않는다.
      setLines(lines);
      setInput(q);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="floorplan-chat" aria-label="도면 상담">
      <div className="floorplan-chat-head">
        <strong>이 도면에 대해 물어보세요</strong>
        <span>쉬운 말로 답하고, 답이 가리키는 자리는 위 도면에 칠해 드립니다.</span>
      </div>

      {lines.length > 0 && (
        <div className="floorplan-chat-list" ref={listRef} role="log" aria-live="polite">
          {lines.map((line, i) => (
            <div key={i} className={`floorplan-chat-line floorplan-chat-line--${line.role}`}>
              <div className="floorplan-chat-bubble">
                {line.content.split(/\n{2,}/).map((para, j) => (
                  <p key={j}>{para}</p>
                ))}
              </div>
              {line.role === "assistant" && (
                <div className="floorplan-chat-tools">
                  <SpeakButton text={line.content} />
                  {line.card != null && (
                    <button type="button" className="floorplan-chat-card-btn" onClick={() => onShowCard(line.card! - 1)}>
                      {line.card}번 카드 자리 도면에서 보기
                    </button>
                  )}
                  {line.source === "rule" && <small>AI 없이 요약문으로 답했습니다</small>}
                </div>
              )}
            </div>
          ))}
          {loading && <p className="floorplan-chat-loading">답을 쓰는 중…</p>}
        </div>
      )}
      {loading && lines.length === 0 && <p className="floorplan-chat-loading">답을 쓰는 중…</p>}

      {error && <div className="status-banner status-banner--error">{error}</div>}

      <div className="floorplan-chat-chips" aria-label="예시 질문">
        {suggestions.map((q) => (
          <button key={q} type="button" className="floorplan-chat-chip" disabled={loading} onClick={() => ask(q)}>
            {q}
          </button>
        ))}
      </div>

      <form
        className="floorplan-chat-form"
        onSubmit={(e) => {
          e.preventDefault();
          void ask(input);
        }}
      >
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={speech.listening ? "듣는 중… 말씀하세요" : "예: 2번 카드가 무슨 뜻이에요?"}
          aria-label="도면에 대한 질문"
          maxLength={2000}
          disabled={loading}
        />
        {speech.supported && (
          <button
            type="button"
            className={`floorplan-chat-mic${speech.listening ? " floorplan-chat-mic--on" : ""}`}
            onClick={speech.listening ? speech.stop : startListening}
            disabled={loading}
            aria-pressed={speech.listening}
            aria-label={speech.listening ? "음성 입력 멈추기" : "말로 묻기"}
            title={speech.listening ? "다시 누르면 멈춥니다" : "말로 묻기 (크롬·엣지)"}
          >
            {speech.listening ? "■ 듣는 중" : "🎤 말로 묻기"}
          </button>
        )}
        <button type="submit" className="primary-button" disabled={loading || !input.trim()}>
          물어보기
        </button>
      </form>
      {speech.error && (
        <p className="floorplan-chat-loading" role="status">
          {speech.error}
        </p>
      )}
      <p className="floorplan-chat-note">벽과 문의 배치만 보고 계산한 결과라 실제 매출·손님 수로 확인된 것은 아닙니다.</p>
    </section>
  );
}
