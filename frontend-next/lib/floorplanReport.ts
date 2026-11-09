import type {
  FloorplanAnalyzeResponse,
  FloorplanCompareResponse,
  FloorplanDelta,
  FloorplanIssue,
  FloorplanLabel,
  FloorplanRoom,
} from "@/lib/api";

/**
 * 분석 결과를 한 장짜리 HTML 보고서로 굳힌다.
 *
 * 서버를 거치지 않는다. 결과가 이미 브라우저에 다 있고, 도면 그림도 캔버스에
 * 그려져 있어 PNG로 바로 뽑을 수 있다. PDF 라이브러리(jsPDF 등)를 넣지 않은
 * 이유도 같다 — 한글 폰트를 임베드해야 하고 표 레이아웃을 손으로 그려야 하는데,
 * 브라우저의 "PDF로 저장"이 그 둘을 이미 제대로 한다.
 *
 * 그래서 산출물은 **자체 완결 HTML 파일 하나**다. 이미지가 data URI로 박혀 있어
 * 메일에 첨부해도 그대로 열리고, 받는 사람이 인쇄하면 PDF가 된다.
 */

export interface ReportInput {
  fileName: string;
  result: FloorplanAnalyzeResponse;
  /** 도면 캔버스 스냅샷 (PNG data URI). 없으면 그림 없이 표만 낸다. */
  planImage: string | null;
  /** 화면에 떠 있던 AI 해석. 없으면 규칙 문장이 들어간다. */
  narrative: { text: string; source: string } | null;
  comparison: FloorplanCompareResponse | null;
  scaleNote: string;
  /** 도면 실명 — 위치 글을 「16번·23번 매장 사이」로 쓰는 데 쓴다. 없으면 방향만. */
  labels?: FloorplanLabel[];
}

const SEVERITY_KO: Record<FloorplanIssue["severity"], string> = {
  high: "즉시 검토",
  medium: "개선 권장",
  low: "참고",
};

const SEVERITY_COLOR: Record<FloorplanIssue["severity"], string> = {
  high: "#dc2626",
  medium: "#ea580c",
  low: "#2563eb",
};

