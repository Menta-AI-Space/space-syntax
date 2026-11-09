import pytest

from tools.precompute_analysis_cache import (
    iter_grid_points,
    validate_warm_response,
    warm_points,
)


def test_iter_grid_points_stays_inside_bbox():
    bbox = (126.90, 37.50, 126.902, 37.502)

    points = list(iter_grid_points(bbox, step_m=100))

    assert points
    assert all(bbox[0] <= lng <= bbox[2] for lng, _lat in points)
    assert all(bbox[1] <= lat <= bbox[3] for _lng, lat in points)


def test_warm_points_requests_each_radius_and_mode():
    calls = []

    def post(payload):
        calls.append(payload)
        return {"segment_count": 10}

    summary = warm_points(
        points=[(126.9, 37.5)],
        radii=[500, 1000],
        modes=["walk"],
        post=post,
    )

    assert [call["radius_m"] for call in calls] == [500, 1000]
    assert summary == {"requested": 2, "completed": 2, "failed": 0}


def test_validate_warm_response_rejects_python_fallback():
    response = {
        "engines": {
            "depthmapx": {"available": False},
            "cache": {"hit": False, "stored": False, "engine": "python"},
        }
    }

    with pytest.raises(ValueError, match="depthmapx"):
        validate_warm_response(response, "depthmapx")


def test_validate_warm_response_accepts_stored_or_matching_cache_hit():
    cold = {
        "engines": {
            "depthmapx": {"available": True},
            "cache": {"hit": False, "stored": True, "engine": "depthmapx"},
        }
    }
    hit = {
        "engines": {
            "cache": {"hit": True, "engine": "depthmapx"},
        }
    }

    validate_warm_response(cold, "depthmapx")
    validate_warm_response(hit, "depthmapx")
