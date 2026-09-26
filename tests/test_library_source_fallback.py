import asyncio

import pytest

from app.core.settings import get_default_settings
from app.models.library import LibraryCollection, StremioLibraryItem
from app.services import context as context_module
from app.services import manifest as manifest_module
from app.services.context import load_user_context
from app.services.manifest import manifest_service
from app.services.user_cache import user_cache

TOKEN = "tok_fallback"

TRAKT_SETTINGS = get_default_settings().model_copy(update={"watch_history_source": "trakt"})


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


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    for name in ("get", "set", "delete", "expire", "delete_by_pattern"):
        monkeypatch.setattr(f"app.services.user_cache.redis_service.{name}", getattr(fake, name))
    return fake


def library_of(imdb_id: str, source: str) -> LibraryCollection:
    return LibraryCollection(
        watched=[StremioLibraryItem(_id=imdb_id, type="movie", name=imdb_id, temp=False, removed=False)],
        source=source,
    )


async def stremio_fallback(source, user_settings, token, bundle, auth_key):
    """What fetch_library_for_source returns for a Trakt user while Trakt is down."""
    return library_of("tt9999999", source="stremio")


def cached_library() -> LibraryCollection | None:
    return asyncio.run(user_cache.get_library_items(TOKEN))


def test_warm_up_keeps_the_external_library_when_the_fetch_falls_back(fake_redis, monkeypatch):
    """Cached, the Stremio fallback replaces the user's Trakt history, and the profiles
    get rebuilt from the wrong account's watches."""
    asyncio.run(user_cache.set_library_items(TOKEN, library_of("tt0000001", source="trakt")))
    profile_builds = []

    class RecordingProfileService:
        def __init__(self, *args, **kwargs):
            pass

        async def build_and_cache_profile(self, token, content_type, *args, **kwargs):
            profile_builds.append(content_type)
            return None, set(), set()

    monkeypatch.setattr(manifest_module, "fetch_library_for_source", stremio_fallback)
    monkeypatch.setattr(manifest_module, "ProfileService", RecordingProfileService)

    asyncio.run(
        manifest_service.cache_library_and_profiles(
            bundle=object(), auth_key="auth-key", user_settings=TRAKT_SETTINGS, token=TOKEN
        )
    )

    assert [i.id for i in cached_library().all_items()] == ["tt0000001"]
    assert profile_builds == []


def test_request_serves_the_fallback_without_caching_it(fake_redis, monkeypatch):
    """The fallback keeps this request's rows populated. Cached, the next request sees
    the source mismatch and drops it again, wiping every cached row each time."""

    class Bundle:
        async def close(self):
            pass

    async def resolve_alias(token):
        return token

    async def get_user_data(token):
        return {"authKey": "auth-key", "settings": TRAKT_SETTINGS.model_dump()}

    async def resolve_auth_key(bundle, credentials, token):
        return "auth-key"

    monkeypatch.setattr(context_module.token_store, "resolve_alias", resolve_alias)
    monkeypatch.setattr(context_module.token_store, "get_user_data", get_user_data)
    monkeypatch.setattr(context_module.auth_service, "resolve_auth_key_with_bundle", resolve_auth_key)
    monkeypatch.setattr(context_module, "StremioBundle", Bundle)
    monkeypatch.setattr(context_module, "fetch_library_for_source", stremio_fallback)

    ctx = asyncio.run(load_user_context(TOKEN, require_auth=False))

    assert [i.id for i in ctx.library.all_items()] == ["tt9999999"]
    assert cached_library() is None
