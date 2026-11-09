import type { ParcelStructure, ParcelStructureSummary } from "@/lib/api";
// 같은 lib 안이라 상대경로로 둔다 — 테스트 러너(node --experimental-strip-types)는
// tsconfig의 "@/" 별칭을 풀지 못해 값 import가 깨진다.
import { GRADE_COLORS, UNGRADED_COLOR, money, outerRings, topPercentText } from "./parcelGrade.ts";

/**
 * 필지·획지 등급을 한 장짜리 HTML 보고서로 굳힌다.
 *
 * 도면 보고서(`floorplanReport.ts`)와 같은 방식이다 — 서버를 거치지 않고, 이미지가
 * 박힌 자체 완결 HTML 하나. 다른 점은 **그림을 직접 그린다**는 것이다. 지도는
 * 카카오 타일이라 캔버스로 못 뽑고, 뽑더라도 남의 지도를 보고서에 실어 배포하는
 * 셈이 된다. 대신 우리가 가진 필지 경계를 SVG로 그리면 출처가 분명하고 인쇄에도
 * 깨끗하다.
 */

export interface ParcelReportInput {
  title: string;
  /** "고른 12필지" 또는 "논현동 일대 블록"처럼 대상이 무엇인지. */
  scopeNote: string;
  summary: ParcelStructureSummary;
  parcels: ParcelStructure[];
  caveats: string[];
  /** 총액을 넣고 돌렸다면 그 값(만원). 배분 열을 낼지 결정한다. */
  totalManwon?: number | null;
}

const SVG_W = 820;
const SVG_H = 560;

