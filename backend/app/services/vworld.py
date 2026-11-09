"""V-world WFS client for land-use zone lookup."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.config import settings

WFS_URL = "https://api.vworld.kr/req/wfs"


class VWorldError(Exception):
    pass


@dataclass
class LandUseZone:
    code: str
    name: str


async def lookup_land_use_zone(lng: float, lat: float) -> LandUseZone | None:
    if not settings.vworld_api_key:
        return None

    params = {
        "service": "WFS",
        "version": "1.1.0",
        "request": "GetFeature",
        "typename": "lt_c_landinfobasemap",
        "srsname": "EPSG:4326",
        "bbox": f"{lng},{lat},{lng},{lat}",
        "key": settings.vworld_api_key,
        "domain": settings.vworld_domain or "http://localhost:8000",
        "output": "text/xml; subtype=gml/2.1.2",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(WFS_URL, params=params)

    if response.status_code != 200:
        raise VWorldError(f"V-world WFS failed: HTTP {response.status_code}")

    text = response.text
    if "unimk" not in text and "UNMK" not in text:
        return None

    code = "unknown"
    name = "unknown"
    for line in text.splitlines():
        if "unimk" in line.lower():
            start = line.find(">")
            end = line.rfind("<")
            if start >= 0 and end > start:
                code = line[start + 1 : end].strip() or code
        if "unimk_nm" in line.lower() or "uname" in line.lower():
            start = line.find(">")
            end = line.rfind("<")
            if start >= 0 and end > start:
                name = line[start + 1 : end].strip() or name

    return LandUseZone(code=code, name=name)
