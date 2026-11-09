"""가상의 택지 계획도를 만든다 — B안(계획 단계 획지 등급)의 시험·시연용.

실제 지구단위계획 도면은 기관에 요청해야 구할 수 있어 개발이 그때까지 멈춘다.
그래서 **구조가 일부러 불균등한** 가상 지구를 만들어 파이프라인부터 세운다.
실제 도면이 오면 좌표만 바꿔 끼우면 된다.

배치(세종 인근 빈 땅 좌표에 얹었다 — 기존 도로와 겹치지 않게):

    간선도로(동서) ─────────────────────────────
        │        │        │        │
      지선1     지선2    지선3    지선4(막다름)
        │        │        │
    ─────── 내부순환(동서) ───────

간선에 접한 획지는 목이 좋고, 막다른 지선 끝 획지는 나쁘다. 그 차이가
등급으로 갈리는지가 이 픽스처의 목적이다.

실행: python tools/make_planned_site_fixture.py
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "frontend-next" / "public" / "samples" / "planned-site.json"

# 세종시 북서쪽 개발 예정지 근처. 정확한 실제 지구가 아니라 좌표 자리만 빌린다.
BASE_LNG, BASE_LAT = 127.2300, 36.5300
# 1도 ≈ 111km(위도), 경도는 cos(36.53°) 보정
DEG_LNG = 1.0 / (111_320 * 0.803)
DEG_LAT = 1.0 / 110_574


def P(east_m: float, north_m: float) -> list[float]:
    return [round(BASE_LNG + east_m * DEG_LNG, 7), round(BASE_LAT + north_m * DEG_LAT, 7)]


def main() -> None:
    roads: list[dict] = []

    def road(points: list[list[float]], kind: str = "residential", name: str = "") -> None:
        roads.append({"coordinates": points, "road_type": kind, "name": name})

    # 간선(북쪽, 동서 700m)
    road([P(0, 400), P(700, 400)], "residential", "간선도로")
    # 내부 순환(남쪽, 동서)
    road([P(0, 0), P(700, 0)], "residential", "내부순환")
    # 지선 4개(남북). 4번은 중간에서 끊겨 막다른 길이다.
    for i, x in enumerate((100, 250, 400, 600), start=1):
        end = 150 if i == 4 else 0
        road([P(x, 400), P(x, end)], "footway", f"지선{i}")
    # 내부순환과 간선을 잇는 서쪽 연결로
    road([P(0, 0), P(0, 400)], "footway", "서측연결")

    # 획지: 지선 사이 블록을 좌우로 나눠 놓는다.
    lots: list[dict] = []
    xs = [0, 100, 250, 400, 600, 700]
    ys = [0, 130, 260, 400]
    n = 1
    for xi in range(len(xs) - 1):
        for yi in range(len(ys) - 1):
            x0, x1 = xs[xi] + 8, xs[xi + 1] - 8
            y0, y1 = ys[yi] + 8, ys[yi + 1] - 8
            if x1 - x0 < 20 or y1 - y0 < 20:
                continue
            lots.append(
                {
                    "lot_id": f"{n:02d}블록",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[P(x0, y0), P(x1, y0), P(x1, y1), P(x0, y1), P(x0, y0)]],
                    },
                }
            )
            n += 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"roads": roads, "lots": lots}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {OUT}  (도로 {len(roads)}개, 획지 {len(lots)}개)")


if __name__ == "__main__":
    main()
