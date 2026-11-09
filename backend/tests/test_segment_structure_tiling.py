"""compute_segment_structure의 순수 함수 — DB 없이 도는 부분만."""

import math

from tools.compute_segment_structure import (
    ANALYSIS_RADIUS_M,
    HALO_M,
    fetch_radius_for,
    in_core,
    midpoint,
    seg_key,
    tile_grid,
)

SEOUL_BBOX = (126.76, 37.42, 127.19, 37.70)


# ── seg_key: 타일이 달라도 같은 세그먼트면 같은 키 ────────────────────────

def test_seg_key_is_stable_for_same_geometry():
    coords = [[127.0, 37.5], [127.001, 37.501]]
    assert seg_key(123, coords) == seg_key(123, [list(c) for c in coords])


def test_seg_key_ignores_float_noise_below_1cm():
    a = seg_key(123, [[127.0, 37.5], [127.001, 37.501]])
    b = seg_key(123, [[127.00000001, 37.5], [127.001, 37.501]])
    assert a == b


def test_seg_key_differs_for_different_geometry():
    a = seg_key(123, [[127.0, 37.5], [127.001, 37.501]])
    b = seg_key(123, [[127.0, 37.5], [127.002, 37.502]])
    assert a != b


def test_seg_key_differs_for_same_geometry_under_different_osm_id():
    coords = [[127.0, 37.5], [127.001, 37.501]]
    assert seg_key(1, coords) != seg_key(2, coords)


# ── midpoint: 결정론적이어야 한다 ─────────────────────────────────────────

def test_midpoint_of_odd_vertex_count_is_the_middle_vertex():
    assert midpoint([[0.0, 0.0], [1.0, 2.0], [10.0, 10.0]]) == (1.0, 2.0)


def test_midpoint_of_even_vertex_count_averages_the_two_middle():
    assert midpoint([[0.0, 0.0], [1.0, 1.0], [3.0, 3.0], [4.0, 4.0]]) == (2.0, 2.0)


def test_midpoint_of_two_vertices():
    assert midpoint([[0.0, 0.0], [2.0, 4.0]]) == (1.0, 2.0)


# ── fetch 반경: 코어 어디서든 halo가 확보돼야 한다 ─────────────────────────

def test_fetch_radius_covers_core_half_diagonal_plus_halo():
    tile_m = 800
    r = fetch_radius_for(tile_m)
    assert r >= tile_m * math.sqrt(2) / 2 + HALO_M


def test_halo_is_not_smaller_than_analysis_radius():
    """halo < 분석반경이면 코어 세그먼트의 반경이 잘려 값이 과소평가된다."""
    assert HALO_M >= ANALYSIS_RADIUS_M


def test_worst_case_core_point_still_has_full_analysis_radius():
    """코어 모서리(중심에서 가장 먼 점)에서도 분석 반경이 온전한지."""
    tile_m = 800
    r = fetch_radius_for(tile_m)
    farthest_core_point = tile_m * math.sqrt(2) / 2
    assert farthest_core_point + ANALYSIS_RADIUS_M <= r


# ── 타일 격자: 겹치지 않고 빠짐없이 덮어야 한다 ───────────────────────────

def test_tile_grid_covers_the_whole_bbox():
    tiles = tile_grid(SEOUL_BBOX, 800)
    min_lng, min_lat, max_lng, max_lat = SEOUL_BBOX
    assert min(t["lng0"] for t in tiles) <= min_lng
    assert min(t["lat0"] for t in tiles) <= min_lat
    assert max(t["lng1"] for t in tiles) >= max_lng
    assert max(t["lat1"] for t in tiles) >= max_lat


def test_tile_keys_are_unique():
    tiles = tile_grid(SEOUL_BBOX, 800)
    assert len({t["key"] for t in tiles}) == len(tiles)


def test_every_point_falls_in_exactly_one_core():
    """세그먼트 중복 저장을 막는 핵심 성질 — 경계 위 점도 딱 하나에만 들어간다."""
    tiles = tile_grid(SEOUL_BBOX, 800)
    # 격자 경계에 정확히 놓인 점들을 포함해서 확인한다
    probes = [
        (tiles[0]["lng0"], tiles[0]["lat0"]),
        (tiles[0]["lng1"], tiles[0]["lat1"]),
        (127.0, 37.55),
        (126.9779, 37.5665),
    ]
    for lng, lat in probes:
        hits = [t for t in tiles if in_core(t, lng, lat)]
        assert len(hits) == 1, f"({lng}, {lat}) → {len(hits)}개 타일"


def test_in_core_includes_lower_left_and_excludes_upper_right():
    tile = tile_grid(SEOUL_BBOX, 800)[0]
    assert in_core(tile, tile["lng0"], tile["lat0"])
    assert not in_core(tile, tile["lng1"], tile["lat1"])


def test_smaller_tiles_produce_more_tiles():
    assert len(tile_grid(SEOUL_BBOX, 400)) > len(tile_grid(SEOUL_BBOX, 800))


def test_tile_center_is_inside_its_own_core():
    for tile in tile_grid(SEOUL_BBOX, 800)[:50]:
        assert in_core(tile, tile["center_lng"], tile["center_lat"])
