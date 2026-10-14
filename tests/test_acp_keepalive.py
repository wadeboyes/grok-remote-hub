"""ACP keepalive blip: busy grok must not be marked zombie or lose the turn."""

from __future__ import annotations

import asyncio
from pathlib import Path

from hub.acp_client import ACP_WS_PING_INTERVAL, ACP_WS_PING_TIMEOUT, AcpClient
from hub.config import Config
from hub.session_policy import ACP_BLIP_USER_MSG, is_keepalive_ping_timeout

ROOT = Path(__file__).resolve().parents[1]
KEEPALIVE_CLOSE = (
    "sent 1011 (internal error) keepalive ping timeout; no close frame received"
)


def _client() -> AcpClient:
    return AcpClient(Config(), secret="test-secret-keepalive")


def _slice_method(src: str, name: str) -> str:
    for prefix in (f"async def {name}(", f"def {name}("):
        start = src.find(prefix)
        if start >= 0:
            break
    else:
        raise AssertionError(f"method {name} not found")
    line_start = src.rfind("\n", 0, start) + 1
    indent = 0
    while line_start + indent < len(src) and src[line_start + indent] in " \t":
        indent += 1
    pos = start
    while pos < len(src):
        nl = src.find("\n", pos)
        if nl < 0:
            return src[start:]
        next_line = nl + 1
        if next_line >= len(src):
            return src[start:]
        if src[next_line] == "\n":
            pos = next_line
            continue
        j = next_line
        while j < len(src) and src[j] in " \t":
            j += 1
        if j >= len(src):
            return src[start:]
        if (j - next_line) <= indent and src.startswith(("def ", "async def "), j):
            return src[start:next_line]
        pos = next_line
    return src[start:]


class _FailSend:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    async def send(self, _data: str) -> None:
        raise self.exc

    async def close(self) -> None:
        return None


def test_keepalive_ping_timeout_detector_and_copy() -> None:
    assert is_keepalive_ping_timeout(ConnectionError(KEEPALIVE_CLOSE))
    assert is_keepalive_ping_timeout(
        "sent 1011 (internal error) keepalive ping timeout; no close frame received"
    )
    assert is_keepalive_ping_timeout("1011 ping timeout")
    assert not is_keepalive_ping_timeout(ConnectionError("connection reset"))
    assert not is_keepalive_ping_timeout("1011")
    assert not is_keepalive_ping_timeout("ping timeout")
    assert ACP_BLIP_USER_MSG == (
        "Connection to Grok blipped, reconnected. Grok may still be working."
    )
    assert "\u2014" not in ACP_BLIP_USER_MSG
    assert "\u2013" not in ACP_BLIP_USER_MSG


def test_library_ping_disabled_probe_covers_idle() -> None:
    assert ACP_WS_PING_INTERVAL is None
    assert ACP_WS_PING_TIMEOUT is None
    src = (ROOT / "hub" / "acp_client.py").read_text(encoding="utf-8")
    assert "ping_interval=ACP_WS_PING_INTERVAL" in src
    assert "ping_timeout=ACP_WS_PING_TIMEOUT" in src
    assert "maybe_probe_liveness" in src


def test_keepalive_send_failures_do_not_zombie_while_prompt_pending() -> None:
    async def run() -> None:
        client = _client()
        client.active_turns["sid-busy"] = {"started_at": 0.0}
        client._ws = _FailSend(ConnectionError(KEEPALIVE_CLOSE))
        for method in ("session/prompt", "session/update"):
            try:
                await client._send({"method": method})
            except ConnectionError as exc:
                assert is_keepalive_ping_timeout(exc)
        events = client.trace.snapshot(20)
        names = [e["event"] for e in events]
        assert names.count("send_fail") == 2
        assert "zombie" not in names
        assert "force_unhealthy" not in names
        assert all(e.get("keepalive_blip") is True for e in events if e["event"] == "send_fail")
        assert client.consecutive_send_failures == 0
        assert client._force_unhealthy_scheduled is False
        assert client.should_preserve_turn_on_disconnect() is True
        assert "sid-busy" in client.blip_hold_session_ids
        assert "sid-busy" in client.active_turns

    asyncio.run(run())


def test_non_keepalive_send_failures_still_zombie() -> None:
    async def run() -> None:
        client = _client()
        client.active_turns["sid-busy"] = {"started_at": 0.0}
        client._ws = _FailSend(ConnectionError("connection reset"))
        for _ in range(2):
            try:
                await client._send({"method": "session/prompt"})
            except ConnectionError as exc:
                assert "connection reset" in str(exc)
        events = client.trace.snapshot(20)
        names = [e["event"] for e in events]
        assert names.count("send_fail") == 2
        assert "zombie" in names
        assert "force_unhealthy" in names
        assert client.consecutive_send_failures >= 2
        assert client._force_unhealthy_scheduled is True
        await asyncio.sleep(0)

    asyncio.run(run())


