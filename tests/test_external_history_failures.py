import asyncio
import copy
import time

import httpx
import pytest

from app.core.settings import get_default_settings
from app.services.profile.service import ProfileService
from app.services.simkl import simkl_service
from app.services.token_store import token_store
from app.services.trakt import trakt_service

TOKEN = "tok_history"


def http_error(status: int, url: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"https://api.example{url}")
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=httpx.Response(status, request=request))


def test_trakt_history_raises_when_every_endpoint_fails(monkeypatch):
    """An empty history returned for an outage gets cached as the user's history,
    dropping everything they've watched until the next good fetch. (One failed
    endpoint still returns the others: test_trakt_pagination.)"""

    async def get(url, params=None, headers=None):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(trakt_service.client, "get", get)

    with pytest.raises(httpx.ConnectError):
        asyncio.run(trakt_service.get_history("access-token"))


def test_simkl_history_raises_when_every_endpoint_fails(monkeypatch):
    async def get(path, headers=None):
        raise http_error(503, path)

    monkeypatch.setattr(simkl_service.client, "get", get)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(simkl_service.get_history("access-token", "client-id"))


@pytest.fixture
def trakt_user(monkeypatch):
    """A Trakt user whose access token Trakt rejects, with credentials in a fake store."""
    user_settings = get_default_settings().model_copy(
        update={
            "watch_history_source": "trakt",
            "trakt_access_token": "access-token",
            "trakt_refresh_token": "refresh-token",
            "trakt_token_expires_at": int(time.time()) + 30 * 24 * 60 * 60,
        }
    )
    stored = {TOKEN: {"authKey": "auth-key", "settings": user_settings.model_dump()}}

    async def get_user_data(token):
        return copy.deepcopy(stored[token])

    async def update_user_data(token, payload):
        stored[token] = payload

    async def rejected_access_token(url, params=None, headers=None):
        raise http_error(401, url)

    monkeypatch.setattr(token_store, "get_user_data", get_user_data)
    monkeypatch.setattr(token_store, "update_user_data", update_user_data)
    monkeypatch.setattr(trakt_service.client, "get", rejected_access_token)
    return user_settings, stored


def test_unreachable_trakt_refresh_keeps_the_connection(trakt_user, monkeypatch):
    """Only an explicit rejection means the refresh token is dead. Clearing it on a
    network error disconnects a user whose credentials are fine."""
    user_settings, stored = trakt_user

    async def unreachable(refresh_token, redirect_uri):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(trakt_service, "refresh_token", unreachable)

    history, _, revoked = asyncio.run(ProfileService().fetch_external_watch_history("trakt", user_settings, TOKEN))

    assert history is None
    assert not revoked
    assert stored[TOKEN]["settings"]["trakt_refresh_token"] == "refresh-token"


def test_rejected_trakt_refresh_clears_the_connection(trakt_user, monkeypatch):
    user_settings, stored = trakt_user

    async def invalid_grant(refresh_token, redirect_uri):
        raise http_error(400, "/oauth/token")

    monkeypatch.setattr(trakt_service, "refresh_token", invalid_grant)

    history, _, revoked = asyncio.run(ProfileService().fetch_external_watch_history("trakt", user_settings, TOKEN))

    assert history is None
    assert revoked
    assert not stored[TOKEN]["settings"]["trakt_refresh_token"]


def test_simkl_history_keeps_the_endpoint_that_worked(monkeypatch):
    async def get(path, headers=None):
        if path == "/sync/all-items/shows":
            raise http_error(503, path)
        return {"movies": [{"movie": {"title": "Kept", "ids": {"imdb": "tt0000001"}}, "status": "completed"}]}

    monkeypatch.setattr(simkl_service.client, "get", get)

    history = asyncio.run(simkl_service.get_history("access-token", "client-id"))

    assert [item.imdb_id for item in history.items] == ["tt0000001"]
