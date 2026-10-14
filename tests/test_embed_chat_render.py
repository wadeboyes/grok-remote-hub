"""The embedded chat page must show history (or an empty note) and a composer.

Loads the real ``/?open=&embed=chat&token=`` URL at phone and tablet sizes
against an empty history, the same shape the Android WebView opens after resume.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from pathlib import Path

import pytest
from aiohttp import web

from hub.http_auth import stamp_token_on_asset_urls, token_accepted

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
TOKEN = "s3cret"
SID = "01a13a07-a83e-7abc-8def-0123456789ab"
EMPTY_NOTE = "No messages yet"

# Phone, tablet portrait pane, and a wide tablet pane (desktop breakpoint).
VIEWPORTS = (
    {"width": 390, "height": 844},
    {"width": 800, "height": 1280},
    {"width": 1100, "height": 800},
)


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


def _authorized(request: web.Request) -> bool:
    return token_accepted(
        TOKEN,
        authorization=request.headers.get("Authorization") or "",
        query_token=request.rel_url.query.get("token") or "",
        cookie_token=request.cookies.get("hub_token") or "",
    )


def _static_file(path: str) -> Path | None:
    rel = path.lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    candidate = (STATIC / rel).resolve()
    try:
        candidate.relative_to(STATIC.resolve())
    except ValueError:
        return None
    if candidate.is_file():
        return candidate
    return None


def make_app(sockets: dict[str, int]) -> web.Application:
    @web.middleware
    async def middleware(request: web.Request, handler):
        if request.path != "/health" and not _authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    async def index(_request: web.Request) -> web.Response:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        html = stamp_token_on_asset_urls(html, TOKEN)
        return web.Response(
            text=html,
            content_type="text/html",
            charset="utf-8",
            headers={"Cache-Control": "no-store"},
        )

    async def sessions(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "items": [
                    {
                        "sessionId": SID,
                        "title": "updates",
                        "cwd": r"C:\Users\WADEBOYES\.grok\worktrees\wadeboyes\updates",
                        "isSubagent": False,
                        "isWorking": True,
                    }
                ]
            }
        )

    async def history(_request: web.Request) -> web.Response:
        # Replay-suppressed loads leave the REST history empty.
        return web.json_response({"messages": []})

    async def attach(_request: web.Request) -> web.Response:
        # Resume on the phone spends about two seconds in session/load before
        # the page opens its second socket. Hold the attach so that race shows.
        await asyncio.sleep(1.2)
        return web.json_response(
            {
                "ok": True,
                "liveSessionId": SID,
                "switched": False,
                "reason": "reuse",
            }
        )

    async def skills(_request: web.Request) -> web.Response:
        return web.json_response({"items": []})

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        sockets["n"] = sockets.get("n", 0) + 1
        await ws.send_str(
            json.dumps(
                {
                    "type": "status",
                    "agent": "up",
                    "agentProcess": "up",
                    "acpConnected": True,
                    "acpQuality": "ok",
                }
            )
        )
        async for _msg in ws:
            pass
        return ws

    async def static_or_missing(request: web.Request) -> web.StreamResponse:
        found = _static_file(request.path)
        if found is None:
            return web.json_response({"error": "not found"}, status=404)
        return web.FileResponse(found)

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def missing_plan(_request: web.Request) -> web.Response:
        return web.Response(status=404)

    async def empty_usage(_request: web.Request) -> web.Response:
        return web.json_response({})

    app = web.Application(middlewares=[middleware])
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/sessions", sessions)
    app.router.add_get("/api/sessions/{sid}/history", history)
    app.router.add_post("/api/sessions/{sid}/attach", attach)
    app.router.add_get("/api/sessions/{sid}/plan", missing_plan)
    app.router.add_get("/api/sessions/{sid}/usage", empty_usage)
    app.router.add_get("/api/skills", skills)
    app.router.add_get("/ws", ws_handler)
    app.router.add_route("*", "/{tail:.*}", static_or_missing)
    return app


def _serve() -> tuple[str, dict[str, int], threading.Thread, asyncio.AbstractEventLoop]:
    sockets: dict[str, int] = {"n": 0}
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def run() -> None:
        asyncio.set_event_loop(loop)
        runner = web.AppRunner(make_app(sockets))
        loop.run_until_complete(runner.setup())
        site = web.SockSite(runner, sock)
        loop.run_until_complete(site.start())
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(10)
    return f"http://127.0.0.1:{port}", sockets, thread, loop


def _layout(page) -> dict:
    return page.evaluate(
        """() => {
          const input = document.getElementById("composer-input");
          const shell = document.querySelector(".composer-shell");
          const panel = document.getElementById("chat-panel");
          const app = document.getElementById("app");
          const box = (el) => {
            if (!el) return null;
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return {
              w: r.width, h: r.height, top: r.top, bottom: r.bottom,
              display: s.display, visibility: s.visibility, opacity: s.opacity
            };
          };
          const inputDisabled = !!(input && input.disabled);
          const ib = input ? input.getBoundingClientRect() : null;
          let hit = "";
          if (ib && ib.width > 0 && ib.height > 0) {
            const node = document.elementFromPoint(ib.left + ib.width / 2, ib.top + Math.min(8, ib.height / 2));
            hit = node ? (node.id || node.className || node.tagName) : "";
          }
          const note = (document.body.innerText || "").includes("No messages yet");
          const empty = document.getElementById("empty-main");
          const emptyShown = !!(empty && getComputedStyle(empty).display !== "none" && !empty.hidden);
          return {
            input: box(input),
            shell: box(shell),
            panel: box(panel),
            app: box(app),
            hit,
            note,
            emptyShown,
            inputDisabled,
            innerHeight: window.innerHeight,
            appDisplay: app ? getComputedStyle(app).display : ""
          };
        }"""
    )


@pytest.mark.skipif(not _playwright_available(), reason="playwright package not installed")
def test_embed_chat_shows_composer_at_phone_and_tablet() -> None:
    from playwright.sync_api import sync_playwright

    base, sockets, _thread, loop = _serve()
    url = (
        f"{base}/?open={SID}"
        "&cwd=C%3A%5CUsers%5CWADEBOYES%5C.grok%5Cworktrees%5Cwadeboyes%5Cupdates"
        "&embed=chat"
        f"&token={TOKEN}"
    )
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            for viewport in VIEWPORTS:
                sockets["n"] = 0
                page = browser.new_page(viewport=viewport)
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_selector("#composer-input", timeout=15_000)
                page.wait_for_function(
                    """() => (document.body.innerText || "").includes("No messages yet")""",
                    timeout=15_000,
                )
                # The phone used to open a second socket about two seconds later.
                page.wait_for_timeout(2500)
                layout = _layout(page)
                assert layout["note"] is True, layout
                assert layout["emptyShown"] is False, layout
                shell = layout["shell"]
                panel = layout["panel"]
                assert shell is not None and shell["display"] != "none", layout
                assert shell["h"] >= 40, layout
                assert shell["bottom"] <= viewport["height"] + 2, layout
                assert shell["top"] < viewport["height"] - 20, layout
                assert panel is not None and panel["h"] >= 120, layout
                assert "rail-backdrop" not in str(layout["hit"]), layout
                assert layout["inputDisabled"] is False, layout
                assert sockets["n"] == 1, (sockets["n"], viewport, errors)
                page.close()
        finally:
            browser.close()
    assert not errors, errors
    loop.call_soon_threadsafe(loop.stop)
