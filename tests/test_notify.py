"""Phone alerts: turn end and pending question, behind hub_token."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from hub.config import Config
from hub.notify_feed import (
    LINE_ERROR,
    LINE_FINISHED,
    LINE_NEEDS_ANSWER,
    NotifyFeed,
    classify_turn_notify,
    group_key,
    should_notify_session,
)
from hub.server import Hub
from hub.session_policy import ACP_BLIP_USER_MSG

ROOT = Path(__file__).resolve().parents[1]


class _Req:
    def __init__(self, query: dict[str, str]) -> None:
        self.query = query


def test_lines_are_plain_and_skip_the_open_session() -> None:
    assert LINE_FINISHED == "Finished"
    assert LINE_ERROR == "Stopped with an error"
    assert LINE_NEEDS_ANSWER == "Needs your answer"
    for line in (LINE_FINISHED, LINE_ERROR, LINE_NEEDS_ANSWER):
        assert "\u2014" not in line
        assert "\u2013" not in line
    assert classify_turn_notify("running", None) is None
    assert classify_turn_notify("idle", None) == ("turn_end", "finished")
    assert classify_turn_notify("idle", "ACP disconnected") == ("turn_end", "error")
    assert classify_turn_notify("idle", ACP_BLIP_USER_MSG, blip_text=ACP_BLIP_USER_MSG) is None
    assert classify_turn_notify("idle", None, plan_waiting=True) == (
        "pending_question",
        "permission",
    )
    assert should_notify_session("sess-open", "sess-open") is False
    assert should_notify_session("sess-open", "sess-other") is True
    assert should_notify_session("", "sess-other") is True
    assert should_notify_session("sess-open", "  ") is False
    assert group_key("abc") == "session:abc"
    assert group_key("abc") != group_key("def")


def test_feed_dedupes_and_long_poll_returns_new_events() -> None:
    async def run() -> None:
        feed = NotifyFeed()
        first = feed.publish(
            session_id="s1",
            kind="turn_end",
            reason="finished",
            title="Updates",
            chat_path="/?open=s1&embed=chat",
        )
        assert first is not None
        assert first["line"] == "Finished"
        assert first["title"] == "Updates"
        again = feed.publish(
            session_id="s1",
            kind="turn_end",
            reason="finished",
            title="Updates",
        )
        assert again is None
        question = feed.publish(
            session_id="s2",
            kind="pending_question",
            reason="question",
            title="Tablet notes",
        )
        assert question is not None
        assert question["line"] == "Needs your answer"
        permission = feed.publish(
            session_id="s3",
            kind="pending_question",
            reason="permission",
            title="Plan",
        )
        assert permission is not None
        assert permission["line"] == "Needs your answer"
        assert permission["reason"] == "permission"
        primed, cursor = await feed.poll(-1, 0)
        assert primed == []
        assert cursor == permission["id"]
        events, cursor = await feed.poll(first["id"], 0)
        assert [row["sessionId"] for row in events] == ["s2", "s3"]
        assert cursor == permission["id"]
        empty, _ = await feed.poll(cursor, 0.05)
        assert empty == []
        failed = feed.publish(
            session_id="s1",
            kind="turn_end",
            reason="error",
            title="Updates",
        )
        assert failed is not None
        assert failed["line"] == "Stopped with an error"
        assert feed.publish(session_id="", kind="turn_end", reason="finished", title="x") is None

    asyncio.run(run())


def test_notify_poll_route_is_behind_the_token() -> None:
    server = (ROOT / "hub" / "server.py").read_text(encoding="utf-8")
    start = server.find("async def _auth_middleware")
    end = server.find("async def _on_startup")
    middleware = server[start:end]
    assert 'request.path == "/health"' in middleware
    assert "/api/notify" not in middleware
    assert server.count('add_get("/api/notify/poll"') == 1
    assert "--always-approve" in (ROOT / "hub" / "agent_supervisor.py").read_text(encoding="utf-8")

    async def run() -> None:
        hub = Hub(Config())
        hub.notify_feed.publish(
            session_id="live-1",
            kind="turn_end",
            reason="finished",
            title="Updates",
            chat_path="/?open=live-1&embed=chat",
        )
        response = await hub.handle_notify_poll(_Req({"since": "0", "wait": "0"}))
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["cursor"] >= 1
        assert payload["events"][0]["sessionId"] == "live-1"
        assert payload["events"][0]["line"] == "Finished"
        assert payload["events"][0]["kind"] == "turn_end"
        quiet = await hub.handle_notify_poll(_Req({"since": "-1", "wait": "0"}))
        primed = json.loads(quiet.body.decode("utf-8"))
        assert primed["events"] == []
        assert primed["cursor"] == payload["cursor"]

    asyncio.run(run())


def test_android_alerts_group_per_session_and_skip_the_open_chat() -> None:
    kotlin = (ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "wadeboyes" / "grokremote")
    watch = (kotlin / "TurnWatch.kt").read_text(encoding="utf-8")
    settings = (kotlin / "SettingsStore.kt").read_text(encoding="utf-8")
    manifest = (ROOT / "android" / "app" / "src" / "main" / "AndroidManifest.xml").read_text(encoding="utf-8")
    app = (kotlin / "ui" / "GrokApp.kt").read_text(encoding="utf-8")
    gradle = (ROOT / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    assert "POST_NOTIFICATIONS" in manifest
    assert "FOREGROUND_SERVICE_DATA_SYNC" in manifest
    assert 'android:foregroundServiceType="dataSync"' in manifest
    assert "fun shouldAlert" in watch
    assert "notifyGroupKey" in watch
    assert 'setGroup(group)' in watch
    assert "setGroupSummary(true)" in watch
    assert "class TurnWatchWorker" in watch
    assert "PeriodicWorkRequestBuilder" in watch
    assert "15, TimeUnit.MINUTES" in watch
    assert "TurnWatchService.running.get()" in watch
    assert "/api/notify/poll?since=" in (kotlin / "HubClient.kt").read_text(encoding="utf-8")
    assert 'getBoolean(KEY_NOTIFY, true)' in settings
    assert "setOpenSession" in settings
    assert "Finished" in watch
    assert "Needs your answer" in app or "needs your answer" in app
    assert "The open chat stays quiet." in app
    assert "Session alerts" in app
    assert "versionCode = 6" in gradle
    assert 'versionName = "1.0.5"' in gradle
    for path in (watch, app, settings):
        assert "\u2014" not in path
        assert "\u2013" not in path
