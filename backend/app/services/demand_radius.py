"""Scale SGIS 행정동 demand stats to the analysis radius.

SGIS only exposes full-dong totals in our current integration. We estimate
반경 Nm 수요 by applying the dong's population density to a circular catchment
(πr²), capped at the dong total — the same density proxy used in isochrone.py.
Age/gender/housing mix percentages stay dong-level (structure); absolute counts
are scaled proportionally.
"""

from __future__ import annotations

import math
from dataclasses import replace

from app.services.sgis import (
    AgeBand,
    DemandProfile,
    GenderStats,
    HousingMix,
    IndustryStats,
)
from app.services.sgis_grid import (
    GridRadiusStats,
    grid_population_density,
    is_grid_data_available,
    query_grid_radius,
    resolution_label,
)

# Urban fallback when SGIS does not return ppltn_dnsty (people/km²).
_DEFAULT_DENSITY_PER_KM2 = 12_000.0


def _circle_area_km2(radius_m: int) -> float:
    return math.pi * (radius_m / 1000) ** 2


def _scale_int(value: int | None, ratio: float) -> int | None:
    if value is None:
        return None
    return max(0, int(round(value * ratio)))


def _dong_area_km2(
    dong_population: int | None,
    population_density: float | None,
) -> float | None:
    """Best-effort 행정동 면적 (km²) from SGIS population and density."""
    if dong_population and dong_population > 0:
        if population_density and population_density > 0:
            return dong_population / population_density
        return dong_population / _DEFAULT_DENSITY_PER_KM2
    return None


def _estimate_dong_area_km2(population: int, density: float | None) -> float:
    return _dong_area_km2(population, density) or (population / _DEFAULT_DENSITY_PER_KM2)


def radius_coverage_ratio(
    *,
    radius_m: int,
    dong_population: int | None,
    population_density: float | None,
) -> float:
    """Fraction of dong population attributed to a circular catchment (0–1)."""
    if radius_m <= 0:
        return 0.0
    circle_km2 = _circle_area_km2(radius_m)
    if dong_population and dong_population > 0:
        if population_density and population_density > 0:
            density_est = min(dong_population, int(population_density * circle_km2))
            return min(1.0, density_est / dong_population)
        dong_km2 = _estimate_dong_area_km2(dong_population, population_density)
        if dong_km2 > 0:
            return min(1.0, circle_km2 / dong_km2)
    if population_density and population_density > 0:
        # No dong total — treat density estimate as standalone (ratio = 1 for display)
        return 1.0
    return min(1.0, circle_km2 / 2.0)  # ~2 km² typical urban dong fallback


def estimate_radius_population(
    *,
    radius_m: int,
    dong_population: int | None,
    population_density: float | None,
) -> int | None:
    if dong_population is None and population_density is None:
        return None
    circle_km2 = _circle_area_km2(radius_m)
    if population_density and population_density > 0:
        est = int(population_density * circle_km2)
        if dong_population is not None:
            return min(est, dong_population)
        return est
    if dong_population is not None:
        ratio = radius_coverage_ratio(
            radius_m=radius_m,
            dong_population=dong_population,
            population_density=population_density,
        )
        return max(0, int(round(dong_population * ratio)))
    return None


def estimate_radius_workers(
    *,
    radius_m: int,
    dong_workers: int | None,
    dong_population: int | None,
    population_density: float | None,
) -> int | None:
    """Estimate workers in a circular catchment using the same area proxy as population."""
    if dong_workers is None:
        return None
    if radius_m <= 0:
        return 0

    circle_km2 = _circle_area_km2(radius_m)
    dong_area = _dong_area_km2(dong_population, population_density)
    if dong_area and dong_area > 0:
        est = int(round((dong_workers / dong_area) * circle_km2))
        return min(max(0, est), dong_workers)

    ratio = radius_coverage_ratio(
        radius_m=radius_m,
        dong_population=dong_population,
        population_density=population_density,
    )
    return max(0, int(round(dong_workers * ratio)))


