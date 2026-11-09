// 탭 화면은 app-shell을 뷰포트 높이에 고정하고 안쪽 패널(.tab-page 등)만 스크롤한다.
// 그런데 element.scrollIntoView는 안쪽 패널을 끝까지 굴린 뒤 창(문서)까지 굴려, 휴대폰에서
// 「분석 실행」 뒤 창이 48px 올라가 위 탭 줄(AI 입지·지도·평면도·계획도면)이 사라지고 아래에
// 빈 회색 띠가 생겼다. body가 overflow:hidden이라 손으로는 되돌릴 수도 없었다 — 가장 가까운
// 스크롤 패널만 굴린다 (2026-11-07 UX 자동 개선, 390px).

export function scrollParent(el: HTMLElement): HTMLElement | null {
  let p = el.parentElement;
  while (p && p !== document.body) {
    const oy = getComputedStyle(p).overflowY;
    if ((oy === "auto" || oy === "scroll") && p.scrollHeight > p.clientHeight) return p;
    p = p.parentElement;
  }
  return null;
}

/** el을 가장 가까운 스크롤 패널 안에서만 보이게 굴린다(창은 굴리지 않는다). */
export function scrollIntoPanel(el: HTMLElement, block: "start" | "center" = "start", marginTop = 8): void {
  const panel = scrollParent(el);
  if (!panel) {
    el.scrollIntoView({ behavior: "smooth", block });
    return;
  }
  const pr = panel.getBoundingClientRect();
  const er = el.getBoundingClientRect();
  const delta =
    block === "start" ? er.top - pr.top - marginTop : er.top + er.height / 2 - (pr.top + pr.height / 2);
  panel.scrollBy({ top: delta, behavior: "smooth" });
}
