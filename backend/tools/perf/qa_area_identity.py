"""area_search 결과의 동일성(determinism) 확인 — 캐시 적용 전/후 비교용 기준선.

왜 필요한가
-----------
docs/PERF_AREA_SEARCH.md의 결론은 `_extract_spots`(goals.py)를 analysis_cache에
태우자는 것이다. 그런데 이 경로의 위험은 느려지는 게 아니라 **추천이 조용히
바뀌는 것**이다 — 캐시 키(segment_limit·반경·엔진·network_version)를 어긋나게
잡으면 다른 도로 집합에서 뽑은 후보가 나온다. 응답은 200이고 화면도 멀쩡한데
"어디에 가게를 내라"는 답만 달라진다.

그래서 캐시를 붙이기 **전에** 지금 결과가 애초에 결정적인지부터 재 둔다.
지금도 호출마다 흔들린다면, 캐시를 붙인 뒤의 비교로는 캐시 버그와 원래 있던
비결정성을 구분할 수 없다.

쓰는 법
-------
    PYTHONUTF8=1 python backend/tools/perf/qa_area_identity.py

캐시를 붙인 뒤 같은 명령을 다시 돌려, 아래 두 가지가 모두 유지되는지 본다.
  1) 같은 질의를 두 번 부르면 후보 지문이 완전히 같다
  2) 그 지문이 캐시 적용 전 기준선과도 같다  (--baseline 파일로 비교)

기준선 저장/비교:
    ... qa_area_identity.py --save baseline.json
    ... qa_area_identity.py --baseline baseline.json
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request

BASE = "https://space-syntax-production.up.railway.app"

CAFE = {"inds_lcls_cd": "I2", "inds_mcls_cd": "I212", "inds_scls_cd": "I21201"}

#: 성격이 다른 동네를 섞는다 — 조밀한 상권, 하천/철도로 도로망이 끊기는 곳,
#: 외곽. 캐시 키가 어긋나면 어디서든 티가 나지만 경계 지역에서 먼저 난다.
REGIONS = ["연남동", "성수동", "구로디지털단지역", "중계동"]


def post(path: str, body: dict, timeout: int = 300) -> tuple[float, dict]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    return time.time() - started, data


def fingerprint(response: dict) -> list[dict]:
    """추천을 결정하는 값만 뽑는다.

    서술(narrative)·경고 문구는 LLM이나 표현 변경으로 흔들릴 수 있으니 뺀다.
    여기 있는 값이 하나라도 달라지면 사용자가 받는 답이 달라진 것이다.
    """
    out: list[dict] = []
    for c in response.get("candidates", []):
        spot = (c.get("evidence") or {}).get("spot") or {}
        out.append(
            {
                "osm_id": spot.get("osm_id"),
                "lat": c.get("lat"),
                "lng": c.get("lng"),
                "recommend_score": c.get("recommend_score"),
                "verdict": c.get("verdict"),
                "expected_monthly_sales_man": c.get("expected_monthly_sales_man"),
                "max_acceptable_rent_man": c.get("max_acceptable_rent_man"),
                "search_radius_m": spot.get("search_radius_m"),
            }
        )
    return out


def diff_lines(a: list[dict], b: list[dict]) -> list[str]:
    """무엇이 달라졌는지 사람이 읽을 수 있게. 같으면 빈 목록."""
    if len(a) != len(b):
        return [f"후보 수가 다르다: {len(a)}곳 → {len(b)}곳"]
    lines: list[str] = []
    for i, (x, y) in enumerate(zip(a, b), start=1):
        for key in x:
            if x[key] != y.get(key):
                lines.append(f"  {i}번 후보 {key}: {x[key]} → {y.get(key)}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", metavar="FILE", help="이번 지문을 기준선으로 저장")
    parser.add_argument("--baseline", metavar="FILE", help="저장된 기준선과 비교")
    parser.add_argument("--repeats", type=int, default=2, help="지역당 호출 횟수")
    args = parser.parse_args()

    baseline = {}
    if args.baseline:
        with open(args.baseline, encoding="utf-8") as fh:
            baseline = json.load(fh)

    current: dict[str, list[dict]] = {}
    failures = 0

    for region in REGIONS:
        body = {"region_query": region, "radius_m": 500, "max_candidates": 5, **CAFE}
        runs: list[list[dict]] = []
        times: list[float] = []
        for _ in range(args.repeats):
            try:
                elapsed, data = post("/api/area-search", body)
            except Exception as exc:  # 네트워크·5xx는 동일성 판정과 구분해 적는다
                print(f"[{region}] 호출 실패: {exc}")
                failures += 1
                runs = []
                break
            runs.append(fingerprint(data))
            times.append(elapsed)
        if not runs:
            continue

        current[region] = runs[0]
        timing = " / ".join(f"{t:.1f}s" for t in times)
        drift = []
        for i in range(1, len(runs)):
            drift.extend(diff_lines(runs[0], runs[i]))

        if drift:
            failures += 1
            print(f"[{region}] 반복 호출이 다른 답을 냈다  ({timing})")
            for line in drift:
                print(line)
        else:
            print(f"[{region}] 반복 {args.repeats}회 동일  ({timing})  후보 {len(runs[0])}곳")

        if baseline.get(region):
            against = diff_lines(baseline[region], runs[0])
            if against:
                failures += 1
                print(f"[{region}] 기준선과 다르다 — 추천이 바뀌었다")
                for line in against:
                    print(line)
            else:
                print(f"[{region}] 기준선과 동일")

    if args.save:
        with open(args.save, "w", encoding="utf-8") as fh:
            json.dump(current, fh, ensure_ascii=False, indent=2)
        print(f"\n기준선 저장: {args.save}")

    print(f"\n{'실패 ' + str(failures) + '건' if failures else '모두 동일'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