/** 사용자 데이터(실명·파일명)가 그대로 HTML에 들어가므로 반드시 막는다. */
function esc(value: unknown): string {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function roomArea(room: FloorplanRoom): string {
  return room.area_m2 != null ? `${room.area_m2.toFixed(0)}㎡` : `${room.cell_count}칸`;
}

/** 도면 글자 「매장 07」 → 「7번 매장」 (backend `floorplan_issues._room`과 같은 규칙). */
export function roomName(text: string): string {
  const m = /^\s*(매장|호실|점포|상가|사무실|룸|방)\s*0*(\d+)\s*$/.exec(text);
  return m ? `${Number(m[2])}번 ${m[1]}` : text.trim();
}

/**
 * 공간별 비교 표 「지나가는 길목」 칸 — 원래 choice_mean 원값(「2154」「807」)을 그대로 찍어
 * 단위도 뜻도 알 수 없었다(2026-11-08 UX 자동 개선). 계산된 공간끼리 통행 순위를 매겨
 * 「상위 N%」로 보인다(옆 「목」 칸과 같은 말투). 계산에서 빠진 공간(analyzed_pct 0)은 null.
 */
export function roomThroughRanks(rooms: FloorplanRoom[]): Map<number, string> {
  const analyzed = rooms.filter((r) => r.analyzed_pct > 0).sort((a, b) => b.choice_mean - a.choice_mean);
  const n = analyzed.length;
  const out = new Map<number, string>();
  let rank = 0;
  analyzed.forEach((room, i) => {
    // 같은 값은 같은 순위
    if (i === 0 || room.choice_mean !== analyzed[i - 1].choice_mean) rank = i + 1;
    out.set(room.index, `상위 ${Math.max(1, Math.ceil((rank / n) * 100))}%`);
  });
  return out;
}

/**
 * 카드 아래 작은 위치 글 — 「도면 왼쪽 위에서 오른쪽으로 112m」는 실무자에게 안 와닿는다
 * (2026-10-27). 도면의 어느 쪽인지 + 가까운 실명 둘로 말한다: 「도면 가운데쯤, 16번·23번 매장 사이」.
 * 가까운 실명이 없으면 조금 더 먼 실명 「근처」, 실명이 아예 없는 도면만 m 좌표.
 */
export function issuePlace(
  issue: Pick<FloorplanIssue, "x" | "y" | "x_m" | "y_m">,
  labels: Pick<FloorplanLabel, "text" | "x" | "y">[],
  width: number,
  height: number,
): string {
  const ns = issue.y < height / 3 ? "북" : issue.y > (2 * height) / 3 ? "남" : "";
  const ew = issue.x < width / 3 ? "서" : issue.x > (2 * width) / 3 ? "동" : "";
  const side = ns + ew ? `도면 ${ns}${ew}쪽` : "도면 가운데쯤";
  const reach = Math.max(width, height) * 0.12;
  const near = labels
    .map((l) => ({ name: roomName(l.text), d: Math.hypot(l.x - issue.x, l.y - issue.y) }))
    .filter((l) => l.d <= reach)
    .sort((a, b) => a.d - b.d)
    .map((l) => l.name)
    .filter((name, i, all) => all.indexOf(name) === i)
    .slice(0, 2);
  if (near.length === 2) {
    const [a, b] = near;
    const kindA = a.split(" ").pop();
    const kindB = b.split(" ").pop();
    // 「16번 매장·23번 매장」 → 「16번·23번 매장」
    const pair = kindA === kindB && /번 /.test(a) && /번 /.test(b) ? `${a.split(" ")[0]}·${b}` : `${a}·${b}`;
    return `${side}, ${pair} 사이`;
  }
  if (near.length === 1) return `${side}, ${near[0]} 옆`;
  // 2026-11-07 UX 자동 개선: 넓은 외진 구역은 가운데가 빈 바닥이라 12% 안에 실명이 없어
  // 「도면 남동쪽 (도면 왼쪽 위에서 오른쪽으로 115m, 아래로 85m)」 좌표 괄호로 떨어졌다(샘플 몰
  // 1번 카드, 다른 카드는 매장 이름). 실명이 있는 도면이면 두 배 거리 안의 가장 가까운 실명을
  // 「근처」로 대고, 그마저 없으면 방향만 — 좌표는 실명 없는 도면에서만 쓴다.
  if (labels.length > 0) {
    const nearest = labels
      .map((l) => ({ name: roomName(l.text), d: Math.hypot(l.x - issue.x, l.y - issue.y) }))
      .filter((l) => l.d <= reach * 2)
      .sort((a, b) => a.d - b.d)[0];
    return nearest ? `${side}, ${nearest.name} 근처` : side;
  }
  if (issue.x_m != null && issue.y_m != null) {
    return `${side} (도면 왼쪽 위에서 오른쪽으로 ${issue.x_m.toFixed(0)}m, 아래로 ${issue.y_m.toFixed(0)}m)`;
  }
  return side;
}

/**
 * 카드가 가리키는 구역을 칠할 색 — **점수 색과 같은 방향**으로 맞춘다. 예전엔 중요도 색(참고=파랑)을
 * 써서 「가장 목이 좋은 곳」이 파랗게 칠해졌고, 큰 도면에선 파랑이 구석이라 「파란데 제일 잘 되는
 * 곳이냐」는 지적이 나왔다(2026-10-27). 사람이 모이는 곳은 주황, 구석은 파랑, 그 밖은 회색.
 */
export function issueZoneColor(issue: Pick<FloorplanIssue, "title">): string {
  if (issue.title.includes("가장 목이 좋은")) return "#ea580c";
  if (issue.title.includes("외진") || issue.title.includes("시야")) return "#2563eb";
  return "#6b7280";
}

function issueWhere(issue: FloorplanIssue, labels: FloorplanLabel[], width: number, height: number): string {
  return issuePlace(issue, labels, width, height);
}

function issuesSection(issues: FloorplanIssue[], labels: FloorplanLabel[], width: number, height: number): string {
  if (!issues.length) return "<p>눈에 띄는 문제를 찾지 못했습니다.</p>";
  return `<p class="note">번호(1, 2, 3…)는 도면 그림에 같은 번호 동그라미로 찍힌 자리입니다.</p><ol class="issues">${issues
    .map(
      (issue) => `<li>
      <div class="issue-head">
        <strong>${esc(issue.title)}</strong>
        <span style="color:${SEVERITY_COLOR[issue.severity]}">${SEVERITY_KO[issue.severity]}</span>
      </div>
      <p>${esc(issue.detail)}</p>
      <small>${esc(issue.evidence)} · ${esc(issueWhere(issue, labels, width, height))}</small>
    </li>`,
    )
    .join("")}</ol>`;
}

function roomsSection(rooms: FloorplanRoom[]): string {
  if (!rooms.length) return "";
  const analyzed = rooms.filter((r) => r.analyzed_pct > 0);
  const totalArea = rooms.reduce((sum, r) => sum + (r.area_m2 ?? 0), 0);
  const through = roomThroughRanks(rooms);
  const rows = rooms
    .map(
      (room) => `<tr>
      <td>${room.index}</td>
      <td>${esc(room.name || "이름 없음")}</td>
      <td>${esc(roomArea(room))}</td>
      <td>${
        room.analyzed_pct === 0
          ? "문 없음"
          : `상위 ${Math.max(1, Math.round(100 - room.integration_percentile) + 1)}%`
      }</td>
      <td>${through.get(room.index) ?? "—"}</td>
    </tr>`,
    )
    .join("");
  return `<h2>공간별 비교</h2>
    <p class="note">벽으로 닫힌 공간 ${rooms.length}곳${
      totalArea > 0 ? ` · 합계 약 ${totalArea.toFixed(0)}㎡` : ""
    }${rooms.length - analyzed.length > 0 ? ` · 문이 없어 계산에서 빠진 곳 ${rooms.length - analyzed.length}곳` : ""} · 「지나가는 길목」은 공간끼리 견준 통행 순위</p>
    <table>
      <thead><tr><th>순위</th><th>공간</th><th>넓이</th><th>목</th><th>지나가는 길목</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

/**
 * 비교표 한 칸 — 「목 1.74→1.83」처럼 단위 없는 지표값은 읽는 사람이 뜻을 모른다.
 * 보이는 범위는 실내 대비 %로, 꺾는 횟수는 「번」으로 바꾼다. 바꿀 수 없는 값(통행량 등)은
 * 고치기 전을 100으로 둔 지수(「100 (기준)」→「103」)로 보인다 — 처음엔 「—」였는데 전·후가
 * 둘 다 「—」라 값이 빠진 것처럼 읽혔다(2026-11-07 UX 자동 개선). 화면 카드와 PDF가 같이 쓴다.
 */
export function formatCompareValue(
  delta: Pick<FloorplanDelta, "key" | "unit"> & { before?: number },
  value: number,
  cellCount: number,
): string {
  if (delta.key === "connectivity_mean") {
    return cellCount ? `실내의 ${Math.max(1, Math.round((value / cellCount) * 100))}%` : "—";
  }
  if (!delta.unit) {
    const base = delta.before;
    if (!base || base <= 0 || !Number.isFinite(value)) return "—";
    return value === base ? "100 (기준)" : Math.round((value / base) * 100).toLocaleString();
  }
  if (delta.unit === "번") return `${value.toFixed(1)}번`;
  return `${Math.round(value).toLocaleString()}${delta.unit}`;
}

/** 비교 결과의 첫 줄 — 손님 발길이 잘 안 닿는 넓이가 몇 % 줄었나/늘었나. */
export function compareHeadline(comparison: FloorplanCompareResponse): string {
  // 로봇 통로를 잰 도면(물류센터)은 로봇 비율이 머리글 — 랙을 옮긴 뒤 가장 먼저 볼 숫자다(2026-11-09).
  const robot = comparison.deltas.find((d) => d.key === "robot_passable");
  if (robot && robot.direction !== "same") {
    const verb = robot.after > robot.before ? "늘었습니다" : "줄었습니다";
    return `로봇이 지나갈 수 있는 바닥이 ${robot.before.toFixed(0)}% → ${robot.after.toFixed(0)}%로 ${verb}`;
  }
  const dead = comparison.deltas.find((d) => d.key === "dead_area");
  if (!dead || dead.direction === "same") return "손님 발길이 잘 안 닿는 구석은 거의 그대로입니다";
  const verb = dead.after < dead.before ? "줄었습니다" : "늘었습니다";
  return `손님 발길이 잘 안 닿는 구석이 ${Math.abs(dead.change_pct).toFixed(0)}% ${verb}`;
}

export const COMPARE_WHY = "설계할 때 미리 고치면, 다 지은 뒤 오래 비는 구석 호실을 줄일 수 있습니다.";

function compareSection(comparison: FloorplanCompareResponse | null): string {
  if (!comparison) return "";
  const dirKo: Record<string, string> = { better: "좋아짐", worse: "나빠짐", same: "그대로" };
  const dirColor: Record<string, string> = { better: "#059669", worse: "#dc2626", same: "#6b7280" };
  const cells = comparison.before_summary?.cell_count ?? 0;
  const rows = comparison.deltas
    .map(
      (d) => `<tr>
      <td>${esc(d.label)}</td>
      <td>${esc(formatCompareValue(d, d.before, cells))}</td>
      <td>${esc(formatCompareValue(d, d.after, cells))}</td>
      <td style="color:${dirColor[d.direction]}">${
        d.direction === "same"
          ? "그대로"
          : `${d.change_pct > 0 ? "+" : ""}${d.change_pct.toFixed(0)}% ${dirKo[d.direction]}`
      }</td>
    </tr>`,
    )
    .join("");
  const list = (items: FloorplanIssue[]) =>
    items.length ? `<ul>${items.map((i) => `<li>${esc(i.title)}</li>`).join("")}</ul>` : "<p>없음</p>";
  return `<h2>바꾸면 무엇이 달라지나</h2>
    <p><strong>${esc(compareHeadline(comparison))}</strong></p>
    <table>
      <thead><tr><th>무엇을 봤나</th><th>고치기 전</th><th>고친 뒤</th><th>변화</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p>${esc(COMPARE_WHY)}</p>
    <div class="two-col">
      <div><h3>없어진 구석 ${comparison.resolved.length}</h3>${list(comparison.resolved)}</div>
      <div><h3>새로 생긴 구석 ${comparison.appeared.length}</h3>${list(comparison.appeared)}</div>
    </div>
    <p>${esc(comparison.summary)}</p>`;
}

export function buildFloorplanReport(input: ReportInput): string {
  const { fileName, result, planImage, narrative, comparison, scaleNote } = input;
  const summary = result.summary;
  const printedAt = new Date().toLocaleString("ko-KR");
  const explanation = narrative?.text || result.explanation.text;
  const isAi = narrative?.source === "llm";

  const facts: Array<[string, string]> = [
    ["살펴본 칸", summary.cell_count.toLocaleString()],
    ["평균 목", summary.integration_mean.toFixed(3)],
    ["가장 좋은 목", summary.integration_max.toFixed(3)],
    ["평균 걸음 수", summary.mean_depth_mean.toFixed(2)],
    ["한눈에 보이는 범위", summary.connectivity_mean.toFixed(1)],
    ["도면 반영률", `${summary.coverage_pct.toFixed(0)}%`],
  ];
  if (summary.cell_size_m != null) facts.push(["한 칸 크기", `${summary.cell_size_m.toFixed(1)}m`]);

  return `<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>도면 분석 보고서 — ${esc(fileName)}</title>
<style>
  :root { color-scheme: light; }
  body { font-family: "Malgun Gothic", "맑은 고딕", system-ui, sans-serif; color: #111827; margin: 0; padding: 32px; line-height: 1.6; background: #fff; }
  header { border-bottom: 2px solid #111827; padding-bottom: 12px; margin-bottom: 20px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  h2 { font-size: 15px; margin: 24px 0 8px; border-left: 4px solid #2563eb; padding-left: 8px; }
  h3 { font-size: 13px; margin: 12px 0 4px; }
  .meta { color: #6b7280; font-size: 12px; }
  .note { color: #6b7280; font-size: 12px; margin: 4px 0 8px; }
  img.plan { max-width: 100%; border: 1px solid #e5e7eb; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; margin: 8px 0; }
  th, td { text-align: left; padding: 4px 6px; border-bottom: 1px solid #f3f4f6; }
  th { background: #f9fafb; color: #374151; }
  dl.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 8px; margin: 8px 0; }
  dl.facts dt { color: #6b7280; font-size: 11px; }
  dl.facts dd { margin: 0; font-size: 15px; font-weight: 600; }
  ol.issues { padding-left: 18px; font-size: 12px; }
  ol.issues li { margin-bottom: 8px; }
  .issue-head { display: flex; justify-content: space-between; gap: 8px; }
  ol.issues p { margin: 2px 0; }
  ol.issues small { color: #6b7280; }
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; font-size: 12px; }
  .ai-tag { display: inline-block; font-size: 11px; color: #4f46e5; background: #eef2ff; border-radius: 999px; padding: 1px 8px; }
  footer { margin-top: 28px; padding-top: 10px; border-top: 1px solid #e5e7eb; color: #6b7280; font-size: 11px; }
  @media print {
    body { padding: 0; }
    h2 { break-after: avoid; }
    table, ol.issues li { break-inside: avoid; }
  }
</style>
</head>
<body>
<header>
  <h1>도면 분석 보고서</h1>
  <div class="meta">${esc(fileName)} · ${esc(scaleNote)} · 작성 ${esc(printedAt)}</div>
</header>

${planImage ? `<img class="plan" src="${planImage}" alt="분석한 도면">` : ""}

<h2>해석</h2>
${isAi ? '<span class="ai-tag">AI 해석</span>' : ""}
${explanation
  .split(/\n{2,}/)
  // 옛 소제목(「한눈에」「어디를 어떻게」)으로 저장된 해석도 새 이름으로 낸다 (2026-10-27).
  .map((p) => p.replace("**한눈에**", "**한 줄 요약**").replace("**어디를 어떻게**", "**고칠 곳과 방법**"))
  .map((p) => `<p>${esc(p).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")}</p>`)
  .join("")}

<dl class="facts">
  ${facts.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}
</dl>

<h2>발견된 문제</h2>
${issuesSection(result.issues || [], input.labels || [], result.summary.cols * result.summary.cell_size, result.summary.rows * result.summary.cell_size)}

${roomsSection(result.rooms || [])}

${compareSection(comparison)}

<footer>
  이 보고서는 도면의 <strong>벽 구조</strong>만으로 계산한 결과입니다. 가구 배치·문 폭·층간 이동은 반영되지 않았고,
  실제 매출로 검증된 수치가 아닙니다. 설계·임대 판단의 참고 자료로만 쓰십시오.
  ${summary.choice_sampled ? "도면이 커서 통행량은 표본으로 계산했으며, 작은 차이는 계산 오차일 수 있습니다." : ""}
</footer>
</body>
</html>`;
}

/** 보고서를 파일로 내려받는다. 사용자가 그 파일을 열어 인쇄하면 PDF가 된다. */
export function downloadFloorplanReport(input: ReportInput): void {
  const html = buildFloorplanReport(input);
  const blob = new Blob([html], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  const stem = input.fileName.replace(/\.[^.]+$/, "") || "도면";
  const stamp = new Date().toISOString().slice(0, 10);
  link.href = url;
  link.download = `${stem}_분석보고서_${stamp}.html`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // 즉시 해제하면 다운로드가 시작되기 전에 blob이 사라지는 브라우저가 있다.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/**
 * DXF가 아닌 파일을 골랐을 때 할 일까지 말한다. 종전엔 무엇을 고르든 「DXF 도면만 올릴 수
 * 있습니다.」 한 줄이라, DWG를 가진 사람은 바꾸는 법을, 사진·PDF를 가진 사람은 다음 할 일을
 * 몰랐다 (2026-11-07 UX 자동 개선). 사진·스캔을 받지 않는 원칙은 그대로다(CLAUDE.md §7).
 */
export function nonDxfMessage(fileName: string): string {
  const ext = /\.([a-z0-9]+)$/i.exec(fileName)?.[1]?.toLowerCase() ?? "";
  if (ext === "dwg") {
    return "DWG 파일은 바로 읽지 못합니다 — 캐드에서 「다른 이름으로 저장」으로 DXF 형식을 골라 저장한 뒤 올려 주세요.";
  }
  if (["png", "jpg", "jpeg", "heic", "heif", "gif", "webp", "bmp", "tif", "tiff", "pdf"].includes(ext)) {
    return "사진·스캔·PDF 도면은 읽지 못합니다 — 캐드로 그린 DXF 원본을 올려 주세요. 원본이 없으면 위 샘플 도면으로 먼저 살펴볼 수 있어요.";
  }
  return "DXF 도면만 올릴 수 있습니다 — 캐드 원본을 DXF 형식으로 저장해 올려 주세요.";
}
