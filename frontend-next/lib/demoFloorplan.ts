/**
 * 데모용 도면(평면도) 분석 — **예시 데이터다.** 실제 DXF 인식 결과도, 모델
 * 출력도 아니다. 무대는 **가상의 5층 사무동 기준층 한 개 층**이고, 실재하는
 * 건물·회사·상호를 쓰지 않는다.
 *
 * 값은 전부 **손으로 적은 고정 표**에서 나온다. 난수를 쓰지 않는 이유는 두
 * 가지다. (1) 발표 중에 같은 화면을 두 번 열었을 때 문제 개수나 이동거리가
 * 달라지면 그건 데모가 아니라 사고다. (2) 손으로 적은 값이 난수보다 훨씬
 * 그럴듯하다 — 난수는 "동선 중첩 6m인데 개선 후 8m" 같은 앞뒤 안 맞는 조합을
 * 아무렇지 않게 만든다.
 *
 * ⚠️ 축척: 인식기가 **도면 축척을 자동 확인하지 못했다**(recogIssues ③).
 * 그래서 `ring`·`path`의 좌표는 viewBox 0 0 1000 700 안의 **도면 좌표**이고,
 * `areaM2`·`distanceM` 같은 실척 값은 좌표에서 계산한 값이 아니라 **도면에 적힌
 * 값**이다. 둘을 곱하거나 나눠서 새 수치를 만들지 마라 — 그러면 확인되지 않은
 * 축척을 확인된 것처럼 쓰게 된다.
 *
 * ⚠️ 표기: 여기 나오는 개선 효과는 "이만큼 좋아진다"가 아니라 **"도면 조건만
 * 바꿨을 때의 계산값"**이다. A/B 비교를 **단일 점수로 합치지 않는다** — 합치면
 * 나빠지거나 그대로인 항목이 좋아진 항목에 묻힌다. 그래서 `ab`에는 `flat`
 * 행이 남아 있고, `abNarrative`가 "그래도 확정하지 마라"를 말한다.
 */

/* ── 모양 ─────────────────────────────────────────────────────────────── */

/** 도면 좌표계. viewBox 0 0 1000 700 안의 값이다. */
export interface Pt { x: number; y: number }
export interface Wall { a: Pt; b: Pt }
export type OpeningKind = "door" | "window" | "main" | "exit";
export interface Opening { a: Pt; b: Pt; kind: OpeningKind }
export type SpaceKind = "use" | "circulation" | "shared" | "facility" | "leftover" | "unassigned";
export interface Space { id: string; name: string; kind: SpaceKind; areaM2: number; ring: Pt[]; note?: string }
export type Severity = "high" | "mid" | "low";
export interface Evidence { k: string; v: string }
export interface Issue {
  no: number; severity: Severity; title: string; body: string;
  impact: string; evidence: Evidence[]; action: string; at: Pt;
}
export type ActorId = "visitor" | "staff" | "manager" | "logistics" | "mobility";
export interface Actor { id: ActorId; label: string }
export interface Route {
  actor: ActorId; label: string; style: "solid" | "dash"; path: Pt[];
  distanceM: number; turns: number; minutes: number; overlapM: number;
  securityZones: number; confusingPoints: number; narrative: string;
  /** 병목 지점 — 도면에 번호로 찍는다. */ bottlenecks: Pt[];
}
export interface AreaSummary { totalM2: number; exclusiveM2: number; sharedM2: number; circulationPct: number; wasteM2: number }
export type SafetyLevel = "ok" | "check" | "risk";
export interface SafetyItem { label: string; value: string; level: SafetyLevel; note?: string }
export interface EffectRow { k: string; before: string; after: string; delta: string }
export interface Improvement {
  id: string; title: string; changes: string[]; effects: EffectRow[];
  /** 도면에 "여기서 저기로" 를 그린다. 없으면 안 그린다. */ moveFrom?: Pt; moveTo?: Pt;
}
export type AbDir = "better" | "worse" | "flat";
export interface AbRow { label: string; a: string; b: string; delta: string; dir: AbDir }
export interface RecogCount { k: string; n: number }
export interface RecogIssue { no: number; text: string; at: Pt }
export interface FloorplanDemo {
  projectName: string; fileName: string; floorLabel: string; scaleLabel: string;
  walls: Wall[]; openings: Opening[]; spaces: Space[];
  recogCounts: RecogCount[]; recogIssues: RecogIssue[];
  issues: Issue[];
  actors: Actor[]; routes: Route[];
  area: AreaSummary;
  evacuation: SafetyItem[]; accessibility: SafetyItem[];
  improvements: Improvement[];
  ab: AbRow[]; abNarrative: string;
  reportSections: string[]; reportFormats: string[];
}

