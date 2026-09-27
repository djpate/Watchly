import asyncio

import pytest
from pydantic import ValidationError

from app.api.models.tokens import TokenRequest
from app.services.auth import auth_service
from app.services.context import extract_settings
from app.services.token_store import token_store


def test_saved_languages_reach_the_user_settings():
    settings = auth_service._build_user_settings(TokenRequest(allowed_languages=["en", "fr"]))

    assert settings.allowed_languages == ["en", "fr"]


def test_no_languages_saved_means_no_filter():
    assert auth_service._build_user_settings(TokenRequest()).allowed_languages == []


@pytest.mark.parametrize("code", ["EN", "english", "e", "en-US"])
def test_a_language_must_be_a_two_letter_code(code):
    """TMDB's original_language and spoken_languages use lowercase ISO 639-1 codes; any
    other form would silently match nothing and hide every title."""
    with pytest.raises(ValidationError):
        TokenRequest(allowed_languages=[code])


def test_saved_languages_come_back_when_the_settings_are_loaded(monkeypatch):
    stored: dict[str, str] = {}

    async def fake_set(key, value, ttl=None):
        stored[key] = value
        return True

    async def fake_get(key):
        return stored.get(key)

    async def fake_delete(key):
        stored.pop(key, None)

    for name, fake in (("set", fake_set), ("get", fake_get), ("delete", fake_delete)):
        monkeypatch.setattr(f"app.services.token_store.redis_service.{name}", fake)
    monkeypatch.setattr("app.services.token_store.settings.TOKEN_SALT", "unit-test-salt")
    settings = auth_service._build_user_settings(TokenRequest(allowed_languages=["en", "fr"]))

    async def save_and_load():
        await token_store.store_user_data("tok_languages", {"settings": settings.model_dump()})
        return await token_store.get_user_data("tok_languages")

    assert extract_settings(asyncio.run(save_and_load())).allowed_languages == ["en", "fr"]
