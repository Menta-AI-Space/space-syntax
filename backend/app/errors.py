"""도로망 조회 실패를 사용자 말로 바꾸는 공통 처리.

같은 상황을 라우터마다 따로 적었더니 문구가 어긋났다 — 입지 분석은 한국어인데
신설도로 시뮬레이션은 "Simulation used the nearest 1000 of 1398 road segments."
처럼 영어가 그대로 떴고, 좌표가 지구 밖일 때는 PostGIS 예외("transform:
latitude or longitude exceeded limits")가 통째로 화면에 실렸다. 문구를 여기
한 곳에 둔다.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def road_fetch_error(exc: Exception, *, lat: float | None = None, lng: float | None = None) -> HTTPException:
    """도로망 조회가 터졌을 때 돌려줄 예외.

    좌표가 지구 밖이면 PostGIS가 transform 오류를 던지는데, 이건 서버 사정이
    아니라 잘못 들어온 입력이므로 400으로 돌려준다. 나머지는 503을 유지하되
    내부 예외 문구 대신 사람 말로 안내하고 원문은 로그에만 남긴다.
    """
    logger.warning("road fetch failed at (%s, %s): %s", lat, lng, exc)
    text = str(exc).lower()
    if "exceeded limits" in text or "transform" in text:
        return HTTPException(status_code=400, detail="좌표가 올바르지 않습니다.")
    return HTTPException(
        status_code=503,
        detail="분석 데이터를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.",
    )


def no_roads_error() -> HTTPException:
    """반경 안에 도로가 하나도 없을 때 — 서울 밖을 찍은 경우가 대부분이다."""
    return HTTPException(
        status_code=404,
        detail=(
            "이 지점 주변에서 도로망을 찾지 못했습니다. "
            "지금은 서울 지역만 분석할 수 있어요."
        ),
    )


def too_many_segments_error(count: int) -> HTTPException:
    """반경이 넓어 도로가 너무 많을 때 — 사용자가 할 수 있는 일을 알려준다."""
    return HTTPException(
        status_code=422,
        detail=f"이 반경은 도로가 너무 많아({count:,}개) 분석하지 못했습니다. 반경을 줄여 주세요.",
    )


def analysis_failed_error(exc: BaseException, *, what: str = "분석") -> HTTPException:
    """계산 중 터졌을 때 — 예외 원문은 로그에만 남기고 사람 말로 안내한다.

    'Graph construction failed: <파이썬 예외>'처럼 내부 사정을 그대로 내보내던
    자리를 대신한다.
    """
    logger.warning("%s failed: %s", what, exc)
    return HTTPException(
        status_code=503,
        detail=f"{what}에 실패했습니다. 잠시 후 다시 시도해 주세요.",
    )


def segment_cap_warning(used: int, total: int) -> str:
    """분석 속도를 위해 도로를 잘라 썼을 때의 안내 — 화면마다 같은 말이어야 한다."""
    return f"빠른 분석을 위해 가까운 도로 {used:,}개만 사용했습니다 (전체 {total:,}개)."
