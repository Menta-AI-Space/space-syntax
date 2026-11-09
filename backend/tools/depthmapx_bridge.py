"""JSON bridge for the official depthmapX command-line application."""
import argparse
import csv
import json
import math
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path


def run(cli: str, *args: str) -> None:
    result = subprocess.run([cli, *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())


def normalise(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def find_column(fields: list[str], *needles: str) -> str:
    wanted = [normalise(needle) for needle in needles]
    for field in fields:
        key = normalise(field)
        if all(needle in key for needle in wanted):
            return field
    raise KeyError(f"Missing depthmapX column containing {needles}: {fields}")


def find_exact_column(fields: list[str], name: str) -> str:
    wanted = normalise(name)
    for field in fields:
        if normalise(field) == wanted:
            return field
    raise KeyError(f"Missing depthmapX column {name!r}: {fields}")


def write_input(path: Path, segments: list[dict], mode: str) -> None:
    flag = "walkable" if mode == "walk" else "drivable"
    points = [point for segment in segments if segment.get(flag) for point in segment["coordinates"]]
    origin_lng = sum(point[0] for point in points) / len(points) if points else 0.0
    origin_lat = sum(point[1] for point in points) / len(points) if points else 0.0
    longitude_scale = math.cos(math.radians(origin_lat)) * 111_320.0

    def project(point: list[float]) -> tuple[float, float]:
        return (
            (point[0] - origin_lng) * longitude_scale,
            (point[1] - origin_lat) * 111_320.0,
        )

    with path.open("w", encoding="utf-8", newline="") as handle:
        # depthmapX 0.8.0 Linux preserves CR from CRLF immediately before
        # appending analysis columns, which splits the exported header. Use
        # Unix newlines explicitly so T1024 Choice/Integration remain columns.
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["Ref", "x1", "y1", "x2", "y2", "source_index", "source_length"])
        ref = 0
        for segment in segments:
            if not segment.get(flag):
                continue
            coords = segment["coordinates"]
            span_length = float(segment.get("length_m", 0.0)) / max(len(coords) - 1, 1)
            for start, end in zip(coords, coords[1:]):
                x1, y1 = project(start)
                x2, y2 = project(end)
                writer.writerow([ref, x1, y1, x2, y2, segment["source_index"], span_length])
                ref += 1


def analyse_mode(cli: str, work: Path, payload: dict, mode: str) -> dict:
    source = work / f"{mode}.csv"
    imported = work / f"{mode}-imported.graph"
    segment_map = work / f"{mode}-segments.graph"
    analysed = work / f"{mode}-analysed.graph"
    exported = work / f"{mode}-results.csv"
    # 반경은 payload가 정한다. 기본값 n(전역)은 타일 배치가 쓰던 값이라 그대로 둔다 —
    # 여기를 바꾸면 road_segment_structure 의 기존 값과 비교가 안 된다.
    # radius_type: "steps"(위상) | "metric"(m) | "angular". metric 반경은 탐색을
    # 잘라내므로 n보다 훨씬 싸다 — 서울 전역처럼 큰 그래프에서 이 차이가 결정적이다.
    radius = str(payload.get("radius") or "n")
    radius_type = str(payload.get("radius_type") or "steps")
    write_input(source, payload["segments"], mode)
    run(cli, "-m", "IMPORT", "-f", str(source), "-o", str(imported), "-it", "data")
    run(cli, "-m", "MAPCONVERT", "-f", str(imported), "-o", str(segment_map), "-co", "segment", "-con", f"{mode}-segments", "-coc", "-cir")
    run(cli, "-m", "SEGMENT", "-f", str(segment_map), "-o", str(analysed), "-st", "tulip", "-sr", radius, "-srt", radius_type, "-stb", "1024", "-sic")
    run(cli, "-m", "EXPORT", "-f", str(analysed), "-o", str(exported), "-em", "shapegraph-map-csv")

    with exported.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        source_col = find_column(fields, "source", "index")
        length_col = find_column(fields, "segment", "length")
        choice_col = find_column(fields, "t1024", "choice")
        depth_col = find_column(fields, "t1024", "total", "depth")
        count_col = find_column(fields, "t1024", "node", "count")
        connectivity_col = find_exact_column(fields, "connectivity")
        buckets = defaultdict(
            lambda: {
                "weight": 0.0,
                "choice": 0.0,
                "integration": 0.0,
                "mean_depth": 0.0,
                "connectivity": 0.0,
            }
        )
        for row in reader:
            source_index = int(float(row[source_col]))
            length = max(float(row[length_col]), 1e-9)
            raw_choice = float(row[choice_col])
            total_depth = float(row[depth_col])
            node_count = float(row[count_col])
            mean_depth = total_depth / max(node_count - 1.0, 1.0)
            connectivity = float(row[connectivity_col])
            nach = math.log(raw_choice + 1.0) / math.log(total_depth + 3.0) if total_depth > 0 else 0.0
            nain = node_count ** 1.2 / total_depth if total_depth > 0 else 0.0
            bucket = buckets[source_index]
            bucket["weight"] += length
            bucket["choice"] += nach * length
            bucket["integration"] += nain * length
            bucket["mean_depth"] += mean_depth * length
            bucket["connectivity"] += connectivity * length

    result = {
        metric: {}
        for metric in ("choice", "integration", "mean_depth", "connectivity")
    }
    for index in range(len(payload["segments"])):
        bucket = buckets.get(index)
        for metric in result:
            result[metric][str(index)] = (
                bucket[metric] / bucket["weight"] if bucket else 0.0
            )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", default="depthmapXcli")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    modes = tuple(payload.get("modes") or ("walk", "drive"))
    with tempfile.TemporaryDirectory(prefix="depthmapx-native-") as temp:
        output = {mode: analyse_mode(args.cli, Path(temp), payload, mode) for mode in modes}
    Path(args.output).write_text(json.dumps(output), encoding="utf-8")


if __name__ == "__main__":
    main()
