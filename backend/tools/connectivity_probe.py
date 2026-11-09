"""Ad-hoc connectivity health probe for the space-syntax road graph.

Measures, for real Seoul click points at the real analysis radii, how much of the
walkable/drivable angular network is EXCLUDED from the largest connected component
(the component `_prepare_angular_main` keeps). Excluded segments get no space-syntax
value and distort normalization of the rest, so a high exclusion % means cached
values in that area would be wrong. Run from backend/ with the venv python.
"""
from __future__ import annotations

import networkx as nx

from app.services.spatial_query import fetch_roads_in_radius
from app.services.graph_builder import build_graph, build_angular_graph

# (label, lng, lat) — representative Seoul areas: dense commercial, residential, riverside.
POINTS = [
    ("Gangnam",   127.0276, 37.4979),
    ("Sinchon",   126.9370, 37.5559),
    ("Hongdae",   126.9236, 37.5563),
    ("Jamsil",    127.1000, 37.5130),
    ("Yeouido",   126.9245, 37.5219),
    ("Jongno",    126.9910, 37.5704),
    ("Mok-dong",  126.8664, 37.5304),
]
RADII = [500, 1000]


def component_stats(angular: nx.Graph) -> tuple[int, int, int, int, float]:
    """Return (nodes, n_components, largest_size, excluded, excluded_pct)."""
    n = angular.number_of_nodes()
    if n == 0:
        return 0, 0, 0, 0, 0.0
    comps = list(nx.connected_components(angular))
    largest = max(comps, key=len)
    excluded = n - len(largest)
    return n, len(comps), len(largest), excluded, (excluded / n * 100.0)


def main() -> None:
    print(f"{'area':10} {'r':>5} {'segs':>6} | "
          f"{'walk:comp':>9} {'lgst':>6} {'excl':>5} {'excl%':>6} | "
          f"{'drive:comp':>10} {'lgst':>6} {'excl':>5} {'excl%':>6}")
    print("-" * 96)
    worst = []
    for label, lng, lat in POINTS:
        for r in RADII:
            segs = fetch_roads_in_radius(lng, lat, r)
            if not segs:
                print(f"{label:10} {r:>5} {'(no roads)':>6}")
                continue
            G, _ = build_graph(segs)
            wa = build_angular_graph(G, "walkable")
            da = build_angular_graph(G, "drivable")
            wn, wc, wl, we, wp = component_stats(wa)
            dn, dc, dl, de, dp = component_stats(da)
            flag = "  <-- HIGH" if (wp > 2.0 or dp > 2.0) else ""
            print(f"{label:10} {r:>5} {len(segs):>6} | "
                  f"{wc:>9} {wl:>6} {we:>5} {wp:>5.1f}% | "
                  f"{dc:>10} {dl:>6} {de:>5} {dp:>5.1f}%{flag}")
            if wp > 2.0 or dp > 2.0:
                worst.append((label, r, wp, dp))
    print("-" * 96)
    if worst:
        print(f"\n{len(worst)} case(s) exceed the 2% exclusion gate (values there are suspect):")
        for label, r, wp, dp in worst:
            print(f"  {label} @ {r}m: walk {wp:.1f}% / drive {dp:.1f}% excluded")
    else:
        print("\nAll cases within the 2% exclusion gate — connectivity looks healthy.")


if __name__ == "__main__":
    main()
