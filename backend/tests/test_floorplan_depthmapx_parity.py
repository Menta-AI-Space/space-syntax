"""우리 실내 VGA(`floorplan_vga`, scipy 자체 구현)를 depthmapX로 대조한다.

같은 벽 마스크·같은 칸 크기로 두 계산을 돌려 칸 대 칸으로 견준다. depthmapXcli가
있는 랩탑에서만 돈다 — 없으면 skip(CI·운영엔 없다). 찾는 순서: `DEPTHMAPX_COMMAND`
환경변수 → 설정값 → PATH의 `depthmapXcli` → `C:\\dev\\tools\\depthmapx\\depthmapXcli.exe`.

기준은 2026-10-25 실측(목 ρ 0.969 · 연결도 ρ 0.990 · 목 상위 10% 겹침 90%)에서 여유를
조금 둔 값이다. 2026-10-26 시선 판정을 고친 뒤 실내 샘플 다섯 개의 가장 낮은 값: 목 ρ 0.980,
연결도 ρ 0.990, 상위 10% 겹침 84%(wtc-mall). 범위는 벽을 읽은 **뒤의** 계산이다 — DXF에서 벽·문을 제대로 읽었는지는
다른 테스트가 맡는다. 선택도는 depthmapX VGA에 같은 지표가 없어 대조하지 않는다.
"""

import os
import shutil
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import spearmanr

from app.config import settings
from app.services.floorplan_extraction import extract_from_dxf
from app.services.floorplan_vga import WallSegment, compute_floorplan_vga

SHOWCASE = Path(__file__).resolve().parents[2] / "frontend-next" / "public" / "samples" / "showcase-mall.dxf"
_LAPTOP_DEFAULT = Path(r"C:\dev\tools\depthmapx\depthmapXcli.exe")


def _find_depthmapx() -> str | None:
    for candidate in (os.environ.get("DEPTHMAPX_COMMAND", ""), settings.depthmapx_command):
        candidate = candidate.strip()
        if candidate and (Path(candidate.strip('"')).is_file() or shutil.which(candidate)):
            return candidate
    found = shutil.which("depthmapXcli")
    if found:
        return found
    return str(_LAPTOP_DEFAULT) if _LAPTOP_DEFAULT.is_file() else None


DEPTHMAPX = _find_depthmapx()
pytestmark = pytest.mark.skipif(DEPTHMAPX is None, reason="depthmapXcli가 없는 환경")


# 계획도면(planned-site-sample)은 실내 도면이 아니라 뺀다.
SAMPLES = ["showcase-mall.dxf", "complex-mall.dxf", "synthetic-large-mall.dxf", "synthetic-mall-labeled.dxf", "wtc-mall.dxf"]


@pytest.fixture(scope="module", params=SAMPLES)
def paired(request):
    from app.services import floorplan_depthmapx_engine as dx

    extracted = extract_from_dxf((SHOWCASE.parent / request.param).read_bytes())
    walls = [WallSegment(l.x1, l.y1, l.x2, l.y2) for l in extracted.lines]
    openings = [WallSegment(l.x1, l.y1, l.x2, l.y2) for l in extracted.openings]
    args = (walls, extracted.canvas_width, extracted.canvas_height)
    kwargs = dict(openings=openings, meters_per_unit=extracted.meters_per_unit)

    ours = compute_floorplan_vga(*args, **kwargs)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(dx.settings, "depthmapx_command", DEPTHMAPX)
        mp.setattr(dx.settings, "depthmapx_timeout_seconds", 600)
        ref = dx.compute_floorplan_vga_depthmapx(*args, **kwargs)
    return ours, ref


def test_bridge_covers_the_whole_floor_on_our_grid(paired):
    """옛 브리지는 `-pb` 때문에 벽에 붙은 띠(바닥의 18%)만, 좌표를 두 번 빼서 옆 칸에
    결과를 얹었다. 이제 같은 격자에서 우리가 계산한 칸 전부가 짝지어져야 한다."""
    ours, ref = paired
    assert (ref.rows, ref.cols, ref.cell_size) == (ours.rows, ours.cols, ours.cell_size)
    ours_keys = {(c.row, c.col) for c in ours.cells}
    ref_keys = {(c.row, c.col) for c in ref.cells}
    assert ref_keys <= ours_keys
    assert len(ref_keys) >= 0.99 * len(ours_keys)


def _pairs(ours, ref):
    by_ref = {(c.row, c.col): c for c in ref.cells}
    both = [(c, by_ref[(c.row, c.col)]) for c in ours.cells if (c.row, c.col) in by_ref]
    a_int = np.array([o.integration for o, _ in both])
    b_int = np.array([r.integration for _, r in both])
    a_con = np.array([o.connectivity for o, _ in both])
    b_con = np.array([r.connectivity for _, r in both])
    return a_int, b_int, a_con, b_con


def test_integration_ranks_match_depthmapx(paired):
    a_int, b_int, _, _ = _pairs(*paired)
    assert spearmanr(a_int, b_int).statistic >= 0.95


def test_connectivity_ranks_match_depthmapx(paired):
    _, _, a_con, b_con = _pairs(*paired)
    assert spearmanr(a_con, b_con).statistic >= 0.98


def test_top_decile_of_integration_overlaps(paired):
    """화면이 「가장 목 좋은 곳」으로 짚는 건 상위권이다 — 순위 상관보다 이쪽이 직접적이다."""
    a_int, b_int, _, _ = _pairs(*paired)
    k = max(1, len(a_int) // 10)
    top_ours = set(np.argsort(-a_int)[:k])
    top_ref = set(np.argsort(-b_int)[:k])
    assert len(top_ours & top_ref) / k >= 0.80