/* ── 좌표 짧게 쓰기 ───────────────────────────────────────────────────── */

const p = (x: number, y: number): Pt => ({ x, y });
const wall = (x1: number, y1: number, x2: number, y2: number): Wall => ({ a: p(x1, y1), b: p(x2, y2) });
const open = (kind: OpeningKind, x1: number, y1: number, x2: number, y2: number): Opening => ({
  a: p(x1, y1), b: p(x2, y2), kind,
});
const rect = (x1: number, y1: number, x2: number, y2: number): Pt[] => [p(x1, y1), p(x2, y1), p(x2, y2), p(x1, y2)];

/* ── 벽체 46개 ────────────────────────────────────────────────────────── */

/**
 * 외곽선은 창·주출입구가 뚫린 자리에서 끊어 **여러 토막**으로 둔다. 한 변을
 * 통짜 선 하나로 두면 그 위에 창을 겹쳐 그리게 되고, 그건 도면이 아니라
 * 그림이다. 벽 46 · 문 19 · 창 11은 `recogCounts`에 그대로 적히므로
 * **배열 길이가 곧 인식 결과다** — 테스트가 이 일치를 지킨다.
 */
const WALLS: Wall[] = [
  // 북측 외벽 (창 4개 사이의 다섯 토막)
  wall(60, 60, 200, 60),
  wall(280, 60, 320, 60),
  wall(400, 60, 600, 60),
  wall(680, 60, 750, 60),
  wall(810, 60, 940, 60),
  // 동측 외벽 (창 2개 사이의 세 토막)
  wall(940, 60, 940, 180),
  wall(940, 250, 940, 480),
  wall(940, 560, 940, 640),
  // 남측 외벽 (창 4개 사이의 다섯 토막)
  wall(940, 640, 800, 640),
  wall(720, 640, 680, 640),
  wall(600, 640, 530, 640),
  wall(450, 640, 380, 640),
  wall(300, 640, 60, 640),
  // 서측 외벽 (주출입구 1 + 창 1 사이의 세 토막)
  wall(60, 640, 60, 465),
  wall(60, 405, 60, 260),
  wall(60, 200, 60, 60),
  // 서측 코어
  wall(160, 60, 160, 270),
  wall(60, 160, 160, 160),
  wall(60, 270, 160, 270),
  // 북측 실들의 남측벽 = 중앙 복도 북측벽
  wall(160, 270, 835, 270),
  wall(560, 60, 560, 270),
  wall(720, 60, 720, 270),
  // 동측 코어
  wall(835, 60, 835, 640),
  wall(835, 160, 940, 160),
  wall(835, 270, 940, 270),
  wall(835, 435, 940, 435),
  // 로비·안내데스크
  wall(250, 270, 250, 500),
  wall(165, 270, 165, 375),
  wall(165, 375, 250, 375),
  wall(60, 495, 250, 495),
  // 중앙 복도 남측벽과 그 아래 실들
  wall(260, 360, 835, 360),
  wall(260, 360, 260, 520),
  wall(410, 360, 410, 520),
  wall(420, 360, 420, 470),
  wall(530, 360, 530, 470),
  wall(420, 470, 530, 470),
  wall(540, 360, 540, 510),
  wall(680, 360, 680, 510),
  wall(540, 510, 680, 510),
  wall(690, 360, 690, 510),
  wall(830, 360, 830, 510),
  wall(690, 510, 830, 510),
  // 남측 업무공간
  wall(60, 520, 835, 520),
  // 내부 부분벽(파티션) — 실을 나누지 않으므로 공간 수에는 안 들어간다
  wall(300, 60, 300, 150),
  wall(300, 190, 300, 270),
  wall(400, 520, 400, 640),
];

/* ── 개구부 33개 (문 19 · 창 11 · 주출입구 1 · 비상구 2) ─────────────── */

