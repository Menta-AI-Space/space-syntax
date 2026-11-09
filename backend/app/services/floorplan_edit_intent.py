"""말로 고치기 — 도면에서 찍은 자리 + 한 문장 지시 → 벽·문 편집 (2026-11-09).

사용자는 도면을 누르거나(점) 끌어서(네모) 자리를 고르고 「여기 문 내줘」처럼 말한다.
GPT 이미지 수정처럼 보이지만 **그림을 고치지 않는다** — 분석 엔진은 정확한 벽·문 선이
있어야 돌아가므로, GPT는 「어느 벽에 무엇을 할지」만 고르고 선 계산은 여기서 한다
(SPEC_FLOORPLAN_TABS_UI 2026-11-09).

GPT에게는 고른 자리 주변의 벽·문만 번호(w1, d1 …)를 붙여 보여 주고, 명령은 다섯 가지 중에서만
고르게 한다. 그 밖의 것, 번호가 틀린 것, 고른 자리에서 멀리 떨어진 좌표는 버린다. GPT가 꺼져
있거나 실패하면 낱말 규칙으로 같은 명령을 만든다 — 기능이 GPT 없이도 죽지 않게.

결과는 「더할 선 / 지울 선 번호」다. 번호는 요청에 실려 온 배열의 순서이고, 프론트는 미리보기를
보여 주는 동안 도면을 잠가 두었다가 그대로 적용한다.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.services.floorplan_issues import _room
from app.services.llm_client import complete_json

logger = logging.getLogger(__name__)

Seg = tuple[float, float, float, float]

DEFAULT_DOOR_M = 3.0
MIN_DOOR_M = 0.6
MAX_DOOR_M = 12.0
# 점으로 골랐을 때 「이 근처」로 보는 거리. 매장 하나 폭(6~10m)보다 조금 넓게.
REACH_M = 12.0
MAX_WALLS = 40
MAX_DOORS = 15
MAX_LABELS = 12
MAX_OPS = 6

OP_KINDS = ("add_door", "remove_wall", "close_door", "add_wall", "clear_walls_in_box")


@dataclass
class Selection:
    x: float
    y: float
    box: tuple[float, float, float, float] | None = None  # x1 < x2, y1 < y2


@dataclass
class EditPlan:
    add_lines: list[Seg] = field(default_factory=list)
    remove_lines: list[int] = field(default_factory=list)
    add_openings: list[Seg] = field(default_factory=list)
    remove_openings: list[int] = field(default_factory=list)
    steps: list[tuple[str, str]] = field(default_factory=list)  # (op, 한국어 설명)
    message: str = ""
    question: str | None = None
    source: str = "rule"
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Ctx:
    lines: list[Seg]
    openings: list[Seg]
    labels: list[tuple[str, float, float]]
    sel: Selection
    unit_per_m: float
    wall_ids: dict[str, int]  # "w3" → lines 번호
    door_ids: dict[str, int]  # "d1" → openings 번호

    def m(self, meters: float) -> float:
        return meters * self.unit_per_m


# --- 기하 ------------------------------------------------------------------


def _length(s: Seg) -> float:
    return math.hypot(s[2] - s[0], s[3] - s[1])


def _project_t(s: Seg, x: float, y: float) -> float:
    dx, dy = s[2] - s[0], s[3] - s[1]
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return 0.0
    return ((x - s[0]) * dx + (y - s[1]) * dy) / l2


def _at(s: Seg, t: float) -> tuple[float, float]:
    return s[0] + t * (s[2] - s[0]), s[1] + t * (s[3] - s[1])


def _dist(s: Seg, x: float, y: float) -> float:
    t = max(0.0, min(1.0, _project_t(s, x, y)))
    px, py = _at(s, t)
    return math.hypot(x - px, y - py)


def _clip(s: Seg, box: tuple[float, float, float, float]) -> tuple[float, float] | None:
    """Liang–Barsky — 선이 네모 안에 드는 구간 (t0, t1). 안 들면 None."""
    x1, y1, x2, y2 = s
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - box[0]), (dx, box[2] - x1), (-dy, y1 - box[1]), (dy, box[3] - y1)):
        if p == 0:
            if q < 0:
                return None
            continue
        r = q / p
        if p < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
        if t0 > t1:
            return None
    return t0, t1


def _expand(box: tuple[float, float, float, float], pad: float) -> tuple[float, float, float, float]:
    return box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad


def _ray_hit(ox: float, oy: float, dx: float, dy: float, s: Seg) -> float | None:
    """(ox,oy)에서 (dx,dy) 쪽으로 쏜 반직선이 선 s와 만나는 거리 비율 u(>0)."""
    ex, ey = s[2] - s[0], s[3] - s[1]
    den = dx * ey - dy * ex
    if abs(den) < 1e-12:
        return None
    u = ((s[0] - ox) * ey - (s[1] - oy) * ex) / den
    v = ((s[0] - ox) * dy - (s[1] - oy) * dx) / den
    if u > 1e-9 and -1e-9 <= v <= 1 + 1e-9:
        return u
    return None


def _direction(dx: float, dy: float) -> str:
    """도면 좌표는 y가 아래로 커진다 — 위쪽이 북쪽."""
    angle = math.degrees(math.atan2(-dy, dx)) % 360
    names = ["동", "북동", "북", "북서", "서", "남서", "남", "남동"]
    return names[int(((angle + 22.5) % 360) // 45)] + "쪽"


def _orientation(s: Seg) -> str:
    angle = abs(math.degrees(math.atan2(s[3] - s[1], s[2] - s[0]))) % 180
    if angle < 20 or angle > 160:
        return "가로"
    if 70 < angle < 110:
        return "세로"
    return "사선"


# --- 후보 고르기 ------------------------------------------------------------


def _in_reach(ctx: _Ctx, s: Seg) -> bool:
    sel = ctx.sel
    if sel.box:
        return _clip(s, _expand(sel.box, ctx.m(1.5))) is not None
    return _dist(s, sel.x, sel.y) <= ctx.m(REACH_M)


def _candidates(ctx: _Ctx) -> None:
    sel = ctx.sel
    walls = sorted(
        (i for i, s in enumerate(ctx.lines) if _in_reach(ctx, s)),
        key=lambda i: _dist(ctx.lines[i], sel.x, sel.y),
    )[:MAX_WALLS]
    doors = sorted(
        (i for i, s in enumerate(ctx.openings) if _in_reach(ctx, s)),
        key=lambda i: _dist(ctx.openings[i], sel.x, sel.y),
    )[:MAX_DOORS]
    ctx.wall_ids = {f"w{k + 1}": i for k, i in enumerate(walls)}
    ctx.door_ids = {f"d{k + 1}": i for k, i in enumerate(doors)}


def _near_name(ctx: _Ctx, x: float, y: float, reach_m: float = 8.0) -> str:
    best = None
    for text, lx, ly in ctx.labels:
        d = math.hypot(lx - x, ly - y)
        if d <= ctx.m(reach_m) and (best is None or d < best[0]):
            best = (d, text)
    return _room(best[1]) if best else ""


def _sides(ctx: _Ctx, s: Seg, reach_m: float = 8.0) -> tuple[str, str]:
    """벽 양쪽에 가장 가까운 이름표 — 「17번과 18번 매장 사이 벽」을 GPT가 고를 수 있게.

    2026-11-09 라이브: 이게 없어 「17번이랑 18번 사이 벽」에 17번 앞벽(가로)이 골라졌다."""
    length = _length(s)
    if length == 0:
        return "", ""
    nx, ny = -(s[3] - s[1]) / length, (s[2] - s[0]) / length
    mx, my = (s[0] + s[2]) / 2, (s[1] + s[3]) / 2
    best: dict[int, tuple[float, str]] = {}
    for text, lx, ly in ctx.labels:
        t = _project_t(s, lx, ly)
        if t < -0.3 or t > 1.3:
            continue  # 벽 끝을 한참 벗어난 이름은 그 벽의 「양쪽」이 아니다
        d = _dist(s, lx, ly)
        if d > ctx.m(reach_m):
            continue
        side = 1 if (lx - mx) * nx + (ly - my) * ny >= 0 else -1
        if side not in best or d < best[side][0]:
            best[side] = (d, _room(text))
    return best.get(1, (0, ""))[1], best.get(-1, (0, ""))[1]


def _and(word: str) -> str:
    """받침 있으면 「과」, 없으면 「와」 — 「17번 매장과」「통로와」."""
    last = word.strip()[-1:] if word.strip() else ""
    if "가" <= last <= "힣":
        return "와" if (ord(last) - 0xAC00) % 28 == 0 else "과"
    return "와"


def _where(ctx: _Ctx, s: Seg) -> str:
    """「17번 매장과 18번 매장 사이 세로 벽」「17번 매장 근처 세로 벽」처럼 사람이 알아볼 이름."""
    a, b = sorted(_sides(ctx, s), key=lambda n: (len(n), n))
    if a and b and a != b:
        return f"{a}{_and(a)} {b} 사이 {_orientation(s)} 벽"
    mx, my = (s[0] + s[2]) / 2, (s[1] + s[3]) / 2
    name = _near_name(ctx, mx, my)
    return f"{name} 근처 {_orientation(s)} 벽" if name else f"고른 자리 {_orientation(s)} 벽"


def _fmt_m(units: float, ctx: _Ctx) -> str:
    return f"{units / ctx.unit_per_m:.1f}m"


# --- 명령 → 선 ---------------------------------------------------------------


def _overlapping(ctx: _Ctx, index: int) -> set[int]:
    """같은 자리에 겹쳐 그린 벽 — 하나만 지우면 다른 선이 그대로 막는다(프론트 지우개와 같은 규칙)."""
    picked = ctx.lines[index]
    tol = ctx.m(0.3)
    a0 = math.atan2(picked[3] - picked[1], picked[2] - picked[0])
    out = {index}
    for i, s in enumerate(ctx.lines):
        if i == index:
            continue
        a = math.atan2(s[3] - s[1], s[2] - s[0])
        if abs(math.sin(a - a0)) >= math.sin(math.radians(5)):
            continue
        m1 = ((s[0] + s[2]) / 2, (s[1] + s[3]) / 2)
        m2 = ((picked[0] + picked[2]) / 2, (picked[1] + picked[3]) / 2)
        if _dist(picked, *m1) <= tol or _dist(s, *m2) <= tol:
            out.add(i)
    return out


def _snap_end(ctx: _Ctx, x: float, y: float, removed: set[int]) -> tuple[float, float]:
    """벽 끝점에 먼저, 없으면 벽 선 위에 붙인다 — 새 벽이 안 닿으면 바깥이 샌다."""
    best, best_d = None, ctx.m(1.0)
    for i, s in enumerate(ctx.lines):
        if i in removed:
            continue
        for ex, ey in ((s[0], s[1]), (s[2], s[3])):
            d = math.hypot(ex - x, ey - y)
            if d <= best_d:
                best, best_d = (ex, ey), d
    if best:
        return best
    best_d = ctx.m(1.0)
    for i, s in enumerate(ctx.lines):
        if i in removed:
            continue
        d = _dist(s, x, y)
        if d <= best_d:
            best, best_d = _at(s, max(0.0, min(1.0, _project_t(s, x, y)))), d
    return best or (x, y)


def _extend_to_walls(ctx: _Ctx, seg: Seg, removed: set[int]) -> Seg:
    """새 벽 양 끝을 바깥쪽으로 가까운 벽(3m 안)까지 늘린다 — 통로를 막는 벽이 양옆 벽에 닿게."""
    x1, y1, x2, y2 = seg
    length = math.hypot(x2 - x1, y2 - y1)
    if length == 0:
        return seg
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    reach = ctx.m(3.0)

    def stretch(ox: float, oy: float, dx: float, dy: float) -> tuple[float, float]:
        best = None
        for i, s in enumerate(ctx.lines):
            if i in removed:
                continue
            u = _ray_hit(ox, oy, dx, dy, s)
            if u is not None and u <= reach and (best is None or u < best):
                best = u
        return (ox + dx * best, oy + dy * best) if best is not None else (ox, oy)

    nx1, ny1 = stretch(x1, y1, -ux, -uy)
    nx2, ny2 = stretch(x2, y2, ux, uy)
    return nx1, ny1, nx2, ny2


def _ortho(seg: Seg) -> Seg:
    x1, y1, x2, y2 = seg
    angle = abs(math.degrees(math.atan2(y2 - y1, x2 - x1))) % 180
    if min(angle, 180 - angle) <= 6:
        return x1, y1, x2, y1
    if abs(angle - 90) <= 6:
        return x1, y1, x1, y2
    return seg


def _across(ctx: _Ctx, removed: set[int]) -> Seg | None:
    """고른 자리를 가로질러 통로를 막는 벽 — 양옆 벽까지.

    가로·세로 중 **벽 사이가 더 좁은 쪽**으로 세운다. 네모 모양으로 정하면 통로를 길게 고른 사람과
    통로를 가로질러 끈 사람에게 반대 방향 벽이 섰다."""
    sel = ctx.sel
    reach = ctx.m(REACH_M)

    def hit(dx: float, dy: float) -> float | None:
        best = None
        for i, s in enumerate(ctx.lines):
            if i in removed:
                continue
            u = _ray_hit(sel.x, sel.y, dx, dy, s)
            if u is not None and u <= reach and (best is None or u < best):
                best = u
        return best

    options = []
    for dx, dy in ((1.0, 0.0), (0.0, 1.0)):
        a, b = hit(dx, dy), hit(-dx, -dy)
        if a is not None and b is not None:
            options.append((a + b, (sel.x - dx * b, sel.y - dy * b, sel.x + dx * a, sel.y + dy * a)))
    if not options:
        return None
    return min(options)[1]


def _point(value: Any) -> tuple[float, float] | None:
    if isinstance(value, dict):
        value = [value.get("x"), value.get("y")]
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            x, y = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
        if math.isfinite(x) and math.isfinite(y):
            return x, y
    return None


def _apply_ops(ctx: _Ctx, ops: list[dict[str, Any]], plan: EditPlan) -> None:
    removed_lines: set[int] = set()
    removed_openings: set[int] = set()
    sel = ctx.sel

    for op in ops[:MAX_OPS]:
        kind = str(op.get("op", "")).strip()
        if kind == "add_door":
            idx = ctx.wall_ids.get(str(op.get("wall", "")).strip())
            if idx is None or idx in removed_lines:
                plan.warnings.append("문을 낼 벽을 고른 자리 근처에서 찾지 못했습니다.")
                continue
            wall = ctx.lines[idx]
            length = _length(wall)
            if length < ctx.m(0.5):
                plan.warnings.append("벽이 너무 짧아 문을 낼 수 없습니다.")
                continue
            try:
                width_m = float(op.get("width_m") or DEFAULT_DOOR_M)
            except (TypeError, ValueError):
                width_m = DEFAULT_DOOR_M
            width_m = max(MIN_DOOR_M, min(MAX_DOOR_M, width_m))
            width = ctx.m(width_m)
            if width >= length * 0.95:
                width = length * 0.8
                plan.warnings.append(f"벽이 짧아 문 폭을 {_fmt_m(width, ctx)}로 줄였습니다.")
            at = op.get("at", "selection")
            if isinstance(at, (int, float)) and not isinstance(at, bool):
                center_t = float(at)
            else:
                center_t = _project_t(wall, sel.x, sel.y)
            half = (width / length) / 2 if length else 0.5
            center_t = max(half, min(1 - half, center_t))
            ax, ay = _at(wall, center_t - half)
            bx, by = _at(wall, center_t + half)
            plan.add_openings.append((ax, ay, bx, by))
            plan.steps.append(("add_door", f"{_where(ctx, wall)}에 폭 {_fmt_m(width, ctx)} 문을 냅니다"))
        elif kind == "remove_wall":
            idx = ctx.wall_ids.get(str(op.get("wall", "")).strip())
            if idx is None or idx in removed_lines:
                plan.warnings.append("지울 벽을 고른 자리 근처에서 찾지 못했습니다.")
                continue
            doomed = _overlapping(ctx, idx) - removed_lines
            removed_lines |= doomed
            extra = f"(겹쳐 그린 선 {len(doomed) - 1}개 포함)" if len(doomed) > 1 else ""
            plan.steps.append(("remove_wall", f"{_where(ctx, ctx.lines[idx])}을 지웁니다{extra}"))
        elif kind == "close_door":
            idx = ctx.door_ids.get(str(op.get("door", "")).strip())
            if idx is None or idx in removed_openings:
                plan.warnings.append("막을 문을 고른 자리 근처에서 찾지 못했습니다.")
                continue
            removed_openings.add(idx)
            s = ctx.openings[idx]
            name = _near_name(ctx, (s[0] + s[2]) / 2, (s[1] + s[3]) / 2)
            plan.steps.append(("close_door", f"{name + ' 근처 ' if name else ''}문(폭 {_fmt_m(_length(s), ctx)})을 막습니다"))
        elif kind == "clear_walls_in_box":
            if not sel.box:
                plan.warnings.append("「네모 안 벽 모두 지우기」는 도면을 끌어 네모로 골랐을 때만 됩니다.")
                continue
            count = 0
            for i, s in enumerate(ctx.lines):
                if i in removed_lines:
                    continue
                span = _clip(s, sel.box)
                if span is None or (span[1] - span[0]) * _length(s) < 1e-9:
                    continue
                removed_lines.add(i)
                count += 1
                # 네모 밖으로 나간 부분은 남긴다 — 매장 벽 전체가 아니라 고른 자리만 튼다.
                for t0, t1 in ((0.0, span[0]), (span[1], 1.0)):
                    if (t1 - t0) * _length(s) > ctx.m(0.2):
                        plan.add_lines.append((*_at(s, t0), *_at(s, t1)))
            if count:
                plan.steps.append(("clear_walls_in_box", f"고른 네모 안의 벽 {count}개를 지웁니다"))
            else:
                plan.warnings.append("고른 네모 안에 지울 벽이 없습니다.")
        elif kind == "add_wall":
            across = bool(op.get("across") or op.get("across_box"))
            if across:
                seg_or_none = _across(ctx, removed_lines)
                if seg_or_none is None:
                    plan.warnings.append("고른 자리 양옆에서 벽을 찾지 못해 가로지르는 벽을 세우지 못했습니다.")
                    continue
                seg: Seg = seg_or_none
            else:
                a, b = _point(op.get("from")), _point(op.get("to"))
                if not a or not b:
                    plan.warnings.append("새 벽의 양 끝을 알아듣지 못했습니다.")
                    continue
                seg = (a[0], a[1], b[0], b[1])
            # 고른 자리에서 먼 좌표는 받지 않는다 — GPT가 엉뚱한 곳에 벽을 세우는 걸 막는다.
            limit = ctx.m(REACH_M * 1.5)
            if across:
                ok = True  # 양옆 벽까지 — _across가 이미 REACH_M 안으로 묶었다.
            elif sel.box:
                box = _expand(sel.box, ctx.m(3.0))
                ok = all(box[0] <= px <= box[2] and box[1] <= py <= box[3] for px, py in ((seg[0], seg[1]), (seg[2], seg[3])))
            else:
                ok = all(math.hypot(px - sel.x, py - sel.y) <= limit for px, py in ((seg[0], seg[1]), (seg[2], seg[3])))
            if not ok:
                plan.warnings.append("고른 자리에서 너무 먼 곳에는 벽을 세우지 않았습니다.")
                continue
            if not across:
                seg = _ortho(seg)
                seg = _extend_to_walls(ctx, seg, removed_lines)
                seg = (*_snap_end(ctx, seg[0], seg[1], removed_lines), *_snap_end(ctx, seg[2], seg[3], removed_lines))
            if _length(seg) < ctx.m(0.3):
                plan.warnings.append("새 벽이 너무 짧습니다.")
                continue
            plan.add_lines.append(seg)
            name = _near_name(ctx, (seg[0] + seg[2]) / 2, (seg[1] + seg[3]) / 2)
            plan.steps.append(("add_wall", f"{name + ' 근처에 ' if name else ''}길이 {_fmt_m(_length(seg), ctx)} 벽을 세웁니다"))
        else:
            plan.warnings.append(f"알 수 없는 명령은 건너뛰었습니다: {kind or '(빈 명령)'}")

    plan.remove_lines = sorted(removed_lines)
    plan.remove_openings = sorted(removed_openings)


# --- 낱말 규칙 (GPT가 없을 때) ------------------------------------------------

_WIDTH = re.compile(r"(\d+(?:\.\d+)?)\s*(?:m|M|미터)")
_CLOSE_DOOR = re.compile(r"문.*(막|닫|메워|메꿔|없애|지워|폐쇄)|(막|닫|폐쇄).*문")
# 「미터」「센터」의 「터」는 「벽 터 줘」가 아니다 — 「아니 2미터로」가 벽 지우기로 잡혔다.
_REMOVE = re.compile(r"(?<![미센])터|허물|철거|없애|지워|지우|헐|틔|트자|트고|삭제")
_ADD_DOOR = re.compile(r"문|출입|입구|뚫|통로")
_ADD_WALL = re.compile(r"(벽|칸막이|가벽).*(세|만들|쌓|추가|생기|설치|넣|막)|칸막이|가벽|막아|막자|막기|차단|나눠|분리")


_INTERNAL_ID = re.compile(r"(?<![A-Za-z0-9])[wd]\d+(?![0-9])")
_OPEN_OPS = {"add_door", "remove_wall", "clear_walls_in_box"}
_SHUT_OPS = {"close_door", "add_wall"}


def _intent(text: str) -> str | None:
    """지시가 「여는」 말인지 「막는」 말인지 — GPT가 거꾸로 알아들었는지 보는 데만 쓴다."""
    if _CLOSE_DOOR.search(text):
        return "shut"
    if _REMOVE.search(text) or re.search(r"문|출입|입구|뚫|통로", text):
        return "open"
    if _ADD_WALL.search(text):
        return "shut"
    return None


def _contradicts(instruction: str, previous: str | None, ops: list[dict[str, Any]]) -> bool:
    """여는 말에 막는 명령만(또는 그 반대) 돌아오면 GPT가 거꾸로 읽은 것이다 — 규칙으로 넘긴다.

    2026-11-09 라이브: 「17번이랑 18번 매장 사이 벽 뚫어서 통로 내줘」에 「기존 문 d2를 메우는」 제안이 나왔다."""
    want = _intent(instruction) or (_intent(previous) if previous else None)
    kinds = {str(op.get("op", "")) for op in ops}
    if want == "open" and kinds and kinds <= _SHUT_OPS:
        logger.info("말로 고치기: 여는 지시에 막는 명령만 — GPT 답을 버림 (%s)", kinds)
        return True
    if want == "shut" and kinds and kinds <= _OPEN_OPS:
        logger.info("말로 고치기: 막는 지시에 여는 명령만 — GPT 답을 버림 (%s)", kinds)
        return True
    return False


def _nearest(ids: dict[str, int], items: list[Seg], sel: Selection) -> str | None:
    if not ids:
        return None
    return min(ids, key=lambda k: _dist(items[ids[k]], sel.x, sel.y))


def rule_ops(instruction: str, ctx: _Ctx) -> tuple[list[dict[str, Any]], str | None]:
    text = instruction.strip()
    sel = ctx.sel
    width = _WIDTH.search(text)
    if _CLOSE_DOOR.search(text):
        door = _nearest(ctx.door_ids, ctx.openings, sel)
        if not door:
            return [], "고른 자리 근처에 막을 문이 없습니다. 문(초록 점선) 가까이를 눌러 주세요."
        return [{"op": "close_door", "door": door}], None
    if _REMOVE.search(text) and not re.search(r"문|출입|입구", text):
        if sel.box:
            return [{"op": "clear_walls_in_box"}], None
        wall = _nearest(ctx.wall_ids, ctx.lines, sel)
        if not wall:
            return [], "고른 자리 근처에 지울 벽이 없습니다."
        return [{"op": "remove_wall", "wall": wall}], None
    if _ADD_DOOR.search(text):
        wall = _nearest(ctx.wall_ids, ctx.lines, sel)
        if not wall:
            return [], "고른 자리 근처에 문을 낼 벽이 없습니다. 벽 가까이를 눌러 주세요."
        op: dict[str, Any] = {"op": "add_door", "wall": wall, "at": "selection"}
        if width:
            op["width_m"] = float(width.group(1))
        return [op], None
    if _ADD_WALL.search(text):
        return [{"op": "add_wall", "across": True}], None
    return [], "무엇을 할지 알아듣지 못했어요. 예: 「여기 문 내줘」, 「이 벽 없애 줘」, 「이 문 막아 줘」."


# --- GPT -----------------------------------------------------------------------

SYSTEM_PROMPT = """너는 건축 평면도 편집 도우미다. 사용자가 도면에서 고른 자리와 한국어 지시를 받아 편집 명령 JSON만 낸다.
쓸 수 있는 명령은 다섯 가지뿐이다:
- {"op":"add_door","wall":"w3","at":"selection","width_m":3}  벽에 문(통로)을 낸다. at은 "selection"(고른 자리에 가장 가까운 곳) 또는 0~1(벽 시작~끝 비율). width_m 기본 3.
- {"op":"remove_wall","wall":"w3"}  벽 하나를 통째로 지운다.
- {"op":"close_door","door":"d1"}  문을 막는다(벽으로 메운다).
- {"op":"add_wall","from":[x,y],"to":[x,y]}  새 벽을 세운다. 좌표는 도면 좌표이고 고른 자리 근처여야 한다. 통로를 막으라는 뜻이면 좌표 대신 {"op":"add_wall","across":true} — 고른 자리를 가로질러 양옆 벽까지 세운다.
- {"op":"clear_walls_in_box"}  고른 네모 안에 든 벽을 모두 지운다(네모 밖 부분은 남는다). 네모로 골랐을 때만.
규칙:
- 벽과 문은 아래 목록의 번호(w1, d1 …)로만 가리킨다. 목록에 없는 번호를 만들지 않는다.
- 「여기」「이 벽」은 고른 자리에 가장 가까운 것이다(목록은 가까운 순).
- 「터 줘」「틔워 줘」는 벽 지우기, 「뚫어 줘」「통로 내 줘」는 문 내기다. 여는 말에 문 막기(close_door)나 벽 세우기를 내지 않는다.
- 「A와 B 사이 벽」은 벽 목록에서 「양쪽: A | B」인 벽이다. 그런 벽이 있으면 고른 자리에 더 가까운 다른 벽보다 그 벽을 고른다.
- 무엇을 할지 정말 애매할 때만 ops를 비우고 question에 짧게 되묻는다.
- message는 무엇을 바꿀지 제안하고 적용할지 묻는 한두 문장(해요체) — 예: 「17번과 18번 매장 사이 벽에 3m 통로를 낼게요. 적용할까요?」. 사용자가 「응」이라고 답해야 적용된다.
- 「직전 지시」가 있으면 사용자가 그 제안을 고쳐 말하는 중이다(예: 직전 「문 내줘」, 이번 「아니 2미터로」 → 같은 벽에 2m 문). 두 말을 합쳐 이해한다. 좌표·번호(w3)를 쓰지 말고 매장 이름과 방향으로 말한다.
출력 형식: {"ops":[...],"message":"...","question":null}"""


def _prompt(instruction: str, ctx: _Ctx) -> str:
    sel = ctx.sel
    rows = [f"지시: {instruction.strip()}"]
    if sel.box:
        bw, bh = sel.box[2] - sel.box[0], sel.box[3] - sel.box[1]
        rows.append(
            f"고른 자리: 네모 ({sel.box[0]:.0f},{sel.box[1]:.0f})~({sel.box[2]:.0f},{sel.box[3]:.0f}), "
            f"가로 {_fmt_m(bw, ctx)} × 세로 {_fmt_m(bh, ctx)}"
        )
    else:
        rows.append(f"고른 자리: 점 ({sel.x:.0f},{sel.y:.0f})")
    name = _near_name(ctx, sel.x, sel.y)
    if name:
        rows.append(f"고른 자리에 가장 가까운 이름: {name}")
    rows.append(f"축척: 도면 1단위 = {1 / ctx.unit_per_m:.3f}m. y가 커질수록 남쪽(아래).")

    rows.append("벽 목록(가까운 순):")
    for wid, i in ctx.wall_ids.items():
        s = ctx.lines[i]
        t = max(0.0, min(1.0, _project_t(s, sel.x, sel.y)))
        px, py = _at(s, t)
        gap = math.hypot(px - sel.x, py - sel.y)
        rel = "고른 자리 바로 위" if gap < ctx.m(0.5) else f"고른 자리에서 {_direction(px - sel.x, py - sel.y)} {_fmt_m(gap, ctx)}"
        a, b = _sides(ctx, s)
        if a and b and a != b:
            side = f", 양쪽: {a} | {b}"
        elif a or b:
            side = f", 한쪽: {a or b}"
        else:
            side = ""
        rows.append(
            f"- {wid}: {_orientation(s)} 벽, 길이 {_fmt_m(_length(s), ctx)}, {rel}{side}"
            f", 양 끝 ({s[0]:.0f},{s[1]:.0f})-({s[2]:.0f},{s[3]:.0f})"
        )
    if not ctx.wall_ids:
        rows.append("- (근처에 벽 없음)")
    rows.append("문 목록(가까운 순):")
    for did, i in ctx.door_ids.items():
        s = ctx.openings[i]
        mx, my = (s[0] + s[2]) / 2, (s[1] + s[3]) / 2
        gap = math.hypot(mx - sel.x, my - sel.y)
        rows.append(f"- {did}: 폭 {_fmt_m(_length(s), ctx)}, 고른 자리에서 {_direction(mx - sel.x, my - sel.y)} {_fmt_m(gap, ctx)}")
    if not ctx.door_ids:
        rows.append("- (근처에 문 없음)")
    near_labels = sorted(ctx.labels, key=lambda l: math.hypot(l[1] - sel.x, l[2] - sel.y))[:MAX_LABELS]
    if near_labels:
        rows.append("근처 이름표:")
        for text, lx, ly in near_labels:
            gap = math.hypot(lx - sel.x, ly - sel.y)
            rows.append(f"- {_room(text)}: 고른 자리에서 {_direction(lx - sel.x, ly - sel.y)} {_fmt_m(gap, ctx)}")
    return "\n".join(rows)


# --- 진입점 ---------------------------------------------------------------------


def _unit_per_m(meters_per_unit: float | None, canvas_w: float, canvas_h: float) -> float:
    if meters_per_unit and meters_per_unit > 0:
        return 1.0 / meters_per_unit
    # 축척을 모르면 도면 긴 변을 150m로 본다 — 대형 몰 샘플(160m) 크기.
    return max(canvas_w, canvas_h, 1.0) / 150.0


def _finish(plan: EditPlan) -> EditPlan:
    if plan.steps and not plan.message:
        plan.message = " · ".join(text for _, text in plan.steps)
    # LLM이 먼저 묻는다(2026-11-09 소유자 「LLM이 물으라고」) — 사용자가 「응」이라고 답해야 적용된다.
    if plan.steps and "?" not in plan.message:
        plan.message = plan.message.rstrip(". ") + ". 적용할까요?"
    if not plan.steps and not plan.question:
        # 명령은 알아들었지만 그 자리에 할 게 없던 경우 — 그 이유를 그대로 말한다(「네모 안에 벽이 없습니다」).
        plan.question = plan.warnings[0] if plan.warnings else "바꿀 것을 찾지 못했어요. 자리를 다시 고르거나 다르게 말해 주세요."
    return plan


async def plan_edit(
    *,
    instruction: str,
    point: tuple[float, float],
    box: Seg | None,
    lines: list[Seg],
    openings: list[Seg],
    labels: list[tuple[str, float, float]],
    meters_per_unit: float | None,
    canvas_width: float,
    canvas_height: float,
    previous_instruction: str | None = None,
    use_llm: bool = True,
) -> EditPlan:
    norm_box = None
    if box:
        x1, y1, x2, y2 = box
        norm_box = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        # 너무 작은 네모는 실수로 끈 것 — 점으로 본다.
        if (norm_box[2] - norm_box[0]) < 1e-6 or (norm_box[3] - norm_box[1]) < 1e-6:
            norm_box = None
    sel = Selection(x=point[0], y=point[1], box=norm_box)
    if norm_box:
        sel.x, sel.y = (norm_box[0] + norm_box[2]) / 2, (norm_box[1] + norm_box[3]) / 2
    ctx = _Ctx(
        lines=lines, openings=openings, labels=labels, sel=sel,
        unit_per_m=_unit_per_m(meters_per_unit, canvas_width, canvas_height),
        wall_ids={}, door_ids={},
    )
    _candidates(ctx)

    if use_llm:
        user = _prompt(instruction, ctx)
        if previous_instruction and previous_instruction.strip():
            user = f"직전 지시(사용자가 고쳐 말하는 중): {previous_instruction.strip()}\n" + user
        raw = await complete_json(system=SYSTEM_PROMPT, user=user, max_tokens=1500, timeout_s=15.0)
        if isinstance(raw, dict):
            ops = [op for op in (raw.get("ops") or []) if isinstance(op, dict)]
            question = raw.get("question")
            message = raw.get("message")
            if ops and not _contradicts(instruction, previous_instruction, ops):
                plan = EditPlan(source="llm")
                _apply_ops(ctx, ops, plan)
                if plan.steps:
                    msg = message.strip() if isinstance(message, str) else ""
                    # 「기존 문 d2를 메우는…」처럼 안쪽 번호가 새면 사용자가 알 수 없다 — 우리 문장으로 바꾼다.
                    if _INTERNAL_ID.search(msg):
                        msg = ""
                    plan.message = msg
                    return _finish(plan)
                logger.info("말로 고치기: GPT 명령이 모두 무효 — 규칙으로 넘어감 (%s)", plan.warnings)
            elif isinstance(question, str) and question.strip():
                return EditPlan(question=question.strip(), source="llm")

    plan = EditPlan(source="rule")
    ops, question = rule_ops(instruction, ctx)
    # 「아니 2미터로」처럼 고쳐 말한 것은 혼자서는 뜻이 없다 — 직전 지시와 합쳐 다시 본다.
    if question and previous_instruction and previous_instruction.strip():
        ops, question = rule_ops(f"{previous_instruction.strip()} {instruction}", ctx)
    if question:
        plan.question = question
        return plan
    _apply_ops(ctx, ops, plan)
    return _finish(plan)
