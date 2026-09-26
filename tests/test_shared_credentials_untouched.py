import asyncio
import copy
import time

import pytest

from app.core.settings import get_default_settings
from app.services.auth import auth_service
from app.services.profile.service import ProfileService
from app.services.token_store import token_store
from app.services.trakt import trakt_service

TOKEN = "tok_shared"


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


@pytest.fixture(autouse=True)
def real_token_store(monkeypatch):
    fake = FakeRedis()
    for name in ("get", "set", "delete"):
        monkeypatch.setattr(f"app.services.token_store.redis_service.{name}", getattr(fake, name))
    monkeypatch.setattr("app.services.token_store.settings.TOKEN_SALT", "unit-test-salt")
    token_store._get_user_data_cached.cache_clear()


async def store_and_share() -> dict:
    """Store a Trakt-connected user and return the process-cached dict every request
    reads. Callers run in the same event loop, as in the app: alru_cache clears
    itself when the loop changes."""
    settings = get_default_settings().model_copy(
        update={"trakt_access_token": "trakt-access", "trakt_refresh_token": "trakt-refresh"}
    )
    payload = {"authKey": "auth-key", "email": "me@example.com", "password": "hunter2"}
    await token_store.store_user_data(TOKEN, {**payload, "settings": settings.model_dump()})
    return await token_store.get_user_data(TOKEN)


def stored() -> dict:
    token_store._get_user_data_cached.cache_clear()
    return asyncio.run(token_store.get_user_data(TOKEN))


def test_stremio_relogin_leaves_the_shared_credentials_alone():
    class Auth:
        async def get_user_info(self, auth_key):
            raise ValueError("Stremio API Error: Session not found")

        async def login(self, email, password):
            if (email, password) != ("me@example.com", "hunter2"):
                raise ValueError("Stremio API Error: Wrong email or password")
            return "new-auth-key"

    class Bundle:
        auth = Auth()

    async def relogin():
        shared = await store_and_share()
        before = copy.deepcopy(shared)
        new_key = await auth_service.resolve_auth_key_with_bundle(Bundle(), shared, TOKEN)
        return shared, before, new_key

    shared, before, new_key = asyncio.run(relogin())

    assert new_key == "new-auth-key"
    assert stored()["authKey"] == "new-auth-key"
    assert shared == before


def test_trakt_token_refresh_leaves_the_shared_credentials_alone(monkeypatch):
    async def refresh_token(refresh_token, redirect_uri):
        if refresh_token != "trakt-refresh":
            raise ValueError("invalid_grant")
        return {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 7 * 24 * 60 * 60,
            "created_at": int(time.time()),
        }

    monkeypatch.setattr(trakt_service, "refresh_token", refresh_token)

    async def refresh():
        shared = await store_and_share()
        before = copy.deepcopy(shared)
        new_access = await ProfileService()._refresh_trakt_token(TOKEN, "trakt-refresh")
        return shared, before, new_access

    shared, before, new_access = asyncio.run(refresh())

    assert new_access == "new-access"
    assert stored()["settings"]["trakt_refresh_token"] == "new-refresh"
    assert shared == before


def test_clearing_a_revoked_token_leaves_the_shared_credentials_alone():
    async def clear():
        shared = await store_and_share()
        before = copy.deepcopy(shared)
        await ProfileService()._clear_revoked_token(TOKEN, "trakt")
        return shared, before

    shared, before = asyncio.run(clear())

    assert not stored()["settings"]["trakt_refresh_token"]
    assert shared == before