const OPENINGS: Opening[] = [
  // 주출입구 1 — 서측 외벽
  open("main", 60, 405, 60, 465),
  // 비상구 2 — 양쪽 계단실 출입
  open("exit", 160, 90, 160, 140),
  open("exit", 835, 90, 835, 140),
  // 창 11 — 전부 외벽에 붙는다
  open("window", 200, 60, 280, 60),
  open("window", 320, 60, 400, 60),
  open("window", 600, 60, 680, 60),
  open("window", 750, 60, 810, 60),
  open("window", 940, 180, 940, 250),
  open("window", 940, 480, 940, 560),
  open("window", 800, 640, 720, 640),
  open("window", 680, 640, 600, 640),
  open("window", 530, 640, 450, 640),
  open("window", 380, 640, 300, 640),
  open("window", 60, 260, 60, 200),
  // 문 19
  open("door", 160, 200, 160, 245), // 화장실
  open("door", 330, 270, 390, 270), // 업무공간 1 ← 복도 (서)
  open("door", 470, 270, 520, 270), // 업무공간 1 ← 복도 (동)
  open("door", 620, 270, 670, 270), // 회의실 A ← 복도
  open("door", 560, 145, 560, 190), // 회의실 A ← 업무공간 1 (오인식 의심 구간)
  open("door", 760, 270, 805, 270), // 실명 미상 공간 ← 복도
  open("door", 880, 270, 925, 270), // 설비실 ← 서비스 구역
  open("door", 880, 435, 925, 435), // 휴게실 ← 서비스 구역
  open("door", 165, 300, 165, 340), // 안내데스크 ← 로비
  open("door", 250, 300, 250, 345), // 로비 ← 중앙 복도
  open("door", 300, 360, 360, 360), // 중앙 홀 ← 복도
  open("door", 450, 360, 500, 360), // 엘리베이터 홀 ← 복도
  open("door", 580, 360, 640, 360), // 회의실 B ← 복도
  open("door", 720, 360, 770, 360), // 잔여공간 ← 복도
  open("door", 835, 300, 835, 345), // 동측 서비스 구역 ← 복도
  open("door", 300, 520, 360, 520), // 업무공간 2 ← 중앙 홀
  open("door", 450, 520, 500, 520), // 업무공간 2 ← 엘리베이터 홀 방면
  open("door", 580, 520, 640, 520), // 업무공간 2 ← 회의실 B
  open("door", 120, 520, 170, 520), // 업무공간 2 ← 로비
];

/* ── 공간 17개 ────────────────────────────────────────────────────────── */

/**
 * `areaM2`는 도면에 적힌 면적이다(위 축척 주의 참고). 합이 520㎡가 되도록
 * 손으로 맞췄고, 테스트가 그 합을 지킨다.
 *
 * **전용/공용은 `kind`와 1:1이 아니다.** 전용 369㎡ = 임차인이 쓰는 실
 * (안내데스크·회의실·업무공간·실명 미상·휴게실·설비실·서비스 구역·잔여공간),
 * 공용 151㎡ = 코어와 공용부(로비·중앙 복도·중앙 홀·엘리베이터 홀·계단 2·화장실).
 * 실제 임대차가 그렇게 갈리기 때문이고, `kind`는 **동선상의 성격**을 말한다.
 */
