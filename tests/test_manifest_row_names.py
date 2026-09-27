import asyncio

import pytest

from app.core.settings import UserSettings
from app.models.library import LibraryCollection
from app.services import manifest as manifest_module
from app.services.manifest import manifest_service

TOKEN = "tok_row_names"
ROWS = [
    {"id": "watchly.item.1", "name": "Because you loved Ready or Not", "type": "movie"},
    {"id": "watchly.theme.1", "name": "Infernal Wagers & Chases", "type": "movie"},
]


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


class Stremio:
    """The user's Stremio addon collection, as far as a manifest push can see it."""

    def __init__(self):
        self.pushed: list[tuple[str, list[str]]] = []
        self.down = False


@pytest.fixture
def stremio(monkeypatch):
    fake = FakeRedis()
    for name in ("get", "set", "delete", "expire", "delete_by_pattern"):
        monkeypatch.setattr(f"app.services.user_cache.redis_service.{name}", getattr(fake, name))
    collection = Stremio()

    class Addons:
        async def update_catalogs(self, auth_key, catalogs):
            if collection.down:
                raise ConnectionError("Stremio API unreachable")
            collection.pushed.append((auth_key, [c["name"] for c in catalogs]))
            return True

    class Bundle:
        def __init__(self):
            self.addons = Addons()

        async def close(self):
            pass

    monkeypatch.setattr(manifest_module, "StremioBundle", Bundle)
    use_account(monkeypatch, auth_key="auth-key", source="stremio")
    build_rows(monkeypatch, ROWS)
    return collection


def use_account(monkeypatch, auth_key, source):
    class Context:
        library = LibraryCollection(source=source)
        user_settings = UserSettings(catalogs=[], watch_history_source=source, language="en-US")

        async def close(self):
            pass

    Context.auth_key = auth_key

    async def load(token, require_auth=False):
        return Context()

    monkeypatch.setattr(manifest_module, "load_user_context", load)


def build_rows(monkeypatch, rows):
    class DynamicCatalogs:
        def __init__(self, **kwargs):
            pass

        async def get_dynamic_catalogs(self, library, user_settings, token=None):
            if rows is None:
                raise RuntimeError("TMDB unreachable")
            return [dict(row) for row in rows]

    monkeypatch.setattr(manifest_module, "DynamicCatalogService", DynamicCatalogs)


def manifest(force_rebuild=False) -> dict:
    async def request():
        result = await manifest_service.get_manifest_for_token(TOKEN, force_rebuild=force_rebuild)
        # Let anything the request left running in the background finish.
        await asyncio.gather(*(t for t in asyncio.all_tasks() if t is not asyncio.current_task()))
        return result

    return asyncio.run(request())


def test_a_rebuilt_manifest_pushes_its_row_names_to_stremio(stremio):
    """Stremio shows row names from the copy in the user's addon collection and fetches
    each row by its slot. A rebuild re-picks what the slots hold, so unless the new
    names are pushed, the TV labels rows with the names of other rows."""
    built = manifest()

    assert stremio.pushed == [("auth-key", [c["name"] for c in built["catalogs"]])]
    assert stremio.pushed[0][1] == ["Because you loved Ready or Not", "Infernal Wagers & Chases"]


def test_a_manifest_served_from_cache_pushes_nothing(stremio):
    manifest()
    manifest()

    assert len(stremio.pushed) == 1


def test_the_scheduled_refresh_pushes_its_own_rebuild(stremio):
    manifest(force_rebuild=True)

    assert stremio.pushed == []


def test_no_push_without_a_stremio_login(stremio, monkeypatch):
    use_account(monkeypatch, auth_key=None, source="trakt")

    manifest()

    assert stremio.pushed == []


def test_a_failed_row_build_never_pushes_an_empty_list(stremio, monkeypatch):
    """Pushing the empty list a failed build leaves would take every Watchly row off
    the user's home screen."""
    build_rows(monkeypatch, None)

    manifest()

    assert stremio.pushed == []


def test_a_failed_push_still_serves_the_manifest(stremio):
    stremio.down = True

    built = manifest()

    assert [c["name"] for c in built["catalogs"]] == ["Because you loved Ready or Not", "Infernal Wagers & Chases"]
