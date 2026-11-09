/** LLM 답변 텍스트를 표/리스트/제목+본문 블록으로 파싱한다.
 *
 * AgentChat 안에 있던 것을 빼 왔다 — 컴포넌트 파일 안에 있으면 테스트가 못
 * 붙는데, 이 파서는 "LLM이 서식을 어떻게 흘려보내든 사람이 읽게 만든다"가
 * 임무라 회귀가 잦다. 실제로 표가 한 줄로 붙어 와 원문 그대로 노출된 사고가
 * 라이브에서 났다(아래 normalizeCollapsedTable).
 */

export interface InlineRun { text: string; bold: boolean }

export type AnswerBlock =
  | { kind: "text"; title: string; body: InlineRun[] }
  | { kind: "list"; title?: string; ordered: boolean; items: InlineRun[][] }
  | { kind: "table"; title?: string; header: InlineRun[][]; rows: InlineRun[][][] };

export function parseInline(text: string): InlineRun[] {
  return text
    .split(/\*\*(.+?)\*\*/g)
    .map((part, i) => ({ text: part, bold: i % 2 === 1 }))
    .filter((run) => run.text !== "");
}

const LIST_MARKER = /^\s*(?:[-*•]|\d+[.)])\s+/;
const ORDERED_MARKER = /^\s*\d+[.)]\s+/;

/** 표 구분줄( |---|---| 류)인지 확인한다 — 헤더 다음 줄에만 나온다. */
function isTableRuleLine(line: string): boolean {
  return /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$/.test(line);
}

function splitTableRow(line: string): InlineRun[][] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => parseInline(cell.trim()));
}

/** 줄바꿈 없이 한 줄로 붙어 온 표의 행 경계를 되살린다.
 *
 * LLM이 표를 "| 지표 | 값 | |---|---| | 거주인구 | 3,472명 |"처럼 공백으로
 * 이어 보낼 때가 있다 — 마크다운에선 행이 줄바꿈으로 나뉘므로 파서가 표인 줄
 * 모르고 원문 그대로(파이프 포함) 노출했다. 라이브에서 "서울 비교" 섹션이
 * 정확히 이 꼴로 보였다.
 *
 * 행 경계의 서명은 "닫는 파이프 + 공백 + 여는 파이프"(`| |`)다. 셀 사이는
 * 파이프가 하나뿐이라 안 걸린다. 빈 셀(`||`)은 공백이 없으므로 역시 안 걸린다.
 * 구분줄이 있는 블록에만 손댄다 — 표가 아닌 문장의 파이프는 그대로 둔다.
 */
export function normalizeCollapsedTable(block: string): string {
  if (!/\|\s*:?-{2,}/.test(block)) return block;
  return block.replace(/\|\s+\|/g, "|\n|");
}

/** 답변 한 블록(빈 줄로 구분된 문단)을 표/리스트/텍스트로 분류한다. */
export function parseAnswerBlock(raw: string): AnswerBlock {
  const block = normalizeCollapsedTable(raw);
  let lines = block.split("\n").map((l) => l.trim()).filter(Boolean);

  // "**서울 비교**" 같은 제목 줄이 표·리스트와 **같은 블록**에 붙어 올 때가
  // 있다(빈 줄 없이). 제목 패턴을 먼저 맞추면 표 전체가 본문으로 빨려 들어가
  // 파이프가 그대로 노출된다 — 라이브에서 실제로 그랬다. 제목이 한 줄을
  // 통째로 차지하면 떼어 두고 나머지를 다시 분류한다.
  let title = "";
  if (lines.length > 1) {
    const alone = lines[0].match(/^\*\*(.+?)\*\*\s*[—–:·-]?\s*$/);
    if (alone) {
      title = alone[1].trim();
      lines = lines.slice(1);
    }
  }

  if (lines.length >= 2 && lines[0].includes("|") && isTableRuleLine(lines[1])) {
    return {
      kind: "table",
      title: title || undefined,
      header: splitTableRow(lines[0]),
      rows: lines.slice(2).filter((l) => l.includes("|")).map(splitTableRow),
    };
  }

  if (lines.length >= 1 && lines.every((l) => LIST_MARKER.test(l))) {
    return {
      kind: "list",
      title: title || undefined,
      ordered: ORDERED_MARKER.test(lines[0]),
      items: lines.map((l) => parseInline(l.replace(LIST_MARKER, ""))),
    };
  }

  if (title) {
    return { kind: "text", title, body: parseInline(lines.join(" ")) };
  }
  // 닫는 ** 바로 뒤가 공백·구분기호·끝일 때만 제목이다. 「**망원동 카페 자리**는 길 구조가…」처럼
  // 굵은 말이 문장의 주어면 떼지 않는다 — 떼면 본문이 「는 길 구조가…」로 시작했다 (2026-11-06).
  const titled = block.match(/^\*\*(.+?)\*\*(?=\s|[—–:·-]|$)\s*(?:[—–:·-]\s*)?([\s\S]*)$/);
  if (titled) {
    return { kind: "text", title: titled[1].trim(), body: parseInline(titled[2].trim()) };
  }
  return { kind: "text", title: "", body: parseInline(block.trim()) };
}

/** LLM 답변을 문단 단위(빈 줄 구분)로 쪼개 블록들로 만든다. */
export function parseAnswerBlocks(answer: string): AnswerBlock[] {
  // 빈 글이면 블록도 없다 — 빈 text 블록 하나를 돌려주면 「답변을 쓰지 못했습니다」 카드 밑에
  // 빈 칸(약 32px)이 그려졌다 (2026-11-06 UX 자동 개선, 390px).
  if (!answer || !answer.trim()) return [];
  const blocks = answer
    .split(/\n\s*\n/)
    .map((b) => b.trim())
    .filter(Boolean)
    .map(parseAnswerBlock);
  return blocks.length ? blocks : [{ kind: "text", title: "", body: parseInline(answer.trim()) }];
}

/** 표 행에서 "서울 상위 N%"를 찾아 숫자로 돌려준다. 없으면 null.
 *
 * 비교 표는 "이 동네가 서울에서 어디쯤인가"가 본론인데, 상위 1%와 상위 99%가
 * 같은 글자 크기의 텍스트로만 나오면 눈이 훑어서는 차이가 안 잡힌다 — 막대
 * 하나면 즉시 잡힌다. 그리는 쪽에서 이 값으로 채움 폭(100-N)을 정한다.
 */
export function rowTopPercent(row: InlineRun[][]): number | null {
  for (const cell of row) {
    const text = cell.map((r) => r.text).join("");
    const m = text.match(/상위\s*(\d+(?:\.\d+)?)\s*%/);
    if (m) {
      const pct = parseFloat(m[1]);
      if (pct >= 0 && pct <= 100) return pct;
    }
  }
  return null;
}
