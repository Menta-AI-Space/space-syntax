// 발표 덱에 끼울 새 슬라이드 4장을 만든다 (06 저평가 탐색 · 07 필지 판정 ·
// 08 평면도 분석 · 16 vs 생성형 AI). 원본 덱은 건드리지 않는다 — graft.py가
// 이 출력을 원본에 끼워 넣고 페이지 번호를 다시 매긴다. README.md 참고.
//
// 색·서체는 원본 덱에서 그대로 뽑은 값이다(배경 F8F9FB / 진한 1B2A44 /
// 청록 eyebrow 2C7A88 / 레드 D8503A / 맑은 고딕). 새 색을 만들지 않는다.
const pptxgen = require("pptxgenjs");

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5 — matches base deck

// ── palette lifted from the base deck ──
const INK      = "1B2230"; // titles
const DARKBG   = "1B2A44"; // dark slide ground
const DARKCARD = "24385C"; // card on dark
const SLATE    = "3A4358";
const BODY     = "5B6472"; // body text
const TEAL     = "2C7A88"; // eyebrow / structure
const RED      = "D8503A"; // accent
const REDDK    = "9E4A38";
const REDTINT  = "FBEDEA";
const REDTINT2 = "FBE4DF";
const LINE     = "E1E4EA";
const ICEBLUE  = "C3D6E3";
const LIGHTBG  = "F8F9FB";
const GRAYCELL = "F0F2F5";
const WHITE    = "FFFFFF";
const F = "맑은 고딕";

const M = 0.6;            // left/right margin
const CW = 12.133;        // content width

// shared furniture ─────────────────────────────────────────────
function head(s, eyebrow, title, sub, dark) {
  s.addText(eyebrow, {
    x: M, y: 0.5, w: CW, h: 0.28, margin: 0,
    fontFace: F, fontSize: 12, bold: true, charSpacing: 2,
    color: dark ? ICEBLUE : TEAL,
  });
  s.addText(title, {
    x: M, y: 0.86, w: CW, h: 0.66, margin: 0,
    fontFace: F, fontSize: 34, bold: true, color: dark ? WHITE : INK,
  });
  if (sub) {
    s.addText(sub, {
      x: M, y: 1.54, w: CW, h: 0.3, margin: 0,
      fontFace: F, fontSize: 13, color: dark ? ICEBLUE : BODY,
    });
  }
}
function pageNo(s, n, dark) {
  s.addText(n, {
    x: 12.43, y: 6.95, w: 0.6, h: 0.35, margin: 0,
    fontFace: F, fontSize: 10, color: dark ? "7C8CA6" : BODY, align: "right", valign: "middle",
  });
}

// ═══════════════ NEW A — 저평가 판정 기준 (page 06) ═══════════════
{
  const s = pres.addSlide();
  s.background = { color: LIGHTBG };
  head(s, "저평가 판정 ① 탐색",
    "목은 좋은데 아직 싼 곳",
    "먼저 동네부터 좁힌다 — 서울 426개 행정동 전수. 다음 장에서 필지 하나까지 내려간다");

  const cards = [
    { n: "①", c: TEAL, t: "목 점수 → 백분위", big: "0 ~ 100",
      d: "목 = 다른 데를 오갈 때 지나게 되는 자리.\n길 구조(Choice · Integration)로 계산한다.",
      cap: "동 중심 500m 반경 · 426개 동 분포 백분위" },
    { n: "②", c: SLATE, t: "실거래가 → 백분위", big: "0 ~ 100",
      d: "동 경계 안 최근 24개월 실거래의\n㎡당 가격 중앙값을 쓴다.",
      cap: "같은 426개 동 분포에서의 백분위" },
    { n: "③", c: RED, t: "갭 점수  =  ① − ②", big: "+47",
      d: "길 구조 백분위에서 가격 백분위를 뺀다.\n클수록 길 구조 대비 가격이 덜 올라온 동.",
      cap: "이 값으로 서울 전체를 줄 세운다" },
  ];
  const cw = (CW - 0.56) / 3, gp = 0.28;
  cards.forEach((c, i) => {
    const x = M + i * (cw + gp), y = 2.0, h = 2.12;
    s.addShape(pres.ShapeType.rect, {
      x, y, w: cw, h,
      fill: { color: i === 2 ? REDTINT : WHITE },
      line: { color: i === 2 ? RED : LINE, width: i === 2 ? 1.5 : 1 },
    });
    s.addText(c.n, {
      x: x + 0.26, y: y + 0.2, w: 0.4, h: 0.3, margin: 0,
      fontFace: F, fontSize: 15, bold: true, color: c.c,
    });
    s.addText(c.t, {
      x: x + 0.66, y: y + 0.2, w: cw - 0.92, h: 0.3, margin: 0,
      fontFace: F, fontSize: 14.5, bold: true, color: INK, valign: "middle",
    });
    s.addText(c.d, {
      x: x + 0.26, y: y + 0.6, w: cw - 0.52, h: 0.66, margin: 0,
      fontFace: F, fontSize: 11.5, color: BODY, lineSpacingMultiple: 1.3,
    });
    s.addText(c.big, {
      x: x + 0.26, y: y + 1.3, w: cw - 0.52, h: 0.44, margin: 0,
      fontFace: F, fontSize: 26, bold: true, color: c.c,
    });
    s.addText(c.cap, {
      x: x + 0.26, y: y + 1.74, w: cw - 0.52, h: 0.26, margin: 0,
      fontFace: F, fontSize: 10, color: BODY,
    });
  });

  // ── dark rule band ──
  s.addShape(pres.ShapeType.rect, {
    x: M, y: 4.38, w: CW, h: 1.34, fill: { color: DARKBG }, line: { color: DARKBG, width: 0 },
  });
  s.addText("판정 규칙", {
    x: M + 0.34, y: 4.6, w: 2.2, h: 0.28, margin: 0,
    fontFace: F, fontSize: 12, bold: true, charSpacing: 1, color: ICEBLUE,
  });
  s.addText([
    { text: "목은 좋은데", options: { color: RED, bold: true } },
    { text: "(길 구조 상위)", options: { color: ICEBLUE, breakLine: true } },
    { text: "실거래가는 낮으면", options: { color: WHITE, bold: true } },
    { text: " → 그 동이 저평가다.", options: { color: ICEBLUE } },
  ], {
    x: M + 0.34, y: 4.86, w: 7.3, h: 0.8, margin: 0,
    fontFace: F, fontSize: 16, valign: "middle", lineSpacingMultiple: 1.25,
  });
  s.addText([
    { text: "목 88", options: { color: RED, bold: true, fontSize: 17 } },
    { text: "  −  ", options: { color: "7C8CA6", fontSize: 14 } },
    { text: "가격 41", options: { color: ICEBLUE, bold: true, fontSize: 17 } },
    { text: "  =  ", options: { color: "7C8CA6", fontSize: 14 } },
    { text: "갭 +47", options: { color: WHITE, bold: true, fontSize: 19 } },
  ], {
    x: 8.1, y: 4.94, w: 4.5, h: 0.56, margin: 0,
    fontFace: F, valign: "middle", align: "right",
  });

  // ── notes strip ──
  const notes = [
    ["426개", "행정동 전수 계산"],
    ["24개월", "실거래 ㎡당가 중앙값"],
    ["표본 0", "인 동은 판정하지 않는다"],
  ];
  const nw = (CW - 0.56) / 3;
  notes.forEach((nt, i) => {
    const x = M + i * (nw + 0.28);
    s.addText([
      { text: nt[0], options: { bold: true, color: RED, fontSize: 14 } },
      { text: "  " + nt[1], options: { color: BODY, fontSize: 11.5 } },
    ], { x, y: 5.94, w: nw, h: 0.34, margin: 0, fontFace: F, valign: "middle" });
  });
  s.addText("동 단위는 탐색용이다 — 동 평균은 같은 동 안 대로변과 이면도로의 차이를 지운다. 그 차이가 공간구문론이 파는 신호이므로, 실제 판정은 다음 장의 필지 단위에서 한다.", {
    x: M, y: 6.38, w: CW, h: 0.32, margin: 0,
    fontFace: F, fontSize: 10.5, color: BODY, italic: true,
  });
  pageNo(s, "06", false);
  s.addNotes("사용자가 요청한 지표값 정의. 계산은 backend/app/services/undervalued_areas.py의 compute_gap_scores와 동일하다. 백테스트는 아직 안 했으므로 예측력을 주장하지 않는다.");
}

