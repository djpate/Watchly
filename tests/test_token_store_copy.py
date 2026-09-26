import asyncio
import copy

import pytest

from app.core.settings import get_default_settings
from app.services.token_store import token_store

TOKEN = "tok_copy"


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


@pytest.fixture
def real_token_store(monkeypatch):
    fake = FakeRedis()
    for name in ("get", "set", "delete"):
        monkeypatch.setattr(f"app.services.token_store.redis_service.{name}", getattr(fake, name))
    monkeypatch.setattr("app.services.token_store.settings.TOKEN_SALT", "unit-test-salt")
    token_store._get_user_data_cached.cache_clear()


def credentials_with_every_nested_secret() -> dict:
    settings = get_default_settings().model_copy(
        update={
            "tmdb_api_key": "tmdb-key",
            "simkl_api_key": "simkl-key",
            "gemini_api_key": "gemini-key",
            "trakt_access_token": "trakt-access",
            "trakt_refresh_token": "trakt-refresh",
            "simkl_access_token": "simkl-access",
        }
    )
    payload = {"authKey": "auth-key", "password": "hunter2", "settings": settings.model_dump()}
    payload["settings"]["poster_rating"] = {"provider": "rpdb", "api_key": "rpdb-key", "url_template": None}
    payload["settings"]["llm"] = {"provider": "anthropic", "api_key": "llm-key", "model": None}
    return payload


def test_storing_leaves_the_callers_dict_as_it_was(real_token_store):
    """Callers hand store_user_data the shared, process-cached credentials dict, which
    other requests are reading. Encrypting its nested settings in place gave those
    requests ciphertext for every API key and token until they finished."""
    payload = credentials_with_every_nested_secret()
    before = copy.deepcopy(payload)

    asyncio.run(token_store.store_user_data(TOKEN, payload))

    assert payload == before


def test_stored_secrets_still_round_trip(real_token_store):
    payload = credentials_with_every_nested_secret()
    asyncio.run(token_store.store_user_data(TOKEN, payload))
    token_store._get_user_data_cached.cache_clear()

    stored = asyncio.run(token_store.get_user_data(TOKEN))

    assert stored["authKey"] == "auth-key"
    assert stored["settings"]["tmdb_api_key"] == "tmdb-key"
    assert stored["settings"]["trakt_refresh_token"] == "trakt-refresh"
    assert stored["settings"]["poster_rating"]["api_key"] == "rpdb-key"
    assert stored["settings"]["llm"]["api_key"] == "llm-key"
