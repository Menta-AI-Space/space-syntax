"""신도시 길 점수 대조 1단계 — 앱의 depthmapX 엔진으로 신도시 한 곳의 보행망 각도 통합·선택(r1000·r2000).

  backend\\.venv\\Scripts\\python.exe tools/validation/crosscheck_towns_dmx.py <town> <data_dir>

운정 대조(`crosscheck_cityseer_depthmapx.py`)의 depthmapX 쪽과 같은 방식: F10 조사의 보행망 규칙
(`C:\\dev\\tools\\scratch\\unjeong-f10\\work\\walknet.py` — 보도·주차 통로·차량 터널 빼기, 이름 없는 보도선 빼기,
꺾이는 점마다 자른 직선 조각)으로 망을 만들고 `compute_depthmapx_metrics`로 잰다. 다른 점: 길 원본은 10곳 학습에
쓴 cityseer 캐시(`osm_<town>.json`, 중심 3.5km)라 도시마다 같은 조건이다. 결과는 `data/dmx_<town>.json`.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from shapely.geometry import LineString, Point

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))
WALKNET = Path(r"C:\dev\tools\scratch\unjeong-f10\work")
sys.path.insert(0, str(WALKNET))

import walknet as wn  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.depthmapx_engine import compute_depthmapx_metrics  # noqa: E402
from app.services.graph_builder import build_graph  # noqa: E402
from towns import TOWNS  # noqa: E402

# depthmapXcli는 한글 경로를 못 읽을 수 있다 — 임시 폴더를 ASCII 경로로
ASCII_TMP = r"C:\dev\tools\scratch\tmp"
os.makedirs(ASCII_TMP, exist_ok=True)
tempfile.tempdir = ASCII_TMP
settings.depthmapx_command = (
    rf"{sys.executable} {BACKEND}\tools\depthmapx_bridge.py --cli C:\dev\tools\depthmapx\depthmapXcli.exe"
)
WINDOW_M = 3500.0
RADII = ("1000", "2000")


def osm_ways(path: Path, centre: Point) -> list[tuple[LineString, dict]]:
    """cityseer 캐시(노드·웨이 따로) → (5179 선, 태그). walknet 보행망 규칙으로 거른다."""
    d = json.loads(path.read_text(encoding="utf-8"))
    nodes = {e["id"]: wn.TO_M.transform(e["lon"], e["lat"]) for e in d["elements"] if e["type"] == "node"}
    window = centre.buffer(WINDOW_M)
    kept = []
    for e in d["elements"]:
        if e["type"] != "way":
            continue
        pts = [nodes[n] for n in e["nodes"] if n in nodes]
        if len(pts) < 2:
            continue
        line = LineString(pts)
        if not line.intersects(window):
            continue
        tags = dict(e.get("tags") or {})
        if wn.walk_exclusion(tags, "current"):
            continue
        tags["_osm_id"] = e["id"]
        kept.append((line, tags))
    return kept


def main() -> None:
    town, data = sys.argv[1], Path(sys.argv[2])
    lat, lng = TOWNS[town]
    centre = Point(*wn.TO_M.transform(lng, lat))
    t0 = time.time()
    ways, n_sidewalk = wn.drop_untagged_sidewalks(osm_ways(data / f"osm_{town}.json", centre), [])
    segments, _ = wn.to_segments(ways)
    G, _ = build_graph(segments)
    out = {"town": town, "segments": len(segments), "coords": [s.coordinates for s in segments]}
    for r in RADII:
        tr = time.time()
        metrics, err = compute_depthmapx_metrics(G, segments, modes=("walk",), radius=r, radius_type="metric",
                                                 timeout_seconds=3600)
        if metrics is None:
            raise SystemExit(f"{town} depthmapX 실패 R{r}: {err}")
        walk = metrics["walk"]
        out[f"i{r}"] = [walk["integration"].get(i, 0.0) for i in range(len(segments))]
        out[f"c{r}"] = [walk["choice"].get(i, 0.0) for i in range(len(segments))]
        print(f"{town} R{r} {time.time() - tr:.0f}s", flush=True)
    (data / f"dmx_{town}.json").write_text(json.dumps(out), encoding="utf-8")
    print(town, "조각", len(segments), "보도선 뺌", n_sidewalk, f"{time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