const SPACES: Space[] = [
  { id: "stair-w", name: "서측 계단실", kind: "facility", areaM2: 10, ring: rect(60, 60, 160, 160) },
  { id: "wc", name: "화장실", kind: "facility", areaM2: 11, ring: rect(60, 160, 160, 270) },
  { id: "work-1", name: "업무공간 1", kind: "use", areaM2: 108, ring: rect(160, 60, 560, 270) },
  { id: "mtg-a", name: "회의실 A", kind: "use", areaM2: 34, ring: rect(560, 60, 720, 270) },
  {
    id: "unnamed", name: "실명 미상 공간", kind: "unassigned", areaM2: 28, ring: rect(720, 60, 835, 270),
    note: "도면에 실명이 없어 용도를 확정하지 못했습니다.",
  },
  { id: "stair-e", name: "동측 계단실", kind: "facility", areaM2: 10, ring: rect(835, 60, 940, 160) },
  { id: "mech", name: "설비실", kind: "facility", areaM2: 12, ring: rect(835, 160, 940, 270) },
  {
    // 안내데스크 자리를 도려낸 ㄴ자 형태다 — 그 도려낸 자리가 문제 2번의 원인이다.
    id: "lobby", name: "로비", kind: "circulation", areaM2: 34,
    ring: [p(60, 270), p(165, 270), p(165, 375), p(250, 375), p(250, 495), p(60, 495)],
  },
  {
    id: "reception", name: "안내데스크", kind: "shared", areaM2: 8, ring: rect(165, 270, 250, 375),
    note: "주출입구 시야축에서 벗어나 있습니다.",
  },
  { id: "corridor", name: "중앙 복도", kind: "circulation", areaM2: 46, ring: rect(260, 270, 835, 360) },
  { id: "service", name: "동측 서비스 구역", kind: "facility", areaM2: 17, ring: rect(835, 270, 940, 435) },
  { id: "hall", name: "중앙 홀", kind: "circulation", areaM2: 26, ring: rect(260, 360, 410, 520) },
  { id: "elev", name: "엘리베이터 홀", kind: "circulation", areaM2: 14, ring: rect(420, 360, 530, 470) },
  { id: "mtg-b", name: "회의실 B", kind: "use", areaM2: 26, ring: rect(540, 360, 680, 510) },
  {
    id: "leftover", name: "잔여공간", kind: "leftover", areaM2: 18, ring: rect(690, 360, 830, 510),
    note: "복도 동측 끝. 진입 방향이 하나뿐이라 실로 쓰기 어렵습니다.",
  },
  { id: "lounge", name: "휴게실", kind: "shared", areaM2: 22, ring: rect(835, 435, 940, 640) },
  { id: "work-2", name: "업무공간 2", kind: "use", areaM2: 96, ring: rect(60, 520, 835, 640) },
];

/* ── 인식 결과 ────────────────────────────────────────────────────────── */

const RECOG_COUNTS: RecogCount[] = [
  { k: "공간", n: 17 },
  { k: "벽체", n: 46 },
  { k: "문", n: 19 },
  { k: "창문", n: 11 },
  { k: "주출입구", n: 1 },
  { k: "비상구", n: 2 },
  { k: "계단", n: 2 },
  { k: "엘리베이터", n: 1 },
  { k: "인식하지 못한 요소", n: 3 },
];

/** 인식기가 **틀렸을 수 있는 자리**를 먼저 말한다. 맞은 것만 세면 신뢰가 아니라 착시다. */
const RECOG_ISSUES: RecogIssue[] = [
  { no: 1, text: "이 끊어진 벽체를 출입문으로 판단했습니다.", at: p(560, 167) },
  { no: 2, text: "이 공간에는 실명이 없습니다.", at: p(777, 165) },
  { no: 3, text: "도면 축척을 자동으로 확인하지 못했습니다.", at: p(500, 615) },
];

/* ── 문제 5개 ─────────────────────────────────────────────────────────── */

/**
 * 중요도 순으로 둔다(high → mid → low). 번호는 **표시 순서**이지 심각도
 * 점수가 아니다. 근거에 Space Syntax 지표(Integration·Choice·Depth)를 섞되
 * 지표를 주인공으로 세우지 않는다 — 읽는 사람이 답을 얻어야 할 질문은
 * "Choice가 몇 %인가"가 아니라 "여기서 뭐가 잘못되나"다.
 */
