"""Franchise-brand autocomplete + detail endpoints.

Backed by the local `franchise_brands` table (공정위 가맹정보공개서 snapshot,
loaded by `python -m tools.ingest_franchise_brands`). Models live here on
purpose — schemas.py is shared and owned by another workstream.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.services.spatial_query import get_connection

router = APIRouter(prefix="/api/franchise", tags=["franchise"])


class FranchiseBrandItem(BaseModel):
    brand_nm: str
    industry_mid: str | None = None
    hq_conm: str | None = None
    frcs_cnt: int | None = None
    yr: str


class FranchiseBrandListOut(BaseModel):
    brands: list[FranchiseBrandItem]


class FranchiseBrandDetailOut(BaseModel):
    brand_nm: str
    industry_mid: str | None = None
    hq_conm: str | None = None
    yr: str
    frcs_cnt: int | None = None
    new_open_cnt: int | None = None
    contract_end_cnt: int | None = None
    contract_cancel_cnt: int | None = None
    churn_pct: float | None = None
    startup_cost_total_manwon: float | None = None


def _table_missing(exc: Exception) -> bool:
    # psycopg2.errors.UndefinedTable without importing the errors module at
    # call sites; pgcode 42P01 = undefined_table (ingest not run yet).
    return getattr(exc, "pgcode", None) == "42P01"


def _search_brands(q: str, limit: int) -> list[dict]:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT brand_nm, industry_mid, hq_conm, frcs_cnt, yr
                    FROM franchise_brands
                    WHERE brand_nm ILIKE %s
                    ORDER BY frcs_cnt DESC NULLS LAST, brand_nm
                    LIMIT %s
                    """,
                    (f"%{q}%", limit),
                )
                rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001 - only swallow "table missing"
        if _table_missing(exc):
            return []
        raise
    return [
        {"brand_nm": r[0], "industry_mid": r[1], "hq_conm": r[2] or None,
         "frcs_cnt": r[3], "yr": r[4]}
        for r in rows
    ]


def _brand_detail(name: str) -> dict | None:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Same brand_nm can have one row per snapshot year, so picking
                # a brand first and its year second keeps us from mixing an
                # old year's churn/cost numbers with a newer frcs_cnt (or
                # vice versa) into one Frankenstein row.
                cur.execute(
                    "SELECT 1 FROM franchise_brands WHERE brand_nm = %s LIMIT 1",
                    (name,),
                )
                if cur.fetchone() is not None:
                    target_nm = name
                else:
                    # No exact match; fall back to the most prominent brand
                    # whose name contains the query.
                    cur.execute(
                        """
                        SELECT brand_nm
                        FROM franchise_brands
                        WHERE brand_nm ILIKE %s
                        ORDER BY frcs_cnt DESC NULLS LAST, brand_nm
                        LIMIT 1
                        """,
                        (f"%{name}%",),
                    )
                    match = cur.fetchone()
                    if match is None:
                        return None
                    target_nm = match[0]

                # Latest snapshot year for that brand wins, not the year with
                # the largest frcs_cnt.
                cur.execute(
                    """
                    SELECT brand_nm, industry_mid, hq_conm, yr, frcs_cnt,
                           new_open_cnt, contract_end_cnt, contract_cancel_cnt,
                           startup_cost_total_manwon
                    FROM franchise_brands
                    WHERE brand_nm = %s
                    ORDER BY yr DESC, frcs_cnt DESC NULLS LAST
                    LIMIT 1
                    """,
                    (target_nm,),
                )
                row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001 - only swallow "table missing"
        if _table_missing(exc):
            return None
        raise
    if row is None:
        return None
    frcs_cnt = row[4]
    end_cnt = row[6] or 0
    cancel_cnt = row[7] or 0
    churn_pct = round((end_cnt + cancel_cnt) / max(frcs_cnt or 0, 1) * 100, 1)
    return {
        "brand_nm": row[0],
        "industry_mid": row[1],
        "hq_conm": row[2] or None,
        "yr": row[3],
        "frcs_cnt": frcs_cnt,
        "new_open_cnt": row[5],
        "contract_end_cnt": row[6],
        "contract_cancel_cnt": row[7],
        "churn_pct": churn_pct,
        "startup_cost_total_manwon": float(row[8]) if row[8] is not None else None,
    }


@router.get("/brands", response_model=FranchiseBrandListOut)
async def franchise_brands(
    q: str = Query(""),
    limit: int = Query(10, ge=1, le=50),
):
    q = q.strip()
    if len(q) < 1:
        return FranchiseBrandListOut(brands=[])
    rows = await run_in_threadpool(_search_brands, q, limit)
    return FranchiseBrandListOut(brands=[FranchiseBrandItem(**r) for r in rows])


@router.get("/brand-detail", response_model=FranchiseBrandDetailOut)
async def franchise_brand_detail(name: str = Query(..., min_length=1)):
    detail = await run_in_threadpool(_brand_detail, name.strip())
    if detail is None:
        raise HTTPException(status_code=404, detail=f"brand not found: {name}")
    return FranchiseBrandDetailOut(**detail)