// ═══════════════ NEW A2 — 필지 평가 방법 (page 07) ═══════════════
{
  const s = pres.addSlide();
  s.background = { color: LIGHTBG };
  head(s, "저평가 판정 ② 판정",
    "동네가 아니라, 땅 하나를 고른다",
    "동네를 골랐으니 그 안에서 어느 땅이 싼지 — 비교는 같은 조건끼리만 한다");

  // ── 왼쪽: 4단계 ──
  const LW = 6.0;
  const steps = [
    { n: "1", t: "그 땅 앞 도로를 찾는다",
      d: "필지에서 가장 가까운 도로(50m 안).\n그 도로의 목 점수를 이 필지의 목으로 쓴다." },
    { n: "2", t: "비교 상대를 고른다",
      d: "같은 동 · 같은 용도지역 필지끼리만 비교한다.\n그 묶음이 30필지가 안 되면 동을 구로 넓혀 표본을 채운다." },
    { n: "3", t: "그 안에서 등수를 매긴다",
      d: "목 등수와 공시지가 등수를 따로 매긴다.\n서울 전체가 아니라 이 묶음 안에서만." },
    { n: "4", t: "두 등수의 차이를 본다",
      d: "목 등수는 위인데 가격 등수는 아래 — 그 차이가 큰 땅이\n같은 조건의 다른 땅보다 덜 매겨진 땅이다." },
  ];
  steps.forEach((st, i) => {
    const y = 2.0 + i * 0.74;
    s.addShape(pres.ShapeType.ellipse, {
      x: M, y: y + 0.06, w: 0.34, h: 0.34,
      fill: { color: i === 3 ? RED : TEAL }, line: { color: i === 3 ? RED : TEAL, width: 0 },
    });
    s.addText(st.n, {
      x: M, y: y + 0.06, w: 0.34, h: 0.34, margin: 0,
      fontFace: F, fontSize: 13, bold: true, color: WHITE, align: "center", valign: "middle",
    });
    s.addText(st.t, {
      x: M + 0.48, y: y, w: LW - 0.48, h: 0.28, margin: 0,
      fontFace: F, fontSize: 14.5, bold: true, color: INK,
    });
    s.addText(st.d, {
      x: M + 0.48, y: y + 0.29, w: LW - 0.48, h: 0.44, margin: 0,
      fontFace: F, fontSize: 11, color: BODY, lineSpacingMultiple: 1.25,
    });
  });

  // ── 오른쪽: 왜 묶어야 하나 ──
  const RX = 7.0, RW = 5.733;
  s.addShape(pres.ShapeType.rect, {
    x: RX, y: 2.0, w: RW, h: 2.96,
    fill: { color: WHITE }, line: { color: LINE, width: 1 },
  });
  s.addText("왜 묶어서 비교하는가", {
    x: RX + 0.28, y: 2.2, w: RW - 0.56, h: 0.3, margin: 0,
    fontFace: F, fontSize: 14.5, bold: true, color: INK,
  });
  s.addText([
    { text: "상업지역은 애초에 간선도로변에 지정되고 용적률이 높다 —", options: { breakLine: true } },
    { text: "그 자체로 비싸다." },
  ], {
    x: RX + 0.28, y: 2.5, w: RW - 0.56, h: 0.54, margin: 0,
    fontFace: F, fontSize: 11.5, color: BODY, lineSpacingMultiple: 1.25,
  });

  // 섞어서 비교 (틀림)
  s.addShape(pres.ShapeType.rect, {
    x: RX + 0.28, y: 3.1, w: RW - 0.56, h: 0.82,
    fill: { color: REDTINT }, line: { color: REDTINT2, width: 1 },
  });
  s.addText([
    { text: "섞어서 비교하면", options: { bold: true, color: REDDK, breakLine: true } },
    { text: "주거지역 필지가 전부 저평가로 뜬다 — 목은 괜찮은데 상업지역만큼 안 비싸니까.", options: { color: REDDK } },
  ], {
    x: RX + 0.5, y: 3.1, w: RW - 1.0, h: 0.82, margin: 0,
    fontFace: F, fontSize: 11.5, valign: "middle", lineSpacingMultiple: 1.25,
  });

  // 나눠서 비교 (맞음)
  s.addShape(pres.ShapeType.rect, {
    x: RX + 0.28, y: 4.02, w: RW - 0.56, h: 0.82,
    fill: { color: "E6F3EE" }, line: { color: "CFE6DC", width: 1 },
  });
  s.addText([
    { text: "나눠서 비교하면", options: { bold: true, color: "1E5C46", breakLine: true } },
    { text: "주거는 주거끼리, 상업은 상업끼리 — 진짜 싼 필지만 남는다.", options: { color: "1E5C46" } },
  ], {
    x: RX + 0.5, y: 4.02, w: RW - 1.0, h: 0.82, margin: 0,
    fontFace: F, fontSize: 11.5, valign: "middle", lineSpacingMultiple: 1.25,
  });

  // ── 제외 규칙 ──
  s.addShape(pres.ShapeType.rect, {
    x: M, y: 5.14, w: CW, h: 0.96, fill: { color: DARKBG }, line: { color: DARKBG, width: 0 },
  });
  s.addText("판정하지 않는 필지", {
    x: M + 0.3, y: 5.3, w: 2.4, h: 0.28, margin: 0,
    fontFace: F, fontSize: 12, bold: true, color: ICEBLUE,
  });
  const excl = [
    "접면 도로 50m 밖",
    "용도지역 미확인",
    "개발제한 · 녹지지역",
    "매매 불가 지목 · 작은 필지",
  ];
  const ew = (CW - 3.0) / 4;
  excl.forEach((e, i) => {
    s.addText("· " + e, {
      x: M + 2.8 + i * ew, y: 5.3, w: ew, h: 0.28, margin: 0,
      fontFace: F, fontSize: 11.5, color: WHITE,
    });
  });
  s.addText("임야·농지처럼 그 용도로는 쓸 수 없어 싼 땅, 철도·학교용지처럼 매매가 안 되는 땅을 뺀다 — 안 빼면 목록 위를 덮는다(실측: 상위 1,000필지의 21%가 임야).", {
    x: M + 0.3, y: 5.68, w: CW - 0.6, h: 0.3, margin: 0,
    fontFace: F, fontSize: 11, color: "8FA3BE",
  });

  s.addText("가격은 개별공시지가로 본다. 그래서 결과는 “시세보다 싸다”가 아니라 “같은 조건 땅들의 공시지가보다 낮다”는 뜻이고, 금액이 아니라 %로만 말한다 — 이 한계는 다음 장에서 따로 다룬다.", {
    x: M, y: 6.26, w: CW, h: 0.5, margin: 0,
    fontFace: F, fontSize: 10, color: BODY, italic: true, lineSpacingMultiple: 1.3,
  });
  pageNo(s, "07", false);
  s.addNotes("계산은 backend/app/services/parcel_undervalued.py의 compute_parcel_gaps와 동일하다. 계층화가 핵심 — 이게 없으면 주거지역이 전부 저평가로 뜬다. 회귀(로그선형 헤도닉)는 statsmodels 도입 후 붙일 예정이며 현재는 계층별 백분위다.");
}