const ISSUES: Issue[] = [
  {
    no: 1, severity: "high", title: "방문자·물류 동선 충돌",
    body: "로비와 중앙 홀 사이 약 6m 구간에서 방문자와 배송 직원의 동선이 겹칩니다.",
    impact: "출근 시간과 입고 시간이 겹치면 로비 혼잡이 발생하고 직원 전용구역이 방문자에게 노출될 수 있습니다.",
    evidence: [
      { k: "동선 중첩", v: "6m" },
      { k: "최단경로 집중도", v: "상위 9%" },
      { k: "해당 구간 연결 공간", v: "4개" },
      { k: "예상 병목지점", v: "1곳" },
    ],
    action: "물류 출입구를 동측 서비스 구역과 연결하면 방문자 동선과 분리할 수 있습니다.",
    at: p(250, 317),
  },
  {
    no: 2, severity: "high", title: "입구에서 안내데스크가 보이지 않음",
    body: "주출입구에서 안내데스크까지 직선 시야가 확보되지 않습니다. 로비 북측 벽이 시야축을 가로막습니다.",
    impact: "방문자가 처음에 갈 곳을 찾지 못하면 가까운 직원에게 길을 묻게 되고, 안내 업무가 업무공간으로 넘어옵니다.",
    evidence: [
      { k: "입구에서 보이는 공간", v: "2개" },
      { k: "안내데스크까지 방향 전환", v: "2회" },
      { k: "로비 가시영역", v: "층 평균의 61%" },
      { k: "길 찾기 어려운 지점", v: "2곳" },
    ],
    action: "안내데스크를 입구 전면 시야축으로 옮기면 방향 전환 없이 인지됩니다.",
    at: p(207, 322),
  },
  {
    no: 3, severity: "high", title: "비상구까지 경로가 한 방향에 의존",
    body: "휴게실과 동측 서비스 구역에서 비상구로 가는 경로가 동측 계단실 한 방향뿐입니다.",
    impact: "동측 통로가 막히면 두 공간의 재실자가 우회할 경로가 없습니다.",
    evidence: [
      { k: "단일 경로 의존 공간", v: "2곳" },
      { k: "대체 경로", v: "0개" },
      { k: "출구까지 최대 이동거리", v: "31m" },
      { k: "막다른 복도", v: "1곳" },
    ],
    action: "휴게실 서측 벽에 개구부를 두어 중앙 복도로 직접 연결하면 경로가 둘이 됩니다.",
    at: p(887, 540),
  },
  {
    no: 4, severity: "mid", title: "사용하기 어려운 잔여공간",
    body: "중앙 복도 동측 끝에 18㎡의 잔여공간이 있습니다. 진입 방향이 하나뿐이고 폭 대비 깊이가 커서 실로 쓰기 어렵습니다.",
    impact: "전용면적으로는 잡히지만 업무에 쓰이지 않아 면적당 임대 효율이 떨어집니다.",
    evidence: [
      { k: "잔여면적", v: "18㎡" },
      { k: "전체 대비", v: "3.5%" },
      { k: "진입 동선", v: "1방향" },
      { k: "Depth", v: "층 내 상위 12% (깊다)" },
    ],
    action: "회의실 B와 통합하거나 창고·집중업무실로 용도를 지정하면 면적을 회수할 수 있습니다.",
    at: p(760, 435),
  },
  {
    no: 5, severity: "low", title: "휠체어 이동 폭 확인 필요",
    body: "중앙 홀 입구의 유효폭이 도면상 1.4m로 읽힙니다. 권장 기준(1.5m)에 못 미칠 수 있습니다.",
    impact: "실측 결과에 따라 보완이 필요할 수 있습니다. 지금 단계에서 확정할 사항은 아닙니다.",
    evidence: [
      { k: "해당 구간", v: "1곳" },
      { k: "도면 표기 유효폭", v: "1.4m" },
      { k: "권장 기준", v: "1.5m" },
      { k: "축척 확인", v: "미확인" },
    ],
    action: "실측으로 유효폭을 확인하십시오. 도면 축척이 자동 확인되지 않아 위 수치는 참고값입니다.",
    at: p(330, 360),
  },
];

/* ── 사용자별 동선 ────────────────────────────────────────────────────── */

const ACTORS: Actor[] = [
  { id: "visitor", label: "방문자" },
  { id: "logistics", label: "물류" },
  { id: "staff", label: "직원" },
  { id: "manager", label: "관리자" },
  { id: "mobility", label: "교통약자" },
];

/**
 * 경로는 복도를 따라 꺾이는 폴리라인이다. 직선 하나로 두면 "여기서 저기까지
 * 얼마"만 남고 **어디서 겹치는지**가 사라진다 — 이 화면이 답해야 할 질문이
 * 바로 그거다.
 *
 * 방문자와 물류는 주출입구 → 로비 → 복도 서측 구간을 **실제로 공유한다.**
 * 문제 1번의 "중첩 6m"이 그 구간이고, 개선안 ①이 그걸 끊는다.
 */
