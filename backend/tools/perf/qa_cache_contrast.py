"""캐시를 쓰는 경로(/api/analyze)와 안 쓰는 경로(/api/area-search)의 대비."""
import json, time, urllib.request
BASE = "https://space-syntax-production.up.railway.app"

def post(path, body, timeout=300):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    return time.time() - t, d

# /api/analyze — analysis_cache(격자 스냅 영구 캐시)를 쓰는 경로
body = {"address": "연남동", "radius_m": 1000, "mode": "walk"}
for i in (1, 2):
    t, d = post("/api/analyze", body)
    print(f"analyze 1km {i}회차       {t:5.1f}s  구간 {d.get('segment_count'):,}개")

# 같은 격자 안의 다른 지점 — 스냅이 히트로 바뀌는지
t, d = post("/api/analyze", {"address": "연남동 227-15", "radius_m": 1000, "mode": "walk"})
print(f"analyze 1km 인접지점     {t:5.1f}s  구간 {d.get('segment_count'):,}개")
