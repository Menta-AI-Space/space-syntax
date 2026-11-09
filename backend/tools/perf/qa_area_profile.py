"""area_search가 왜 느린지 — 후보 수에 따른 응답 시간 기울기.

후보 1곳당 입지 분석이 통째로 한 번 돈다(goals.py 주석). 그게 사실이라면
총 시간 ≈ 고정비 + n × 후보당비용 이어야 하고, 기울기가 곧 후보당 비용이다.
"""
import json, time, urllib.request

BASE = "https://space-syntax-production.up.railway.app"
CAFE = {"inds_lcls_cd": "I2", "inds_mcls_cd": "I212", "inds_scls_cd": "I21201"}

def post(path, body, timeout=180):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    return time.time() - t, data

# 기준: 단일 지점 입지 분석 한 번
t, _ = post("/api/site-score", {"address": "연남동", "radius_m": 500, **CAFE})
print(f"site-score 1회            {t:5.1f}s")

# 워밍 후 한 번 더 (도로망 캐시 효과 분리)
t, _ = post("/api/site-score", {"address": "연남동", "radius_m": 500, **CAFE})
print(f"site-score 1회 (재요청)   {t:5.1f}s")

print()
prev = None
for n in (1, 2, 3, 5):
    t, d = post("/api/area-search",
                {"region_query": "연남동", "radius_m": 500, "max_candidates": n, **CAFE})
    got = len(d.get("candidates", []))
    slope = "" if prev is None else f"  (+{t - prev[1]:4.1f}s / +{n - prev[0]}후보)"
    print(f"area-search 후보 {n}곳      {t:5.1f}s  실제 {got}곳{slope}")
    prev = (n, t)