const ROUTES: Route[] = [
  {
    actor: "visitor", label: "방문자 · 주출입구 → 회의실 A", style: "solid",
    path: [p(60, 435), p(150, 435), p(150, 317), p(350, 317), p(350, 165), p(645, 165)],
    distanceM: 42, turns: 4, minutes: 1.0, overlapM: 6,
    securityZones: 1, confusingPoints: 2,
    narrative:
      "방문자는 회의실에 도달하기 위해 중앙 업무구역을 통과합니다. 회의실 출입문을 로비 방향으로 옮기면 이동거리를 11% 줄이고 직원 공간 노출을 방지할 수 있습니다.",
    bottlenecks: [p(250, 317), p(560, 167)],
  },
  {
    actor: "logistics", label: "물류 · 주출입구 → 동측 서비스 구역", style: "dash",
    path: [p(60, 435), p(150, 435), p(150, 317), p(880, 317), p(880, 380)],
    distanceM: 38, turns: 3, minutes: 0.9, overlapM: 6,
    securityZones: 2, confusingPoints: 1,
    narrative:
      "배송 물품이 주출입구로 들어와 중앙 복도 전체를 지나갑니다. 동측에 서비스 출입문이 있는데도 연결되어 있지 않아, 방문자가 쓰는 구간을 그대로 되짚습니다.",
    bottlenecks: [p(250, 317)],
  },
  {
    actor: "staff", label: "직원 · 주출입구 → 업무공간 1", style: "solid",
    path: [p(60, 435), p(150, 435), p(150, 317), p(470, 317), p(470, 180)],
    distanceM: 31, turns: 3, minutes: 0.7, overlapM: 4,
    securityZones: 0, confusingPoints: 0,
    narrative:
      "직원 동선은 짧고 단순합니다. 다만 방문자와 같은 문으로 들어와 같은 복도를 쓰므로, 출근 시간대에는 방문자 혼잡을 그대로 겪습니다.",
    bottlenecks: [p(250, 317)],
  },
  {
    actor: "manager", label: "관리자 · 업무공간 1 → 회의실 B", style: "dash",
    path: [p(355, 150), p(355, 317), p(610, 317), p(610, 430)],
    distanceM: 27, turns: 2, minutes: 0.6, overlapM: 0,
    securityZones: 0, confusingPoints: 1,
    narrative:
      "층 내부 이동이라 외부 동선과 겹치지 않습니다. 회의실 B가 복도에 바로 붙어 있어 방향 전환이 두 번으로 끝납니다.",
    bottlenecks: [],
  },
  {
    actor: "mobility", label: "교통약자 · 주출입구 → 엘리베이터 홀", style: "solid",
    path: [p(60, 435), p(150, 435), p(150, 317), p(475, 317), p(475, 420)],
    distanceM: 33, turns: 3, minutes: 1.4, overlapM: 6,
    securityZones: 1, confusingPoints: 2,
    narrative:
      "단차 없이 연결되지만 중앙 홀 입구의 유효폭이 도면상 1.4m로 읽힙니다(문제 5번). 이동 시간은 같은 거리의 다른 동선보다 길게 잡았습니다.",
    bottlenecks: [p(330, 360)],
  },
];

/* ── 면적 ─────────────────────────────────────────────────────────────── */

const AREA: AreaSummary = {
  totalM2: 520,
  exclusiveM2: 369,
  sharedM2: 151,
  // 이동공간 = kind가 "circulation"인 공간(로비·중앙 복도·중앙 홀·엘리베이터 홀)의 합 120㎡.
  circulationPct: 23,
  wasteM2: 18,
};

/* ── 안전·접근성 사전검토 ────────────────────────────────────────────── */

/** **사전검토다. 법정 적합 판정이 아니다.** 값은 도면 표기 기준이고 축척이 미확인이다. */
const EVACUATION: SafetyItem[] = [
  { label: "주 피난경로", value: "중앙 복도 → 서측 계단실", level: "ok" },
  { label: "대체 피난경로", value: "중앙 복도 → 동측 계단실", level: "ok" },
  { label: "출구까지 이동거리", value: "최대 31m (권장 30m)", level: "check" },
  { label: "막다른 복도", value: "1곳 · 약 8m", level: "check" },
  { label: "병목 가능 구간", value: "중앙 홀 입구 · 유효폭 1.4m", level: "check" },
  { label: "비상구가 보이지 않는 공간", value: "3곳 (휴게실 · 잔여공간 · 설비실)", level: "check" },
  {
    label: "출구 집중 구간", value: "휴게실 → 동측 계단실 단일 경로", level: "risk",
    note: "해당 경로가 차단되면 대체 경로가 부족할 수 있으므로 추가 검토가 필요합니다.",
  },
];

