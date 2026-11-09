import asyncio

import pytest

from app.services.kakao_place_enrich import (
    CompetitorEnrichment,
    _build_tagline,
    _extract_photo_url,
    _parse_panel,
    _pick_best_match,
    enrich_competitor,
    is_allowed_photo_url,
    proxied_photo_url,
)


class TestPickBestMatch:
    def test_prefers_exact_name(self):
        docs = [
            {"place_name": "다른카페", "distance": "10"},
            {"place_name": "메가커피 신촌점", "distance": "30"},
        ]
        picked = _pick_best_match(docs, "메가커피 신촌점")
        assert picked["place_name"] == "메가커피 신촌점"


class TestParsePanel:
    def test_extracts_rating_and_photo(self):
        panel = {
            "kakaomap_review": {"score": 4.2, "review_count": 128},
            "photos": {"list": [{"url": "https://example.com/photo.jpg"}]},
            "summary": {"intro": "조용한 공부 카페"},
        }
        rating, review_count, photo_url = _parse_panel(panel)
        assert rating == 4.2
        assert review_count == 128
        assert photo_url == "https://example.com/photo.jpg"


class TestExtractPhotoUrl:
    def test_prefers_poi_over_review_vod(self):
        panel = {
            "photos": {
                "photos": [
                    {
                        "type": "KMAPREVIEW",
                        "media_type": "VOD",
                        "url": "https://thumb.kakaocdn.net/review-vod.jpg",
                    },
                    {
                        "type": "POI",
                        "media_type": "PHOTO",
                        "url": "http://t1.daumcdn.net/localfiy/shop.jpg",
                    },
                ]
            }
        }
        assert _extract_photo_url(panel) == "https://t1.daumcdn.net/localfiy/shop.jpg"

    def test_reads_photos_list_and_main_photo(self):
        panel = {
            "my_store": {"main_photo_url": "http://t1.daumcdn.net/localfiy/main.jpg"},
            "photos": {"photosList": [{"url": "https://thumb.kakaocdn.net/other.jpg"}]},
        }
        assert _extract_photo_url(panel) == "https://t1.daumcdn.net/localfiy/main.jpg"


class TestPhotoProxy:
    def test_allowed_host_and_proxy_path(self):
        url = "https://t1.daumcdn.net/localfiy/abc.jpg"
        assert is_allowed_photo_url(url)
        assert proxied_photo_url(url).startswith("/api/place-photo?url=")

    def test_rejects_unknown_host(self):
        assert not is_allowed_photo_url("https://evil.example/photo.jpg")
        assert proxied_photo_url("https://evil.example/photo.jpg") is None


class TestBuildTagline:
    def test_uses_intro_and_category(self):
        tagline = _build_tagline(
            kakao_category="음식점 > 카페 > 커피전문점",
            sbiz_category="카페",
            panel={"summary": {"intro": "빈티지 인테리어 카페"}},
            phone="02-123-4567",
        )
        assert "빈티지" in tagline
        assert "☎" in tagline


def test_enrich_competitor_without_api_key(monkeypatch):
    monkeypatch.setattr("app.services.kakao_place_enrich.settings.kakao_rest_key", "")
    result = asyncio.run(
        enrich_competitor(
            name="테스트카페",
            category="카페",
            lat=37.5,
            lng=126.9,
        )
    )
    assert result == CompetitorEnrichment()


def test_enrich_competitor_with_mocked_kakao(monkeypatch):
    monkeypatch.setattr(
        "app.services.kakao_place_enrich.settings.kakao_rest_key",
        "test-key",
    )

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class FakeClient:
        async def get(self, url, headers=None, params=None):
            if "keyword" in url:
                return FakeResponse(
                    200,
                    {
                        "documents": [
                            {
                                "id": "12345",
                                "place_name": "메가커피",
                                "category_name": "음식점 > 카페",
                                "phone": "02-111-2222",
                                "place_url": "https://place.map.kakao.com/12345",
                                "distance": "42",
                            }
                        ]
                    },
                )
            if "panel3" in url:
                return FakeResponse(
                    200,
                    {
                        "kakaomap_review": {"score": 3.8, "review_count": 55},
                        "photos": {
                            "photos": [
                                {
                                    "type": "POI",
                                    "media_type": "PHOTO",
                                    "url": "https://t1.daumcdn.net/localfiy/a.jpg",
                                }
                            ]
                        },
                        "summary": {"intro": "저가 커피 전문"},
                    },
                )
            return FakeResponse(404, {})

        async def aclose(self):
            return None

    result = asyncio.run(
        enrich_competitor(
            name="메가커피",
            category="카페",
            lat=37.5,
            lng=126.9,
            client=FakeClient(),
        )
    )
    assert result.kakao_place_id == "12345"
    assert result.rating == 3.8
    assert result.review_count == 55
    assert result.photo_url == "https://t1.daumcdn.net/localfiy/a.jpg"
    assert "저가 커피" in (result.tagline or "")
