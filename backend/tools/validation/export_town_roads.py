"""신도시마다 길(roads_clean)과 아파트·주거 면(planet_osm_polygon)을 EPSG:5179 GeoJSON으로 꺼낸다.

cityseer는 따로 둔 가상환경에서 돌리므로 DB 접속은 여기(백엔드 venv)에서만 한다.
  <backend venv python> export_town_roads.py <out_dir>
"""
import json
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.config import settings  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from towns import TOWNS  # noqa: E402

WINDOW_M = 3000


def main() -> None:
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    for town, (lat, lng) in TOWNS.items():
        centre = "ST_Transform(ST_SetSRID(ST_MakePoint(%s,%s),4326),5179)"
        cur.execute(
            f"""select osm_id, highway, walkable, drivable, layer, bridge, tunnel, coalesce(name,''),
                       ST_AsGeoJSON(geom_5179) from roads_clean
                where ST_DWithin(geom_5179, {centre}, %s)""",
            (lng, lat, WINDOW_M),
        )
        roads = [
            {"type": "Feature", "geometry": json.loads(g),
             "properties": {"osm_id": o, "highway": h, "walkable": w, "drivable": d, "layer": la or 0,
                            "bridge": bool(b), "tunnel": bool(t), "name": n}}
            for o, h, w, d, la, b, t, n, g in cur.fetchall()
        ]
        # 주거: 아파트 건물 면적, 주거 토지이용 면 — 「걸어서 오는 이웃 손님」 크기를 재는 데 쓴다
        cur.execute(
            f"""select coalesce(building,''), coalesce(landuse,''), ST_AsGeoJSON(ST_Transform(way, 5179)), ST_Area(ST_Transform(way, 5179))
                from planet_osm_polygon
                where (building in ('apartments','residential','house','yes') or landuse='residential')
                  and ST_DWithin(ST_Transform(way, 5179), {centre}, %s)""",
            (lng, lat, WINDOW_M),
        )
        homes = [
            {"type": "Feature", "geometry": json.loads(g), "properties": {"building": b, "landuse": lu, "area": a}}
            for b, lu, g, a in cur.fetchall()
        ]
        (out_dir / f"roads_{town}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": roads}), encoding="utf-8")
        (out_dir / f"homes_{town}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": homes}), encoding="utf-8")
        apts = sum(1 for h in homes if h["properties"]["building"] == "apartments")
        print(town, "길", len(roads), "주거 면", len(homes), "아파트 건물", apts, flush=True)


if __name__ == "__main__":
    main()