const ACCESSIBILITY: SafetyItem[] = [
  { label: "휠체어 이동 경로", value: "주출입구 → 엘리베이터 홀까지 연속", level: "ok" },
  { label: "출입문 유효폭", value: "최소 0.85m (권장 0.8m 이상)", level: "ok" },
  {
    label: "복도 유효폭", value: "중앙 복도 1.8m / 중앙 홀 입구 1.4m", level: "check",
    note: "중앙 홀 입구만 권장 1.5m에 못 미치는 것으로 읽힙니다.",
  },
  { label: "회전 가능 공간", value: "1.5m×1.5m 확보 4곳 · 미확보 1곳", level: "check" },
  { label: "엘리베이터 접근성", value: "중앙 복도에서 단차 없이 연결", level: "ok" },
  { label: "장애인 화장실까지 거리", value: "최대 38m", level: "check" },
  { label: "단차 의심 구간", value: "동측 서비스 구역 경계 1곳", level: "check" },
];

/* ── 개선 제안 ────────────────────────────────────────────────────────── */

const IMPROVEMENTS: Improvement[] = [
  {
    id: "svc-split", title: "서비스 동선 분리",
    changes: [
      "동측 서비스 출입문과 물류공간을 직접 연결합니다.",
      "중앙 홀을 통과하던 배송 동선을 제거합니다.",
    ],
    effects: [
      { k: "동선 중첩", before: "6m", after: "0m", delta: "해소" },
      { k: "방문자 혼잡", before: "출근·입고 시간대 발생", after: "시간대 중복 없음", delta: "감소 예상" },
      { k: "직원 전용공간 노출", before: "1구간", after: "없음", delta: "제거" },
      { k: "물류 이동거리", before: "38m", after: "41m", delta: "+3m" },
    ],
    moveFrom: p(80, 435), moveTo: p(930, 320),
  },
  {
    id: "desk-move", title: "안내데스크 이동",
    changes: [
      "안내데스크를 주출입구 전면 시야축으로 옮깁니다.",
      "로비 북측의 도려낸 벽면은 대기 공간으로 남깁니다.",
    ],
    effects: [
      { k: "입구에서 보이는 공간", before: "2개", after: "4개", delta: "가시성 증가" },
      { k: "방향 전환", before: "4회", after: "2회", delta: "−2회" },
      { k: "길 찾기 취약지점", before: "2곳", after: "0곳", delta: "해소" },
    ],
    moveFrom: p(207, 322), moveTo: p(180, 445),
  },
];

/* ── A/B 대안 비교 ────────────────────────────────────────────────────── */

/**
 * **단일 점수로 합치지 않는다.** 여섯 줄 중 한 줄은 `flat`이고, 그 줄이 남아
 * 있어야 "B안이 다 낫다"가 아니라 "B안은 이건 낫고 저건 그대로다"로 읽힌다.
 */
const AB: AbRow[] = [
  { label: "전용면적 비율", a: "71%", b: "74%", delta: "+3%p", dir: "better" },
  { label: "방문자 이동거리", a: "42m", b: "37m", delta: "−11.9%", dir: "better" },
  { label: "동선 중첩", a: "6m", b: "0m", delta: "해소", dir: "better" },
  { label: "길 찾기 취약지점", a: "2곳", b: "0곳", delta: "개선", dir: "better" },
  { label: "피난 취약지점", a: "1곳", b: "1곳", delta: "변화 없음", dir: "flat" },
  { label: "가시 사각지대", a: "3곳", b: "2곳", delta: "1곳 감소", dir: "better" },
];

const AB_NARRATIVE =
  "B안은 방문자 동선과 공간 효율이 개선되었습니다. 다만 휴게실의 대체 피난경로 문제는 해결되지 않았으므로 B안을 그대로 확정하기보다 피난 동선을 추가로 수정하는 것이 좋습니다.";

/* ── 보고서 ───────────────────────────────────────────────────────────── */

