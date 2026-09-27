import asyncio

from app.core.settings import UserSettings
from app.services.recommendation.metadata import RecommendationMetadata


def details(tmdb_id: int, original_language: str | None, spoken: list[str]) -> dict:
    """TMDB /movie/{id} details, as get_movie_details returns them."""
    return {
        "id": tmdb_id,
        "title": f"Title {tmdb_id}",
        "external_ids": {"imdb_id": f"tt{tmdb_id:07d}"},
        "original_language": original_language,
        "spoken_languages": [{"iso_639_1": code, "english_name": code} for code in spoken],
        "genres": [],
        "release_date": "2023-09-21",
    }


class FakeTmdb:
    def __init__(self, *titles: dict):
        self.titles = {title["id"]: title for title in titles}

    async def get_movie_details(self, movie_id):
        return self.titles[movie_id]

    async def get_images_for_title(self, media_type, tmdb_id, language=None):
        return {}


def enriched_ids(titles: list[dict], allowed: list[str] | None) -> list[int]:
    settings = None if allowed is None else UserSettings(catalogs=[], allowed_languages=allowed)
    candidates = [{"id": title["id"]} for title in titles]
    metas = asyncio.run(RecommendationMetadata.fetch_batch(FakeTmdb(*titles), candidates, "movie", settings))
    return sorted(meta["_tmdb_id"] for meta in metas)


def test_a_title_spoken_only_in_another_language_is_dropped_despite_its_label():
    """TMDB lists "¿Quieres ser mi hijo?" with original_language "en" but spoken only in
    Spanish, so the earlier original_language filter lets it through."""
    mislabeled = details(1181678, "en", ["es"])

    assert enriched_ids([mislabeled], ["en", "fr"]) == []


def test_a_title_with_any_allowed_spoken_language_is_kept():
    assert enriched_ids([details(1, "en", ["en", "es"])], ["en", "fr"]) == [1]


def test_original_language_decides_when_no_spoken_languages_are_listed():
    titles = [details(1, "fr", []), details(2, "ko", [])]

    assert enriched_ids(titles, ["en", "fr"]) == [1]


def test_a_title_with_no_language_information_is_kept():
    assert enriched_ids([details(1, None, [])], ["en"]) == [1]


def test_every_title_is_kept_without_allowed_languages():
    titles = [details(1, "en", ["es"]), details(2, "ko", ["ko"])]

    assert enriched_ids(titles, []) == [1, 2]
    assert enriched_ids(titles, None) == [1, 2]
