"""depthmapX CLI bridge for indoor floor-plan VGA.

Mirrors ``depthmapx_engine.py``'s contract for the road network: an external
depthmapXcli binary does the actual visibility-graph computation instead of
our own scipy re-implementation in ``floorplan_vga.py``. Enabled the same way
(``DEPTHMAPX_ENABLED`` + ``DEPTHMAPX_COMMAND``), so one flag switches both
engines over. Today it is off in production and serves as the reference our
own engine is checked against (``tests/test_floorplan_depthmapx_parity.py``).

depthmapX has no notion of a building envelope — VISPREP floods a grid outward
from one seed point through anything connected, exactly the bug fixed in
``floorplan_vga._exterior_mask`` (011a02f): an entrance gap fuses the corridor
to the street and the flood covers both. So the exterior-detection step is not
optional scaffolding to drop once depthmapX takes over — it runs first, and
depthmapX only ever sees the validated indoor region. Concretely: everything
that is *not* that region (real walls plus the street/margin outside the
building) becomes "wall" in the synthetic drawing handed to depthmapX.

The synthetic drawing is generated from our own rasterized wall mask rather
than the original wall geometry, drawing one line segment per exposed cell
edge (the boundary of the blocked region). This is deliberate: depthmapX's
line-of-sight test needs continuous wall boundaries, and re-deriving them from
the mask guarantees the geometry matches exactly what ``_exterior_mask`` and
``_largest_free_component`` already agreed was a wall — no risk of the
original CAD lines and the computed mask disagreeing at a doorway. The mask
comes from ``floorplan_vga._analysis_mask``, the same function our own engine
uses, so the two results line up cell for cell.

Sample grid (measured 2026-10-25 on depthmapXcli; see
``docs/SPEC_FLOORPLAN_TABS_UI.md`` 「depthmapX 대조 검증」): VISPREP puts its
points on exact multiples of the spacing in drawing coordinates, the CSV
reports those same absolute coordinates, and a point that lands on a wall line
is dropped. So the drawing is shifted by half a cell — wall edges move to
half-multiples and every cell centre lands on a grid point. The spacing is our
own cell size: an earlier finer pitch (cell/3) produced ~15k points and the
VGA step ran for over 22 hours, while at cell size the sample mall is ~1.6k
points and under a minute. Every analyzed cell is passed as a fill seed
(``-pf``) instead of flooding from one point, and no boundary graph (``-pb``):
with it depthmapX kept only the strip of points touching a wall (18% of the
floor).
"""

from __future__ import annotations

import csv
import math
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

import ezdxf
import numpy as np

from app.config import settings
from app.services.floorplan_vga import (
    MAX_GRID_CELLS,
    ExcludedCell,
    GridCell,
    VGAResult,
    WallSegment,
    _analysis_mask,
    _choose_grid,
)

# depthmapX's CSV export names these columns; global visibility integration is
# the "[HH]" (Hillier-Hanson) normalization, the same family of measure
# floorplan_vga's own NAIN-style integration is meant to approximate.
_CSV_INTEGRATION_COL = "Visual Integration [HH]"
_CSV_CONNECTIVITY_COL = "Connectivity"


class DepthmapXFloorplanError(Exception):
    pass


def _boundary_dxf_from_mask(blocked: np.ndarray, cell_size: float, shift: float, path: Path) -> None:
    """Write one wall LINE per exposed edge of the blocked mask.

    "Exposed" means the neighboring cell across that edge is open (or off the
    grid) — i.e. this is a true boundary of the wall region, not an edge
    between two wall cells. Tracing the mask itself (rather than reusing the
    original CAD line coordinates) guarantees depthmapX's geometry agrees
    exactly with what excluded the exterior, cell for cell. ``shift`` moves
    every coordinate so cell centres, not wall edges, sit on VISPREP's grid.
    """
    doc = ezdxf.new()
    doc.layers.add(name="WALL")
    msp = doc.modelspace()
    # Off the grid counts as wall. Our engine has no cells beyond the grid, so
    # floor on the grid's edge is bounded there; without this ring no line was
    # drawn on that side and VISPREP's fill escaped outside the drawing —
    # on showcase-mall 192 phantom points joined depthmapX's graph and lifted
    # every cell's mean depth (2026-10-26).
    padded = np.pad(blocked, 1, constant_values=True)
    rows, cols = padded.shape

    def is_blocked(r: int, c: int) -> bool:
        return 0 <= r < rows and 0 <= c < cols and bool(padded[r, c])

    attribs = {"layer": "WALL"}
    for r in range(rows):
        for c in range(cols):
            if not padded[r, c]:
                continue
            x0, x1 = (c - 1) * cell_size + shift, c * cell_size + shift
            y0, y1 = (r - 1) * cell_size + shift, r * cell_size + shift
            if not is_blocked(r - 1, c):
                msp.add_line((x0, y0), (x1, y0), dxfattribs=attribs)
            if not is_blocked(r + 1, c):
                msp.add_line((x0, y1), (x1, y1), dxfattribs=attribs)
            if not is_blocked(r, c - 1):
                msp.add_line((x0, y0), (x0, y1), dxfattribs=attribs)
            if not is_blocked(r, c + 1):
                msp.add_line((x1, y0), (x1, y1), dxfattribs=attribs)
    doc.saveas(str(path))