// ═══════════════ NEW B — 도면 분석 (page 08) ═══════════════
{
  const s = pres.addSlide();
  s.background = { color: DARKBG };
  head(s, "차별점 01 · 도면 분석",
    "평면도를 넣으면, 공간이 답한다",
    "아직 지어지지 않은 설계안에서도 어느 공간이 잘 보이고 잘 통하는지 계산한다", true);

  const steps = [
    { n: "01", t: "평면도 업로드", d: "DXF는 벽 선을, 사진·PNG는 윤곽을 읽는다.\n정면에서 그린 도면이어야 한다.", hot: false },
    { n: "02", t: "벽 선 확인·수정", d: "자동 인식을 그대로 믿지 않는다.\n캔버스에서 사람이 고친 뒤 계산한다.", hot: false },
    { n: "03", t: "가시성 분석", d: "도면을 격자로 잘라 서로 보이는 관계를 센다.\n어디가 잘 보이고 잘 통하는지 나온다.", hot: true },
  ];
  const sw = (CW - 0.56) / 3;
  steps.forEach((st, i) => {
    const x = M + i * (sw + 0.28), y = 2.02, h = 1.96;
    s.addShape(pres.ShapeType.rect, {
      x, y, w: sw, h,
      fill: { color: DARKCARD },
      line: { color: st.hot ? RED : "36486B", width: st.hot ? 1.5 : 1 },
    });
    s.addText(st.n, {
      x: x + 0.26, y: y + 0.18, w: 1.2, h: 0.42, margin: 0,
      fontFace: F, fontSize: 22, bold: true, color: st.hot ? RED : "6B7FA3",
    });
    s.addText(st.t, {
      x: x + 0.26, y: y + 0.66, w: sw - 0.52, h: 0.32, margin: 0,
      fontFace: F, fontSize: 15, bold: true, color: WHITE,
    });
    s.addText(st.d, {
      x: x + 0.26, y: y + 1.04, w: sw - 0.52, h: 0.72, margin: 0,
      fontFace: F, fontSize: 11.5, color: ICEBLUE, lineSpacingMultiple: 1.3,
    });
    if (i < 2) {
      s.addText("›", {
        x: x + sw, y, w: 0.28, h, margin: 0,
        fontFace: F, fontSize: 22, bold: true, color: "6B7FA3", align: "center", valign: "middle",
      });
    }
  });

  // ── 왼쪽: 쓰임 ──
  s.addText("무엇에 쓰나", {
    x: M, y: 4.24, w: 5.6, h: 0.3, margin: 0,
    fontFace: F, fontSize: 14, bold: true, color: WHITE,
  });
  s.addText([
    { text: "매장 안 진열·계산대를 어디에 둘지", options: { bullet: true, breakLine: true } },
    { text: "주출입구 위치에 따른 동선 변화", options: { bullet: true, breakLine: true } },
    { text: "잘 안 보여서 죽는 구석 찾기", options: { bullet: true, breakLine: true } },
    { text: "재건축 단지 상가의 내부 배치", options: { bullet: true } },
  ], {
    x: M + 0.22, y: 4.62, w: 5.4, h: 1.5, margin: 0,
    fontFace: F, fontSize: 13, color: ICEBLUE, paraSpaceAfter: 8,
  });
  s.addText("길 분석과 똑같은 식으로 계산해서, 점수의 뜻과 색이 앱 전체에서 하나다.", {
    x: M, y: 6.2, w: 5.9, h: 0.3, margin: 0,
    fontFace: F, fontSize: 10.5, color: "7C8CA6", italic: true,
  });

  // ── 오른쪽: 반영률 — 정직성이 곧 차별점 ──
  s.addShape(pres.ShapeType.rect, {
    x: 6.5, y: 4.24, w: 6.233, h: 2.26,
    fill: { color: RED, transparency: 88 }, line: { color: RED, width: 1.5 },
  });
  s.addText("얼마나 못 봤는지도 말한다", {
    x: 6.82, y: 4.48, w: 5.6, h: 0.3, margin: 0,
    fontFace: F, fontSize: 14, bold: true, color: RED,
  });
  s.addText("자동 인식은 도면마다 벽 표현이 달라 완전할 수 없다. 그래서 반영률을 함께 내고, 빠진 공간을 도면 위에 회색으로 덮어 보여준다.", {
    x: 6.82, y: 4.86, w: 5.6, h: 0.76, margin: 0,
    fontFace: F, fontSize: 12.5, color: WHITE, lineSpacingMultiple: 1.3,
  });
  s.addText([
    { text: "조용히 틀리느니 ", options: { color: ICEBLUE } },
    { text: "못 본 만큼을 밝힌다", options: { color: WHITE, bold: true } },
    { text: ".", options: { color: ICEBLUE } },
  ], {
    x: 6.82, y: 5.72, w: 5.6, h: 0.32, margin: 0,
    fontFace: F, fontSize: 13, valign: "middle",
  });
  s.addText("실측 도면으로 두 번 고쳤다 — 막힌 문 때문에 방 하나만 잡히던 것, 건물 밖 도로를 함께 평균 내던 것. 고친 뒤에도 매번 표시한다.", {
    x: 6.82, y: 6.08, w: 5.6, h: 0.32, margin: 0,
    fontFace: F, fontSize: 10.5, color: ICEBLUE, italic: true,
  });

  pageNo(s, "09", true);
  s.addNotes("실내 VGA(Turner 가시성 그래프)다. 도로망 Choice가 아니라 Integration·Connectivity를 낸다. 입력은 정면 도면이어야 한다 — 기울여 찍은 사진(Lulu Mall 샘플)은 유리 반사와 원근 왜곡 탓에 벽이 봉투를 이루지 못해 실내/실외 구분이 성립하지 않는다. Choice는 격자 2200셀에서 5초 룰을 위협해 v1에서 뺐다. DWG는 무료 파서가 없어 미지원 — DXF로 내보내야 한다. 단지 배치도(실외 가로) 분석은 로드맵.");
}

// ═══════════════ NEW C — vs 생성형 AI (page 15) ═══════════════
{
  const s = pres.addSlide();
  s.background = { color: LIGHTBG };
  head(s, "COMPARISON 02",
    "그럼 ChatGPT · Claude에 물어보면 안 되나",
    "생성형 AI는 회상하고, 우리는 계산한다 — 물어서 얻을 수 없는 값이 있다");

  const c0 = { x: M, w: 2.75 };
  const c1 = { x: 3.35, w: 4.5 };
  const c2 = { x: 7.85, w: 4.883 };

  // header row
  const hy = 2.02, hh = 0.6;
  s.addShape(pres.ShapeType.rect, {
    x: c0.x, y: hy, w: c0.w + c1.w, h: hh, fill: { color: DARKBG }, line: { color: DARKBG, width: 0 },
  });
  s.addText("ChatGPT · Claude 같은 생성형 AI에 물으면", {
    x: c1.x, y: hy, w: c1.w, h: hh, margin: 0,
    fontFace: F, fontSize: 13, bold: true, color: WHITE, align: "center", valign: "middle",
  });
  s.addShape(pres.ShapeType.rect, {
    x: c2.x, y: hy, w: c2.w, h: hh, fill: { color: RED }, line: { color: RED, width: 0 },
  });
  s.addText("목이 좋은 곳", {
    x: c2.x, y: hy, w: c2.w, h: hh, margin: 0,
    fontFace: F, fontSize: 13, bold: true, color: WHITE, align: "center", valign: "middle",
  });

  const rows = [
    ["이 지점의 목 점수는?",
     "이론은 설명하지만 숫자는 생성한다.\n물을 때마다 값이 달라진다.",
     "실제 서울 도로망 20만 세그먼트에서 계산.\n같은 주소면 항상 같은 값."],
    ["서울에서 저평가된 동은?",
     "답할 수 없다 — 이 순위는 어떤\n학습 데이터에도 존재하지 않는다.",
     "426개 행정동을 전수 계산해\n갭 점수 순위로 제시."],
    ["이 평면도의 어디가 잘 통할까?",
     "도면을 계산할 수 없다.\n비슷한 사례를 문장으로 추측할 뿐.",
     "벽·문을 격자 가시성 그래프로 바꿔\nIntegration · Connectivity를 산출."],
    ["왜 그런 결론이 나왔나?",
     "근거를 역추적할 수 없다.\n같은 질문에 다른 답이 나온다.",
     "백분위 · 갭 점수 · 판정 기준이\n코드로 고정돼 있다."],
  ];
  const ry = 2.62, rh = 0.88;
  rows.forEach((r, i) => {
    const y = ry + i * rh;
    const odd = i % 2 === 1;
    s.addShape(pres.ShapeType.rect, {
      x: c0.x, y, w: c0.w + c1.w, h: rh,
      fill: { color: odd ? GRAYCELL : WHITE }, line: { color: LINE, width: 0.75 },
    });
    s.addText(r[0], {
      x: c0.x + 0.22, y, w: c0.w - 0.34, h: rh, margin: 0,
      fontFace: F, fontSize: 12.5, bold: true, color: INK, valign: "middle", lineSpacingMultiple: 1.2,
    });
    s.addText(r[1], {
      x: c1.x + 0.06, y, w: c1.w - 0.22, h: rh, margin: 0,
      fontFace: F, fontSize: 11.5, color: BODY, valign: "middle", lineSpacingMultiple: 1.3,
    });
    s.addShape(pres.ShapeType.rect, {
      x: c2.x, y, w: c2.w, h: rh,
      fill: { color: odd ? REDTINT2 : REDTINT }, line: { color: REDTINT2, width: 0.75 },
    });
    s.addText(r[2], {
      x: c2.x + 0.24, y, w: c2.w - 0.42, h: rh, margin: 0,
      fontFace: F, fontSize: 11.5, bold: true, color: REDDK, valign: "middle", lineSpacingMultiple: 1.3,
    });
  });

  s.addText([
    { text: "생성형 AI는 이미 쓰여진 것을 요약한다.  ", options: { color: BODY } },
    { text: "우리는 아직 쓰여지지 않은 값을 계산한다.", options: { color: INK, bold: true } },
  ], {
    x: M, y: 6.32, w: CW, h: 0.42, margin: 0,
    fontFace: F, fontSize: 16, italic: true, valign: "middle",
  });
  pageNo(s, "19", false);
  s.addNotes("기존 상권분석 서비스 비교(앞 장)와 별개로, 생성형 AI와의 비교. 핵심은 계산 가능성과 재현성이다.");
}

