"""Dangling tool recovery: cancel before the next prompt, then offer a restart."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from hub.config import Config
from hub.server import Hub
from hub.turn_recover import (
    LOAD_AFTER_RESTART_MSG,
    OTHER_SESSIONS_MSG,
    REATTACHED_MSG,
    RELOADED_MSG,
    RESTART_BUTTON_LABEL,
    RESTART_FAILED_MSG,
    UNRESUMABLE_RESTART_MSG,
    WEDGED_INTERRUPTED_MSG,
    WEDGED_PENDING_MSG,
    clearing_message,
    find_dangling_turn,
    wedged_no_output_message,
)

ROOT = Path(__file__).resolve().parents[1]
SID = "01a13a07-a83b-4000-8000-000000000001"
OTHER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def _line(update: dict) -> str:
    return json.dumps(
        {
            "method": "session/update",
            "params": {"sessionId": SID, "update": update},
        }
    )


def _execute(title: str = "Execute", status: str | None = "pending", tool_id: str = "call-1") -> dict:
    update = {
        "sessionUpdate": "tool_call",
        "toolCallId": tool_id,
        "title": title,
        "_meta": {"x.ai/tool": {"label": title, "kind": "execute"}},
    }
    if status is not None:
        update["status"] = status
    return update


def _update(tool_id: str, status: str) -> dict:
    return {
        "sessionUpdate": "tool_call_update",
        "toolCallId": tool_id,
        "title": "Execute",
        "status": status,
    }


def _write(path: Path, updates: list[dict]) -> None:
    path.write_text("\n".join(_line(item) for item in updates) + "\n", encoding="utf-8")


def test_pending_execute_at_end_is_dangling() -> None:
    path = Path("/tmp/updates-pending.jsonl")
    try:
        _write(
            path,
            [
                {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "scan the repo"},
                },
                _execute(),
            ],
        )
        found = find_dangling_turn(path)
        assert found is not None
        assert found.reason == "pending_tool"
        assert found.label == "Execute"
        assert found.tool_call_id == "call-1"
        text = clearing_message(found.reason, found.label)
        assert text == (
            "The last turn stopped while Execute was still pending. "
            "Clearing that, then continuing."
        )
    finally:
        path.unlink(missing_ok=True)


def test_missing_tool_status_defaults_to_pending() -> None:
    path = Path("/tmp/updates-missing-status.jsonl")
    try:
        _write(path, [_execute(status=None)])
        found = find_dangling_turn(path)
        assert found is not None
        assert found.reason == "pending_tool"
    finally:
        path.unlink(missing_ok=True)


def test_completed_tool_and_turn_is_clean() -> None:
    path = Path("/tmp/updates-clean.jsonl")
    try:
        _write(
            path,
            [
                _execute(status="in_progress"),
                _update("call-1", "completed"),
                {"sessionUpdate": "turn_completed"},
            ],
        )
        assert find_dangling_turn(path) is None
    finally:
        path.unlink(missing_ok=True)


def test_tool_call_update_completed_clears_pending() -> None:
    path = Path("/tmp/updates-updated.jsonl")
    try:
        _write(
            path,
            [
                _execute(),
                _update("call-1", "completed"),
                {"sessionUpdate": "turn_completed"},
            ],
        )
        assert find_dangling_turn(path) is None
    finally:
        path.unlink(missing_ok=True)


def test_activity_without_turn_completed_is_interrupted() -> None:
    path = Path("/tmp/updates-interrupted.jsonl")
    try:
        _write(
            path,
            [
                {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "continue"},
                },
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "working"},
                },
            ],
        )
        found = find_dangling_turn(path)
        assert found is not None
        assert found.reason == "interrupted"
        assert "interrupted" in clearing_message(found.reason, found.label)
        assert wedged_no_output_message("interrupted") == WEDGED_INTERRUPTED_MSG
    finally:
        path.unlink(missing_ok=True)


def test_user_text_after_turn_completed_is_interrupted() -> None:
    path = Path("/tmp/updates-after-done.jsonl")
    try:
        _write(
            path,
            [
                _execute(status="completed"),
                {"sessionUpdate": "turn_completed"},
                {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "continue"},
                },
            ],
        )
        found = find_dangling_turn(path)
        assert found is not None
        assert found.reason == "interrupted"
    finally:
        path.unlink(missing_ok=True)


def test_tail_keeps_pending_execute_at_eof() -> None:
    path = Path("/tmp/updates-tail.jsonl")
    try:
        pending = _line(_execute()) + "\n"
        pad = (b'{"not":"a full line"}\n' + (b"x" * 4000) + b"\n")
        path.write_bytes(pad + pending.encode("utf-8"))
        found = find_dangling_turn(path, max_bytes=700)
        assert found is not None
        assert found.reason == "pending_tool"
        assert found.label == "Execute"
    finally:
        path.unlink(missing_ok=True)


def test_plain_copy_has_no_dash_and_does_not_say_send_again() -> None:
    import hub.turn_recover as recover

    texts = [
        WEDGED_PENDING_MSG,
        WEDGED_INTERRUPTED_MSG,
        RELOADED_MSG,
        REATTACHED_MSG,
        OTHER_SESSIONS_MSG,
        UNRESUMABLE_RESTART_MSG,
        RESTART_FAILED_MSG,
        LOAD_AFTER_RESTART_MSG,
        RESTART_BUTTON_LABEL,
        clearing_message("pending_tool", "Execute"),
        clearing_message("pending_tool", "powershell -Command Get-ChildItem very long"),
        clearing_message("interrupted", ""),
    ]
    for name, value in vars(recover).items():
        if isinstance(value, str):
            texts.append(value)
    for text in texts:
        assert "\u2014" not in text
        assert "\u2013" not in text
        assert "send again" not in text.lower()
    assert RESTART_BUTTON_LABEL == "Restart this session"
    assert "still pending" in WEDGED_PENDING_MSG
    assert "no output" in WEDGED_PENDING_MSG.lower() or "produced no output" in WEDGED_PENDING_MSG


def _session_tree(tmp: Path) -> Path:
    folder = tmp / "sessions" / "proj" / SID
    folder.mkdir(parents=True)
    summary = {
        "hub_title": "Updates",
        "updated_at": "2026-10-14T12:39:00Z",
        "info": {"id": SID, "cwd": str(tmp / "work")},
    }
    (folder / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return folder


def _hub(tmp: Path) -> Hub:
    import hub.server as server_mod

    cfg = Config()
    cfg.sessions_root = tmp / "sessions"
    cfg.log_dir = tmp / "logs"
    cfg.agent_secret_path = tmp / "agent.secret"
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    previous = server_mod.REMOTE_SESSIONS_FILE
    server_mod.REMOTE_SESSIONS_FILE = tmp / "remote-sessions.json"
    try:
        return Hub(cfg)
    finally:
        server_mod.REMOTE_SESSIONS_FILE = previous


def test_resume_prompt_cancels_dangling_execute_once() -> None:
    import hub.server as server_mod

    async def run(tmp: Path) -> None:
        folder = _session_tree(tmp)
        _write(folder / "updates.jsonl", [_execute()])
        hub = _hub(tmp)
        hub.acp.notify_agent_cancel = AsyncMock(return_value=True)
        hub._broadcast_turn = AsyncMock()
        calls = {"n": 0}
        real = server_mod.find_dangling_turn

        def wrapped(path, **kwargs):
            calls["n"] += 1
            return real(path, **kwargs)

        server_mod.find_dangling_turn = wrapped
        try:
            assert await hub._cancel_dangling_before_prompt(SID, SID) is True
            assert calls["n"] == 1
            hub.acp.notify_agent_cancel.assert_awaited()
            msg = hub._broadcast_turn.await_args.args[2]
            assert "Execute" in msg
            assert "Clearing that, then continuing." in msg
            assert "send again" not in msg.lower()
            assert SID in hub._wedged_sessions
            assert hub._wedged_failure_payload(SID) == (
                WEDGED_PENDING_MSG,
                "restart_session",
            )
            # Hot reuse does not reread the transcript, but still cancels.
            assert await hub._cancel_dangling_before_prompt(SID, SID) is True
            assert calls["n"] == 1
            assert hub.acp.notify_agent_cancel.await_count == 2
            assert hub._broadcast_turn.await_count == 1
        finally:
            server_mod.find_dangling_turn = real

    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        asyncio.run(run(Path(raw)))


def test_active_turn_is_not_cancelled() -> None:
    async def run(tmp: Path) -> None:
        folder = _session_tree(tmp)
        _write(folder / "updates.jsonl", [_execute()])
        hub = _hub(tmp)
        hub.acp.notify_agent_cancel = AsyncMock(return_value=True)
        hub.acp.active_turns[SID] = {"cwd_key": "proj"}
        assert await hub._cancel_dangling_before_prompt(SID, SID) is False
        hub.acp.notify_agent_cancel.assert_not_awaited()
        assert SID not in hub._dangling_checked

    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        asyncio.run(run(Path(raw)))


def test_clean_transcript_does_not_cancel() -> None:
    async def run(tmp: Path) -> None:
        folder = _session_tree(tmp)
        _write(
            folder / "updates.jsonl",
            [_execute(status="completed"), {"sessionUpdate": "turn_completed"}],
        )
        hub = _hub(tmp)
        hub.acp.notify_agent_cancel = AsyncMock(return_value=True)
        hub._broadcast_turn = AsyncMock()
        assert await hub._cancel_dangling_before_prompt(SID, SID) is False
        hub.acp.notify_agent_cancel.assert_not_awaited()
        assert hub._wedged_failure_payload(SID) == ("", None)

    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        asyncio.run(run(Path(raw)))


def test_successful_load_rechecks_on_the_next_prompt() -> None:
    async def run(tmp: Path) -> None:
        _session_tree(tmp)
        hub = _hub(tmp)
        hub._dangling_checked.add(SID)
        hub.acp.session_load = AsyncMock(return_value={})
        assert await hub._try_session_load(SID, str(tmp / "work")) is True
        assert SID not in hub._dangling_checked

    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        asyncio.run(run(Path(raw)))


def test_restart_reloads_without_killing_the_process() -> None:
    async def run(tmp: Path) -> None:
        _session_tree(tmp)
        hub = _hub(tmp)
        hub.acp.notify_agent_cancel = AsyncMock(return_value=True)
        hub.acp.session_new = AsyncMock(side_effect=AssertionError("session/new"))
        hub._try_session_load = AsyncMock(return_value=True)
        hub._restart_agent_process = AsyncMock(return_value=(True, None))
        hub._broadcast_turn = AsyncMock()
        hub._record_hub_session = MagicMock()
        result = await hub._restart_wedged_session(SID, str(tmp / "work"))
        assert result["ok"] is True
        assert result["mode"] == "reload"
        assert result["message"] == RELOADED_MSG
        hub._restart_agent_process.assert_not_awaited()
        hub.acp.session_new.assert_not_awaited()
        hub._try_session_load.assert_awaited()

    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        asyncio.run(run(Path(raw)))


def test_restart_refuses_to_kill_while_another_session_is_in_a_turn() -> None:
    async def run(tmp: Path) -> None:
        _session_tree(tmp)
        hub = _hub(tmp)
        hub.acp.notify_agent_cancel = AsyncMock(return_value=True)
        hub.acp.session_new = AsyncMock(side_effect=AssertionError("session/new"))
        hub._try_session_load = AsyncMock(return_value=False)
        hub._restart_agent_process = AsyncMock(return_value=(True, None))
        hub._broadcast_turn = AsyncMock()
        hub.acp.active_turns[OTHER] = {"cwd_key": "other"}
        result = await hub._restart_wedged_session(SID, str(tmp / "work"))
        assert result["ok"] is False
        assert result["mode"] == "others_running"
        assert result["message"] == OTHER_SESSIONS_MSG
        hub._restart_agent_process.assert_not_awaited()
        hub.acp.session_new.assert_not_awaited()

    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        asyncio.run(run(Path(raw)))


def test_restart_reattaches_process_only_when_load_fails_and_idle() -> None:
    async def run(tmp: Path) -> None:
        _session_tree(tmp)
        hub = _hub(tmp)
        hub.acp.notify_agent_cancel = AsyncMock(return_value=True)
        hub.acp.session_new = AsyncMock(side_effect=AssertionError("session/new"))
        hub._try_session_load = AsyncMock(side_effect=[False, True])
        hub._restart_agent_process = AsyncMock(return_value=(True, None))
        hub._broadcast_turn = AsyncMock()
        hub._record_hub_session = MagicMock()
        result = await hub._restart_wedged_session(SID, str(tmp / "work"))
        assert result["ok"] is True
        assert result["mode"] == "reattach"
        assert result["message"] == REATTACHED_MSG
        hub._restart_agent_process.assert_awaited()
        hub.acp.session_new.assert_not_awaited()
        assert "send again" not in result["message"].lower()

    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        asyncio.run(run(Path(raw)))


def test_restart_does_not_kill_a_missing_session() -> None:
    async def run(tmp: Path) -> None:
        _session_tree(tmp)
        hub = _hub(tmp)
        hub.acp.notify_agent_cancel = AsyncMock(return_value=True)
        hub._try_session_load = AsyncMock(return_value=False)
        hub._restart_agent_process = AsyncMock(return_value=(True, None))
        hub._broadcast_turn = AsyncMock()
        hub._unresumable_session_ids.add(SID)
        result = await hub._restart_wedged_session(SID, "")
        assert result["mode"] == "unresumable"
        hub._restart_agent_process.assert_not_awaited()

    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        asyncio.run(run(Path(raw)))


def test_server_cancels_before_prompt_and_keeps_token_auth() -> None:
    server = (ROOT / "hub" / "server.py").read_text(encoding="utf-8")
    execute = server[server.find("async def _execute_prompt") :]
    execute = execute[: execute.find("\n    async def ")]
    cancel_at = execute.find("_cancel_dangling_before_prompt")
    prompt_at = execute.find("session_prompt")
    assert cancel_at >= 0 and prompt_at >= 0 and cancel_at < prompt_at

    retry = server[server.find("async def _no_output_auto_retry") :]
    retry = retry[: retry.find("\n    async def ")]
    retry_cancel = retry.find("_cancel_dangling_before_prompt")
    retry_prompt = retry.find("session_prompt")
    assert retry_cancel >= 0 and retry_prompt >= 0 and retry_cancel < retry_prompt
    assert "_restart_agent_process(" not in retry
    wedged = retry.find("_wedged_failure_payload")
    assert wedged >= 0
    after = retry[wedged:]
    assert after.find("if wedged_msg") < after.find("NO_OUTPUT_RETRY_FAILED_MSG")
    assert "recovery=recovery" in after

    dangling = server[server.find("async def _cancel_dangling_before_prompt") :]
    dangling = dangling[: dangling.find("\n    async def ")]
    assert "_notify_cancel_bounded" in dangling
    assert "find_dangling_turn" in dangling
    assert "is_session_active" in dangling

    notify = server[server.find("async def _notify_cancel_bounded") :]
    notify = notify[: notify.find("\n    async def ")]
    assert "notify_agent_cancel" in notify

    restart = server[server.find("async def _restart_wedged_session") :]
    restart = restart[: restart.find("\n    async def ")]
    assert "_try_session_load" in restart
    assert "_restart_agent_process" in restart
    assert "forget_warm_session" in restart
    assert "session_new" not in restart
    assert "session/new" not in restart
    assert "_other_turns_active" in restart

    assert server.count('"/api/sessions/{id}/restart-turn"') == 1
    assert 'typ == "restart_session"' in server
    start = server.find("async def _auth_middleware")
    end = server.find("async def _on_startup")
    middleware = server[start:end]
    assert 'request.path == "/health"' in middleware
    assert "restart-turn" not in middleware
    assert "restart_session" not in middleware

    supervisor = (ROOT / "hub" / "agent_supervisor.py").read_text(encoding="utf-8")
    assert "--always-approve" in supervisor

    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "20261014f" in html
    assert 'msg.recovery === "restart_session"' in js
    assert "Restart this session" in js
    offer = js[js.find("function offerSessionRestart") :]
    offer = offer[: offer.find("\n  function ")]
    assert "Restart this session" in offer
    assert "send again" not in offer.lower()
    assert "\u2014" not in offer and "\u2013" not in offer
    restart_fn = js[js.find("function restartThisSession") :]
    restart_fn = restart_fn[: restart_fn.find("\n  function ")]
    assert 'type: "restart_session"' in restart_fn
    assert "/restart-turn" in restart_fn
    assert "resendLastPrompt" not in restart_fn
