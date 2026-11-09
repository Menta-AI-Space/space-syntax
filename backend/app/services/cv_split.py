"""Spatial × Temporal Cross Validation 분할 — 세 방법론(A/B/C)이 공유하는 기반.

⚠️ **여기가 새면 모든 성능 수치가 거짓이 된다.** 이 저장소에서 가장 조심해야 하는
모듈이라 주석이 길다. 스펙은 ``docs/SPEC_LAND_AI_METHODOLOGY.md`` "검증 설계"다.

무작위 분할이 왜 안 되는가
--------------------------
공시지가는 **비교표준지 × 토지가격비준표**로 매겨진다. 같은 블록의 필지들은 같은
표준지를 쓰므로 값이 서로 거의 같다. 옆 필지가 train에 있으면 test 필지의 정답을
사실상 보고 있는 것이고, 모델은 "구조를 읽는 법"이 아니라 "이 블록의 값"을 외운다.
같은 이유로 무작위 시간 분할도 안 된다 — 2026년 값을 배우고 2018년을 맞히는 것은
서비스가 하는 일(앞으로를 맞히는 일)과 방향이 반대다.

그래서 스펙은 **둘을 동시에** 걸라고 못박았다: "하나만 걸면 다른 쪽으로 샌다 —
시간만 나누면 같은 필지의 옆 필지가 train에 있고, 공간만 나누면 미래를 본다."
이 모듈의 fold 하나는 (공간 블록 홀드아웃 × 시간 절단) 교차의 결과다.

공간 블록을 무엇으로 잡았나 — 격자냐 동이냐
--------------------------------------------
스펙 원문은 "특정 동/공간블록 전체를 test로 제외"이고, 순수 함수의 검증 성질도
"train과 test에 같은 동이 동시에 들어가지 않는가"다. 그런데 500m 격자와 법정동은
**서로를 포함하지 않는다** — 격자 하나가 동 경계를 걸치므로 어느 한쪽만 쓰면 다른
쪽이 샌다:

- **격자만** 쓰면: 한 동이 여러 fold로 쪼개진다 → 스펙의 명시 요구를 못 지킨다.
  게다가 같은 동·같은 표준지를 쓰는 필지가 train·test에 걸쳐 남는다.
- **동만** 쓰면: 동 경계를 마주 보는 두 필지(20m 거리)가 각각 train·test로 갈린다.
  블록 단위 홀드아웃의 목적이 딱 여기서 무너진다.

둘을 union-find로 합쳐 "동도 격자도 분리되는 덩어리"를 만들어 봤자 쓸 수 없다.
동은 인접해 있고 격자가 경계를 걸치므로 **한 구가 통째로 하나의 성분으로 뭉친다**
(금천 3개 동 → 1개, 강남 14개 동 → 1개). fold가 2개면 분할이 아니다.

그래서 **동 홀드아웃 + 격자 버퍼**로 정했다 (기본 모드 ``ldong``):

1. fold 배정 단위는 **법정동**이다 → 스펙 성질("같은 동이 양쪽에 없다")을 정의상 만족.
2. test 동의 필지와 train 동의 필지가 **같은 500m 격자에 함께 있으면 train 쪽을
   버린다** → 경계 누출을 끊는다. 결과적으로 같은 격자도 양쪽에 걸치지 않는다.

버리는 쪽을 train으로 고른 것은 의도적이다. test를 깎으면 fold마다 평가 대상이
달라져 fold 간 성능 비교가 불가능해진다. train은 줄어도 편향이 아니라 표본 감소다.

``grid`` 모드도 남겨 뒀지만 **진단용**이다(동 하나가 여러 fold로 쪼개진다). 동이
아주 많은 지역으로 확장할 때 더 촘촘한 분할이 필요하면 그때 승격 여부를 정한다.

홀드아웃은 **시군구 안에서** 돈다 (2026-08-24 추가)
------------------------------------------------
블록을 순수 해시로만 배정했더니 한 fold가 **금천구의 배정 가능한 동 전부**를
홀드아웃했다(금천은 동이 3개고 독산동은 데모용 test 고정이라 남은 것이 2개였다).
그 fold는 "강남구로 배워 금천구를 맞히기"가 되어 다른 fold와 다른 것을 재고,
fold 평균이 오염된다 — 구 간 외삽은 구 안 홀드아웃과 다른 문제다. 누출은 아니지만
성능 수치의 의미가 바뀐다. 그래서 배정을 **구별로 층화**한다(``assign_spatial_folds``
의 ``stratum_of``). 배정 가능한 블록이 2개 미만인 구는 경고로 드러내고, 배치는
``missing_train_strata``로 fold마다 확인해 하나라도 비면 저장하지 않는다.

시간 절단과 horizon — 여기가 가장 헷갈리는 곳
----------------------------------------------
방법론 A는 ``X(i,t) → P(i,t+h)``이고 h=1·3·5다. 즉 관측치 하나가 **두 개의 연도**를
쓴다: feature를 읽는 해 ``t``와 정답을 읽는 해 ``t+h``. "test 연도가 train보다
뒤"라는 조건을 t로만 따지면 새어 버린다 — 절단선을 2023에 두고 t=2022·h=3인 행을
train에 넣으면 그 행의 **정답이 2025년 값**이라 모델이 미래를 본 것이 된다.

그래서 이렇게 정했다 (De Prado의 purge/embargo와 같은 형태):

- train: ``base_year + horizon <= cut`` — feature도 정답도 절단선 이전
- test:  ``base_year > cut``           — feature부터 절단선 이후
- 그 사이(``cut - horizon < base_year <= cut``)는 **버린다**. 정답이 절단선을 넘는
  train 후보이면서 feature는 train 시대에 속해 어느 쪽으로도 쓸 수 없다.

이 정의의 좋은 점은 ``max(train base_year) < min(test base_year)``가 **항상**
성립한다는 것이다 — 테스트가 검증하는 성질이 그것이고, 사람이 눈으로도 확인할 수
있다. h=0(방법론 C, 같은 해 횡단면)이면 버리는 구간이 없어 자연히 사라진다.

결정론
------
파이썬 ``hash()``는 실행마다 바뀐다(PYTHONHASHSEED). 절대 쓰지 않는다.
블록 → fold 배정은 ``hashlib.sha256(salt|block)``의 정수 나머지다. 같은 입력이면
언제 어느 기계에서 돌려도 같은 fold가 나온다.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 공간 블록 격자 한 변(m). EPSG:5179가 미터 단위라 좌표를 그대로 나눈다.
# 500m을 고른 근거: 기존 구조 분석 반경(undervalued_areas 500m)과 같은 스케일이고,
# 비교표준지 하나가 담당하는 범위(수십~수백 m)보다 넉넉히 크다. 이보다 작게 잡으면
# 같은 표준지를 쓰는 필지가 격자를 넘어 남는다.
GRID_SIZE_M = 500

# 해시 salt. 분할을 의도적으로 바꾸고 싶을 때만 손댄다 — 바꾸면 과거 성능 수치와
# 비교가 불가능해지므로 값 자체를 결과에 함께 저장한다(cv_folds.scheme/…).
DEFAULT_SALT = "space-syntax-cv-v1"

# 시간 절단선 하나가 성립하려면 train 쪽에 최소 몇 개 연도가 있어야 하는가.
# 2년이면 연도 고정효과가 사실상 두 점을 잇는 직선이 된다.
MIN_TRAIN_YEARS = 3

SPATIAL_MODES = ("ldong", "grid")

# 데모 고정 블록의 fold 번호. 어떤 fold의 train에도 들어가지 않는다는 표시다.
PINNED_FOLD = -1

# 법정동 코드·PNU 앞 5자리가 시군구다. 층화 배정의 층 키.
SGG_CODE_LEN = 5

# scheme 이름. DB에 그대로 저장돼 "이 성능 수치가 어느 분할에서 나왔나"를 남긴다.
SCHEME_SPATIAL_TEMPORAL = "spatial_temporal"
SCHEME_RANDOM_SPATIAL = "random_spatial"

TRAIN = "train"
TEST = "test"


@dataclass(frozen=True)
class PanelObs:
    """패널 관측치 하나 = (필지, 기준연도). 좌표는 EPSG:5179 미터.

    ⚠️ 결측을 채우지 않는다. 좌표나 법정동이 없는 행은 호출 전에 걸러 넣는다 —
    여기서 0,0으로 채우면 서울 밖 격자에 뭉쳐 한 fold를 오염시킨다.
    """

    pnu: str
    base_year: int
    ldong_code: str
    x: float
    y: float


@dataclass(frozen=True)
class Assignment:
    pnu: str
    base_year: int
    split_role: str          # "train" | "test"
    spatial_block: str       # 감사용: 이 행이 어느 블록으로 배정됐나


@dataclass
class Fold:
    fold_id: str
    scheme: str
    horizon: int
    spatial_fold: int
    temporal_cut: int
    spatial_mode: str
    train_years: tuple[int, ...]
    test_years: tuple[int, ...]
    test_blocks: tuple[str, ...]
    assignments: list[Assignment] = field(default_factory=list)
    # 버려진 행 수를 사유별로 남긴다. "왜 표본이 줄었나"를 나중에 설명할 수 있어야
    # 하고, buffer가 0이면 버퍼가 실제로 안 걸린 것이므로 의심해 봐야 한다.
    dropped: dict[str, int] = field(default_factory=dict)

    @property
    def train(self) -> list[Assignment]:
        return [a for a in self.assignments if a.split_role == TRAIN]

    @property
    def test(self) -> list[Assignment]:
        return [a for a in self.assignments if a.split_role == TEST]

    def n_train(self) -> int:
        return sum(1 for a in self.assignments if a.split_role == TRAIN)

    def n_test(self) -> int:
        return sum(1 for a in self.assignments if a.split_role == TEST)


def grid_cell_key(x: float, y: float, *, size_m: int = GRID_SIZE_M) -> str:
    """좌표 → 격자 셀 키. 음수 좌표에서도 일관되게 floor로 내린다."""
    return f"{math.floor(x / size_m)}_{math.floor(y / size_m)}"


def spatial_block_key(obs: PanelObs, *, mode: str = "ldong") -> str:
    """이 관측치가 속한 공간 블록.

    ``ldong``: 법정동 코드 10자리. 스펙의 "동 전체를 test로 제외"가 이것이다.
    ``grid``: 500m 격자 셀. 진단용 — 동을 쪼개므로 스펙 성질을 만족하지 않는다.
    """
    if mode == "ldong":
        return obs.ldong_code
    if mode == "grid":
        return grid_cell_key(obs.x, obs.y)
    raise ValueError(f"알 수 없는 공간 분할 모드: {mode}")


def _stable_digest(key: str, salt: str) -> bytes:
    """결정론적 해시. 파이썬 hash()는 실행마다 바뀌므로 sha256을 쓴다."""
    return hashlib.sha256(f"{salt}|{key}".encode("utf-8")).digest()


def _stable_bucket(key: str, n_buckets: int, salt: str) -> int:
    return int.from_bytes(_stable_digest(key, salt)[:8], "big") % n_buckets


def assign_spatial_folds(
    block_keys: set[str] | list[str],
    *,
    n_folds: int,
    salt: str = DEFAULT_SALT,
    pinned_test_blocks: tuple[str, ...] | None = None,
    stratum_of: dict[str, str] | None = None,
) -> dict[str, int]:
    """블록 → fold 번호. 같은 입력이면 언제나 같은 결과다.

    ``pinned_test_blocks``는 **모든 fold에서 test로 고정**할 블록이다(``PINNED_FOLD``
    로 표시). 화면 데모 구역이 여기 들어간다 — 데모에서 보여 주는 값이 학습에 쓰인
    값이면 "예측"이라고 말할 수 없다. 고정 블록은 어떤 fold의 train에도 안 들어간다.

    ⚠️ **``stratum_of``(블록 → 시군구)를 주면 구 안에서 층화 배정한다. 이게 없으면
    한 fold가 어떤 구의 동을 전부 홀드아웃해 버린다.** 실제로 그렇게 됐다: 금천구는
    동이 3개고 그중 독산동이 test 고정이라 배정 가능한 동이 2개였는데, 순수 해시
    배정에서 한 fold가 그 둘을 다 가져가 **강남구만으로 학습해 금천구를 맞히는
    fold**가 생겼다. 누출은 아니지만 다른 fold와 다른 것을 재는 fold이므로
    fold 평균을 낼 수 없게 된다(구 간 외삽 ≠ 구 안 홀드아웃).

    층화는 이렇게 한다 — 결정론은 그대로다:
      1. 구별로 블록을 모으고, 각 구 안에서 **해시 순서**로 줄을 세운다
         (코드 순으로 세우면 지리적으로 인접한 동이 같은 fold로 몰린다).
      2. 그 줄에 라운드로빈으로 fold를 매긴다. 구마다 시작 offset을 해시로 달리
         둬서 여러 구의 같은 순번이 항상 같은 fold로 붙는 것을 피한다.
    이러면 어떤 fold도 한 구의 블록을 1/n_folds 넘게 가져가지 않는다.
    """
    if n_folds < 2:
        raise ValueError("공간 fold는 2개 이상이어야 한다 (홀드아웃이 성립해야 한다)")
    pinned = set(pinned_test_blocks or ())
    strata: dict[str, list[str]] = defaultdict(list)
    assigned: dict[str, int] = {}
    for key in sorted(set(block_keys)):
        if key in pinned:
            assigned[key] = PINNED_FOLD
            continue
        strata[(stratum_of or {}).get(key, "")].append(key)

    for stratum, keys in sorted(strata.items()):
        if len(keys) < 2:
            # 배정 가능한 블록이 1개면 그 블록을 홀드아웃한 fold에서 이 구가
            # train에서 통째로 사라진다. 조용히 넘기면 s2 사고가 그대로 재발한다.
            logger.warning(
                "층화 경고 — 구 %r의 배정 가능한 블록이 %d개뿐이다. 이 구를 홀드아웃한 "
                "fold에는 이 구의 train 표본이 없다(구 간 외삽 fold가 된다). "
                "블록 정의를 더 잘게 쪼개거나 이 구를 제외할 것.",
                stratum or "(층화 없음)", len(keys),
            )
        offset = _stable_bucket(f"__stratum__|{stratum}", n_folds, salt)
        for rank, key in enumerate(sorted(keys, key=lambda k: _stable_digest(k, salt))):
            assigned[key] = (rank + offset) % n_folds
    return assigned


def stratum_of_blocks(
    rows: list[PanelObs], block_of: dict[tuple[str, int], str]
) -> dict[str, str]:
    """블록 → 층(시군구 5자리). 층화 배정의 입력이다.

    법정동 코드·PNU 모두 앞 5자리가 시군구다. 격자 모드에서는 격자 하나가 구
    경계를 걸칠 수 있으므로 **그 격자에서 PNU가 가장 작은 필지의 구**를 쓴다 —
    임의 선택이지만 결정론적이고, 층화의 목적(어느 구도 train에서 사라지지 않게)
    에는 충분하다.
    """
    best: dict[str, str] = {}
    for (pnu, _year), block in block_of.items():
        prev = best.get(block)
        if prev is None or pnu < prev:
            best[block] = pnu
    return {block: pnu[:SGG_CODE_LEN] for block, pnu in best.items()}


def temporal_cuts(
    years: list[int] | set[int],
    *,
    horizon: int,
    min_train_years: int = MIN_TRAIN_YEARS,
    max_cuts: int | None = None,
) -> list[int]:
    """쓸 수 있는 시간 절단선들. 최신 절단선이 마지막에 온다.

    절단선 ``cut``이 성립할 조건:
      - train 연도(``base_year + horizon <= cut``)가 ``min_train_years`` 개 이상
      - test 연도(``base_year > cut``)가 하나 이상
    ``max_cuts``를 주면 **최신 쪽을 남긴다** — 오래된 절단선은 feature 확보 기간이
    짧아(스펙 "feature마다 확보 기간이 다르다") 정보가 적다.
    """
    ys = sorted(set(years))
    cuts = [
        cut
        for cut in ys
        if len([y for y in ys if y + horizon <= cut]) >= min_train_years
        and any(y > cut for y in ys)
    ]
    if max_cuts is not None and len(cuts) > max_cuts:
        cuts = cuts[-max_cuts:]
    return cuts


def split_spatial_temporal(
    rows: list[PanelObs],
    *,
    horizon: int = 1,
    n_spatial_folds: int = 4,
    spatial_mode: str = "ldong",
    scheme: str = SCHEME_SPATIAL_TEMPORAL,
    salt: str = DEFAULT_SALT,
    pinned_test_blocks: tuple[str, ...] | None = None,
    min_train_years: int = MIN_TRAIN_YEARS,
    max_temporal_cuts: int | None = None,
    buffer_grid: bool = True,
) -> list[Fold]:
    """패널 관측치 → fold 목록. **DB 없이 도는 순수 함수다.**

    fold 하나 = (공간 블록 홀드아웃 하나) × (시간 절단선 하나). 스펙이 요구한
    "동시에 걸기"가 이 교차다. 어느 fold에서도 아래 두 성질이 성립한다:

    1. train과 test에 **같은 공간 블록이 동시에 들어가지 않는다**
       (기본 모드에서 블록 = 법정동, 그리고 buffer_grid=True면 500m 격자까지).
    2. **모든 test 행의 base_year가 모든 train 행의 base_year보다 뒤다**
       (horizon만큼의 구간은 버려서 정답 연도도 넘지 않게 한다 — 모듈 docstring).

    ``scheme=SCHEME_RANDOM_SPATIAL``은 **진단용 비교군**이다. 공간 블록을 PNU
    하나하나로 잡아(=무작위 분할) 시간 분할만 남긴다. 이쪽 성능이 공간 분할보다
    좋으면 **그 차이가 공간 누출의 크기**다 — 성능 개선이 아니다. 절대 이 scheme의
    수치를 서비스 성능으로 보고하지 말 것.
    """
    if horizon < 0:
        raise ValueError("horizon은 0 이상이어야 한다")
    if scheme == SCHEME_RANDOM_SPATIAL:
        # 무작위 분할: 블록 = 필지 그 자체. 이웃끼리 갈라지므로 버퍼도 의미가 없다.
        block_of = {(r.pnu, r.base_year): r.pnu for r in rows}
        buffer_grid = False
    else:
        if spatial_mode not in SPATIAL_MODES:
            raise ValueError(f"알 수 없는 공간 분할 모드: {spatial_mode}")
        block_of = {
            (r.pnu, r.base_year): spatial_block_key(r, mode=spatial_mode) for r in rows
        }
        # 같은 필지가 연도마다 다른 블록으로 배정되면 그 필지는 어떤 fold에서
        # train과 test 양쪽에 등장한다 — 블록끼리는 안 겹치니 check_leakage도
        # 못 잡는 종류의 누출이다. 실측에서 동 코드가 바뀐 행(경계 조정·행정구역
        # 개편)이 그렇게 만든다. 조용히 넘기지 않고 세워서 사람이 보게 한다.
        per_pnu: dict[str, set[str]] = defaultdict(set)
        for (pnu, _year), block in block_of.items():
            per_pnu[pnu].add(block)
        unstable = sorted(p for p, blocks in per_pnu.items() if len(blocks) > 1)
        if unstable:
            raise ValueError(
                f"연도에 따라 공간 블록이 바뀌는 필지 {len(unstable)}개 "
                f"(예: {unstable[:3]}) — 블록을 하나로 고정해서 넣을 것"
            )

    years = {r.base_year for r in rows}
    cuts = temporal_cuts(
        years, horizon=horizon, min_train_years=min_train_years, max_cuts=max_temporal_cuts
    )
    if not cuts:
        # 연도가 모자라면 fold가 없다. 빈 리스트를 주고 조용히 넘어가지 않도록 경고한다.
        logger.warning(
            "시간 절단선이 없다 — 연도 %d종, horizon=%d, 최소 train 연도 %d",
            len(years), horizon, min_train_years,
        )
        return []

    # 시군구 층화 — 한 fold가 어떤 구의 동을 전부 홀드아웃하지 않도록.
    # (assign_spatial_folds docstring에 그 사고 기록이 있다)
    block_fold = assign_spatial_folds(
        set(block_of.values()),
        n_folds=n_spatial_folds,
        salt=salt,
        pinned_test_blocks=pinned_test_blocks,
        stratum_of=stratum_of_blocks(rows, block_of),
    )
    pinned = set(pinned_test_blocks or ())

    # 격자 버퍼용 사전계산: 행 → 격자 셀.
    cell_of = (
        {(r.pnu, r.base_year): grid_cell_key(r.x, r.y) for r in rows}
        if buffer_grid
        else {}
    )

    folds: list[Fold] = []
    for spatial_fold in range(n_spatial_folds):
        test_blocks = tuple(
            sorted(k for k, v in block_fold.items() if v == spatial_fold or k in pinned)
        )
        for cut in cuts:
            train_years = tuple(sorted(y for y in years if y + horizon <= cut))
            test_years = tuple(sorted(y for y in years if y > cut))
            fold = Fold(
                fold_id=f"{scheme}-h{horizon}-s{spatial_fold}-t{cut}",
                scheme=scheme,
                horizon=horizon,
                spatial_fold=spatial_fold,
                temporal_cut=cut,
                spatial_mode=spatial_mode if scheme != SCHEME_RANDOM_SPATIAL else "pnu",
                train_years=train_years,
                test_years=test_years,
                test_blocks=test_blocks,
            )
            _fill_fold(fold, rows, block_of, cell_of, set(test_blocks), buffer_grid)
            folds.append(fold)
    return folds


def _fill_fold(
    fold: Fold,
    rows: list[PanelObs],
    block_of: dict[tuple[str, int], str],
    cell_of: dict[tuple[str, int], str],
    test_blocks: set[str],
    buffer_grid: bool,
) -> None:
    """한 fold의 행 배정. 공간 조건과 시간 조건을 **둘 다** 통과한 행만 남는다."""
    train_years = set(fold.train_years)
    test_years = set(fold.test_years)
    dropped: dict[str, int] = defaultdict(int)

    provisional: list[tuple[PanelObs, str]] = []
    for r in rows:
        key = (r.pnu, r.base_year)
        in_test_block = block_of[key] in test_blocks
        if r.base_year in test_years:
            if in_test_block:
                provisional.append((r, TEST))
            else:
                # 미래 연도 × train 블록. 시간으로는 test인데 공간으로는 train이라
                # 어느 쪽에도 못 쓴다 — 이걸 train에 넣으면 미래를 본다.
                dropped["future_year_train_block"] += 1
        elif r.base_year in train_years:
            if in_test_block:
                # 과거 연도 × test 블록. train에 넣으면 test 블록의 값을 외운다.
                dropped["train_year_test_block"] += 1
            else:
                provisional.append((r, TRAIN))
        else:
            # horizon 때문에 버리는 구간 (cut - horizon < base_year <= cut).
            dropped["embargo_year"] += 1

    if buffer_grid:
        # 경계 누출 차단: test 행이 있는 격자 셀의 train 행을 버린다. 동 경계를
        # 마주 본 두 필지가 각각 train·test로 갈리는 것을 막는 유일한 장치다.
        test_cells = {cell_of[(r.pnu, r.base_year)] for r, role in provisional if role == TEST}
        kept: list[tuple[PanelObs, str]] = []
        for r, role in provisional:
            if role == TRAIN and cell_of[(r.pnu, r.base_year)] in test_cells:
                dropped["grid_buffer"] += 1
                continue
            kept.append((r, role))
        provisional = kept

    fold.assignments = [
        Assignment(
            pnu=r.pnu,
            base_year=r.base_year,
            split_role=role,
            spatial_block=block_of[(r.pnu, r.base_year)],
        )
        for r, role in provisional
    ]
    fold.dropped = dict(dropped)


# ── 진단 ────────────────────────────────────────────────────────────────

@dataclass
class LeakageReport:
    """fold 하나가 누출 성질을 지키는지. **CI에서도, 배치에서도 매번 확인한다.**

    "테스트가 통과했으니 됐다"로 끝내지 않는 이유: 실제 데이터에는 합성 테스트에
    없는 것들이 있다(같은 PNU가 동을 옮긴 행, 좌표가 이상한 행). 실측에서 값이
    깨지면 배치가 멈춰야 한다.
    """

    fold_id: str
    n_train: int
    n_test: int
    shared_blocks: tuple[str, ...]      # train·test에 동시에 있는 공간 블록 (있으면 버그)
    shared_grid_cells: int              # 동시에 있는 500m 격자 셀 수
    max_train_year: int | None
    min_test_year: int | None

    @property
    def ok(self) -> bool:
        if self.n_train == 0 or self.n_test == 0:
            return False
        if self.shared_blocks:
            return False
        if self.max_train_year is None or self.min_test_year is None:
            return False
        return self.max_train_year < self.min_test_year


def check_stratum_coverage(fold: Fold, *, stratum_len: int = SGG_CODE_LEN) -> dict[str, int]:
    """fold의 train·test에 층(시군구)별 행이 몇 개씩 있는지 → {"train|11545": n, …}.

    **어떤 층의 train이 0이면 그 fold는 다른 fold와 다른 것을 재고 있다** — 구 간
    외삽(강남으로 배우고 금천을 맞히기)이지 구 안 홀드아웃이 아니다. fold 평균을
    내면 그 fold가 평균을 오염시킨다. 층화 배정이 이걸 막지만, 막혔는지는 실측으로
    확인한다(배치가 매번 본다).
    """
    counts: dict[str, int] = defaultdict(int)
    for a in fold.assignments:
        counts[f"{a.split_role}|{a.pnu[:stratum_len]}"] += 1
    return dict(counts)


def missing_train_strata(fold: Fold, *, stratum_len: int = SGG_CODE_LEN) -> tuple[str, ...]:
    """이 fold의 test에는 있는데 train에는 없는 층. 비어 있어야 정상이다."""
    counts = check_stratum_coverage(fold, stratum_len=stratum_len)
    train = {k.split("|", 1)[1] for k in counts if k.startswith("train|")}
    test = {k.split("|", 1)[1] for k in counts if k.startswith("test|")}
    return tuple(sorted(test - train))


def check_leakage(fold: Fold, rows: list[PanelObs] | None = None) -> LeakageReport:
    """fold를 훑어 누출 성질을 확인한다. rows를 주면 격자 겹침까지 본다."""
    train_blocks: set[str] = set()
    test_blocks: set[str] = set()
    train_years: list[int] = []
    test_years: list[int] = []
    train_keys: set[tuple[str, int]] = set()
    test_keys: set[tuple[str, int]] = set()
    for a in fold.assignments:
        if a.split_role == TRAIN:
            train_blocks.add(a.spatial_block)
            train_years.append(a.base_year)
            train_keys.add((a.pnu, a.base_year))
        else:
            test_blocks.add(a.spatial_block)
            test_years.append(a.base_year)
            test_keys.add((a.pnu, a.base_year))

    shared_cells = 0
    if rows is not None:
        cell = {(r.pnu, r.base_year): grid_cell_key(r.x, r.y) for r in rows}
        tr_cells = {cell[k] for k in train_keys if k in cell}
        te_cells = {cell[k] for k in test_keys if k in cell}
        shared_cells = len(tr_cells & te_cells)

    return LeakageReport(
        fold_id=fold.fold_id,
        n_train=len(train_years),
        n_test=len(test_years),
        shared_blocks=tuple(sorted(train_blocks & test_blocks)),
        shared_grid_cells=shared_cells,
        max_train_year=max(train_years) if train_years else None,
        min_test_year=min(test_years) if test_years else None,
    )