def _is_commercial_dong(
    dong_population: int | None,
    dong_workers: int | None,
) -> bool:
    """True when the 행정동 has more jobs than residents (typical commercial cores)."""
    return (
        dong_population is not None
        and dong_population > 0
        and dong_workers is not None
        and dong_workers > dong_population
    )


def scale_demand_profile_to_radius(
    profile: DemandProfile,
    radius_m: int,
    *,
    lat: float | None = None,
    lng: float | None = None,
) -> DemandProfile:
    """Return a copy with counts scaled to analysis radius; mix % unchanged.

    When ``SGIS_GRID_DATA_DIR`` is configured and lat/lng are provided, sums
    local grid cells intersecting the circle instead of the density proxy.
    """
    if radius_m <= 0:
        return profile

    if lat is not None and lng is not None and is_grid_data_available():
        grid_stats = query_grid_radius(lat, lng, radius_m)
        if grid_stats is not None:
            return _apply_grid_stats_to_profile(profile, radius_m, grid_stats)

    dong_pop = profile.population
    dong_workers = profile.workers
    dong_households = profile.households

    density = profile.population_density
    est_pop = estimate_radius_population(
        radius_m=radius_m,
        dong_population=dong_pop,
        population_density=density,
    )
    if est_pop is None and dong_pop is None:
        return replace(
            profile,
            data_scope=_radius_data_scope(radius_m, profile.stats_year, method="no_data"),
        )

    est_workers = estimate_radius_workers(
        radius_m=radius_m,
        dong_workers=dong_workers,
        dong_population=dong_pop,
        population_density=density,
    )

    commercial = _is_commercial_dong(dong_pop, dong_workers)
    if (
        est_workers is not None
        and est_pop is not None
        and est_workers > est_pop
        and not commercial
    ):
        # Residential-dominant dong: uniform area share should not invert workers > residents.
        est_workers = est_pop

    ratio = 1.0
    if dong_pop and dong_pop > 0 and est_pop is not None:
        ratio = min(1.0, est_pop / dong_pop)

    est_households = _scale_int(dong_households, ratio)
    est_companies = _scale_int(profile.companies, ratio)

    age_bands = _scale_age_bands(profile.age_bands, ratio)
    gender = _scale_gender(profile.gender, ratio)
    housing = _scale_housing(profile.housing, ratio)
    industries = _scale_industries(profile.industries, ratio)

    method = "density" if density else "area_ratio"
    scope = _radius_data_scope(
        radius_m,
        profile.stats_year,
        method=method,
        commercial=commercial and est_workers is not None and est_pop is not None and est_workers > est_pop,
    )

    return replace(
        profile,
        population=est_pop if est_pop is not None else _scale_int(dong_pop, ratio),
        workers=est_workers,
        households=est_households,
        companies=est_companies,
        age_bands=age_bands,
        gender=gender,
        housing=housing,
        industries=industries,
        data_scope=scope,
    )


def _apply_grid_stats_to_profile(
    profile: DemandProfile,
    radius_m: int,
    grid: GridRadiusStats,
) -> DemandProfile:
    """Replace radius counts with grid sums; recompute age/gender from grid."""
    pop = grid.population
    workers = grid.workers
    households = grid.households
    companies = grid.companies

    commercial = workers > pop and pop > 0

    age_bands = _age_bands_from_grid(grid.age_band_counts, pop)
    gender = _gender_from_grid(grid.male, grid.female, pop)

    ratio = 1.0
    if profile.population and profile.population > 0 and pop > 0:
        ratio = min(1.0, pop / profile.population)

    housing = _scale_housing(profile.housing, ratio)
    industries = _scale_industries(profile.industries, ratio)

    density = grid_population_density(pop, radius_m)
    tile_note = ", ".join(grid.tiles)
    pop_res = resolution_label(grid.pop_resolution_m)
    detail_res = resolution_label(grid.detail_resolution_m)
    workers_res = resolution_label(grid.workers_resolution_m)
    res_note = (
        f"인구 {pop_res}"
        if pop_res == detail_res
        else f"인구 {pop_res}·가구·사업체 {detail_res}"
    )
    if workers_res != detail_res:
        res_note += f"·종사자 {workers_res}"
    scope = (
        f"반경 {radius_m}m SGIS 격자 합산 ({res_note}, "
        f"인구·가구 {grid.stats_year_pop}, 종사·사업체 {grid.stats_year_workers}, "
        f"{grid.grid_cells}셀·{tile_note})"
    )
    if commercial and workers > pop:
        scope += ". 종사자>거주인구는 상권·직장 밀집(통근) 특성 반영"

    return replace(
        profile,
        population=pop,
        workers=workers,
        households=households,
        companies=companies,
        population_density=density,
        age_bands=age_bands or profile.age_bands,
        gender=gender or profile.gender,
        housing=housing,
        industries=industries,
        data_scope=scope,
    )


