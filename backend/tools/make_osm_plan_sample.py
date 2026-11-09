"""OpenStreetMap 도로망 → 계획도면 DXF 샘플 (2026-11-09, 해커톤 01).

신도시 계획도면 분석은 LH 획지결정도(DXF)를 받는다. 대회가 글로벌이라 두바이·미국의
실제 신도시 도면이 필요한데, 그 나라의 공식 CAD 도면은 공개돼 있지 않다. 대신
OpenStreetMap(ODbL)의 **실제 도로망**을 받아 LH 도면과 같은 레이어 이름으로 DXF를 만든다:

- `00_계획선-도로`   도로 중심선(간선~주거지 도로, 진입로·보행로 제외)
- `00_계획선-획지선` 도로가 둘러싼 블록(3m 안쪽으로 들여 그림) — 획지 대용
- `00_계획선-획지명` 블록 이름 — OSM 토지이용으로 C(상업)·B(주거)·P(공원)·초(학교), 모르면 비움

좌표는 UTM(m)으로 쓰고 EPSG를 함께 기록한다(화면이 crs로 넘긴다). OSM XML은 아래처럼 받는다
(API는 가로세로 0.25도²·노드 5만 개까지):

    curl -o jvc.osm "https://api.openstreetmap.org/api/0.6/map?bbox=55.195,25.045,55.225,25.075"
    python tools/make_osm_plan_sample.py jvc.osm --crs EPSG:32640 \
        --out ../frontend-next/public/samples/planned-site-dubai-jvc.dxf

출처 표기는 「© OpenStreetMap contributors (ODbL)」 — 화면 샘플 설명에 넣는다.

**실제 필지 모드**(`--parcels`, 2026-11-09 추가): 미국 카운티 감정평가청처럼 필지 GIS(GeoJSON, WGS84)가
공개된 곳은 블록 대신 **실제 필지**를 획지로 쓴다. 플로리다 DOR 용도 코드(`DOR_UC`)로 이름을 붙인다 —
상업·업무(010~039, 048)는 필지 그대로 C, 학교(072·083·084)는 초, 공원·공공(071·073·086·087·091·097)은 P,
주거(000~009)는 콘도 호실·단독주택이 수천 개라 **길로 나뉜 블록 단위로 합쳐** B(LH 공동주택용지 꼴).

    curl -o parcels.json "https://services1.arcgis.com/.../Osceola_2025_Parcels/FeatureServer/0/query?\
      where=1%3D1&geometry=<bbox>&geometryType=esriGeometryEnvelope&inSR=4326&outFields=PARCELNO,DOR_UC&outSR=4326&f=geojson"
    python tools/make_osm_plan_sample.py celebration.osm --crs EPSG:32617 --parcels parcels.json --out ...
"""

from __future__ import annotations

import argparse
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import json

import ezdxf
from pyproj import Transformer
from shapely.geometry import LineString, MultiPolygon, Polygon, box, shape
from shapely.prepared import prep
from shapely.ops import linemerge, polygonize, unary_union

ROAD_TAGS = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "residential", "unclassified",
    "living_street", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
}
MIN_BLOCK_M2 = 1_500.0
MAX_BLOCK_M2 = 150_000.0
MIN_COMPACTNESS = 0.15
INSET_M = 3.0


def _compactness(poly: Polygon) -> float:
    return 4 * math.pi * poly.area / max(poly.length ** 2, 1e-9)


