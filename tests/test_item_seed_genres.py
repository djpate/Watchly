import asyncio
from datetime import datetime, timezone

import pytest

from app.core.settings import CatalogConfig, UserSettings
from app.models.library import LibraryCollection, StremioLibraryItem, StremioState
from app.services.catalog_definitions import DynamicCatalogService

ANIMATION, HORROR = 16, 27

LEGO = ("tt43679852", "LEGO Star Wars: The Mandalorian", ANIMATION)
READY_OR_NOT = ("tt7798634", "Ready or Not", HORROR)


class FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value, ttl=None):
        self.data[key] = value
        return True

    async def delete(self, key: str):
        self.data.pop(key, None)

    async def expire(self, key: str, ttl: int):
        return True

    async def delete_by_pattern(self, pattern: str):
        return 0


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    fake = FakeRedis()
    for name in ("get", "set", "delete", "expire", "delete_by_pattern"):
        monkeypatch.setattr(f"app.services.user_cache.redis_service.{name}", getattr(fake, name))


class FakeTmdb:
    """TMDB as the seed lookup sees it: an IMDb id resolves, and details carry genres."""

    def __init__(self, titles, down=False):
        self.genres = {index: genre for index, (_, _, genre) in enumerate(titles, start=1)}
        self.ids = {imdb_id: index for index, (imdb_id, _, _) in enumerate(titles, start=1)}
        self.down = down

    async def find_by_imdb_id(self, imdb_id):
        return self.ids.get(imdb_id), "movie"

    async def get_movie_details(self, movie_id):
        if self.down:
            raise ConnectionError("TMDB unreachable")
        return {"id": movie_id, "genres": [{"id": self.genres[movie_id], "name": "genre"}]}


def loved(*titles) -> LibraryCollection:
    return LibraryCollection(
        loved=[
            StremioLibraryItem(
                _id=imdb_id,
                type="movie",
                name=name,
                state=StremioState(lastWatched=datetime(2026, 9, day, tzinfo=timezone.utc)),
                temp=False,
                removed=False,
            )
            for day, (imdb_id, name, _) in enumerate(titles, start=20)
        ]
    )


def item_rows(titles, excluded_movie_genres, tmdb_down=False) -> list[str]:
    settings = UserSettings(
        catalogs=[CatalogConfig(id="watchly.item", rows=2, enabled_series=False)],
        excluded_movie_genres=excluded_movie_genres,
    )
    service = DynamicCatalogService(language="en-US", tmdb_api_key="tmdb-key")
    service.tmdb_service = FakeTmdb(titles, down=tmdb_down)
    catalogs = asyncio.run(service.get_dynamic_catalogs(loved(*titles), settings, token="tok_seeds"))
    return sorted(c["name"] for c in catalogs if c["id"].startswith("watchly.item"))


def test_a_title_from_an_excluded_genre_never_seeds_a_row():
    """Every title TMDB recommends for an animated seed is animated too, so with
    Animation excluded the row comes back empty."""
    rows = item_rows([LEGO, READY_OR_NOT], excluded_movie_genres=[str(ANIMATION)])

    assert rows == ["Because you loved Ready or Not"]


def test_no_row_when_every_candidate_is_excluded():
    assert item_rows([LEGO], excluded_movie_genres=[str(ANIMATION)]) == []


def test_a_failed_genre_lookup_keeps_the_candidate():
    """Without its genres a seed can't be ruled out, and dropping it would hide the
    row whenever TMDB has a bad moment."""
    rows = item_rows([READY_OR_NOT], excluded_movie_genres=[str(ANIMATION)], tmdb_down=True)

    assert rows == ["Because you loved Ready or Not"]


def test_without_excluded_genres_every_candidate_can_seed():
    rows = item_rows([LEGO, READY_OR_NOT], excluded_movie_genres=[])

    assert rows == ["Because you loved LEGO Star Wars: The Mandalorian", "Because you loved Ready or Not"]
