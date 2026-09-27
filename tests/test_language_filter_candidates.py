from app.core.settings import UserSettings
from app.services.recommendation.filtering import apply_discover_filters, filter_items_by_settings


def settings(*languages: str) -> UserSettings:
    return UserSettings(catalogs=[], allowed_languages=list(languages))


def candidate(tmdb_id: int, original_language: str | None) -> dict:
    """A TMDB /recommendations or /discover result, which carries original_language."""
    item = {"id": tmdb_id, "release_date": "2020-05-01", "vote_average": 7.5, "vote_count": 5000}
    if original_language is not None:
        item["original_language"] = original_language
    return item


def test_discover_asks_tmdb_for_the_allowed_languages_only():
    """TMDB reads a pipe as OR here; a comma would mean AND and return nothing."""
    params = apply_discover_filters({"with_genres": "35"}, settings("en", "fr"))

    assert params["with_original_language"] == "en|fr|xx"


def test_discover_is_unrestricted_without_allowed_languages():
    assert "with_original_language" not in apply_discover_filters({"with_genres": "35"}, settings())


def test_candidates_in_other_languages_are_dropped():
    items = [candidate(1, "en"), candidate(2, "es"), candidate(3, "fr"), candidate(4, "ko")]

    kept = filter_items_by_settings(items, settings("en", "fr"))

    assert [item["id"] for item in kept] == [1, 3]


def test_a_candidate_without_a_language_is_kept():
    """Nothing rules it out here; the spoken-language check after enrichment still applies."""
    assert [i["id"] for i in filter_items_by_settings([candidate(1, None)], settings("en"))] == [1]


def test_every_language_passes_without_allowed_languages():
    items = [candidate(1, "en"), candidate(2, "es"), candidate(3, "ko")]

    assert [item["id"] for item in filter_items_by_settings(items, settings())] == [1, 2, 3]


def test_a_film_without_dialogue_is_not_a_foreign_film():
    """TMDB marks silent films "xx"; they belong in every language's results."""
    assert [i["id"] for i in filter_items_by_settings([candidate(1, "xx")], settings("en"))] == [1]