// ═══════════════ NEW E — 이제까지 만든 것 (page 17) ═══════════════
// 원본 13번(STATUS & ROADMAP) 바로 앞에 온다. 그 장이 "다음"으로 예고했던
// 두 건('뜰 자리'·도면 업로드)이 실제 기능이 됐으므로, 그 앞에서 먼저 보여준다.
{
  const s = pres.addSlide();
  s.background = { color: LIGHTBG };
  head(s, "진행 상황",
    "예고했던 “다음”이 이번에 들어왔다",
    "2026-08-21~23 작업 — 뒤 장에서 “다음”으로 적었던 두 건이 실제 기능이 됐다");

  const cards = [
    { n: "①", c: TEAL, t: "저평가 판정 — 동에서 필지로", items: [
      "행정동 426개 “뜰 자리” 찾기 배치·API",
      "서울 66.9만 필지 판정 완료 — 동을 눌러 그 안 필지로 내려간다",
      "전 서울 배치를 15배 빠르게 — 타일당 126초에서 8초로",
    ] },
    { n: "②", c: SLATE, t: "토지 가격 데이터를 실제로 확보", items: [
      "개별공시지가 전 필지 2009~2026 적재 (실측으로 취득 경로 확정)",
      "토지특성까지 받아 용도지역 공간조인이 통째로 필요 없어졌다",
      "방향이 성립하는지 먼저 실험으로 확인 — 목이 공시지가를 설명하는가",
    ] },
    { n: "③", c: RED, t: "평면도 분석 — 건물 안에서의 목", items: [
      "평면도 업로드 → 실내 목 계산 → 색 오버레이",
      "문 자리 표시 · 분석에서 빠진 공간 표시",
      "5초 룰 회복 — 해상도는 그대로 두고 계산을 고쳤다",
    ] },
    { n: "④", c: SLATE, t: "화면과 말을 정리", items: [
      "첫 화면을 질의창 하나로 · “저평가 부지” 탭 신설",
      "화면에서 “구조”를 “목”으로 — 영어·전문 용어를 걷어냈다",
      "발표 덱 빌드도 저장소로 (같은 결과가 다시 나온다)",
    ] },
  ];

  const cw2 = (CW - 0.28) / 2, ch = 1.95;
  cards.forEach((c, i) => {
    const x = M + (i % 2) * (cw2 + 0.28);
    const y = 2.02 + Math.floor(i / 2) * 2.1;
    s.addShape(pres.ShapeType.rect, {
      x, y, w: cw2, h: ch, fill: { color: WHITE }, line: { color: LINE, width: 1 },
    });
    s.addShape(pres.ShapeType.rect, {
      x, y, w: cw2, h: 0.07, fill: { color: c.c }, line: { color: c.c, width: 0 },
    });
    s.addText(c.n, {
      x: x + 0.26, y: y + 0.24, w: 0.4, h: 0.3, margin: 0,
      fontFace: F, fontSize: 15, bold: true, color: c.c,
    });
    s.addText(c.t, {
      x: x + 0.64, y: y + 0.24, w: cw2 - 0.9, h: 0.3, margin: 0,
      fontFace: F, fontSize: 15, bold: true, color: INK, valign: "middle",
    });
    s.addText(
      c.items.map((t, k) => ({
        text: "· " + t,
        options: { breakLine: k < c.items.length - 1 },
      })),
      {
        x: x + 0.26, y: y + 0.66, w: cw2 - 0.52, h: 1.14, margin: 0,
        fontFace: F, fontSize: 11.5, color: BODY, lineSpacingMultiple: 1.34,
      },
    );
  });

  s.addText([
    { text: "아직 아닌 것 — ", options: { color: INK, bold: true } },
    { text: "계획 도로의 지가 영향은 ", options: { color: BODY } },
    { text: "“목이 이 정도인 다른 필지들의 현재 지가 수준”", options: { color: REDDK, bold: true } },
    { text: "으로만 말한다 — 인과 추정이 아니다.", options: { color: BODY } },
  ], {
    x: M, y: 6.32, w: CW, h: 0.42, margin: 0,
    fontFace: F, fontSize: 12.5, valign: "middle",
  });

  pageNo(s, "23", false);
  s.addNotes("8/21~8/22 이틀치 작업. 뒤 STATUS 장이 '다음'으로 적어 둔 두 건 — '뜰 자리'와 도면 업로드 — 이 이미 들어왔다는 게 이 장의 요점이다. 마지막 줄은 과장하지 않겠다는 선언이라 빼지 말 것.");
}

