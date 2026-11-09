"""D-1 동네 «안» 배분 검정 — 「선점 기회형」 자리에 가게가 먼저 들어서는가.

docs/VALIDATION_DESIGN_2026-09-17.md §5 D-1 · docs/DECISIONS_LAND_MODEL.md §27.

질문: 기준 연도 t0 말에 영업 중인 가게가 하나도 없던 도로 세그먼트 가운데, 길 구조(Choice)
상위 절반은 하위 절반보다 t1 까지 신규 개업이 더 많이 들어왔는가. **같은 행정동 안에서만**
비교한다 — "성수동이 떴다"가 아니라 "성수동 안에서 어느 골목이냐"를 본다.

⚠️ 실행 전에 고정한 것 (사전등록):
  · 창        2012→2017 · 2015→2020 · 2018→2023 (코로나 전·중·후)
  · 모집단    t0 12월 31일에 영업 중인 업소가 0 인 세그먼트 (biz_licenses 4업종 기준)
  · 결과      (t0, t1] 인허가일 기준 신규 개업 ≥1 (주) · 건수 (보조)
  · 구조 지표 road_segment_structure.choice_raw (NACH, 1.8km 창 radius-n — §22 정정 ①).
              설계 §8 은 NACH r1000 이라 적었지만 서울 전역 r1000 은 이 랩탑에 없다
              (seoul_global 계산이 2026-09-10 반경 500 에서 멈췄다). 중심 5구 r1000
              (central5.csv) 으로 민감도만 본다.
  · 컷        행정동 안 상위 절반 vs 하위 절반 (발표 유형표와 같다). 다른 컷은 쓰지 않는다.
  · 대조군    ① 무작위 절반 (기대 1.0) ② Integration 으로 같은 컷 (성분 대조)
              ③ 「비어 있음」만 — 즉 상·하위 합친 전체 개업률이 기준선
  · 판정      동 층화 Mantel-Haenszel 위험비 + 동 클러스터 부트스트랩 95% CI (공간 상관 보정).
              동의 절반 이상에서 상위>하위 인지도 같이 본다.

⚠️ 운영 DB에 쓰지 않는다. SELECT 만 한다.

Run:
  cd backend && .venv/Scripts/python -m tools.experiment_d1_allocation
  cd backend && .venv/Scripts/python -m tools.experiment_d1_allocation --central5 C:/dev/tools/scratch/central5.csv
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd
import psycopg2

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WINDOWS = [(2012, 2017), (2015, 2020), (2018, 2023)]
# 1차 실행(2026-10-13) 후 Integration 이 사후적으로 떠올라, 손대지 않은 창 하나를 홀드아웃으로 추가했다.
HOLDOUT = [(2009, 2014)]
MIN_EMPTY_PER_DONG = 20
BOOT = 1000
SEED = 0
STREET_HIGHWAYS = {
    "residential", "living_street", "pedestrian", "unclassified", "road",
    "tertiary", "tertiary_link", "secondary", "secondary_link", "primary", "primary_link",
}


def db_url() -> str:
    env = pathlib.Path(__file__).resolve().parents[1] / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("backend/.env 에 DATABASE_URL 이 없다")


def load_segments(conn) -> pd.DataFrame:
    sql = """
        SELECT s.seg_key, s.highway, s.choice_raw, s.integration_raw,
               d.adm_cd, d.adm_nm
        FROM road_segment_structure s
        JOIN sgis_dong_boundary d
          ON ST_Intersects(d.geom, ST_PointOnSurface(s.geom_5179))
    """
    return pd.read_sql(sql, conn)


def load_licenses(conn) -> pd.DataFrame:
    sql = """
        SELECT seg_key, biz_type, license_date, close_date
        FROM biz_licenses
        WHERE seg_key IS NOT NULL AND license_date IS NOT NULL
    """
    df = pd.read_sql(sql, conn)
    df["license_date"] = pd.to_datetime(df["license_date"])
    df["close_date"] = pd.to_datetime(df["close_date"])
    return df


def load_neighbor_counts(conn, cut: str) -> pd.Series:
    """cut 일에 영업 중인 업소 중 세그먼트 100m 안에 있는 수 (옆 골목 파급 통제용)."""
    sql = """
        SELECT s.seg_key, COUNT(b.mgt_no) AS n_nb
        FROM road_segment_structure s
        LEFT JOIN biz_licenses b
          ON b.geom_5179 IS NOT NULL AND b.license_date <= %(cut)s
         AND (b.close_date IS NULL OR b.close_date > %(cut)s)
         AND ST_DWithin(s.geom_5179, b.geom_5179, 100)
        GROUP BY s.seg_key
    """
    df = pd.read_sql(sql, conn, params={"cut": cut})
    return df.set_index("seg_key").n_nb


def build(seg: pd.DataFrame, lic: pd.DataFrame, t0: int, t1: int, biz: str | None = None) -> pd.DataFrame:
    cut = pd.Timestamp(f"{t0}-12-31")
    end = pd.Timestamp(f"{t1}-12-31")
    operating = lic[(lic.license_date <= cut) & (lic.close_date.isna() | (lic.close_date > cut))]
    occupied = set(operating.seg_key)
    df = seg.copy()
    df["occupied_t0"] = df.seg_key.isin(occupied)
    new = lic[(lic.license_date > cut) & (lic.license_date <= end)]
    if biz:
        new = new[new.biz_type == biz]
    cnt = new.groupby("seg_key").size()
    df["n_open"] = df.seg_key.map(cnt).fillna(0).astype(int)
    df["any_open"] = df.n_open > 0
    return df


def within_dong_half(df: pd.DataFrame, col: str, rng: np.random.Generator | None = None) -> pd.Series:
    """행정동 안 백분위 상위 절반 = True. rng 가 있으면 무작위 절반."""
    if rng is not None:
        return pd.Series(rng.random(len(df)) >= 0.5, index=df.index)
    pct = df.groupby("adm_cd")[col].rank(pct=True)
    return pct > 0.5


def dong_table(df: pd.DataFrame, top: pd.Series) -> pd.DataFrame:
    """동별 (상위 개업 세그먼트 a, 상위 n1, 하위 개업 c, 하위 n0)."""
    d = df.assign(top=top.values, y=df.any_open.astype(int))
    g = d.groupby(["adm_cd", "top"]).agg(y=("y", "sum"), n=("y", "size")).unstack(fill_value=0)
    t = pd.DataFrame({"a": g[("y", True)], "n1": g[("n", True)], "c": g[("y", False)], "n0": g[("n", False)]})
    return t[(t.n1 > 0) & (t.n0 > 0)]


def mh_from_table(t: pd.DataFrame) -> float:
    n = t.n1 + t.n0
    num = (t.a * t.n0 / n).sum(); den = (t.c * t.n1 / n).sum()
    return num / den if den else float("nan")


def evaluate(df: pd.DataFrame, col: str, rng_random: bool = False) -> dict:
    rng = np.random.default_rng(SEED)
    top = within_dong_half(df, col, rng if rng_random else None)
    d = df.assign(top=top.values)
    t = dong_table(df, top)
    rr = mh_from_table(t)
    brng = np.random.default_rng(SEED + 1)
    arr = t[["a", "n1", "c", "n0"]].to_numpy(float)
    boots = []
    for _ in range(BOOT):
        b = arr[brng.integers(0, len(arr), len(arr))]
        n = b[:, 1] + b[:, 3]
        den = (b[:, 2] * b[:, 1] / n).sum()
        boots.append((b[:, 0] * b[:, 3] / n).sum() / den if den else np.nan)
    lo, hi = np.nanpercentile(boots, [2.5, 97.5])
    share_top_wins = float(((t.a / t.n1) > (t.c / t.n0)).mean())
    return {
        "rate_top": d[d.top].any_open.mean(),
        "rate_bottom": d[~d.top].any_open.mean(),
        "rate_all": d.any_open.mean(),
        "mean_open_top": d[d.top].n_open.mean(),
        "mean_open_bottom": d[~d.top].n_open.mean(),
        "rr_mh": rr, "rr_lo": lo, "rr_hi": hi,
        "dongs": len(t), "share_dongs_top_wins": share_top_wins,
        "n": len(d),
    }


def fmt(r: dict) -> str:
    return (f"상위 {r['rate_top']*100:5.1f}% · 하위 {r['rate_bottom']*100:5.1f}% · 전체 {r['rate_all']*100:5.1f}% "
            f"| 건수 {r['mean_open_top']:.3f} vs {r['mean_open_bottom']:.3f} "
            f"| MH RR {r['rr_mh']:.2f} [{r['rr_lo']:.2f}, {r['rr_hi']:.2f}] "
            f"| 동 {r['dongs']}개 중 상위 우세 {r['share_dongs_top_wins']*100:.0f}% | n={r['n']:,}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--central5", default=None, help="compute_global_segment_structure CSV (nach_r1000)")
    ap.add_argument("--out", default=str(pathlib.Path(__file__).parent / "out" / "d1_allocation.csv"))
    args = ap.parse_args()

    conn = psycopg2.connect(db_url())
    seg = load_segments(conn)
    lic = load_licenses(conn)
    neighbors = {}
    for t0, _ in WINDOWS + HOLDOUT:
        neighbors[t0] = load_neighbor_counts(conn, f"{t0}-12-31")
        print(f"{t0} 100m 안 영업 업소 수 계산 완료")
    conn.close()
    print(f"세그먼트 {len(seg):,} (행정동 붙음) · 인허가 {len(lic):,}")

    r1000 = None
    if args.central5:
        r1000 = pd.read_csv(args.central5, usecols=["seg_key", "nach_r1000", "nain_r1000"])
        print(f"central5 r1000 {len(r1000):,}행")

    rows = []
    for t0, t1 in WINDOWS + HOLDOUT:
        tag = " [홀드아웃]" if (t0, t1) in HOLDOUT else ""
        df = build(seg, lic, t0, t1)
        df["n_nb"] = df.seg_key.map(neighbors[t0]).fillna(0).astype(int)
        empty = df[~df.occupied_t0]
        counts = empty.groupby("adm_cd").size()
        empty = empty[empty.adm_cd.isin(counts[counts >= MIN_EMPTY_PER_DONG].index)]
        occ = df[df.occupied_t0]
        print(f"\n===== {t0}→{t1} : 빈 세그먼트 {len(empty):,} · 가게 있던 세그먼트 {len(occ):,} "
              f"(있던 곳 개업률 {occ.any_open.mean()*100:.1f}%, 빈 곳 {empty.any_open.mean()*100:.1f}%) =====")

        specs = [
            ("Choice(NACH) 상위 절반 [주]", empty, "choice_raw", False),
            ("Integration 상위 절반 [성분 대조]", empty, "integration_raw", False),
            ("무작위 절반 [대조]", empty, "choice_raw", True),
            ("Choice · 차도만 (footway/service 제외)", empty[empty.highway.isin(STREET_HIGHWAYS)], "choice_raw", False),
            ("Integration · 차도만", empty[empty.highway.isin(STREET_HIGHWAYS)], "integration_raw", False),
            ("Integration · 100m 안 기존 가게 0", empty[empty.n_nb == 0], "integration_raw", False),
            ("Integration · 100m 안 기존 가게 1~4", empty[(empty.n_nb >= 1) & (empty.n_nb <= 4)], "integration_raw", False),
            ("Integration · 100m 안 기존 가게 5+", empty[empty.n_nb >= 5], "integration_raw", False),
            ("Choice · 100m 안 기존 가게 0", empty[empty.n_nb == 0], "choice_raw", False),
            ("Integration · 차도만 · 100m 기존 가게 0", empty[empty.highway.isin(STREET_HIGHWAYS) & (empty.n_nb == 0)], "integration_raw", False),
            ("Integration · residential 만", empty[empty.highway == "residential"], "integration_raw", False),
            ("Integration · footway/service/path 만", empty[empty.highway.isin({"footway", "service", "path", "steps"})], "integration_raw", False),
        ]
        for biz in ("일반음식점", "휴게음식점", "미용업"):
            dfb = build(seg, lic, t0, t1, biz=biz)
            eb = dfb[~dfb.occupied_t0]
            eb = eb[eb.adm_cd.isin(counts[counts >= MIN_EMPTY_PER_DONG].index)]
            specs.append((f"Choice · 업종 {biz}", eb, "choice_raw", False))
            specs.append((f"Integration · 업종 {biz}", eb, "integration_raw", False))
        if r1000 is not None:
            e5 = empty.merge(r1000, on="seg_key")
            specs.append(("NACH r1000 (중심5구) [민감도]", e5, "nach_r1000", False))
            specs.append(("NACH 1.8km창 (같은 5구) [대조]", e5, "choice_raw", False))
            specs.append(("NAIN r1000 (중심5구)", e5, "nain_r1000", False))

        for label, d, col, rnd in specs:
            if len(d) == 0:
                continue
            r = evaluate(d, col, rnd)
            print(f"{label:38s} {fmt(r)}")
            rows.append({"window": f"{t0}-{t1}", "spec": label, **r})

        # 수요 수준별 — 동을 빈 세그먼트당 개업 건수 3분위로 나눈다 (뜬 동네 vs 안 뜬 동네)
        demand = empty.groupby("adm_cd").n_open.mean()
        terc = pd.qcut(demand, 3, labels=["하", "중", "상"])
        for lv in ("하", "중", "상"):
            d = empty[empty.adm_cd.isin(terc[terc == lv].index)]
            r = evaluate(d, "choice_raw")
            print(f"{'  수요 '+lv+' 동네 · Choice':38s} {fmt(r)}")
            rows.append({"window": f"{t0}-{t1}", "spec": f"수요 {lv} 동네 · Choice", **r})

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n기록: {out}")


if __name__ == "__main__":
    main()
