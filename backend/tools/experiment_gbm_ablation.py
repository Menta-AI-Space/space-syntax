"""같은 fold·같은 feature 층으로 헤도닉(baseline) · LightGBM · CatBoost 를 나란히 잰다.

역할 분담이 다르다.

* **Baseline — 로그선형 헤도닉 OLS.** 계수가 해석되고 외삽이 되므로 "무엇이 값을
  만드는가"를 말할 수 있는 유일한 모형이다. 시뮬레이션(도로를 새로 뚫으면 어떻게
  되나)은 결정 문서가 GBM 을 금지했으므로 **여기서만** 나온다.
* **Main — LightGBM / CatBoost.** 적정가 추정(예측 정확도)은 결정 문서가 GBM 을
  허용한 쪽이다. 비선형·상호작용을 잡아 baseline 보다 잘 맞히는 것이 목표다.

⚠️ 이 모델들로 한계효과를 말하지 마라. GBM 은 외삽이 안 되고 학습 범위 밖에서
한계효과가 0 이 된다 (docs/DECISIONS_LAND_MODEL.md). "접근성이 1분 줄면 얼마"는
헤도닉 계수에서만 인용한다.

⚠️ 재는 것은 여기서도 ΔR² 다. 개별공시지가 = 비교표준지 × 비준표 배율이라 Layer A
(용도지역·도로접면·지목·이용상황)로 공시지가를 맞히면 행정 산식을 재현할 뿐이고
높은 R² 는 성과가 아니다. 그래서 M0(통제) 위에 하위군을 **각각 단독으로** 올려
증분만 본다. 이웃 가격은 "옆 필지에 매겨진 공시지가"라 같은 산식의 다른 통로이므로
우리 기여로 인용하지 않는다 — 주장할 숫자는 M1a(접근성+길구조)의 증분이다.

⚠️ 하이퍼파라미터는 모든 spec 에 **동일하게** 고정한다. spec 별로 튜닝하면 ΔR² 가
"Layer B 의 몫"이 아니라 "어느 쪽을 더 튜닝했나"가 된다. 그래서 early stopping 도
쓰지 않는다 (fold 마다 멈추는 시점이 달라져 비교가 흐려진다).

범주형 처리가 세 모형에서 다르고, 그게 의도다:
  헤도닉  → one-hot (train 범주만, 첫 범주 기준)
  LightGBM → 정수 인코딩 + categorical_feature (train 에 없던 범주는 -1)
  CatBoost → 문자열 그대로 cat_features (native 처리)

Run:
    cd backend
    ./.venv/Scripts/python.exe -m tools.experiment_gbm_ablation --json ../gbm.json
    ./.venv/Scripts/python.exe -m tools.experiment_gbm_ablation --specs core   # M0/M1a/M1 만
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
from dotenv import load_dotenv

from tools.experiment_layer_ablation import (
    _apply_standardize,
    _as_index,
    _logged,
    _r2,
    _r2_within_year,
    _solve_ols,
    _standardize,
    Panel,
    build_specs,
    fit_design,
    load_fold,
    load_fold_ids,
    load_panel,
    resolve_dsn,
    transform_design,
)

# ── 모형 설정 ────────────────────────────────────────────────────────────────
# spec 간 비교가 목적이므로 모든 spec·fold·모형에 이 값을 그대로 쓴다.
LGBM_PARAMS = dict(
    objective="regression",
    n_estimators=400,
    learning_rate=0.08,
    num_leaves=63,
    min_child_samples=40,
    subsample=0.9,
    subsample_freq=1,
    colsample_bytree=0.9,
    reg_lambda=1.0,
    n_jobs=-1,
    verbose=-1,
)
# ⚠️ thread_count 를 4 로 낮춘다. 기본(-1, 전 논리 코어)로 fold 1 M0 스펙조차
# CatBoostError: bad allocation 으로 죽었다 — 이 기계가 지금 여유 메모리 1.5GB
# 안팎이라(다른 claude·node 프로세스들이 이미 몇 GB를 쥐고 있다), 스레드마다 따로
# 두는 히스토그램 버퍼가 16개 동시에 필요한 게 못 버틴다. 배정에 걸리는 시간이
# 늘지만(스레드당 버퍼는 줄고 반복 수는 그대로) R²·ΔR² 비교에는 영향이 없다 —
# 스레드 수는 학습 데이터도 하이퍼파라미터도 바꾸지 않는다.
CATBOOST_PARAMS = dict(
    loss_function="RMSE",
    iterations=400,
    learning_rate=0.08,
    depth=6,
    l2_leaf_reg=3.0,
    verbose=0,
    allow_writing_files=False,
    thread_count=4,
)

SPEC_PRESETS = {
    # 미팅용 핵심 셋 — 통제, 우리 증분, Layer B 전체.
    "core": ("M0", "M1a(접근성+길구조)", "M1(LayerB 전체)"),
    "full": None,  # build_specs() 전부
}


# ── feature 행렬 (GBM 용) ────────────────────────────────────────────────────


def numeric_matrix(
    panel: Panel, names: list[str], idx: np.ndarray
) -> np.ndarray:
    """수치형만 float 행렬로. 결측은 NaN 으로 **그대로 둔다**.

    GBM 은 결측을 자기 규칙으로 가른다 — 중위값으로 채우면 오히려 정보를 지운다.
    헤도닉 쪽만 중위 대체 + 결측표시 열을 쓴다(선형 모형은 NaN 을 못 먹는다).
    """
    if not names:
        return np.empty((len(idx), 0), dtype=np.float64)
    cols = []
    for name in names:
        col, _ = _logged(panel, name)
        cols.append(col[idx])
    return np.column_stack(cols)


def encode_categoricals(
    panel: Panel, names: list[str], train_idx: np.ndarray, idx: np.ndarray
) -> np.ndarray:
    """범주형 → 정수 인코딩. 어느 코드를 살릴지는 **train 에서만** 정한다.

    ``panel.categorical`` 은 이미 전역 정수 코드다(문자열로 들고 있으면 부분집합을
    뜰 때마다 포인터 배열이 새로 할당돼 MemoryError 가 났다). 여기서는 그 코드를
    train 에 나타난 것만 0..k-1 로 다시 매기고, train 에 없던 코드는 -1 로 둔다 —
    test 에서 처음 보는 용도지역을 아는 척하지 않는다.
    """
    if not names:
        return np.empty((len(idx), 0), dtype=np.float64)
    cols = []
    for name in names:
        raw = panel.categorical[name]
        seen = np.unique(raw[train_idx])
        # searchsorted 로 코드를 다시 매긴다. seen 에 없는 값은 -1.
        pos = np.searchsorted(seen, raw[idx])
        pos = np.clip(pos, 0, len(seen) - 1)
        remapped = np.where(seen[pos] == raw[idx], pos, -1)
        cols.append(remapped.astype(np.float64))
    return np.column_stack(cols)


def string_categoricals(
    panel: Panel, names: list[str], idx: np.ndarray
) -> np.ndarray:
    """범주형 → 문자열 행렬. CatBoost 가 직접 처리한다.

    저장은 정수 코드이므로 cat_levels 로 되돌린다. CatBoost 는 문자열을 그대로
    먹는 게 강점이라(용도지역·지목·도로접면이 전부 범주형) 여기서만 되돌린다.
    """
    if not names:
        return np.empty((len(idx), 0), dtype=object)
    cols = []
    for name in names:
        levels = np.array(panel.cat_levels[name], dtype=object)
        cols.append(levels[panel.categorical[name][idx]])
    return np.column_stack(cols)


# ── 모형별 적합 ──────────────────────────────────────────────────────────────


def fit_hedonic(
    panel: Panel,
    numeric: list[str],
    categorical: list[str],
    tr: np.ndarray,
    te: np.ndarray,
    year_origin: float,
) -> np.ndarray:
    """Baseline. 열 목록은 train 이 정하고 test 는 따른다.

    ⚠️ 표준화도 최소자승도 ``experiment_layer_ablation`` 의 것을 그대로 쓴다.
    여기서 ``np.linalg.lstsq`` 를 따로 부르고 표준화를 건너뛰었더니 **같은 데이터·
    같은 fold·같은 spec 인데 두 스크립트의 baseline R² 가 달랐다** — M1(LayerB
    전체)이 이쪽에서는 -0.4~0.94, 저쪽에서는 -1e23 이었다. baseline 은 하나여야
    한다. 인용할 숫자가 어느 스크립트로 돌렸느냐에 따라 달라지면 그건 결과가 아니다.
    """
    spec = fit_design(panel, numeric, categorical, tr)
    X_tr = transform_design(panel, spec, tr, year_origin)
    mu, sd = _standardize(X_tr)
    _apply_standardize(X_tr, mu, sd)
    beta, _rank = _solve_ols(X_tr, panel.y[tr])
    del X_tr
    X_te = transform_design(panel, spec, te, year_origin)
    _apply_standardize(X_te, mu, sd)
    return X_te @ beta


def _gbm_frames(
    panel: Panel,
    numeric: list[str],
    categorical: list[str],
    tr: np.ndarray,
    te: np.ndarray,
    year_origin: float,
    categorical_as_string: bool,
) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """(X_train, X_test, 범주형 열 위치). 연도추세를 첫 열로 함께 넣는다.

    연도 더미가 아니라 추세인 이유는 헤도닉과 같다 — test 연도(2024~2026)가 train
    (2018~2022)에 없어서 더미는 test 에서 전부 0 이 된다. 트리에게도 연도는
    "이 값 이상"으로 갈리는 수치가 낫다.
    """
    trend_tr = (panel.base_year[tr] - year_origin).astype(np.float64).reshape(-1, 1)
    trend_te = (panel.base_year[te] - year_origin).astype(np.float64).reshape(-1, 1)
    num_tr = numeric_matrix(panel, numeric, tr)
    num_te = numeric_matrix(panel, numeric, te)

    if categorical_as_string:
        cat_tr = string_categoricals(panel, categorical, tr)
        cat_te = string_categoricals(panel, categorical, te)
        # object 로 합쳐야 문자열이 살아남는다.
        X_tr = np.hstack(
            [trend_tr.astype(object), num_tr.astype(object), cat_tr]
        )
        X_te = np.hstack(
            [trend_te.astype(object), num_te.astype(object), cat_te]
        )
    else:
        cat_tr = encode_categoricals(panel, categorical, tr, tr)
        cat_te = encode_categoricals(panel, categorical, tr, te)
        X_tr = np.hstack([trend_tr, num_tr, cat_tr])
        X_te = np.hstack([trend_te, num_te, cat_te])

    offset = 1 + len(numeric)
    cat_positions = list(range(offset, offset + len(categorical)))
    return X_tr, X_te, cat_positions


def fit_lightgbm(
    panel: Panel,
    numeric: list[str],
    categorical: list[str],
    tr: np.ndarray,
    te: np.ndarray,
    year_origin: float,
) -> np.ndarray:
    import lightgbm as lgb

    X_tr, X_te, cat_pos = _gbm_frames(
        panel, numeric, categorical, tr, te, year_origin, categorical_as_string=False
    )
    model = lgb.LGBMRegressor(**LGBM_PARAMS)
    model.fit(X_tr, panel.y[tr], categorical_feature=cat_pos or "auto")
    return model.predict(X_te)


def _catboost_frame(
    panel: Panel,
    numeric: list[str],
    categorical: list[str],
    idx: np.ndarray,
    year_origin: float,
):
    """CatBoost 입력용 DataFrame. 컬럼마다 dtype 을 따로 들고 간다.

    ⚠️ 한 덩어리 object 행렬로 넘기면 "bad allocation" 으로 죽는다(실측). 수치
    10만×40 을 파이썬 객체 포인터로 만드는 순간 메모리가 터진다. DataFrame 은
    수치는 float32, 범주는 문자열 열로 따로 두므로 그 폭발이 없다.
    """
    import pandas as pd

    data: dict[str, np.ndarray] = {
        # 연도는 더미가 아니라 추세다 — test 연도가 train 에 없어 더미는 0 이 된다.
        "연도추세": (panel.base_year[idx] - year_origin).astype(np.float32),
    }
    for name in numeric:
        col, label = _logged(panel, name)
        data[label] = col[idx].astype(np.float32)  # NaN 은 CatBoost 가 직접 가른다
    for name in categorical:
        levels = np.array(panel.cat_levels[name], dtype=object)
        data[name] = levels[panel.categorical[name][idx]]
    return pd.DataFrame(data)


def fit_catboost(
    panel: Panel,
    numeric: list[str],
    categorical: list[str],
    tr: np.ndarray,
    te: np.ndarray,
    year_origin: float,
) -> np.ndarray:
    """⚠️ 이 프로세스 안에서 84번(12 fold × 7 spec) 새로 만들어진다.

    train Pool 을 test frame 을 만들기 전에 지운다 — 실측으로 fold 3(train
    146,805행)에서 ``CatBoostError: bad allocation`` 이 났다. df_tr·train_pool·
    df_te·test_pool·model 을 동시에 들고 있을 이유가 없고, gc.collect() 로
    다음 호출이 조각난 메모리를 다시 쥐지 않게 한다. 이 방 자체가 좁아서
    (16GB 중 여유가 1GB 안팎이었다) 여유를 만드는 쪽이 스레드를 줄이는 것보다
    재현성을 덜 건드린다.
    """
    import gc

    from catboost import CatBoostRegressor, Pool

    df_tr = _catboost_frame(panel, numeric, categorical, tr, year_origin)
    train_pool = Pool(df_tr, panel.y[tr], cat_features=list(categorical))
    del df_tr
    model = CatBoostRegressor(**CATBOOST_PARAMS)
    model.fit(train_pool)
    del train_pool

    df_te = _catboost_frame(panel, numeric, categorical, te, year_origin)
    test_pool = Pool(df_te, cat_features=list(categorical))
    del df_te
    pred = model.predict(test_pool)
    del test_pool, model
    gc.collect()
    return pred


MODELS = {
    "헤도닉(baseline)": fit_hedonic,
    "LightGBM": fit_lightgbm,
    "CatBoost": fit_catboost,
}


# ── 실행 ─────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", help="요약을 이 경로에 JSON 으로 쓴다")
    ap.add_argument(
        "--specs", choices=sorted(SPEC_PRESETS), default="full",
        help="core = M0/M1a/M1 만 (빠름), full = 하위군까지 전부",
    )
    ap.add_argument(
        "--models", default=",".join(MODELS),
        help="쉼표로 구분한 모형 이름. 기본은 셋 다.",
    )
    ap.add_argument("--folds", type=int, help="앞쪽 N개 fold 만 (시험 실행용)")
    ap.add_argument(
        "--no-resume", action="store_true",
        help="--json 자리에 체크포인트가 있어도 무시하고 처음부터 다시 돈다",
    )
    ap.add_argument(
        "--max-new-folds", type=int,
        help="이번 실행에서 새로 계산할 fold 수. 다 되면 멈춘다(요약은 안 낸다) — "
             "이 프로세스 안에서 fold 를 오래 누적하면 메모리가 안 돌아와 "
             "CatBoost 가 bad allocation 으로 죽는 기계라, bash 에서 이 값을 1 로 "
             "주고 반복 호출하면 fold 마다 새 프로세스로 뜬다.",
    )
    args = ap.parse_args()

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    dsn = resolve_dsn()
    if not dsn:
        sys.stderr.write("EXPERIMENT_DATABASE_URL 도 DATABASE_URL 도 없다\n")
        return 1

    wanted_models = [m.strip() for m in args.models.split(",") if m.strip()]
    unknown = [m for m in wanted_models if m not in MODELS]
    if unknown:
        sys.stderr.write(f"모르는 모형: {unknown}. 가능: {list(MODELS)}\n")
        return 1

    all_specs = build_specs()
    keep = SPEC_PRESETS[args.specs]
    specs = all_specs if keep is None else {k: all_specs[k] for k in keep}

    panel = load_panel(dsn)
    folds = load_fold_ids(dsn)
    if args.folds:
        folds = folds[: args.folds]

    print(
        f"[run] fold {len(folds)}개 × spec {len(specs)}개 × 모형 {len(wanted_models)}개"
        f" = {len(folds) * len(specs) * len(wanted_models)}회 적합"
    )

    # (모형, spec) → fold 별 R²
    r2: dict[tuple[str, str], list[float]] = {}
    r2w: dict[tuple[str, str], list[float]] = {}
    resume_from = 0

    # ⚠️ CatBoost 가 이 기계에서 fold 도중 CatBoostError: bad allocation 으로
    # 두 번 죽었다(다른 프로세스가 이미 메모리 대부분을 쥐고 있어서, 남는 양이
    # fold 마다 들쭉날쭉하다). 같은 명령을 그대로 다시 치면 체크포인트에서
    # 이어서 돌게 한다 — 매번 처음부터 다시 도는 대신, 죽은 fold 부터 재개한다.
    if args.json and not args.no_resume and os.path.exists(args.json):
        with open(args.json, "r", encoding="utf-8") as fh:
            prior = json.load(fh)
        prior_specs = sorted(specs)
        prior_models = sorted(wanted_models)
        if (
            prior.get("n_folds_total") == len(folds)
            and sorted({k.split("|", 1)[1] for k in prior.get("per_fold_r2", {})} | set(prior_specs)) == prior_specs
            and sorted({k.split("|", 1)[0] for k in prior.get("per_fold_r2", {})} | set(prior_models)) == prior_models
        ):
            for key, vals in prior.get("per_fold_r2", {}).items():
                model_name, spec_name = key.split("|", 1)
                r2[(model_name, spec_name)] = list(vals)
            for key, vals in prior.get("per_fold_r2_within", {}).items():
                model_name, spec_name = key.split("|", 1)
                r2w[(model_name, spec_name)] = list(vals)
            resume_from = int(prior.get("n_folds_done", 0))
            print(f"[resume] {args.json} 에서 fold {resume_from}개를 이어받는다")
        else:
            print(f"[resume] {args.json} 이 이번 --specs/--models/fold 수와 안 맞아 처음부터 돈다")

    stop_at = len(folds)
    if args.max_new_folds is not None:
        stop_at = min(stop_at, resume_from + args.max_new_folds)

    for fi, fold in enumerate(folds, 1):
        if fi <= resume_from:
            continue
        if fi > stop_at:
            print(f"[stop] --max-new-folds 만큼 새로 했다. 이어서 같은 명령을 다시 치면 된다"
                  f" ({resume_from}/{len(folds)} → {stop_at}/{len(folds)})")
            return 0
        tr, te = load_fold(dsn, panel, fold)
        if len(tr) == 0 or len(te) == 0:
            print(f"  [{fi:2d}/{len(folds)}] {fold} — 행이 없어 건너뛴다")
            continue
        year_origin = float(panel.base_year[tr].min())
        y_te = panel.y[te]
        years_te = panel.base_year[te]
        print(
            f"  [{fi:2d}/{len(folds)}] {fold:34s} train={len(tr):>7,} test={len(te):>6,}"
        )
        for model_name in wanted_models:
            fit = MODELS[model_name]
            line = []
            for spec_name, (numeric, categorical) in specs.items():
                t0 = time.perf_counter()
                pred = fit(panel, numeric, categorical, tr, te, year_origin)
                score = _r2(y_te, pred)
                r2.setdefault((model_name, spec_name), []).append(score)
                r2w.setdefault((model_name, spec_name), []).append(
                    _r2_within_year(y_te, pred, years_te)
                )
                line.append(f"{spec_name}={score:.4f}({time.perf_counter() - t0:.0f}s)")
            print(f"        {model_name:16s} " + "  ".join(line))

        # ⚠️ fold 를 끝낼 때마다 원본 점수를 그대로 적는다. CatBoost 가 fold 3 에서
        # bad allocation 으로 죽었을 때 fold 1·2 의 84개 결과가 저장 없이
        # 통째로 날아갔다 — 다음 fold 로 넘어가기 전에 지금까지 쥔 것을 남긴다.
        if args.json:
            with open(args.json, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "n_rows": len(panel),
                        "n_folds_total": len(folds),
                        "n_folds_done": fi,
                        "scheme": "spatial_temporal h=1",
                        "lgbm_params": LGBM_PARAMS,
                        "catboost_params": CATBOOST_PARAMS,
                        "per_fold_r2": {f"{m}|{s}": v for (m, s), v in r2.items()},
                        "per_fold_r2_within": {f"{m}|{s}": v for (m, s), v in r2w.items()},
                    },
                    fh,
                    ensure_ascii=False,
                    indent=2,
                )

    # ── 보고 ────────────────────────────────────────────────────────────────
    summary: dict = {"folds": len(folds), "models": {}}
    print()
    print("=" * 92)
    print("모형 × spec 별 out-of-fold R²  (log 개별공시지가, spatial_temporal h=1)")
    print("=" * 92)
    for model_name in wanted_models:
        print(f"\n[{model_name}]")
        print(f"  {'spec':24s} {'R² 평균':>9s} {'sd':>7s} {'ΔR² vs M0':>11s} {'Δwithin':>9s}")
        base = np.array(r2.get((model_name, "M0"), []), dtype=np.float64)
        base_w = np.array(r2w.get((model_name, "M0"), []), dtype=np.float64)
        entry: dict = {}
        for spec_name in specs:
            vals = np.array(r2.get((model_name, spec_name), []), dtype=np.float64)
            wvals = np.array(r2w.get((model_name, spec_name), []), dtype=np.float64)
            if vals.size == 0:
                continue
            delta = vals - base if base.size == vals.size else np.array([np.nan])
            dw = wvals - base_w if base_w.size == wvals.size else np.array([np.nan])
            entry[spec_name] = {
                "r2_mean": float(vals.mean()),
                "r2_sd": float(vals.std(ddof=1)) if vals.size > 1 else 0.0,
                "delta_r2_mean": float(np.nanmean(delta)),
                "delta_r2_sd": float(np.nanstd(delta, ddof=1)) if delta.size > 1 else 0.0,
                "delta_within_mean": float(np.nanmean(dw)),
                "folds": int(vals.size),
            }
            e = entry[spec_name]
            print(
                f"  {spec_name:24s} {e['r2_mean']:>9.4f} {e['r2_sd']:>7.4f} "
                f"{e['delta_r2_mean']:>11.4f} {e['delta_within_mean']:>9.4f}"
            )
        summary["models"][model_name] = entry

    print()
    print("-" * 92)
    print("미팅에서 말할 것:")
    for model_name in wanted_models:
        e = summary["models"].get(model_name, {})
        ours = e.get("M1a(접근성+길구조)")
        m0 = e.get("M0")
        if not ours or not m0:
            continue
        print(
            f"  {model_name:16s} M0 R²={m0['r2_mean']:.4f}"
            f" → 접근성+길구조 추가 시 ΔR²={ours['delta_r2_mean']:+.4f}"
            f" (sd {ours['delta_r2_sd']:.4f})"
        )
    print()
    print("  ⚠️ M0 의 R² 자체는 성과가 아니다 — 비준표 변수로 비준표 결과를 맞힌 것이다.")
    print("  ⚠️ '이웃가격' 증분은 우리 기여로 인용하지 않는다 (같은 행정 산식의 다른 통로).")
    print("  ⚠️ LightGBM·CatBoost 로 한계효과·시뮬레이션을 말하지 않는다 — 외삽이 안 된다.")
    print("-" * 92)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "n_rows": len(panel),
                    "n_folds_total": len(folds),
                    "n_folds_done": len(folds),
                    "scheme": "spatial_temporal h=1",
                    "lgbm_params": LGBM_PARAMS,
                    "catboost_params": CATBOOST_PARAMS,
                    "summary": summary,
                    "per_fold_r2": {f"{m}|{s}": v for (m, s), v in r2.items()},
                    "per_fold_r2_within": {f"{m}|{s}": v for (m, s), v in r2w.items()},
                },
                fh,
                ensure_ascii=False,
                indent=2,
            )
        print(f"[out] {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