def _largest_component(lines: list[LineString]) -> list[LineString]:
    """끝점을 공유하는 선끼리 묶어 가장 큰 묶음만 돌려준다(unary_union 뒤라 교차점마다 끝점이 있다)."""
    parent = list(range(len(lines)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_end: dict[tuple[float, float], int] = {}
    for i, line in enumerate(lines):
        for end in (line.coords[0], line.coords[-1]):
            key = (round(end[0], 1), round(end[1], 1))
            if key in by_end:
                a, b = find(i), find(by_end[key])
                if a != b:
                    parent[a] = b
            else:
                by_end[key] = i
    groups: dict[int, list[LineString]] = {}
    for i, line in enumerate(lines):
        groups.setdefault(find(i), []).append(line)
    return max(groups.values(), key=lambda g: sum(l.length for l in g))


def _category(tags: dict[str, str]) -> str | None:
    lu, le, am = tags.get("landuse"), tags.get("leisure"), tags.get("amenity")
    if lu in ("commercial", "retail") or tags.get("shop") == "mall":
        return "C"
    if am in ("school", "college", "university", "kindergarten"):
        return "초"
    if le in ("park", "garden", "golf_course") or lu in ("grass", "recreation_ground", "cemetery", "forest"):
        return "P"
    if lu == "residential":
        return "B"
    return None


def _dor_category(code: str) -> str | None:
    """플로리다 DOR 용도 코드 → 획지 이름 머리글자. 모르면 None(이름 없는 상가 후보)."""
    code = (code or "").strip()
    if not code.isdigit():
        return None
    n = int(code)
    if n <= 9:
        return "B"
    if 10 <= n <= 39 or n == 48:
        return "C"
    if n in (72, 83, 84):
        return "초"
    if n in (71, 73, 86, 87, 91, 97) or 80 <= n <= 89:
        return "P"
    return None


def lots_from_parcels(
    parcel_paths: list[Path], crs: str, clip_ll, road_lines: list[LineString]
) -> list[tuple[str | None, Polygon]]:
    """필지 GeoJSON → (이름, 폴리곤). 주거는 길로 나뉜 블록으로 합친다."""
    to_m = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    singles: list[tuple[str | None, Polygon]] = []
    housing: list[Polygon] = []
    for path in parcel_paths:
        for feat in json.loads(path.read_text(encoding="utf-8")).get("features", []):
            geom = feat.get("geometry")
            if not geom:
                continue
            g = shape(geom).intersection(clip_ll)
            if g.is_empty:
                continue
            cat = _dor_category(str(feat.get("properties", {}).get("DOR_UC", "")))
            from shapely.ops import transform as sh_transform

            gm = sh_transform(lambda x, y, z=None: to_m.transform(x, y), g)
            polys = list(gm.geoms) if isinstance(gm, MultiPolygon) else [gm] if isinstance(gm, Polygon) else []
            for poly in polys:
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if poly.is_empty or poly.area < 150:
                    continue
                if cat == "B":
                    housing.append(poly)
                else:
                    singles.append((cat, poly))
    # 주거: 서로 붙은 필지를 합치고 길로 다시 나눈다 — 호실 하나하나는 획지가 아니다.
    merged = unary_union([p.buffer(0.5) for p in housing]).buffer(-0.5) if housing else None
    blocks: list[Polygon] = []
    if merged is not None and not merged.is_empty:
        pieces = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
        for piece in pieces:
            inside = [r for r in road_lines if r.intersects(piece)]
            faces = [f for f in polygonize(unary_union([piece.boundary] + inside)) if piece.contains(f.representative_point())]
            for f in faces or [piece]:
                if f.area >= 800:
                    blocks.append(f)
    counters: dict[str, int] = {}

    def named(cat: str | None, poly: Polygon) -> tuple[str | None, Polygon]:
        if not cat:
            return None, poly
        counters[cat] = counters.get(cat, 0) + 1
        return f"{cat}{counters[cat]}", poly

    def split_by_roads(poly: Polygon) -> list[Polygon]:
        """길이 가로지르는 필지는 길에서 나눈다 — 추출기가 어차피 도로선으로 면을 자르는데, 이름표가
        한 조각에만 붙으면 나머지는 「획지 N」(이름 없는 후보)이 된다."""
        inside = [r for r in road_lines if r.crosses(poly) or poly.contains(r)]
        if not inside:
            return [poly]
        faces = [f for f in polygonize(unary_union([poly.boundary] + inside)) if poly.contains(f.representative_point())]
        return [f for f in faces if f.area >= 300] or [poly]

    out: list[tuple[str | None, Polygon]] = []
    for cat, poly in sorted(singles, key=lambda t: -t[1].area):
        # 25ha짜리 빈 상업 용지(010)도 실제 필지라 그대로 둔다 — 빼면 그 자리가 이름 없는 큰 면으로 남아
        # 어차피 후보가 된다(2026-11-09 실측). 화면은 처음 골라 둘 땅을 중심가 필지로 지정해 피한다.
        for piece in split_by_roads(poly):
            out.append(named(cat, piece))
    out += [named("B", b) for b in sorted(blocks, key=lambda b: -b.area)]
    return out


def build(osm_path: Path, crs: str, bbox: tuple[float, float, float, float] | None, parcels: list[Path] | None = None):
    root = ET.parse(osm_path).getroot()
    nodes: dict[str, tuple[float, float]] = {}
    for n in root.iter("node"):
        nodes[n.get("id")] = (float(n.get("lon")), float(n.get("lat")))
    if bbox is None:
        b = root.find("bounds")
        bbox = (float(b.get("minlon")), float(b.get("minlat")), float(b.get("maxlon")), float(b.get("maxlat")))
    clip_ll = box(*bbox)
    to_m = Transformer.from_crs("EPSG:4326", crs, always_xy=True)

    def project(coords):
        return [to_m.transform(x, y) for x, y in coords]

    roads: list[LineString] = []
    uses: list[tuple[str, Polygon]] = []
    for w in root.iter("way"):
        tags = {t.get("k"): t.get("v") for t in w.findall("tag")}
        refs = [nd.get("ref") for nd in w.findall("nd")]
        coords = [nodes[r] for r in refs if r in nodes]
        if len(coords) < 2:
            continue
        hw = tags.get("highway")
        if hw in ROAD_TAGS and tags.get("area") != "yes":
            line = LineString(coords).intersection(clip_ll)
            if line.is_empty:
                continue
            parts = list(line.geoms) if hasattr(line, "geoms") else [line]
            for p in parts:
                if isinstance(p, LineString) and len(p.coords) >= 2:
                    roads.append(LineString(project(p.coords)))
            continue
        cat = _category(tags)
        if cat and len(coords) >= 4 and coords[0] == coords[-1]:
            try:
                poly = Polygon(project(coords))
                if poly.is_valid and poly.area > 0:
                    uses.append((cat, poly))
            except Exception:
                continue

    if not roads:
        raise SystemExit("도로를 찾지 못했습니다")
    merged = linemerge(unary_union(roads))
    road_lines = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    # 상자 끝에서 잘린 고가·진입로 조각은 본망과 안 이어져 분석에서 빠진다 — 가장 큰 연결 성분만 남긴다.
    road_lines = _largest_component(road_lines)

    if parcels:
        return road_lines, lots_from_parcels(parcels, crs, clip_ll, road_lines), bbox

    faces = [
        f for f in polygonize(unary_union(road_lines))
        if MIN_BLOCK_M2 <= f.area <= MAX_BLOCK_M2 and _compactness(f) >= MIN_COMPACTNESS
    ]
    faces.sort(key=lambda f: -f.area)

    lots: list[tuple[str | None, Polygon]] = []
    counters: dict[str, int] = {}
    for face in faces:
        inner = face.buffer(-INSET_M)
        if inner.is_empty:
            continue
        if hasattr(inner, "geoms"):
            inner = max(inner.geoms, key=lambda g: g.area)
        name = None
        best, best_share = None, 0.0
        for cat, poly in uses:
            try:
                share = face.intersection(poly).area / face.area
            except Exception:
                continue
            if share > best_share:
                best, best_share = cat, share
        if best and best_share >= 0.35:
            counters[best] = counters.get(best, 0) + 1
            name = f"{best}{counters[best]}"
        lots.append((name, inner))
    return road_lines, lots, bbox


def write_dxf(out: Path, road_lines, lots, crs: str, title: str):
    doc = ezdxf.new("R2010")
    for layer in ("00_계획선-도로", "00_계획선-획지선", "00_계획선-획지명", "00_표제"):
        doc.layers.add(name=layer)
    msp = doc.modelspace()
    for line in road_lines:
        msp.add_lwpolyline([(round(x, 2), round(y, 2)) for x, y in line.coords], dxfattribs={"layer": "00_계획선-도로"})
    for name, poly in lots:
        pts = [(round(x, 2), round(y, 2)) for x, y in poly.exterior.coords]
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "00_계획선-획지선"})
        if name:
            c = poly.representative_point()
            msp.add_text(name, dxfattribs={"layer": "00_계획선-획지명", "height": 6.0, "insert": (c.x, c.y)})
    minx = min(min(l.bounds[0] for l in road_lines), 0) if False else min(l.bounds[0] for l in road_lines)
    miny = min(l.bounds[1] for l in road_lines)
    msp.add_text(
        f"{title} · © OpenStreetMap contributors (ODbL) · {crs}",
        dxfattribs={"layer": "00_표제", "height": 12.0, "insert": (minx, miny - 40)},
    )
    doc.saveas(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("osm", type=Path)
    ap.add_argument("--crs", required=True, help="미터 좌표계 EPSG, 예: EPSG:32640(두바이 UTM 40N)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--title", default="OSM plan sample")
    ap.add_argument("--bbox", help="minlon,minlat,maxlon,maxlat — 파일 bounds보다 좁게 자를 때")
    ap.add_argument("--parcels", type=Path, nargs="*", help="실제 필지 GeoJSON(WGS84, DOR_UC 필드) — 주면 블록 대신 필지를 획지로 쓴다")
    args = ap.parse_args()
    bbox = tuple(float(v) for v in args.bbox.split(",")) if args.bbox else None
    road_lines, lots, used = build(args.osm, args.crs, bbox, args.parcels)  # type: ignore[arg-type]
    write_dxf(args.out, road_lines, lots, args.crs, args.title)
    named = sum(1 for n, _ in lots if n)
    print(f"bbox {used} → 도로 {len(road_lines)}개, 블록 {len(lots)}개(이름 {named}개) → {args.out}")

    # 추출기로 되읽어 화면이 받을 모양을 확인한다 (MAX_ROADS 400 · MAX_LOTS 2000).
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from app.services.planned_site_dxf import extract_plan

        res = extract_plan(args.out.read_bytes(), args.crs)
        print(f"extract_plan: 길 {len(res.roads)}개 · 획지 {len(res.lots)}곳 · 중심 {res.centre_lat}, {res.centre_lng}")
        for note in res.notes:
            print("  note:", note)
    except Exception as exc:  # noqa: BLE001
        print("extract_plan 확인 실패:", exc)


if __name__ == "__main__":
    main()
