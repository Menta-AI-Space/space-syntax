"""Compare two versions of the same plan — "바꾸면 무엇이 좋아지고 무엇이 나빠지나".

The spec's tab 6 (A/B 비교) says the answer must not collapse into one score
(결정 3): a change that opens up a dead corner usually costs something else,
and hiding that behind a single number is how a tool talks people into bad
renovations. So this returns a short list of named deltas, each with its own
direction, plus the two issue lists so the screen can show what appeared and
what went away.

Both sides are ordinary ``VGAResult``s — the caller runs ``compute_floorplan_vga``
twice (original walls, edited walls) and hands them here. Nothing is recomputed.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.floorplan_issues import Issue
from app.services.floorplan_vga import VGAResult

# 이 비율 미만의 변화는 격자 잡음으로 보고 "차이 없음"이라고 말한다. 격자가
# 재생성되면 칸 경계가 조금씩 달라져 지표가 소수점 아래에서 늘 흔들린다.
NOISE_PCT = 2.0
# 두 판의 문제를 같은 문제로 볼 최대 거리(칸). 넓이가 조금 달라지면 제목도
# 달라지므로("약 537㎡" → "약 471㎡") 제목이 아니라 자리로 짝짓는다.
SAME_ISSUE_CELLS = 8.0


@dataclass
class Delta:
    key: str
    label: str
    before: float
    after: float
    # 좋아졌나 나빠졌나 — 지표마다 방향이 다르다(걸음 수는 낮을수록 좋다).
    direction: str  # better | worse | same
    unit: str = ""
    note: str = ""

    @property
    def change_pct(self) -> float:
        if self.before == 0:
            return 0.0
        return 100.0 * (self.after - self.before) / abs(self.before)


# 건물 전체를 두고 하는 말이라 마커 자리가 옮겨다니는 종류. 이것들은 거리를
# 보지 않고 종류만 같으면 같은 문제로 본다 — 안 그러면 길목이 조금 이동한 것만으로
# "없어진 문제 1개 / 새로 생긴 문제 1개"에 같은 문장이 동시에 뜬다.
# 「가장 목이 좋은 곳」(best)은 문제가 아니라 안내라서, 구역이 조금 옮겨도 같은 것으로 본다.
_PLACELESS = {"coverage", "best", "spread"}


def _category(issue: Issue) -> str:
    """제목에서 문제 종류를 뽑는다 — 짝짓기는 종류가 같을 때만 한다."""
    for needle, key in (("빠졌습니다", "coverage"), ("외진", "dead"), ("시야", "blind"),
                        ("동선이 몰립니다", "choke"), ("가장 목이 좋은 곳", "best"),
                        ("차이가 큽니다", "spread")):
        if needle in issue.title:
            return key
    return "other"


def _match_issues(
    before: list[Issue], after: list[Issue], cell_size: float
) -> tuple[list[Issue], list[Issue]]:
    """(새로 생긴 것, 없어진 것). 같은 종류가 가까운 자리에 있으면 같은 문제로
    본다 — 구역이 조금 줄어든 것을 "없어짐 + 새로 생김"으로 두 번 세지 않는다."""
    reach = SAME_ISSUE_CELLS * cell_size
    unmatched_before = list(before)
    appeared: list[Issue] = []
    for issue in after:
        mate = None
        category = _category(issue)
        for candidate in unmatched_before:
            if _category(candidate) != category:
                continue
            if category in _PLACELESS:
                mate = candidate
                break
            if ((candidate.x - issue.x) ** 2 + (candidate.y - issue.y) ** 2) ** 0.5 <= reach:
                mate = candidate
                break
        if mate is None:
            appeared.append(issue)
        else:
            unmatched_before.remove(mate)
    return appeared, unmatched_before


def _dead_area(result: VGAResult, issues: list[Issue]) -> float:
    """외진·시야 막힘으로 지목된 넓이 합. 축척이 없으면 칸 수를 쓴다."""
    return float(sum(i.area_m2 or i.cell_count for i in issues if "외진" in i.title or "시야" in i.title))


def _worst_zone(issues: list[Issue]) -> float:
    """가장 넓은 외진 구역 하나의 넓이 — 「그중 가장 외진 구석」."""
    return float(max((i.area_m2 or i.cell_count for i in issues if "외진" in i.title), default=0.0))


def _delta(key: str, label: str, before: float, after: float, *, higher_is_better: bool, unit: str = "", note: str = "") -> Delta:
    if before == 0 and after == 0:
        direction = "same"
    else:
        change = 100.0 * (after - before) / abs(before) if before else (100.0 if after else 0.0)
        if abs(change) < NOISE_PCT:
            direction = "same"
        elif (change > 0) == higher_is_better:
            direction = "better"
        else:
            direction = "worse"
    return Delta(key=key, label=label, before=before, after=after, direction=direction, unit=unit, note=note)


def compare_plans(
    before: VGAResult,
    after: VGAResult,
    before_issues: list[Issue],
    after_issues: list[Issue],
) -> tuple[list[Delta], list[Issue], list[Issue]]:
    """Return (deltas, 새로 생긴 문제, 사라진 문제)."""
    unit = "㎡" if before.cell_size_m and after.cell_size_m else "칸"
    deltas = [
        _delta(
            "integration_mean", "건물 어디서든 찾아가기 쉬운 정도",
            before.integration_mean, after.integration_mean,
            higher_is_better=True,
            note="평균이 오르면 어디서 출발하든 닿기 쉬워졌다는 뜻입니다.",
        ),
        _delta(
            "mean_depth", "어디든 가는 데 꺾는 횟수",
            before.mean_depth_mean, after.mean_depth_mean,
            higher_is_better=False, unit="번",
            note="한 자리에서 다른 자리까지 평균 몇 번 꺾어 가는지입니다. 낮을수록 좋습니다.",
        ),
        _delta(
            "dead_area", "손님 발길이 잘 안 닿는 넓이",
            _dead_area(before, before_issues), _dead_area(after, after_issues),
            higher_is_better=False, unit=unit,
            note="찾아가기 먼 외진 구석과 벽에 가려 잘 안 보이는 구석을 합한 넓이입니다.",
        ),
        _delta(
            "worst_zone", "그중 가장 외진 구석",
            _worst_zone(before_issues), _worst_zone(after_issues),
            higher_is_better=False, unit=unit,
            note="외진 구석 가운데 가장 넓은 한 덩어리의 넓이입니다.",
        ),
        _delta(
            "coverage", "계산에 들어간 바닥",
            before.coverage_pct, after.coverage_pct,
            higher_is_better=True, unit="%",
            note="벽에 막혀 계산에서 빠진 공간이 줄면 올라갑니다.",
        ),
        _delta(
            "connectivity_mean", "한 자리에서 보이는 범위",
            before.connectivity_mean, after.connectivity_mean,
            higher_is_better=True,
            note="넓어지면 길을 찾기 쉬워지지만, 매장 안이 통로에서 다 보이는 문제도 함께 옵니다.",
        ),
        _delta(
            "choice_max", "가장 붐비는 길목에 몰리는 정도",
            before.choice_max, after.choice_max,
            higher_is_better=False,
            note="한 곳에 동선이 더 몰리면 올라갑니다. 붐빔은 나쁘지만 그 자리의 매장 가치는 올라갑니다.",
        ),
    ]

    appeared, resolved = _match_issues(before_issues, after_issues, after.cell_size)
    return deltas, appeared, resolved


def summarize_comparison(deltas: list[Delta], appeared: list[Issue], resolved: list[Issue]) -> str:
    """One paragraph that refuses to declare a winner — it names both sides."""
    better = [d for d in deltas if d.direction == "better"]
    worse = [d for d in deltas if d.direction == "worse"]
    if not better and not worse:
        return "바꾼 결과가 지표로는 거의 같습니다. 격자 오차 범위 안이라 좋아졌다고 보기 어렵습니다."

    parts: list[str] = []
    dead = next((d for d in deltas if d.key == "dead_area"), None)
    if dead and dead.direction != "same":
        verb = "줄었습니다" if dead.after < dead.before else "늘었습니다"
        parts.append(
            f"손님 발길이 잘 안 닿는 넓이가 {dead.before:,.0f}{dead.unit} → {dead.after:,.0f}{dead.unit}로 "
            f"{abs(dead.change_pct):.0f}% {verb}"
        )
    if better:
        parts.append("좋아진 것: " + ", ".join(f"{d.label} {abs(d.change_pct):.0f}%" for d in better))
    if worse:
        parts.append("나빠진 것: " + ", ".join(f"{d.label} {abs(d.change_pct):.0f}%" for d in worse))
    if resolved:
        parts.append(f"없어진 구석 {len(resolved)}곳 (예: {resolved[0].title})")
    if appeared:
        parts.append(f"새로 생긴 구석 {len(appeared)}곳 (예: {appeared[0].title})")
    parts.append("어느 쪽이 낫다고 한 줄로 말하지 않습니다 — 얻는 것과 잃는 것을 위 표에서 함께 보세요.")
    return " / ".join(parts)