const REPORT_SECTIONS = [
  "프로젝트 개요",
  "도면 인식 결과",
  "주요 문제 요약",
  "사용자별 동선 분석",
  "공간 효율 분석",
  "안전·접근성 사전검토",
  "가시성 및 공간구조 분석",
  "개선 제안",
  "A/B 대안 비교",
  "분석 조건과 한계",
];

const REPORT_FORMATS = [
  "PDF",
  "PPT",
  "Excel 면적표",
  "문제 위치가 표시된 도면 이미지",
  "검토 의견서",
];

/* ── 조립 ─────────────────────────────────────────────────────────────── */

/**
 * 항상 같은 결과다(난수 없음). 매번 새 객체를 만들어 돌려주는 이유는, 렌더러가
 * 실수로 배열을 정렬하거나 좌표를 고쳐도 다음 호출이 오염되지 않게 하기 위해서다.
 */
export function buildFloorplanDemo(): FloorplanDemo {
  return {
    projectName: "가상 사무동 기준층 검토",
    fileName: "office-typical-floor.dxf",
    floorLabel: "지상 5층 중 기준층 1개 층",
    scaleLabel: "축척 미확인 (도면 표기값 사용)",
    walls: WALLS.map((w) => ({ a: { ...w.a }, b: { ...w.b } })),
    openings: OPENINGS.map((o) => ({ a: { ...o.a }, b: { ...o.b }, kind: o.kind })),
    spaces: SPACES.map((s) => ({ ...s, ring: s.ring.map((q) => ({ ...q })) })),
    recogCounts: RECOG_COUNTS.map((c) => ({ ...c })),
    recogIssues: RECOG_ISSUES.map((r) => ({ ...r, at: { ...r.at } })),
    issues: ISSUES.map((i) => ({ ...i, evidence: i.evidence.map((e) => ({ ...e })), at: { ...i.at } })),
    actors: ACTORS.map((a) => ({ ...a })),
    routes: ROUTES.map((r) => ({
      ...r,
      path: r.path.map((q) => ({ ...q })),
      bottlenecks: r.bottlenecks.map((q) => ({ ...q })),
    })),
    area: { ...AREA },
    evacuation: EVACUATION.map((e) => ({ ...e })),
    accessibility: ACCESSIBILITY.map((e) => ({ ...e })),
    improvements: IMPROVEMENTS.map((im) => ({
      ...im,
      changes: [...im.changes],
      effects: im.effects.map((e) => ({ ...e })),
      moveFrom: im.moveFrom ? { ...im.moveFrom } : undefined,
      moveTo: im.moveTo ? { ...im.moveTo } : undefined,
    })),
    ab: AB.map((r) => ({ ...r })),
    abNarrative: AB_NARRATIVE,
    reportSections: [...REPORT_SECTIONS],
    reportFormats: [...REPORT_FORMATS],
  };
}

/* ── 표기 ─────────────────────────────────────────────────────────────── */

/** 면적. 소수 첫째 자리까지만 쓴다 — 축척이 미확인인데 둘째 자리를 쓰면 없는 정밀도를 있는 척한다. */
export function areaText(m2: number): string {
  return `${m2.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}㎡`;
}

/** 거리. 도면 표기 기준이라 미터 단위에서 끊는다. */
export function meters(m: number): string {
  return `${m.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}m`;
}

/**
 * 중요도 표기. "high/mid/low"를 화면에 그대로 내면 등급처럼 읽힌다 —
 * **무엇을 하라는 말인지**로 바꿔 쓴다. low는 "낮은 문제"가 아니라 참고사항이다.
 */
export function severityLabel(s: Severity): string {
  if (s === "high") return "즉시 검토";
  if (s === "mid") return "개선 권장";
  return "참고사항";
}

/** tokens.css의 빨강/주황/파랑과 같은 값이다. 여기서 새 색을 만들지 않는다. */
export function severityColor(s: Severity): string {
  if (s === "high") return "#dc2626";
  if (s === "mid") return "#d97706";
  return "#2563eb";
}

/** 안전·접근성 항목 상태. "위험"은 법정 위반 판정이 아니라 **추가 검토가 필요하다**는 뜻이다. */
export function levelLabel(l: SafetyLevel): string {
  if (l === "ok") return "확인됨";
  if (l === "check") return "확인 필요";
  return "위험";
}
