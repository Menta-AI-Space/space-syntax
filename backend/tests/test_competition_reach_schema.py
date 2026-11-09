import pytest
from pydantic import ValidationError

from app.models.schemas import DashboardPointRequest


def test_moto_allows_25_minutes():
    req = DashboardPointRequest(lat=37.5, lng=127.0, mode="moto", competitor_minutes=25)
    assert req.competitor_minutes == 25


def test_walk_rejects_20_minutes():
    with pytest.raises(ValidationError):
        DashboardPointRequest(lat=37.5, lng=127.0, mode="walk", competitor_minutes=20)