def _run(command: str, args: list[str], timeout: int) -> None:
    full_args = shlex.split(command, posix=os.name != "nt") + args
    try:
        completed = subprocess.run(
            full_args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DepthmapXFloorplanError(f"depthmapX 실행 실패: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-500:]
        raise DepthmapXFloorplanError(
            f"depthmapX가 {' '.join(args[:2])} 단계에서 {completed.returncode}로 종료: {detail}"
        )


def compute_floorplan_vga_depthmapx(
    walls: list[WallSegment],
    canvas_width: float,
    canvas_height: float,
    max_cells: int = MAX_GRID_CELLS,
    openings: list[WallSegment] | None = None,
    meters_per_unit: float | None = None,
) -> VGAResult:
    """Same contract as ``floorplan_vga.compute_floorplan_vga``, computed by
    the real depthmapX CLI instead of our scipy re-implementation.

    Same grid and same candidate cells as our engine, so the two can be
    compared cell by cell. Only integration and connectivity are filled in —
    depthmapX VGA has no counterpart of our choice measure.
    """
    command = settings.depthmapx_command.strip()
    if not command:
        raise DepthmapXFloorplanError("DEPTHMAPX_COMMAND이 설정되어 있지 않습니다")
    if not walls:
        raise DepthmapXFloorplanError("벽 선이 없어 분석할 수 없습니다")

    cols, rows, cell_size = _choose_grid(canvas_width, canvas_height, max_cells)
    mask = _analysis_mask(walls, cols, rows, cell_size, openings, meters_per_unit)
    free_mask = mask.free_mask
    if not free_mask.any():
        raise DepthmapXFloorplanError("추출된 벽 안에서 열린 공간을 찾지 못했습니다")

    shift = cell_size / 2
    timeout = settings.depthmapx_timeout_seconds
    with tempfile.TemporaryDirectory(prefix="space-syntax-floorplan-depthmapx-") as tmp:
        tmp_path = Path(tmp)
        dxf_path = tmp_path / "plan.dxf"
        seeds_path = tmp_path / "seeds.tsv"
        imported = tmp_path / "imported.graph"
        vis = tmp_path / "vis.graph"
        vga = tmp_path / "vga.graph"
        csv_path = tmp_path / "result.csv"

        # Everything depthmapX should treat as wall: real walls AND the
        # exterior we already excluded. Only the validated indoor region
        # stays open.
        _boundary_dxf_from_mask(~free_mask, cell_size, shift, dxf_path)
        with open(seeds_path, "w", encoding="utf-8") as f:
            f.write("x\ty\n")
            for r, c in np.argwhere(free_mask):
                f.write(f"{(c + 0.5) * cell_size + shift}\t{(r + 0.5) * cell_size + shift}\n")

        _run(command, ["-m", "IMPORT", "-f", str(dxf_path), "-o", str(imported)], timeout)
        _run(
            command,
            [
                "-m", "VISPREP", "-f", str(imported), "-o", str(vis),
                "-pg", repr(cell_size), "-pf", str(seeds_path), "-pm",
            ],
            timeout,
        )
        _run(
            command,
            ["-m", "VGA", "-f", str(vis), "-o", str(vga), "-vm", "visibility", "-vg", "-vr", "n"],
            timeout,
        )
        _run(
            command,
            ["-m", "EXPORT", "-f", str(vga), "-o", str(csv_path), "-em", "pointmap-data-csv"],
            timeout,
        )

        if not csv_path.is_file():
            raise DepthmapXFloorplanError("depthmapX가 결과 CSV를 만들지 않았습니다")

        cells: list[GridCell] = []
        seen = np.zeros((rows, cols), dtype=bool)
        try:
            with open(csv_path, newline="", encoding="utf-8") as f:
                for record in csv.DictReader(f):
                    # CSV coordinates are the drawing's own (absolute), so
                    # undoing ``shift`` puts each point back on its cell.
                    c = math.floor((float(record["x"]) - shift) / cell_size)
                    r = math.floor((float(record["y"]) - shift) / cell_size)
                    if not (0 <= r < rows and 0 <= c < cols) or not free_mask[r, c] or seen[r, c]:
                        continue
                    seen[r, c] = True
                    cells.append(
                        GridCell(
                            row=r,
                            col=c,
                            x=(c + 0.5) * cell_size,
                            y=(r + 0.5) * cell_size,
                            connectivity=float(record[_CSV_CONNECTIVITY_COL]),
                            integration=float(record[_CSV_INTEGRATION_COL]),
                        )
                    )
        except (KeyError, ValueError) as exc:
            raise DepthmapXFloorplanError(f"depthmapX 결과 CSV 형식이 예상과 다릅니다: {exc}") from exc

    if not cells:
        raise DepthmapXFloorplanError("depthmapX가 분석 가능한 셀을 반환하지 않았습니다")

    free_total = int(mask.indoor.sum())
    coverage_pct = 100.0 * len(cells) / max(free_total, 1)
    warnings: list[str] = []
    if coverage_pct < 70.0:
        warnings.append(
            f"도면에서 뚫린 공간의 {coverage_pct:.0f}%만 서로 이어져 있어 그 부분만 살펴봤습니다. "
            "문이 벽으로 막혀 있을 가능성이 높습니다 — 빗금 친 곳의 경계에서 실제 문이 있는 자리를 "
            "'문 자리 표시'로 찍고 다시 분석하면 전체를 함께 볼 수 있습니다."
        )

    excluded_cells = [
        ExcludedCell(row=int(r), col=int(c))
        for r, c in np.argwhere(mask.indoor & ~seen)
    ]

    integrations = [c.integration for c in cells]
    connectivities = [c.connectivity for c in cells]
    return VGAResult(
        cols=cols,
        rows=rows,
        cell_size=cell_size,
        cells=cells,
        integration_mean=float(np.mean(integrations)),
        integration_max=float(np.max(integrations)),
        connectivity_mean=float(np.mean(connectivities)),
        coverage_pct=coverage_pct,
        cell_size_m=cell_size * meters_per_unit if meters_per_unit else None,
        exterior_mask=mask.exterior,
        analyzed_mask=seen,
        excluded_cells=excluded_cells,
        warnings=warnings,
    )
