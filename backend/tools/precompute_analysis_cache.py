"""Warm the persistent /api/analyze cache over a projected point grid.

Example (coarse Seoul walk cache):
    python -m tools.precompute_analysis_cache --step-m 500 --radii 500 1000 --workers 4

Use a smaller bbox first in production. A dense full-Seoul grid is intentionally
an offline batch job because each cold point still runs the analysis engine once.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import product
from typing import Callable, Iterable

import httpx
from pyproj import Transformer

from app.services.analysis_cache import ANALYSIS_GRID_BBOX, ANALYSIS_GRID_STEP_M

# Shared with the live cache-key snap so warmed points and live keys align.
SEOUL_BBOX = ANALYSIS_GRID_BBOX


def iter_grid_points(
    bbox: tuple[float, float, float, float],
    step_m: int,
) -> Iterable[tuple[float, float]]:
    if step_m <= 0:
        raise ValueError("step_m must be positive")
    min_lng, min_lat, max_lng, max_lat = bbox
    to_meters = Transformer.from_crs(4326, 5179, always_xy=True)
    to_wgs84 = Transformer.from_crs(5179, 4326, always_xy=True)
    min_x, min_y = to_meters.transform(min_lng, min_lat)
    max_x, max_y = to_meters.transform(max_lng, max_lat)

    x = min(min_x, max_x)
    x_limit = max(min_x, max_x)
    y_start = min(min_y, max_y)
    y_limit = max(min_y, max_y)
    while x <= x_limit:
        y = y_start
        while y <= y_limit:
            lng, lat = to_wgs84.transform(x, y)
            if min_lng <= lng <= max_lng and min_lat <= lat <= max_lat:
                yield round(lng, 6), round(lat, 6)
            y += step_m
        x += step_m


def validate_warm_response(response: dict, required_engine: str) -> None:
    engines = response.get("engines") or {}
    cache = engines.get("cache") or {}
    if cache.get("engine") != required_engine:
        raise ValueError(f"response did not use required {required_engine} engine")
    if cache.get("hit") is True:
        return
    if cache.get("stored") is not True:
        raise ValueError(f"response was not stored with required {required_engine} engine")


def warm_points(
    points: Iterable[tuple[float, float]],
    radii: Iterable[int],
    modes: Iterable[str],
    post: Callable[[dict], dict],
    *,
    workers: int = 1,
) -> dict[str, int]:
    payloads = [
        {"lng": lng, "lat": lat, "radius_m": radius, "mode": mode}
        for (lng, lat), radius, mode in product(points, radii, modes)
    ]
    completed = 0
    failed = 0
    if workers <= 1:
        for payload in payloads:
            try:
                post(payload)
                completed += 1
            except Exception:
                failed += 1
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(post, payload) for payload in payloads]
            for future in as_completed(futures):
                try:
                    future.result()
                    completed += 1
                except Exception:
                    failed += 1
    return {"requested": len(payloads), "completed": completed, "failed": failed}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", nargs=4, type=float, default=SEOUL_BBOX, metavar=("MIN_LNG", "MIN_LAT", "MAX_LNG", "MAX_LAT"))
    parser.add_argument("--step-m", type=int, default=ANALYSIS_GRID_STEP_M)
    parser.add_argument("--radii", nargs="+", type=int, default=[500, 1000], choices=[500, 1000])
    parser.add_argument("--modes", nargs="+", default=["walk"], choices=["walk", "drive"])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0, help="Limit grid centers for staged runs; 0 means all")
    parser.add_argument("--api-base", default="http://localhost:8000")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    points = list(iter_grid_points(tuple(args.bbox), args.step_m))
    if args.limit > 0:
        points = points[: args.limit]
    request_count = len(points) * len(args.radii) * len(args.modes)
    if args.dry_run:
        print({"centers": len(points), "requests": request_count})
        return 0

    endpoint = f"{args.api_base.rstrip('/')}/api/analyze"

    def post(payload: dict) -> dict:
        response = httpx.post(endpoint, json=payload, timeout=240)
        response.raise_for_status()
        return response.json()

    summary = warm_points(points, args.radii, args.modes, post, workers=args.workers)
    print(summary)
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