def _age_bands_from_grid(
    band_counts: dict[str, int],
    total_pop: int,
) -> list[AgeBand]:
    if not band_counts or total_pop <= 0:
        return []
    bands: list[AgeBand] = []
    for label in (
        "10세 미만",
        "10대",
        "20대",
        "30대",
        "40대",
        "50대",
        "60대",
        "70세+",
    ):
        count = band_counts.get(label, 0)
        if count <= 0:
            continue
        bands.append(
            AgeBand(
                band=label,
                count=count,
                pct=round(100.0 * count / total_pop, 1),
            )
        )
    return bands


def _gender_from_grid(
    male: int,
    female: int,
    total_pop: int,
) -> GenderStats | None:
    if male <= 0 and female <= 0:
        return None
    total = male + female
    if total <= 0:
        return None
    return GenderStats(
        male_pct=round(100.0 * male / total, 1),
        female_pct=round(100.0 * female / total, 1),
        male_count=male,
        female_count=female,
    )


def _radius_data_scope(
    radius_m: int,
    stats_year: str | None,
    *,
    method: str,
    commercial: bool = False,
) -> str:
    year = stats_year or "최근"
    if method == "no_data":
        return f"반경 {radius_m}m (SGIS 데이터 없음)"
    base = (
        f"반경 {radius_m}m 원형 추정 (SGIS {year} 행정동 밀도×πr², 행정동 총량 상한)"
        if method == "density"
        else f"반경 {radius_m}m 원형 추정 (행정동 면적 대비 비율, {year})"
    )
    if commercial:
        return (
            f"{base}. 종사자>거주인구는 상권·직장 밀집(통근) 특성 반영"
        )
    return base


def _scale_age_bands(bands: list[AgeBand], ratio: float) -> list[AgeBand]:
    if ratio >= 0.999 or not bands:
        return list(bands)
    return [
        AgeBand(band=b.band, count=_scale_int(b.count, ratio) or 0, pct=b.pct)
        for b in bands
    ]


def _scale_gender(gender: GenderStats | None, ratio: float) -> GenderStats | None:
    if gender is None or ratio >= 0.999:
        return gender
    return GenderStats(
        male_pct=gender.male_pct,
        female_pct=gender.female_pct,
        male_count=_scale_int(gender.male_count, ratio),
        female_count=_scale_int(gender.female_count, ratio),
    )


def _scale_housing(housing: HousingMix | None, ratio: float) -> HousingMix | None:
    if housing is None or ratio >= 0.999:
        return housing
    return HousingMix(
        apartment_pct=housing.apartment_pct,
        officetel_pct=housing.officetel_pct,
        row_house_pct=housing.row_house_pct,
        detached_pct=housing.detached_pct,
        apartment_count=_scale_int(housing.apartment_count, ratio),
        officetel_count=_scale_int(housing.officetel_count, ratio),
    )


def _scale_industries(
    industries: list[IndustryStats], ratio: float
) -> list[IndustryStats]:
    if ratio >= 0.999 or not industries:
        return list(industries)
    return [
        IndustryStats(
            code=i.code,
            name=i.name,
            companies=_scale_int(i.companies, ratio) or 0,
            workers=_scale_int(i.workers, ratio) or 0,
        )
        for i in industries
    ]
