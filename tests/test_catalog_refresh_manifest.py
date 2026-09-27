import asyncio
import fnmatch

import pytest

from app.core.settings import UserSettings, get_default_settings
from app.models.library import LibraryCollection
from app.services import catalog_updater as catalog_updater_module
from app.services import manifest as manifest_module
from app.services.catalog_updater import catalog_updater
from app.services.recommendation import catalog_service as cs_module
from app.services.token_store import token_store
from app.services.user_cache import user_cache

TOKEN = "tok_refresh_manifest"


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
        doomed = [key for key in self.data if fnmatch.fnmatchcase(key, pattern)]
        for key in doomed:
            del self.data[key]
        return len(doomed)


@pytest.fixture
def refresh(monkeypatch):
    """The real updater, token_store and caches over a fake Redis. Stremio, the library
    fetch, profile builds, row definitions and row builds are stubbed."""
    fake = FakeRedis()
    for name in ("get", "set", "delete", "expire", "delete_by_pattern"):
        monkeypatch.setattr(f"app.services.user_cache.redis_service.{name}", getattr(fake, name))
    monkeypatch.setattr("app.services.token_store.settings.TOKEN_SALT", "unit-test-salt")
    token_store._get_user_data_cached.cache_clear()

    class Addons:
        async def is_addon_installed(self, auth_key):
            return True

        async def update_catalogs(self, auth_key, catalogs):
            return True

    class Bundle:
        def __init__(self):
            self.addons = Addons()

        async def close(self):
            pass

    async def resolve_auth_key(bundle, credentials, token):
        return "auth-key"

    async def fetch(source, user_settings, token, bundle, auth_key):
        return LibraryCollection(source=source)

    class NoProfiles:
        def __init__(self, *args, **kwargs):
            pass

        async def build_and_cache_profile(self, *args, **kwargs):
            return None, set(), set()

    class Context:
        auth_key = "auth-key"
        library = LibraryCollection(source="stremio")
        user_settings = UserSettings(catalogs=[], watch_history_source="stremio", language="en-US")

        async def close(self):
            pass

    async def load(token, require_auth=False):
        return Context()

    class Rows:
        def __init__(self, **kwargs):
            pass

        async def get_dynamic_catalogs(self, library, user_settings, token=None):
            return [{"id": "watchly.item.1", "name": "Because you loved Ready or Not", "type": "movie"}]

    async def build_row(token, content_type, catalog_id):
        return {"metas": []}, {}

    monkeypatch.setattr(catalog_updater_module, "StremioBundle", Bundle)
    monkeypatch.setattr(catalog_updater_module.auth_service, "resolve_auth_key_with_bundle", resolve_auth_key)
    monkeypatch.setattr(manifest_module, "fetch_library_for_source", fetch)
    monkeypatch.setattr(manifest_module, "ProfileService", NoProfiles)
    monkeypatch.setattr(manifest_module, "load_user_context", load)
    monkeypatch.setattr(manifest_module, "DynamicCatalogService", Rows)
    monkeypatch.setattr(cs_module.catalog_service, "get_catalog", build_row)
    asyncio.run(
        token_store.store_user_data(TOKEN, {"authKey": "auth-key", "settings": get_default_settings().model_dump()})
    )


def test_the_refresh_keeps_the_manifest_it_pushed(refresh):
    """The refresh pushes its rebuilt catalog list to Stremio, then stamps last_updated.
    If that credentials write drops the manifest, the next request rebuilds it and
    re-picks every slot, so the rows no longer match the names just pushed and the
    rows the refresh warmed are dropped with them."""

    async def run_the_scheduled_refresh():
        credentials = await token_store.get_user_data(TOKEN)
        await catalog_updater.trigger_update(TOKEN, credentials)
        await asyncio.gather(*list(catalog_updater._pending_tasks))
        return await user_cache.get_manifest(TOKEN)

    cached = asyncio.run(run_the_scheduled_refresh())

    assert cached is not None
    assert [c["name"] for c in cached["catalogs"]] == ["Because you loved Ready or Not"]


def test_a_settings_save_still_drops_the_manifest(refresh):
    async def save_settings():
        await user_cache.set_manifest(TOKEN, {"catalogs": []})
        credentials = await token_store.get_user_data(TOKEN)
        await token_store.update_user_data(TOKEN, credentials)
        return await user_cache.get_manifest(TOKEN)

    assert asyncio.run(save_settings()) is None
