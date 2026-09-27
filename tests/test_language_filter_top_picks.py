import asyncio

from app.core.settings import UserSettings
from app.models.library import LibraryCollection, StremioLibraryItem
from app.models.profile import TasteProfile
from app.services.recommendation.top_picks import TopPicksService

MISLABELED = 1181678  # "¿Quieres ser mi hijo?": TMDB says "en", but it is spoken only in Spanish
PARASITE = 496243  # Korean, with a few lines of English and German
ENGLISH = 1


def details(tmdb_id: int, original_language: str, spoken: list[str]) -> dict:
    return {
        "id": tmdb_id,
        "title": f"Title {tmdb_id}",
        "external_ids": {"imdb_id": f"tt{tmdb_id:07d}"},
        "original_language": original_language,
        "spoken_languages": [{"iso_639_1": code} for code in spoken],
        "genres": [{"id": 35, "name": "Comedy"}],
        "release_date": "2023-09-21",
        "vote_average": 7.5,
        "vote_count": 5000,
        "popularity": 50.0,
    }


TITLES = {
    ENGLISH: details(ENGLISH, "en", ["en"]),
    MISLABELED: details(MISLABELED, "en", ["es"]),
    PARASITE: details(PARASITE, "ko", ["en", "de", "ko"]),
}


def candidate(tmdb_id: int) -> dict:
    """A /discover result: the details minus the spoken languages."""
    return {**TITLES[tmdb_id], "genre_ids": [35], "spoken_languages": None}


class FakeTmdb:
    def __init__(self, discovered: list[int]):
        self.discovered = discovered

    async def get_discover(self, mtype, **params):
        return {"results": [candidate(tmdb_id) for tmdb_id in self.discovered]}

    async def get_movie_details(self, movie_id):
        return TITLES[movie_id]

    async def get_images_for_title(self, media_type, tmdb_id, language=None):
        return {}


def top_pick_ids(tmdb: FakeTmdb, settings: UserSettings, library: LibraryCollection) -> list[int]:
    service = TopPicksService(tmdb, settings)
    profile = TasteProfile(genre_scores={35: 1.0})
    picks = asyncio.run(service.get_top_picks(profile, "movie", library, set(), set()))
    return sorted(pick["_tmdb_id"] for pick in picks)


def test_top_picks_leave_out_a_title_spoken_only_in_another_language():
    tmdb = FakeTmdb(discovered=[ENGLISH, MISLABELED])
    library = LibraryCollection(source="stremio")

    assert top_pick_ids(tmdb, UserSettings(catalogs=[], allowed_languages=["en", "fr"]), library) == [ENGLISH]
    assert top_pick_ids(tmdb, UserSettings(catalogs=[]), library) == [ENGLISH, MISLABELED]


def test_top_picks_from_simkl_leave_out_a_film_in_another_language(monkeypatch):
    """Simkl candidates carry no original_language, so only the check on the details can
    stop Parasite, whose few lines of English must not let it through."""

    async def simkl_recommendations(imdb_ids, mtype, api_key, **kwargs):
        return [{"id": PARASITE, "vote_average": 8.5, "vote_count": 1000, "release_date": "2019-05-30"}]

    monkeypatch.setattr(
        "app.services.recommendation.candidate_sources.simkl_service.get_recommendations_batch",
        simkl_recommendations,
    )
    seed = StremioLibraryItem(_id="tt0000001", type="movie", name="Seed", temp=False, removed=False, _is_loved=True)
    library = LibraryCollection(loved=[seed], source="stremio")
    tmdb = FakeTmdb(discovered=[ENGLISH])

    def settings(*languages: str) -> UserSettings:
        return UserSettings(catalogs=[], simkl_api_key="key", allowed_languages=list(languages))

    assert top_pick_ids(tmdb, settings("en", "fr"), library) == [ENGLISH]
    assert top_pick_ids(tmdb, settings(), library) == [ENGLISH, PARASITE]
