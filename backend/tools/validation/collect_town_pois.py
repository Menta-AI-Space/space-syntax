"""신도시 정답 자료: 카카오 로컬 카테고리 검색으로 가게·역 위치를 모은다.

가게: 음식점(FD6)·카페(CE7)·편의점(CS2)·약국(PM9)·병원(HP8)·학원(AC5) — 길가 상가에 드는 업종.
역: 지하철역(SW8). 한 칸에 45곳까지만 주므로 넘치면 네 칸으로 나눈다.

카카오 키는 운영 환경에만 있어 `railway run`으로 돌린다 (키를 꺼내 쓰지 않는다):
  railway run -p <project> -e production -s backend -- <venv python> collect_town_pois.py <out_dir>
"""
import json
import math
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.config import settings  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from towns import TOWNS  # noqa: E402
RADIUS_M = 2000
STORE_CATS = {"FD6": "음식점", "CE7": "카페", "CS2": "편의점", "PM9": "약국", "HP8": "병원", "AC5": "학원"}
URL = "https://dapi.kakao.com/v2/local/search/category.json"


def collect(code: str, lat: float, lng: float) -> dict[str, dict]:
    headers = {"Authorization": f"KakaoAK {settings.kakao_rest_key}"}
    out: dict[str, dict] = {}
    dlat = RADIUS_M / 111_320
    dlng = RADIUS_M / (111_320 * math.cos(math.radians(lat)))

    def get(params):
        for attempt in range(4):
            r = httpx.get(URL, params=params, headers=headers, timeout=20)
            if r.status_code == 429:
                time.sleep(2 + attempt * 2)
                continue
            r.raise_for_status()
            time.sleep(0.04)
            return r.json()
        r.raise_for_status()

    def rect(x0, y0, x1, y1, depth=0):
        params = {"category_group_code": code, "rect": f"{x0},{y0},{x1},{y1}", "page": 1, "size": 15}
        j = get(params)
        if j["meta"]["total_count"] > 45 and depth < 10:
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            for a in ((x0, y0, mx, my), (mx, y0, x1, my), (x0, my, mx, y1), (mx, my, x1, y1)):
                rect(*a, depth=depth + 1)
            return
        page = 1
        while True:
            for d in j["documents"]:
                out[d["id"]] = {"lon": float(d["x"]), "lat": float(d["y"]), "cat": code, "name": d["place_name"],
                                "addr": d.get("road_address_name") or d.get("address_name")}
            if j["meta"]["is_end"]:
                break
            page += 1
            params["page"] = page
            j = get(params)

    rect(lng - dlng, lat - dlat, lng + dlng, lat + dlat)
    return out


def main() -> None:
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    for town, (lat, lng) in TOWNS.items():
        path = out_dir / f"pois_{town}.json"
        if path.exists():
            print(town, "있음 — 건너뜀")
            continue
        stores: dict[str, dict] = {}
        for code in STORE_CATS:
            stores.update(collect(code, lat, lng))
        stations = list(collect("SW8", lat, lng).values())
        path.write_text(json.dumps({"town": town, "centre": [lat, lng], "radius_m": RADIUS_M,
                                    "stores": list(stores.values()), "stations": stations},
                                   ensure_ascii=False), encoding="utf-8")
        print(town, "가게", len(stores), "역", len(stations), flush=True)


if __name__ == "__main__":
    main()
