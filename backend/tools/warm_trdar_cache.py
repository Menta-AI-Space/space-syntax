"""Warm the persistent /api/analyze cache at every 상권 (trade area) centre.

Unlike tools/precompute_analysis_cache.py (HTTP grid sweep), this runs the
analyze routine IN-PROCESS so it does not depend on — or restart with — the
dev server, and executes sequentially to stay polite to a live server sharing
this machine's CPU.

Points: trdar_zone centroids snapped to the shared analysis grid (deduped).
Radii order: all 500m first (default click radius), then 1000m.

Usage:
    .venv/Scripts/python.exe -B tools/warm_trdar_cache.py [--radii 500 1000] [--limit 0]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import psycopg2  # noqa: E402

from app.config import settings  # noqa: E402
from app.models.schemas import AnalyzeRequest  # noqa: E402
from app.routers.analyze import analyze  # noqa: E402
from app.services.analysis_cache import snap_to_analysis_grid  # noqa: E402


def trdar_grid_points() -> list[tuple[float, float]]:
    """상권 centroids → snapped analysis-grid points, deduped, stable order."""
    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    cur.execute(
        "SELECT ST_X(ST_Centroid(geom)), ST_Y(ST_Centroid(geom)) FROM trdar_zone"
    )
    seen: set[tuple[float, float]] = set()
    points: list[tuple[float, float]] = []
    for lng, lat in cur.fetchall():
        snapped = snap_to_analysis_grid(float(lng), float(lat))
        if snapped not in seen:
            seen.add(snapped)
            points.append(snapped)
    conn.close()
    return points


async def warm(points: list[tuple[float, float]], radii: list[int]) -> None:
    total = len(points) * len(radii)
    done = hits = computed = failed = 0
    t_start = time.monotonic()
    for radius in radii:
        for lng, lat in points:
            done += 1
            try:
                t0 = time.monotonic()
                resp = await analyze(
                    AnalyzeRequest(lat=lat, lng=lng, radius_m=radius, mode="walk")
                )
                dt = time.monotonic() - t0
                if resp.engines["cache"].get("hit"):
                    hits += 1
                else:
                    computed += 1
                    print(
                        f"[{done}/{total}] r{radius} ({lng},{lat}) computed {dt:.1f}s",
                        flush=True,
                    )
            except Exception as exc:  # no roads in radius, engine failure, ...
                failed += 1
                print(f"[{done}/{total}] r{radius} ({lng},{lat}) FAIL {exc}", flush=True)
            if done % 50 == 0:
                elapsed = time.monotonic() - t_start
                print(
                    f"--- progress {done}/{total} hits={hits} computed={computed} "
                    f"failed={failed} elapsed={elapsed/60:.0f}m",
                    flush=True,
                )
    elapsed = time.monotonic() - t_start
    print(
        f"DONE {done}/{total} hits={hits} computed={computed} failed={failed} "
        f"elapsed={elapsed/60:.1f}m",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--radii", nargs="+", type=int, default=[500, 1000], choices=[500, 1000])
    parser.add_argument("--limit", type=int, default=0, help="warm only the first N points (0 = all)")
    args = parser.parse_args()

    points = trdar_grid_points()
    if args.limit > 0:
        points = points[: args.limit]
    print(f"warming {len(points)} grid points x radii {args.radii}", flush=True)
    asyncio.run(warm(points, args.radii))


if __name__ == "__main__":
    main()