function esc(value: unknown): string {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** 필지 경계를 SVG로. 위경도를 그대로 쓰되 위도 방향 축척만 보정한다. */
function parcelSvg(parcels: ParcelStructure[]): string {
  const rings: { d: string; color: string }[] = [];
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const collected: { ring: [number, number][]; color: string }[] = [];

  for (const parcel of parcels) {
    if (!parcel.geometry) continue;
    const color = parcel.grade ? GRADE_COLORS[parcel.grade] : UNGRADED_COLOR;
    for (const ring of outerRings(parcel.geometry)) {
      collected.push({ ring, color });
      for (const [lng, lat] of ring) {
        if (lng < minX) minX = lng;
        if (lng > maxX) maxX = lng;
        if (lat < minY) minY = lat;
        if (lat > maxY) maxY = lat;
      }
    }
  }
  if (!collected.length || !Number.isFinite(minX)) {
    return '<p class="note">필지 경계가 없어 그림을 그리지 못했습니다.</p>';
  }

  // 경도 1도는 위도 1도보다 짧다(위도 37.5°에서 약 0.79배). 보정하지 않으면
  // 필지가 가로로 늘어나 실제 모양과 달라진다.
  const latScale = Math.cos(((minY + maxY) / 2) * (Math.PI / 180));
  const spanX = Math.max((maxX - minX) * latScale, 1e-9);
  const spanY = Math.max(maxY - minY, 1e-9);
  const pad = 12;
  const scale = Math.min((SVG_W - pad * 2) / spanX, (SVG_H - pad * 2) / spanY);
  const offsetX = (SVG_W - spanX * scale) / 2;
  const offsetY = (SVG_H - spanY * scale) / 2;

  for (const { ring, color } of collected) {
    const points = ring.map(([lng, lat]) => {
      const x = offsetX + (lng - minX) * latScale * scale;
      // SVG는 y가 아래로 커진다 — 뒤집지 않으면 남북이 거꾸로 나온다.
      const y = offsetY + (maxY - lat) * scale;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    rings.push({ d: `M${points.join("L")}Z`, color });
  }

  const paths = rings
    .map((r) => `<path d="${r.d}" fill="${r.color}" fill-opacity="0.65" stroke="#94a3b8" stroke-width="0.6"/>`)
    .join("");
  return `<svg viewBox="0 0 ${SVG_W} ${SVG_H}" width="100%" role="img" aria-label="필지 등급 도면">${paths}</svg>`;
}

function legend(): string {
  const items = [1, 2, 3, 4, 5]
    .map((g) => `<span><i style="background:${GRADE_COLORS[g]}"></i>${g}등급</span>`)
    .join("");
  return `<div class="legend">${items}<span><i style="background:${UNGRADED_COLOR}"></i>등급 없음</span></div>`;
}

export function buildParcelReport(input: ParcelReportInput): string {
  const { title, scopeNote, summary, parcels, caveats, totalManwon } = input;
  const ranked = parcels
    .filter((p) => p.grade)
    .sort((a, b) => (b.percentile ?? 0) - (a.percentile ?? 0));
  const ungraded = parcels.filter((p) => !p.grade);
  const hasMoney = ranked.some((p) => p.allocation_manwon != null);
  const totalArea = parcels.reduce((sum, p) => sum + p.area_sqm, 0);
  const printedAt = new Date().toLocaleString("ko-KR");

  const facts: Array<[string, string]> = [
    ["대상 필지", `${summary.parcel_count.toLocaleString()}곳`],
    ["등급 매긴 곳", `${summary.graded_count.toLocaleString()}곳`],
    ["합계 면적", `${totalArea.toFixed(0)}㎡`],
    ["상·하위 10% 차이", summary.structure_spread ? `${summary.structure_spread}배` : "—"],
    ["코너 필지", `${summary.corner_count}곳`],
  ];
  if (summary.landlocked_count) facts.push(["등급 없음", `${summary.landlocked_count}곳`]);
  if (totalManwon) facts.push(["나눈 총액", `${money(totalManwon)}원`]);

  const rows = ranked
    .map(
      (parcel, i) => `<tr>
      <td>${i + 1}</td>
      <td>${esc(parcel.pnu)}</td>
      <td>${parcel.area_sqm.toFixed(0)}㎡</td>
      <td style="color:${GRADE_COLORS[parcel.grade as number]}">${parcel.grade}등급</td>
      <td>${parcel.percentile != null ? esc(topPercentText(parcel.percentile)) : "—"}</td>
      <td>${parcel.frontage_count}</td>
      ${hasMoney ? `<td>${parcel.allocation_manwon != null ? esc(money(parcel.allocation_manwon)) : "—"}</td>` : ""}
    </tr>`,
    )
    .join("");

  const ungradedBlock = ungraded.length
    ? `<h2>등급을 매기지 못한 필지 ${ungraded.length}곳</h2>
       <p class="note">20m 안에서 접한 도로를 찾지 못했습니다. <strong>나쁜 자리라는 뜻이 아니라 모른다는 뜻</strong>입니다 — 맹지이거나 도로 데이터가 닿지 않는 자리입니다.</p>
       <p class="note">${ungraded.map((p) => esc(p.pnu)).join(", ")}</p>`
    : "";

  return `<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>${esc(title)}</title>
<style>
  :root { color-scheme: light; }
  body { font-family: "Malgun Gothic", "맑은 고딕", system-ui, sans-serif; color:#111827; margin:0; padding:32px; line-height:1.6; background:#fff; }
  header { border-bottom:2px solid #111827; padding-bottom:12px; margin-bottom:20px; }
  h1 { font-size:20px; margin:0 0 4px; }
  h2 { font-size:15px; margin:24px 0 8px; border-left:4px solid #2563eb; padding-left:8px; }
  .meta { color:#6b7280; font-size:12px; }
  .note { color:#6b7280; font-size:12px; margin:4px 0 8px; }
  table { width:100%; border-collapse:collapse; font-size:12px; margin:8px 0; }
  th, td { text-align:left; padding:4px 6px; border-bottom:1px solid #f3f4f6; }
  th { background:#f9fafb; color:#374151; }
  dl.facts { display:grid; grid-template-columns:repeat(auto-fit, minmax(120px,1fr)); gap:8px; margin:8px 0; }
  dl.facts dt { color:#6b7280; font-size:11px; }
  dl.facts dd { margin:0; font-size:15px; font-weight:600; }
  .legend { display:flex; flex-wrap:wrap; gap:10px; font-size:11px; color:#374151; margin:6px 0 10px; }
  .legend i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:4px; }
  .plan { border:1px solid #e5e7eb; border-radius:6px; }
  footer { margin-top:28px; padding-top:10px; border-top:1px solid #e5e7eb; color:#6b7280; font-size:11px; }
  footer li { margin-bottom:4px; }
  @media print {
    body { padding:0; }
    h2 { break-after: avoid; }
    table, .plan { break-inside: avoid; }
  }
</style>
</head>
<body>
<header>
  <h1>${esc(title)}</h1>
  <div class="meta">${esc(scopeNote)} · 작성 ${esc(printedAt)}</div>
</header>

${legend()}
<div class="plan">${parcelSvg(parcels)}</div>

<dl class="facts">
  ${facts.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}
</dl>

<h2>등급 순</h2>
<table>
  <thead><tr><th>순위</th><th>지번코드</th><th>면적</th><th>등급</th><th>범위 내 순위</th><th>접한 길</th>${hasMoney ? "<th>배분</th>" : ""}</tr></thead>
  <tbody>${rows}</tbody>
</table>

${ungradedBlock}

<footer>
  <strong>이 보고서를 읽는 법</strong>
  <ul>
    ${caveats.map((c) => `<li>${esc(c)}</li>`).join("")}
  </ul>
</footer>
</body>
</html>`;
}

/** 보고서를 파일로 내려받는다. 열어서 인쇄하면 PDF가 된다. */
export function downloadParcelReport(input: ParcelReportInput, stem: string): void {
  const blob = new Blob([buildParcelReport(input)], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${stem}_${new Date().toISOString().slice(0, 10)}.html`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