def test_preserve_turn_skips_force_clear_on_disconnect() -> None:
    src = (ROOT / "hub" / "acp_client.py").read_text(encoding="utf-8")
    body = _slice_method(src, "_maintain")
    pres = body.find("should_preserve_turn_on_disconnect")
    clear = body.find('force_clear_turn("acp disconnected")')
    assert pres >= 0 and clear >= 0 and pres < clear
    assert "if not preserve:" in body
    reattach = _slice_method(src, "_reattach_after_keepalive")
    assert "session_load" in reattach
    assert "allow_during_turn=True" in reattach
    prompt = _slice_method(src, "session_prompt")
    assert "asyncio.sleep(" not in prompt
    assert "_reattach_after_keepalive" in prompt

    client = _client()
    client._register_active_turn("sid-live", cwd_key="proj")
    client.note_socket_error(ConnectionError(KEEPALIVE_CLOSE))
    assert client.should_preserve_turn_on_disconnect() is True
    called: list[str] = []

    def _no_clear(reason: str, session_id: str | None = None) -> bool:
        called.append(reason)
        return False

    client.force_clear_turn = _no_clear  # type: ignore[method-assign]
    assert client.should_preserve_turn_on_disconnect() is True
    assert called == []
    assert "sid-live" in client.active_turns

    other = _client()
    other._register_active_turn("sid-live")
    other.note_socket_error(ConnectionError("connection reset"))
    assert other.should_preserve_turn_on_disconnect() is False


def test_session_prompt_reattaches_with_session_load() -> None:
    async def run() -> None:
        client = _client()
        sid = "01keepalive"
        calls: list[str] = []
        client.connected = True
        client._ws = object()
        client.loaded_session_id = sid

        async def fake_request(method, params=None, timeout=120.0, *, session_id=None):
            calls.append(method)
            if method == "session/prompt":
                raise ConnectionError(KEEPALIVE_CLOSE)
            if method == "session/load":
                assert session_id == sid

                async def finish() -> None:
                    await asyncio.sleep(0)
                    client.release_load_suppress(sid)
                    await client._track_update(
                        {
                            "method": "session/update",
                            "params": {
                                "sessionId": sid,
                                "update": {"sessionUpdate": "turn_completed"},
                            },
                        }
                    )

                asyncio.create_task(finish())
                return {"sessionId": sid}
            raise AssertionError(method)

        client.request = fake_request  # type: ignore[method-assign]
        result = await asyncio.wait_for(
            client.session_prompt(
                sid,
                "scan the tree",
                cwd="C:/work/repo",
                allow_load=False,
                no_output_seconds=3600,
            ),
            timeout=5,
        )
        assert result == {"reattached": True}
        assert calls == ["session/prompt", "session/load"]
        events = client.trace.snapshot(40)
        ends = [e for e in events if e["event"] == "prompt_end"]
        assert len(ends) == 1
        assert ends[0]["ok"] is True
        assert ends[0].get("via") == "reattach"
        assert not any(e["event"] == "zombie" for e in events)
        assert not any(e["event"] == "prompt_end" and e.get("ok") is False for e in events)
        assert sid not in client.active_turns
        await asyncio.sleep(0)
        for handle in list(client._load_release_handles.values()):
            handle.cancel()

    asyncio.run(run())


def test_ui_and_hub_use_plain_blip_sentence() -> None:
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    server = (ROOT / "hub" / "server.py").read_text(encoding="utf-8")
    assert ACP_BLIP_USER_MSG in js
    assert "keepalive ping timeout" in js
    assert "1011 \\(internal error\\)" in js or "1011 \\\\(internal error\\\\)" in js
    assert "isAcpBlip" in js
    assert "friendlyHubError" in js
    assert "20261014d" in html
    assert "ACP_BLIP_USER_MSG" in server
    assert "blip_hold_session_ids" in server
    on_conn = server[server.find("async def _on_acp_connection") :]
    on_conn = on_conn[: on_conn.find("\n    async def ")]
    assert "blip_hold_session_ids" in on_conn
    assert 'state": "running"' in on_conn or '"running"' in on_conn
    assert "ACP_BLIP_USER_MSG" in on_conn
    execute = server[server.find("async def _execute_prompt") :]
    execute = execute[: execute.find("\n    async def ")]
    blip_at = execute.find("keepalive_blip")
    cancel_at = execute.find("prompt exception")
    assert blip_at >= 0 and cancel_at >= 0 and blip_at < cancel_at
    assert "blip_hold_session_ids" in execute
    assert "\u2014" not in ACP_BLIP_USER_MSG
    assert "\u2014" not in js[js.find("const ACP_BLIP_TEXT") : js.find("const ACP_BLIP_TEXT") + 180]
