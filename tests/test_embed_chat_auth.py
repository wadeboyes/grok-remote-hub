"""Embedded chat must keep hub_token on assets, cookies, and the websocket.

/health stays the only route that skips the check. A wrong cookie is not enough.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from aiohttp import web

from hub.http_auth import stamp_token_on_asset_urls, token_accepted
from hub.server import Hub


def test_token_accepted_matches_bearer_query_or_cookie() -> None:
    assert token_accepted("", authorization="", query_token="", cookie_token="")
    assert token_accepted(
        "s3cret",
        authorization="Bearer s3cret",
        query_token="",
        cookie_token="",
    )
    assert token_accepted(
        "s3cret",
        authorization="",
        query_token="s3cret",
        cookie_token="",
    )
    assert token_accepted(
        "s3cret",
        authorization="",
        query_token="",
        cookie_token="s3cret",
    )
    assert not token_accepted(
        "s3cret",
        authorization="Bearer other",
        query_token="other",
        cookie_token="other",
    )
    assert not token_accepted(
        "s3cret",
        authorization="",
        query_token="",
        cookie_token="",
    )


def test_stamp_adds_token_to_assets_and_leaves_open_hub_alone() -> None:
    html = '<link rel="stylesheet" href="/app.css?v=1" /><script src="/app.js?v=1"></script>'
    assert stamp_token_on_asset_urls(html, "") == html
    stamped = stamp_token_on_asset_urls(html, "a/b c")
    assert "token=a%2Fb%20c" in stamped
    assert stamped.count("token=") == 2
    again = stamp_token_on_asset_urls(stamped, "a/b c")
    assert again.count("token=") == 2


def test_middleware_cookie_does_not_open_a_side_door() -> None:
    hub = object.__new__(Hub)
    hub.config = type("C", (), {"hub_token": "s3cret"})()

    class Req:
        def __init__(self, path: str, query=None, headers=None, cookies=None):
            self.path = path
            self.headers = headers or {}
            self.cookies = cookies or {}
            self.rel_url = type("U", (), {"query": query or {}})()

    async def ok(_request):
        return web.Response(text="ok")

    async def run() -> None:
        denied = await hub._auth_middleware(Req("/app.js"), ok)
        assert denied.status == 401
        health = await hub._auth_middleware(Req("/health"), ok)
        assert health.status == 200
        assert "hub_token" not in health.cookies
        page = await hub._auth_middleware(Req("/", {"token": "s3cret"}), ok)
        assert page.status == 200
        assert page.cookies["hub_token"].value == "s3cret"
        styled = await hub._auth_middleware(
            Req("/app.css", cookies={"hub_token": "s3cret"}),
            ok,
        )
        assert styled.status == 200
        socket = await hub._auth_middleware(
            Req("/ws", cookies={"hub_token": "nope"}),
            ok,
        )
        assert socket.status == 401

    asyncio.run(run())


def test_only_health_skips_auth() -> None:
    server = Path("hub/server.py").read_text(encoding="utf-8")
    start = server.find("async def _auth_middleware")
    end = server.find("async def _on_startup")
    middleware = server[start:end]
    assert 'request.path == "/health"' in middleware
    assert "/app.js" not in middleware
    assert "static" not in middleware.split("if request.path", 1)[-1].split("check_auth", 1)[0]
    assert "HUB_TOKEN_COOKIE" in server or "hub_token" in server
    assert server.count('request.path == "/health"') == 1