// ═══════ NEW F · G — 시장분석 (국내 page 15 · 해외 page 16) ═══════
// 원본 COMPARISON(경쟁 서비스 비교) 바로 앞에 온다. 두 장은 같은 3열 표를
// 쓴다 — 서비스 / 무엇을 주는가 / 한계. 차별점 주장은 해외 장 하단에 모았다.
{
  const cA = { x: M, w: 2.9 };
  const cB = { x: 3.5, w: 4.6 };
  const cC = { x: 8.1, w: 4.633 };

  function marketTable(s, rows, rh) {
    const hy = 2.02, hh = 0.5;
    [[cA, "서비스", DARKBG], [cB, "무엇을 주는가", DARKBG], [cC, "어디까지 못 가는가", REDDK]]
      .forEach(([c, t, col]) => {
        s.addShape(pres.ShapeType.rect, {
          x: c.x, y: hy, w: c.w, h: hh, fill: { color: col }, line: { color: col, width: 0 },
        });
        s.addText(t, {
          x: c.x, y: hy, w: c.w, h: hh, margin: 0,
          fontFace: F, fontSize: 12.5, bold: true, color: WHITE, align: "center", valign: "middle",
        });
      });
    rows.forEach((r, i) => {
      const y = hy + hh + i * rh;
      const odd = i % 2 === 1;
      [[cA, r[0], INK, true], [cB, r[1], BODY, false]].forEach(([c, t, col, bold]) => {
        s.addShape(pres.ShapeType.rect, {
          x: c.x, y, w: c.w, h: rh,
          fill: { color: odd ? GRAYCELL : WHITE }, line: { color: LINE, width: 0.75 },
        });
        s.addText(t, {
          x: c.x + 0.2, y, w: c.w - 0.34, h: rh, margin: 0,
          fontFace: F, fontSize: 11.5, bold, color: col, valign: "middle", lineSpacingMultiple: 1.2,
        });
      });
      s.addShape(pres.ShapeType.rect, {
        x: cC.x, y, w: cC.w, h: rh,
        fill: { color: odd ? REDTINT2 : REDTINT }, line: { color: REDTINT2, width: 0.75 },
      });
      s.addText(r[2], {
        x: cC.x + 0.2, y, w: cC.w - 0.34, h: rh, margin: 0,
        fontFace: F, fontSize: 11.5, color: REDDK, valign: "middle", lineSpacingMultiple: 1.2,
      });
    });
  }

  // ── F. 국내 ──
  {
    const s = pres.addSlide();
    s.background = { color: LIGHTBG };
    head(s, "MARKET 01 · 국내",
      "국내 서비스는 모두 “지금 얼마 벌리나”를 센다",
      "나쁜 서비스라는 뜻이 아니다 — 다섯 곳 모두 매출·유동인구에서 출발한다는 뜻이다");

    marketTable(s, [
      ["소상공인 상권정보시스템\n중기부 · 소진공",
       "전국 무료. 매출 · 유동인구 · 경쟁 밀도를\n정해진 리포트로 묶어 준다.",
       "리포트가 고정돼 있다 —\n내 업종 · 내 질문에는 답하지 않는다."],
      ["나이스비즈맵\nNICE지니데이타",
       "카드 결제 데이터로 업종별 추정 매출.\n국내에서 가장 오래 쓰인 유료 서비스.",
       "카드 매출은 이미 일어난 일이다.\n앞으로 뜰 자리는 보이지 않는다."],
      ["오픈업\n핀다",
       "카드 · 통신 · 국세청 데이터를 결합해\n월별 추정 매출을 낸다.",
       "결국 매출 추정이고,\n상권에 따라 오차가 크다."],
      ["우리마을가게 상권분석\n서울시",
       "골목상권 단위까지 공공데이터를\n무료로 공개한다.",
       "서울 한정 · 상권과 행정동에서 멈춘다.\n필지 하나를 짚어 주지 않는다."],
      ["밸류맵 · 디스코 · 랜드북",
       "토지 · 건물 실거래와 개발 수익성 검토.\n부동산 쪽에서 출발한 도구들.",
       "거래 이력과 규제 중심이다.\n길 구조는 계산하지 않는다."],
    ], 0.72);

    s.addText([
      { text: "매출과 유동인구는 결과다. ", options: { color: BODY } },
      { text: "결과를 아무리 정밀하게 세도, 아직 결과가 없는 곳은 못 찾는다.", options: { color: INK, bold: true } },
    ], {
      x: M, y: 6.3, w: CW, h: 0.42, margin: 0,
      fontFace: F, fontSize: 13.5, valign: "middle",
    });
    pageNo(s, "16", false);
    s.addNotes("국내 경쟁 지형. 이 장의 목적은 '기존 서비스가 나쁘다'가 아니라 '전부 같은 후행지표를 센다'는 지적이다. 뒤 COMPARISON 장이 나이스비즈맵과의 1:1 비교이므로, 여기서는 시장 전체를 훑는다.");
  }

  // ── G. 해외 + 차별점 ──
  {
    const s = pres.addSlide();
    s.background = { color: LIGHTBG };
    head(s, "MARKET 02 · 해외",
      "해외는 방문량을 사고팔거나, 지도를 준다",
      "훨씬 앞선 데이터 인프라를 갖췄지만 파는 것은 데이터와 도구다 — 판단은 여전히 사는 쪽 몫");

    marketTable(s, [
      ["Placer.ai\n미국",
       "수천만 대 모바일 패널로 지점별\n방문량 · 유입 지역을 추정한다.",
       "패널이 젊은 · 고소득에 쏠려 보정이 필요하고,\n방문량 자체가 후행지표다."],
      ["SafeGraph · ADVAN\n미국",
       "전 세계 POI · 지오펜스와\n방문 · 소비 데이터를 판매한다.",
       "원데이터 공급자다.\n판단은 사는 쪽이 직접 만들어야 한다."],
      ["Esri ArcGIS Business Analyst",
       "인구통계와 POI로 입지 적합도 분석.\nGIS 업계의 사실상 표준 도구.",
       "범용 도구라 전문가가 직접 조립해야 한다.\n답이 아니라 지도를 준다."],
      ["Geoblink\n유럽",
       "신규 지점 성과 예측 모델.\n리테일 체인의 점포망 관리에 쓰인다.",
       "체인 본사용이다.\n개인 창업자 · 토지 투자자용이 아니다."],
      ["CoStar\n미국",
       "상업용 부동산 정보 · 분석의\n대형 사업자.",
       "임대 · 거래 정보 중심이라\n“왜 이 자리인가”는 다루지 않는다."],
    ], 0.64);

    // ── 차별점 밴드 ──
    s.addShape(pres.ShapeType.rect, {
      x: M, y: 5.82, w: CW, h: 1.16, fill: { color: DARKBG }, line: { color: DARKBG, width: 0 },
    });
    s.addText("우리가 다른 세 가지", {
      x: M + 0.3, y: 5.98, w: 3, h: 0.28, margin: 0,
      fontFace: F, fontSize: 12, bold: true, charSpacing: 1, color: ICEBLUE,
    });
    const diffs = [
      ["후행이 아니라 선행", "매출 대신 길 구조를 계산한다.\n매출은 바뀌고, 길은 안 바뀐다."],
      ["동네가 아니라 필지", "접면 도로의 목 vs 개별공시지가로\n저평가된 땅 하나를 짚는다."],
      ["데이터가 아니라 답", "한 줄로 물으면 5초 안에\n판단과 그 근거까지 돌려준다."],
    ];
    const dw = (CW - 3.3) / 3;
    diffs.forEach((d, i) => {
      const x = M + 3.0 + i * dw;
      s.addText(d[0], {
        x, y: 5.96, w: dw - 0.3, h: 0.3, margin: 0,
        fontFace: F, fontSize: 13, bold: true, color: i === 0 ? RED : WHITE, valign: "middle",
      });
      s.addText(d[1], {
        x, y: 6.3, w: dw - 0.3, h: 0.56, margin: 0,
        fontFace: F, fontSize: 11, color: ICEBLUE, lineSpacingMultiple: 1.25,
      });
    });

    pageNo(s, "17", false);
    s.addNotes("해외 비교의 요점: 데이터 인프라(모바일 패널·POI)는 우리가 못 따라간다. 그러나 그들이 파는 것은 데이터(SafeGraph·ADVAN)나 도구(Esri)이고, 방문량 기반은 모두 후행지표다. 길 구조를 계산해 저평가 필지를 짚는 제품은 이 목록에 없다. Placer 패널 편향은 업계 공통으로 알려진 한계다.");
  }
}

