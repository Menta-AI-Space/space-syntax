"""LOCALDATA 업종별 인허가 CSV 내려받기 (file.localdata.go.kr, 서울 전체).

www.localdata.go.kr 은 이 PC에서 연결이 안 됐지만(타임아웃) `file.localdata.go.kr` 은
된다. 다운로드 절차가 브라우저 전용처럼 생겨서 적어 둔다:

  1. GET /file/{slug}/info           — 세션 쿠키
  2. GET /file/validate/download-count (Referer 필요, 429면 잠시 뒤)
  3. GET /file/download/{slug}/info?orgCode=6110000_ALL — CSV 본문 (CP949, 서울 전체)

받은 파일은 `data/localdata/{slug}_6110000.csv` 에 두고 `tools.ingest_biz_licenses` 로
적재한다. slug 목록은 `/file/general_restaurants/info` 페이지 사이드바에 208종이 있다.

Run:
  cd backend && .venv/Scripts/python -m tools.fetch_localdata_files
  cd backend && .venv/Scripts/python -m tools.fetch_localdata_files --slug karaoke_rooms --slug pharmacies
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

BASE = "https://file.localdata.go.kr"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
DEFAULT_SLUGS = ["general_restaurants", "rest_cafes", "bakeries", "beauty_salons"]
SEOUL_ALL = "6110000_ALL"


def fetch(slug: str, out_dir: Path, org_code: str) -> Path:
    out = out_dir / f"{slug}_{org_code.split('_')[0]}.csv"
    ref = f"{BASE}/file/{slug}/info"
    with httpx.Client(headers={"User-Agent": UA}, timeout=120.0, follow_redirects=True) as client:
        r = client.get(ref)
        r.raise_for_status()
        v = client.get(f"{BASE}/file/validate/download-count", headers={"Referer": ref})
        if v.status_code == 429:
            raise SystemExit(f"{slug}: 다운로드 횟수 제한 — 잠시 뒤 다시 ({v.text[:80]})")
        v.raise_for_status()
        with client.stream(
            "GET", f"{BASE}/file/download/{slug}/info", params={"orgCode": org_code},
            headers={"Referer": ref}, timeout=900.0,
        ) as resp:
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            if "csv" not in ctype and "octet" not in ctype:
                raise SystemExit(f"{slug}: CSV가 아니라 {ctype} 가 왔다 — 차단됐거나 slug가 틀렸다")
            with out.open("wb") as f:
                for chunk in resp.iter_bytes(1 << 20):
                    f.write(chunk)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slug", action="append", help="받을 업종 slug (여러 번). 기본: 음식점·휴게음식점·제과점·미용업")
    ap.add_argument("--org-code", default=SEOUL_ALL, help="지역 코드. 서울 전체 6110000_ALL, 구 단위는 3000000(종로)~3240000")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "data" / "localdata"))
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for slug in args.slug or DEFAULT_SLUGS:
        p = fetch(slug, out_dir, args.org_code)
        print(f"{slug}: {p} ({p.stat().st_size / 1e6:.1f} MB)", flush=True)
        time.sleep(3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
