"""같은 질의를 반복하면 빨라지는가 — 고정비(도로망·적분)가 캐시되는지."""
import json, time, urllib.request
BASE = "https://space-syntax-production.up.railway.app"
CAFE = {"inds_lcls_cd": "I2", "inds_mcls_cd": "I212", "inds_scls_cd": "I21201"}

def post(path, body, timeout=180):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    return time.time() - t, d

body = {"region_query": "연남동", "radius_m": 500, "max_candidates": 5, **CAFE}
for i in (1, 2, 3):
    t, d = post("/api/area-search", body)
    ev = (d["candidates"][0].get("evidence") or {}).get("spot") or {}
    print(f"연남동 5곳 {i}회차  {t:5.1f}s   탐색반경={ev.get('search_radius_m')}m")

# 다른 동네 — 캐시가 지점별인지 확인
t, d = post("/api/area-search", {**body, "region_query": "성수동"})
ev = (d["candidates"][0].get("evidence") or {}).get("spot") or {}
print(f"성수동 5곳 1회차  {t:5.1f}s   탐색반경={ev.get('search_radius_m')}m")