// ═══════ NEW H · I · J — 수익 모델 / 비용 구조 / 기간별 손익 ═══════
// 가격은 2026-08-23 소유자 결정으로 확정됐다 (아래 상수 넷). 고객 수는 여전히
// 목표치이고 비용은 추정치다 — 그 구분을 각 장에 적어 두었다.
{
  // 가격 가정 (원)
  const P_IND = 19900;      // 개인 구독 / 월
  const P_PRO = 99000;      // 전문가 구독 / 월
  const P_RPT = 49000;      // 필지 리포트 / 건
  const P_B2B = 12000000;   // 기업 API 라이선스 / 연

  // ── H. 수익 모델 ──
  {
    const s = pres.addSlide();
    s.background = { color: LIGHTBG };
    head(s, "BUSINESS MODEL",
      "누가, 무엇에 돈을 내는가",
      "과금 형식과 대상은 만들어 둔 기능에서 갈린다 — 다섯 갈래 모두 이미 돌아가는 기능이다");

    const streams = [
      { c: TEAL, who: "창업 준비자", how: "개인 구독", price: P_IND.toLocaleString() + "원 / 월",
        why: "“신촌 치킨집 자리 어때?”\n한 줄 질문. 무료 3건 → 구독.", tag: "월 정액" },
      { c: SLATE, who: "중개사 · 컨설턴트", how: "전문가 구독", price: P_PRO.toLocaleString() + "원 / 월",
        why: "분석 무제한 + 딜 관리.\n고객에게 근거가 필요한 쪽.", tag: "월 정액" },
      { c: RED, who: "토지 투자자", how: "필지 리포트", price: P_RPT.toLocaleString() + "원 / 건",
        why: "저평가 필지 판정 한 건을\n근거·출처까지 붙여 문서로.", tag: "건별" },
      { c: SLATE, who: "프랜차이즈 · 시행사", how: "기업 API 라이선스", price: (P_B2B / 10000).toLocaleString() + "만원 / 연",
        why: "점포망·사업지 검토에 엔진을\n그대로 붙인다 (MCP · REST).", tag: "연 계약" },
      { c: DARKBG, who: "도시계획 · 공공", how: "도면 분석 용역", price: "프로젝트 단위",
        why: "평면도·배치도를 넣으면\n공간 구조를 자동 산출한다.", tag: "용역 · 입찰" },
    ];

    const sw = (CW - 4 * 0.2) / 5;
    streams.forEach((t, i) => {
      const x = M + i * (sw + 0.2), y = 2.05, h = 3.1;
      s.addShape(pres.ShapeType.rect, {
        x, y, w: sw, h, fill: { color: WHITE }, line: { color: LINE, width: 1 },
      });
      s.addShape(pres.ShapeType.rect, {
        x, y, w: sw, h: 0.5, fill: { color: t.c }, line: { color: t.c, width: 0 },
      });
      s.addText(t.who, {
        x: x + 0.1, y, w: sw - 0.2, h: 0.5, margin: 0,
        fontFace: F, fontSize: 11.5, bold: true, color: WHITE, align: "center", valign: "middle",
      });
      s.addText(t.how, {
        x: x + 0.18, y: y + 0.66, w: sw - 0.36, h: 0.3, margin: 0,
        fontFace: F, fontSize: 13, bold: true, color: INK,
      });
      s.addText(t.price, {
        x: x + 0.18, y: y + 1.0, w: sw - 0.36, h: 0.36, margin: 0,
        fontFace: F, fontSize: 14.5, bold: true, color: t.c === DARKBG ? SLATE : t.c,
      });
      s.addText(t.why, {
        x: x + 0.18, y: y + 1.46, w: sw - 0.36, h: 0.94, margin: 0,
        fontFace: F, fontSize: 10.5, color: BODY, lineSpacingMultiple: 1.3,
      });
      s.addText(t.tag, {
        x: x + 0.18, y: y + 2.6, w: sw - 0.36, h: 0.28, margin: 0,
        fontFace: F, fontSize: 10, color: "7C8CA6",
      });
    });

    s.addShape(pres.ShapeType.rect, {
      x: M, y: 5.42, w: CW, h: 1.24, fill: { color: DARKBG }, line: { color: DARKBG, width: 0 },
    });
    s.addText("왜 돈을 내는가", {
      x: M + 0.3, y: 5.6, w: 2.6, h: 0.28, margin: 0,
      fontFace: F, fontSize: 12, bold: true, charSpacing: 1, color: ICEBLUE,
    });
    s.addText([
      { text: "매출·유동인구는 다른 곳에서도 산다. ", options: { color: ICEBLUE } },
      { text: "길 구조로 저평가를 판정해 주는 곳은 없다", options: { color: WHITE, bold: true } },
      { text: ".", options: { color: ICEBLUE, breakLine: true } },
      { text: "판단 하나가 억 단위 결정을 바꾸는 쪽(토지 투자자·시행사·중개사)이 먼저 돈을 낸다. 개인 구독은 그 아래 저변이다.", options: { color: ICEBLUE } },
    ], {
      x: M + 3.0, y: 5.58, w: CW - 3.3, h: 0.94, margin: 0,
      fontFace: F, fontSize: 12, lineSpacingMultiple: 1.35,
    });

    pageNo(s, "20", false);
    s.addNotes("가격은 2026-08-23에 확정했다 — 물으면 확정가라고 답한다. 형식(월 정액 / 건별 / 연 라이선스 / 용역)은 이미 만든 기능에서 자연히 갈린 구분이다. 매출 축은 개인 구독이 아니라 전문가 구독과 B2B 라이선스다.");
  }

  // ── I. 비용 구조 ──
  {
    const s = pres.addSlide();
    s.background = { color: LIGHTBG };
    head(s, "COST STRUCTURE",
      "고정비가 작다 — 데이터를 사지 않기 때문",
      "경쟁사의 최대 원가는 모바일 패널·카드 데이터 구입비다. 우리는 공공데이터와 계산으로 대체한다");

    s.addText("① 월 고정비", {
      x: M, y: 2.0, w: 5.9, h: 0.3, margin: 0,
      fontFace: F, fontSize: 14, bold: true, color: TEAL,
    });
    const fixed = [
      ["백엔드 · PostGIS 호스팅", "7만원", "DB 11.3GB 상시"],
      ["프론트 호스팅 · CDN", "1만원", "Firebase Hosting"],
      ["지도 · 주소 변환 API", "3만원", "Kakao 초과분만"],
      ["도메인 · 모니터링 · 기타", "3만원", ""],
      ["공공데이터 (공시지가·토지특성·도로망)", "0원", "원가 차이의 핵심"],
    ];
    const fy = 2.4, fh = 0.5;
    fixed.forEach((r, i) => {
      const y = fy + i * fh, odd = i % 2 === 1;
      const last = i === fixed.length - 1;
      s.addShape(pres.ShapeType.rect, {
        x: M, y, w: 5.9, h: fh,
        fill: { color: last ? REDTINT : odd ? GRAYCELL : WHITE },
        line: { color: last ? RED : LINE, width: last ? 1.25 : 0.75 },
      });
      s.addText(r[0], {
        x: M + 0.18, y, w: 3.2, h: fh, margin: 0,
        fontFace: F, fontSize: 10.5, color: last ? REDDK : INK, bold: last, valign: "middle", lineSpacingMultiple: 1.1,
      });
      s.addText(r[1], {
        x: M + 3.42, y, w: 1.0, h: fh, margin: 0,
        fontFace: F, fontSize: 12, bold: true, color: last ? RED : SLATE, align: "right", valign: "middle",
      });
      s.addText(r[2], {
        x: M + 4.54, y, w: 1.2, h: fh, margin: 0,
        fontFace: F, fontSize: 8.5, color: BODY, valign: "middle", lineSpacingMultiple: 1.1,
      });
    });
    s.addText([
      { text: "합계  ", options: { color: BODY } },
      { text: "월 14만원", options: { color: INK, bold: true } },
      { text: "  — 고객이 0명이어도 이 금액만 나간다", options: { color: BODY } },
    ], {
      x: M, y: 4.98, w: 5.9, h: 0.34, margin: 0,
      fontFace: F, fontSize: 12, valign: "middle",
    });

    s.addText("② 분석 1건당 변동비", {
      x: 6.8, y: 2.0, w: 5.93, h: 0.3, margin: 0,
      fontFace: F, fontSize: 14, bold: true, color: RED,
    });
    const varc = [
      ["LLM 추론 (질문 해석 + 서사 생성)", "약 150원"],
      ["공간 계산 (공간구문론 엔진)", "0원 — 미리 적재한 값 조회"],
      ["카드 결제 수수료", "매출의 3%"],
    ];
    varc.forEach((r, i) => {
      const y = 2.4 + i * 0.5;
      s.addShape(pres.ShapeType.rect, {
        x: 6.8, y, w: 5.93, h: 0.5,
        fill: { color: i % 2 === 1 ? GRAYCELL : WHITE }, line: { color: LINE, width: 0.75 },
      });
      s.addText(r[0], {
        x: 6.98, y, w: 3.3, h: 0.5, margin: 0,
        fontFace: F, fontSize: 11, color: INK, valign: "middle",
      });
      s.addText(r[1], {
        x: 10.3, y, w: 2.25, h: 0.5, margin: 0,
        fontFace: F, fontSize: 10.5, bold: true, color: SLATE, align: "right", valign: "middle",
      });
    });

    s.addShape(pres.ShapeType.rect, {
      x: 6.8, y: 4.06, w: 5.93, h: 1.32,
      fill: { color: RED, transparency: 88 }, line: { color: RED, width: 1.5 },
    });
    s.addText("③ 아직 계상하지 않은 것", {
      x: 7.02, y: 4.22, w: 5.5, h: 0.3, margin: 0,
      fontFace: F, fontSize: 13, bold: true, color: REDDK,
    });
    s.addText("창업자 2인의 인건비를 0원으로 두었다 — 뒤 장의 손익도 그 기준이다. 월 600만원으로 계상하면 손익분기는 개인 구독 302명 또는 전문가 구독 61명이다.", {
      x: 7.02, y: 4.58, w: 5.5, h: 0.72, margin: 0,
      fontFace: F, fontSize: 11, color: INK, lineSpacingMultiple: 1.3,
    });

    s.addText([
      { text: "Placer.ai는 모바일 패널을, 나이스비즈맵은 카드 데이터를 산다.  ", options: { color: BODY } },
      { text: "우리 원가에는 그 항목이 없다.", options: { color: INK, bold: true } },
    ], {
      x: M, y: 5.62, w: CW, h: 0.42, margin: 0,
      fontFace: F, fontSize: 13, valign: "middle",
    });
    s.addText("인프라 금액은 현재 스택 기준 추정, LLM 단가는 토큰 사용량 기준 추정이다.", {
      x: M, y: 6.06, w: CW, h: 0.3, margin: 0,
      fontFace: F, fontSize: 10.5, color: BODY, italic: true,
    });

    pageNo(s, "21", false);
    s.addNotes("이 장의 요점은 금액의 정확도가 아니라 원가 구조 — 데이터 구입비가 없다는 것. 인건비 미계상은 반드시 밝힐 것.");
  }

  // ── J. 기간별 매출 · 손익 ──
  {
    const s = pres.addSlide();
    s.background = { color: LIGHTBG };
    head(s, "REVENUE PLAN",
      "언제 얼마가 들어오나 (목표)",
      "가격은 확정, 고객 수는 목표치다 — 창업자 인건비는 계상하지 않았다");

    const cols = [
      { x: M, w: 2.5 },
      { x: 3.1, w: 2.4 },
      { x: 5.5, w: 2.4 },
      { x: 7.9, w: 2.4 },
      { x: 10.3, w: 2.433 },
    ];
    const heads = ["단계 · 기간", "유료 고객 (목표)", "월 매출", "월 비용", "월 손익"];
    const hy = 2.05, hh = 0.5;
    heads.forEach((t, i) => {
      const col = i === 4 ? RED : DARKBG;
      s.addShape(pres.ShapeType.rect, {
        x: cols[i].x, y: hy, w: cols[i].w, h: hh, fill: { color: col }, line: { color: col, width: 0 },
      });
      s.addText(t, {
        x: cols[i].x, y: hy, w: cols[i].w, h: hh, margin: 0,
        fontFace: F, fontSize: 12, bold: true, color: WHITE, align: "center", valign: "middle",
      });
    });

    const rows = [
      ["1단계 · 무료 베타\n2026.09 ~ 12", "0명\n무료 사용자로 지표만 검증",
       "0원", "월 20만원\n고정 14 + 추론 6", "−20만원", false],
      ["2단계 · 유료 전환\n2027.01 ~ 06", "개인 200 · 전문가 20\n리포트 30건 / 월",
       "743만원", "211만원\n고정·추론 111 + 마케팅 100", "+532만원", false],
      ["3단계 · B2B · 전국\n2027.07 ~ 2028.06", "개인 1,000 · 전문가 100\n기업 라이선스 3사",
       "3,280만원", "712만원\n고정·추론 412 + 마케팅 300", "+2,568만원", true],
    ];
    const ry = 2.55, rh = 1.0;
    rows.forEach((r, i) => {
      const y = ry + i * rh, odd = i % 2 === 1;
      for (let k = 0; k < 5; k++) {
        const hot = k === 4;
        s.addShape(pres.ShapeType.rect, {
          x: cols[k].x, y, w: cols[k].w, h: rh,
          fill: { color: hot ? (r[5] ? REDTINT2 : REDTINT) : odd ? GRAYCELL : WHITE },
          line: { color: hot ? REDTINT2 : LINE, width: 0.75 },
        });
        s.addText(r[k], {
          x: cols[k].x + 0.16, y, w: cols[k].w - 0.32, h: rh, margin: 0,
          fontFace: F,
          fontSize: hot ? 15 : k === 3 ? 10.5 : 11.5,
          bold: k === 0 || hot || k === 2,
          color: hot ? REDDK : k === 0 ? INK : BODY,
          align: k >= 2 ? "right" : "left",
          valign: "middle", lineSpacingMultiple: 1.25,
        });
      }
    });

    s.addText("누적 (2028.06까지) — 매출 약 4.4억원 · 비용 약 1.0억원", {
      x: M, y: 5.7, w: CW, h: 0.32, margin: 0,
      fontFace: F, fontSize: 13, bold: true, color: INK,
    });
    s.addText("2단계 6개월 + 3단계 12개월 기준(743만×6 + 3,280만×12). 인건비 미계상이므로 순이익이 아니다.", {
      x: M, y: 6.02, w: CW, h: 0.3, margin: 0,
      fontFace: F, fontSize: 10.5, color: BODY, italic: true,
    });

    s.addText([
      { text: "손익분기 — ", options: { color: INK, bold: true } },
      { text: "고정비가 월 14만원이라 유료 전환 첫 달에 넘는다. 인건비 기준으로는 ", options: { color: BODY } },
      { text: "개인 302명 또는 전문가 61명", options: { color: REDDK, bold: true } },
      { text: " (월 600만원).", options: { color: BODY } },
    ], {
      x: M, y: 6.42, w: CW, h: 0.44, margin: 0,
      fontFace: F, fontSize: 12.5, valign: "middle",
    });

    pageNo(s, "22", false);
    s.addNotes("고객 수는 목표치이고 근거는 없다 — 물으면 그렇게 답할 것. 방어 가능한 건 둘: (1) 고정비가 월 14만원이라 실패해도 손실이 작다, (2) 인건비 기준 손익분기가 개인 302명 / 전문가 61명이라 목표가 크지 않다.");
  }
}

