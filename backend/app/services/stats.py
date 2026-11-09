from app.services.space_syntax import DRIVE, METRICS, WALK
from app.services.spatial_query import RoadSegment


def build_baseline_geojson(segments: list[RoadSegment]) -> dict:
    """Lightweight GeoJSON for map context (no space-syntax metrics)."""
    features = []
    for seg in segments:
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "osm_id": seg.osm_id,
                    "highway": seg.highway,
                    "length_m": round(seg.length_m, 2),
                    "walkable": seg.walkable,
                    "drivable": seg.drivable,
                    "layer": seg.layer,
                    "bridge": seg.bridge,
                    "tunnel": seg.tunnel,
                },
                "geometry": {"type": "LineString", "coordinates": seg.coordinates},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def build_geojson(
    segments: list[RoadSegment],
    metrics: dict[str, dict[str, dict[int, float]]],
    engine_metrics: dict[str, dict[str, dict[str, dict[int, float]]]] | None = None,
) -> dict:
    walk = metrics[WALK]
    drive = metrics[DRIVE]

    features = []
    for idx, seg in enumerate(segments):
        properties = {
                    "osm_id": seg.osm_id,
                    "highway": seg.highway,
                    "length_m": round(seg.length_m, 2),
                    "walkable": seg.walkable,
                    "drivable": seg.drivable,
                    "layer": seg.layer,
                    "bridge": seg.bridge,
                    "tunnel": seg.tunnel,
                }
        for metric in METRICS:
            properties[f"{metric}_walk"] = round(walk[metric].get(idx, 0.0), 6)
            properties[f"{metric}_drive"] = round(drive[metric].get(idx, 0.0), 6)
        for engine, values in (engine_metrics or {}).items():
            for mode in (WALK, DRIVE):
                for metric in METRICS:
                    if metric in values[mode]:
                        properties[f"{metric}_{mode}_{engine}"] = round(
                            values[mode][metric].get(idx, 0.0), 6
                        )
        features.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": {"type": "LineString", "coordinates": seg.coordinates},
            }
        )
    return {"type": "FeatureCollection", "features": features}