// ═══════ NEW K — 판정의 한계와 통제 (필지 판정 바로 뒤) ═══════
// 두 가지를 정직하게 밝히는 장이다. (1) 공시지가에는 도로·용도지역이 이미
// 들어 있어 그대로 회귀하면 행정 산식을 재현한다 — 통제 후 증분만 본다.
// (2) 공시지가는 실거래가가 아니다 — 그래서 금액이 아니라 백분위로만 말한다.
{
  const s = pres.addSlide();
  s.background = { color: LIGHTBG };
  head(s, "판정의 한계",
    "공시지가에는 이미 도로와 용도지역이 들어 있다",
    "그래서 그대로 비교하면 행정 산식을 다시 그리는 것이 된다 — 어떻게 빼내는지와, 남는 한계");

  // ── 왼쪽: 순환성과 통제 ──
  s.addShape(pres.ShapeType.rect, {
    x: M, y: 2.0, w: 6.0, h: 4.05, fill: { color: WHITE }, line: { color: LINE, width: 1 },
  });
  s.addShape(pres.ShapeType.rect, {
    x: M, y: 2.0, w: 6.0, h: 0.5, fill: { color: DARKBG }, line: { color: DARKBG, width: 0 },
  });
  s.addText("① 용도지역·도로 효과를 먼저 빼낸다", {
    x: M + 0.22, y: 2.0, w: 5.6, h: 0.5, margin: 0,
    fontFace: F, fontSize: 13.5, bold: true, color: WHITE, valign: "middle",
  });
  s.addText([
    { text: "개별공시지가 = 비교표준지 × 토지가격비준표 배율", options: { color: INK, bold: true, breakLine: true } },
    { text: "그 비준표 항목에 ", options: { color: BODY } },
    { text: "용도지역 · 도로접면 · 도로거리", options: { color: REDDK, bold: true } },
    { text: "가 이미 들어 있다.", options: { color: BODY } },
  ], {
    x: M + 0.22, y: 2.64, w: 5.56, h: 0.62, margin: 0,
    fontFace: F, fontSize: 11.5, lineSpacingMultiple: 1.35,
  });

  const steps = [
    ["용도지역을 먼저 나눈다", "제1종일반 · 준주거 · 상업 …\n용도지역이 만든 가격 차이를 먼저 걷어낸다."],
    ["도로 조건도 같이 걷어낸다", "큰길에 붙었는지, 길에서 얼마나 먼지 —\n공시지가 산식이 이미 쓰는 항목들이다."],
    ["같은 동 안에서만 비교한다", "동네가 다르면 애초에 값이 다르다.\n강남과 도봉을 나란히 세우지 않는다."],
    ["그래도 남는 차이만 읽는다", "해 봤다 — 남았다. 같은 조건에서 목이 한 단계\n좋아지면 공시지가가 3.3% 높다."],
  ];
  steps.forEach((t, i) => {
    const y = 3.34 + i * 0.66;
    s.addShape(pres.ShapeType.ellipse, {
      x: M + 0.24, y: y + 0.06, w: 0.3, h: 0.3,
      fill: { color: i === 3 ? RED : TEAL }, line: { color: i === 3 ? RED : TEAL, width: 0 },
    });
    s.addText(String(i + 1), {
      x: M + 0.24, y: y + 0.06, w: 0.3, h: 0.3, margin: 0,
      fontFace: F, fontSize: 10, bold: true, color: WHITE, align: "center", valign: "middle",
    });
    s.addText(t[0], {
      x: M + 0.66, y: y + 0.02, w: 5.1, h: 0.26, margin: 0,
      fontFace: F, fontSize: 11.5, bold: true, color: i === 3 ? REDDK : INK,
    });
    s.addText(t[1], {
      x: M + 0.66, y: y + 0.28, w: 5.1, h: 0.38, margin: 0,
      fontFace: F, fontSize: 9.5, color: BODY, lineSpacingMultiple: 1.2,
    });
  });

  // ── 오른쪽: 공시지가 ≠ 실거래가 ──
  s.addShape(pres.ShapeType.rect, {
    x: 6.87, y: 2.0, w: 5.866, h: 4.05, fill: { color: WHITE }, line: { color: RED, width: 1.25 },
  });
  s.addShape(pres.ShapeType.rect, {
    x: 6.87, y: 2.0, w: 5.866, h: 0.5, fill: { color: RED }, line: { color: RED, width: 0 },
  });
  s.addText("② 공시지가는 실거래가가 아니다", {
    x: 7.09, y: 2.0, w: 5.4, h: 0.5, margin: 0,
    fontFace: F, fontSize: 13.5, bold: true, color: WHITE, valign: "middle",
  });

  const limits = [
    ["공시지가는 실제 거래 금액보다 낮고, 그 정도가 지역마다 다르다",
     "→ 그래서 “㎡당 얼마 싸다”고 말하지 않는다. 등수와 % 차이로만 말한다."],
    ["실거래가를 판정 기준으로 쓸 수 없다 — 이유 셋",
     "① 번지를 법으로 가려서 어느 땅인지 특정할 수 없다.  ② 서울 토지거래 94%가 대지라 금액에 건물값이 섞인다.  ③ 순수 토지 거래는 서울 필지의 1.5%뿐이다."],
    ["공시지가는 건물이 없다고 보고 매긴 값이다 (나지상정)",
     "같은 땅에 5층 건물이 서 있어도 건물은 지우고 땅만 평가한다. 우리가 알고 싶은 게 땅값이라 오히려 맞지만, “이 땅 사면 얼마”와는 다른 값이다."],
  ];
  limits.forEach((t, i) => {
    const y = 2.66 + i * 1.1;
    s.addText("· " + t[0], {
      x: 7.09, y, w: 5.42, h: 0.44, margin: 0,
      fontFace: F, fontSize: 11.5, bold: true, color: INK, lineSpacingMultiple: 1.2,
    });
    s.addText(t[1], {
      x: 7.31, y: y + 0.42, w: 5.2, h: 0.68, margin: 0,
      fontFace: F, fontSize: 10, color: BODY, lineSpacingMultiple: 1.25,
    });
  });

  // ── 하단: 그래서 우리가 말하는 저평가란 ──
  s.addText([
    { text: "그래서 “저평가”의 뜻 — ", options: { color: INK, bold: true } },
    { text: "시세보다 싸다가 아니라, ", options: { color: BODY } },
    { text: "같은 동 · 같은 용도지역 안에서 목에 비해 공시지가가 덜 매겨졌다", options: { color: REDDK, bold: true } },
    { text: "는 뜻이다.", options: { color: BODY } },
  ], {
    x: M, y: 6.22, w: CW, h: 0.34, margin: 0,
    fontFace: F, fontSize: 12.5, valign: "middle",
  });
  s.addText("매수 판단에는 현장 시세 확인이 따로 필요하다.", {
    x: M, y: 6.52, w: CW, h: 0.26, margin: 0,
    fontFace: F, fontSize: 10, color: BODY, italic: true,
  });
  s.addText("서울 전역 70만 필지 기준. 평균 설명력 증가는 0.1%p로 작지만, 갭 상위 1%의 목 백분위 평균은 97이다(전체 58.6).", {
    x: M, y: 6.76, w: CW, h: 0.26, margin: 0,
    fontFace: F, fontSize: 10, color: BODY, italic: true,
  });

  pageNo(s, "08", false);
  s.addNotes("3.3%의 출처는 backend/tools/experiment_integration_incremental.py다 — 서울 전역 703,049필지·동 426·타일 945. 스펙 B(raw+타일 고정효과) 계수 +0.0329 = 1 s.d.당 +3.3%, ΔR² +0.0013, t +15.90. 스펙 A(백분위+동 고정효과)는 계수 +0.0985, ΔR² +0.0008, t +10.10으로 같은 방향이다. R² 분해에서 도로접면까지 통제한 뒤에도 남는다는 게 요점이고, ΔR²가 0.001 수준이므로 “효과는 확실히 있으나 크기는 작다”가 정확한 표현이다. 표본을 3.4만 → 7.6만 → 70만으로 늘리는 동안 ΔR²는 줄고 t는 커졌다 — 그래서 ΔR²는 합격 기준으로 못 쓴다(524410c에서 판정 규칙에서 뺐다). “ΔR²가 작으니 저평가 랭킹도 무의미하다”는 반론에는 이렇게 답한다: 86만 필지 실측에서 갭 상위 1%의 구조 백분위 평균이 97.0(전체 58.6)이고 갭과의 상관은 구조 +0.673 / 가격 −0.715로 대칭이다. 회귀의 평균적 증분 설명력과 분포 꼬리의 판별력은 별개다. 다만 이 변수 단독으로 저평가를 판정하기엔 근거가 약하다는 것도 사실이다. 고정효과는 더미 대신 within 변환으로 흡수한다(더미 행렬이 4.37GiB로 터졌다). 전문가 질문이 가장 많이 나오는 장이다. 실거래는 판정에 쓰지 않고 동·구 단위로 뭉쳐 방향이 맞는지 확인하는 데만 쓴다 — 물으면 이렇게 답한다. 핵심: 공시지가에 도로·용도지역이 이미 반영돼 있으므로 통제 없이 회귀하면 행정 산식을 재현할 뿐이고, 높은 설명력은 성과가 아니다. 우리는 용도지역 더미·도로접면·도로거리·동 고정효과를 넣고 남는 증분만 본다. 두 번째 축은 공시지가가 실거래가가 아니라는 점 — 그래서 금액이 아니라 백분위로만 말한다. 실거래를 종속변수로 못 쓰는 이유(지번 마스킹, 대지 94%, 순수토지 1.5%)는 조사 문서에 근거가 있다.");
}

pres.writeFile({ fileName: "new-slides.pptx" }).then((f) => console.log("wrote", f));
