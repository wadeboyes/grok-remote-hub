"""Build index, stored summaries, and resume argv. No live grok process."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hub.build_actions import execute_build_action
from hub.build_index import build_catalog, build_items
from hub.build_summaries import (
    SummaryStore,
    grok_due,
    parse_grok_summary_output,
    plain,
    refresh_item_summary,
    summary_argv,
)
from hub.grok_bin import prefer_grok_exe, resume_command


SID_NEW = "019f493c-af12-7652-a6d8-bf645c10921c"
SID_OLD = "019f06ee-305f-7903-b295-d706c08f0b4f"
SID_SIDE = "019f2222-2222-2222-2222-222222222222"
SID_SCRATCH = "019f3333-3333-3333-3333-333333333333"


def _summary(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _session(root: Path, encoded: str, sid: str, cwd: str, updated: str, title: str) -> Path:
    folder = root / encoded / sid
    _summary(
        folder / "summary.json",
        {
            "info": {"id": sid, "cwd": cwd},
            "generated_title": title,
            "session_summary": f"Building {title} for the demo.",
            "updated_at": updated,
            "num_chat_messages": 2,
            "current_model_id": "grok-build",
        },
    )
    (folder / "updates.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": f"Please build {title}."},
                    }
                ),
                json.dumps(
                    {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": f"Started the {title} scaffold."},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return folder


def _worktree_db(path: Path, rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE worktrees (
            id TEXT PRIMARY KEY,
            path TEXT UNIQUE NOT NULL,
            source_repo TEXT NOT NULL,
            repo_name TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'session',
            creation_mode TEXT NOT NULL DEFAULT 'linked',
            git_ref TEXT,
            head_commit TEXT,
            session_id TEXT,
            creator_pid INTEGER,
            created_at INTEGER NOT NULL,
            last_accessed_at INTEGER,
            status TEXT NOT NULL DEFAULT 'alive',
            metadata TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO worktrees (
            id, path, source_repo, repo_name, kind, creation_mode, git_ref,
            head_commit, session_id, creator_pid, created_at, last_accessed_at,
            status, metadata
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()


def test_index_includes_sessions_outside_grok_builds_and_worktrees(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    grok_home = tmp_path
    _session(
        sessions,
        "D%3A%5Cgrok-builds%5CPipe",
        SID_OLD,
        r"D:\grok-builds\Pipe",
        "2026-07-01T12:00:00Z",
        "Pipe only",
    )
    _session(
        sessions,
        "E%3A%5CSide%5CNotGrokBuilds",
        SID_NEW,
        r"E:\Side\NotGrokBuilds",
        "2026-07-08T12:00:00Z",
        "Side project",
    )
    _session(
        sessions,
        "C%3A%5CUsers%5Cme%5Cdata%5C.grok-hub-summary",
        SID_SCRATCH,
        r"C:\Users\me\data\.grok-hub-summary",
        "2026-07-09T12:00:00Z",
        "Scratch summary",
    )
    wt_path = tmp_path / "worktrees" / "side" / "wt-feature"
    wt_path.mkdir(parents=True)
    (wt_path / ".git").write_text("gitdir: somewhere\n", encoding="utf-8")
    # Newer than both sessions.
    accessed = int(datetime(2026, 7, 12, tzinfo=timezone.utc).timestamp())
    _worktree_db(
        grok_home / "worktrees.db",
        [
            (
                "wt-feature",
                str(wt_path),
                r"E:\Side\NotGrokBuilds",
                "NotGrokBuilds",
                "session",
                "linked",
                "feature",
                "abc123",
                SID_NEW,
                None,
                accessed - 10,
                accessed,
                "alive",
                json.dumps({"label": "Feature tree"}),
            )
        ],
    )
    # Untracked directory worktree, older.
    extra = tmp_path / "worktrees" / "other" / "wt-old"
    extra.mkdir(parents=True)
    (extra / ".git").write_text("gitdir: x\n", encoding="utf-8")
    old_stamp = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
    os.utime(extra, (old_stamp, old_stamp))

    items = build_items(sessions, grok_home, limit=100)
    ids = [(item["kind"], item["id"]) for item in items]
    assert ("session", SID_NEW) in ids
    assert ("session", SID_OLD) in ids
    assert ("worktree", "wt-feature") in ids
    assert all(item["id"] != SID_SCRATCH for item in items)
    assert any(item["kind"] == "worktree" and item["id"].startswith("dir:") for item in items)
    # Newest first: the worktree access time is after the side session.
    assert items[0]["kind"] == "worktree"
    assert items[0]["id"] == "wt-feature"
    assert items[0]["sessionId"] == SID_NEW
    assert items[0]["actionHint"] == "resume"
    cwds = {item["cwd"] for item in items if item["kind"] == "session"}
    assert r"E:\Side\NotGrokBuilds" in cwds
    assert r"D:\grok-builds\Pipe" in cwds
    side = next(item for item in items if item["id"] == SID_NEW)
    assert side["worktreePath"] == str(wt_path)


def test_catalog_stores_summary_for_instant_reload(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    _session(
        sessions,
        "E%3A%5CSide%5CApp",
        SID_SIDE,
        r"E:\Side\App",
        "2026-07-02T12:00:00Z",
        "Phone app",
    )
    store = SummaryStore(tmp_path / "data" / "build_summaries.json")
    first = build_catalog(sessions, tmp_path, store, limit=20)
    assert first["count"] == 1
    summary = first["items"][0]["summary"]
    assert summary["text"]
    assert "Phone app" in summary["what"] or "Phone app" in summary["text"]
    for key in ("what", "built", "progress", "next", "text"):
        assert "\u2014" not in summary[key]
        assert "\u2013" not in summary[key]
    # Second load does not need to invent text; it comes from the store.
    store2 = SummaryStore(tmp_path / "data" / "build_summaries.json")
    second = build_catalog(sessions, tmp_path, store2, limit=20)
    assert second["items"][0]["summary"]["text"] == summary["text"]
    assert second["items"][0]["summary"]["source"] == "local"


def test_summary_refresh_uses_grok_p_and_updates_when_fingerprint_changes(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    folder = _session(
        sessions,
        "E%3A%5CSide%5CApp",
        SID_SIDE,
        r"E:\Side\App",
        "2026-07-02T12:00:00Z",
        "Phone app",
    )
    store = SummaryStore(tmp_path / "summaries.json")
    build_catalog(sessions, tmp_path, store, limit=10)
    item = build_items(sessions, tmp_path, limit=10)[0]
    seen: dict[str, list[str]] = {}

    def runner(argv: list[str], scratch: Path) -> str:
        seen["argv"] = argv
        seen["scratch"] = [str(scratch)]
        return json.dumps(
            {
                "text": json.dumps(
                    {
                        "what": "This is the phone app session.",
                        "built": "It was building a sideload client.",
                        "progress": "It got as far as the session list.",
                        "next": "Next, resume it from the phone.",
                    }
                ),
                "sessionId": "",
                "stopReason": "end_turn",
            }
        )

    exe = tmp_path / "grok.exe"
    exe.write_text("", encoding="utf-8")
    row = refresh_item_summary(
        item,
        store,
        grok_exe=str(exe),
        sessions_root=sessions,
        scratch_root=tmp_path,
        runner=runner,
        force=True,
    )
    assert row["source"] == "grok"
    assert row["summary"]["next"].startswith("Next, resume")
    argv = seen["argv"]
    assert argv[0] == str(exe)
    assert "--always-approve" not in argv
    assert "-p" in argv
    assert "--output-format" in argv
    assert "grok.bat" not in " ".join(argv).lower()
    assert not grok_due(row, item["fingerprint"])

    # Session file change makes the stored summary stale and due again.
    updates = folder / "updates.jsonl"
    updates.write_text(updates.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    changed = build_items(sessions, tmp_path, limit=10)[0]
    assert changed["fingerprint"] != item["fingerprint"]
    catalog = build_catalog(sessions, tmp_path, store, limit=10)
    # History changes rebuild the visible summary immediately and queue grok again.
    assert catalog["items"][0]["summary"]["stale"] is False
    assert catalog["items"][0]["summary"]["source"] == "local"
    assert "Phone app" in catalog["items"][0]["summary"]["text"]
    assert grok_due(store.get(f"session:{SID_SIDE}"), changed["fingerprint"])


def test_parse_grok_summary_strips_dashes() -> None:
    raw = json.dumps(
        {
            "text": json.dumps(
                {
                    "what": "A session \u2014 the app.",
                    "built": "Built the list\u2013view.",
                    "progress": "Reached chat.",
                    "next": "Resume next.",
                }
            )
        }
    )
    fields, _sid = parse_grok_summary_output(raw)
    assert fields is not None
    assert "\u2014" not in fields["text"]
    assert "\u2013" not in fields["text"]
    assert "A session" in fields["what"]


def test_resume_command_uses_exe_not_bat(tmp_path: Path) -> None:
    bat = tmp_path / "grok.bat"
    exe = tmp_path / "grok.exe"
    bat.write_text("@echo off\n", encoding="utf-8")
    exe.write_text("", encoding="utf-8")
    assert prefer_grok_exe(str(bat)) == str(exe)
    argv = resume_command(str(bat), r"E:\Side\App", SID_SIDE)
    assert argv == [str(exe), "--cwd", r"E:\Side\App", "--resume", SID_SIDE]
    assert summary_argv(str(exe), str(tmp_path / "scratch"), "hi")[0] == str(exe)


def test_plain_removes_dash_characters() -> None:
    assert "\u2014" not in plain("left \u2014 right")
    assert "\u2013" not in plain("left \u2013 right")


def test_execute_resume_and_restart(tmp_path: Path) -> None:
    exe = tmp_path / "grok.exe"
    exe.write_text("", encoding="utf-8")

    class FakeAcp:
        connected = True

        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def session_new(self, cwd: str) -> str:
            self.calls.append(("new", cwd))
            return "019f4444-4444-4444-4444-444444444444"

    class FakeHub:
        def __init__(self) -> None:
            self.acp = FakeAcp()
            self.config = type("C", (), {"grok_bin": str(exe)})()
            self.recorded: tuple[str, str] | None = None
            self.ensured: tuple[str, str] | None = None

        def _record_hub_session(self, sid: str, cwd: str) -> None:
            self.recorded = (sid, cwd)

        def _stamp_origin_sync(self, sid: str, origin: str) -> bool:
            return True

        async def _ensure_hub_agent_session(self, view, cwd, ws, *, notify_switch):
            self.ensured = (view, cwd)
            return (view, False, "reuse_cwd", "reuse")

    hub = FakeHub()
    item = {
        "kind": "session",
        "id": SID_SIDE,
        "sessionId": SID_SIDE,
        "cwd": r"E:\Side\App",
        "title": "Phone app",
    }

    import asyncio

    resumed = asyncio.run(execute_build_action(hub, item, mode="resume"))
    assert resumed["action"] == "resume"
    assert resumed["command"] == [str(exe), "--cwd", r"E:\Side\App", "--resume", SID_SIDE]
    assert resumed["liveSessionId"] == SID_SIDE
    assert "embed=chat" in resumed["chatPath"]
    assert hub.ensured == (SID_SIDE, r"E:\Side\App")

    worktree = {
        "kind": "worktree",
        "id": "wt-empty",
        "sessionId": "",
        "cwd": r"E:\Side\App-wt",
        "title": "Empty tree",
    }
    restarted = asyncio.run(execute_build_action(hub, worktree, mode="auto"))
    assert restarted["action"] == "restart"
    assert restarted["command"][0] == str(exe)
    assert "--resume" in restarted["command"]
    assert hub.recorded is not None
    assert hub.recorded[1] == r"E:\Side\App-wt"
    assert hub.acp.calls == [("new", r"E:\Side\App-wt")]


def test_worktree_without_link_resumes_latest_session(tmp_path: Path) -> None:
    """A worktree card with no database session id resumes the newest cwd match."""
    sessions = tmp_path / "sessions"
    cwd = r"E:\Side\updates"
    older = "019f1111-1111-1111-1111-111111111111"
    newer = "019f5555-5555-5555-5555-555555555555"
    sub = "019f6666-6666-6666-6666-666666666666"
    _session(sessions, "E%3A%5CSide%5Cupdates", older, cwd, "2026-07-01T12:00:00Z", "Old updates")
    _session(sessions, "E%3A%5CSide%5Cupdates", newer, "E:/Side/updates", "2026-10-14T10:00:00Z", "Updates")
    sub_dir = sessions / "E%3A%5CSide%5Cupdates" / sub
    _summary(
        sub_dir / "summary.json",
        {
            "info": {"id": sub, "cwd": cwd},
            "generated_title": "Sub updates",
            "session_summary": "A subagent run.",
            "updated_at": "2026-10-14T11:00:00Z",
            "session_kind": "subagent",
            "num_chat_messages": 2,
        },
    )
    grok_home = tmp_path / "home"
    accessed = int(datetime(2026, 10, 14, 12, tzinfo=timezone.utc).timestamp())
    _worktree_db(
        grok_home / "worktrees.db",
        [
            (
                "wt-updates",
                "E:/Side/updates",
                cwd,
                "updates",
                "session",
                "linked",
                "",
                "",
                "",
                None,
                accessed,
                accessed,
                "alive",
                json.dumps({"label": "updates"}),
            )
        ],
    )

    items = build_items(sessions, grok_home, limit=100)
    card = next(item for item in items if item["id"] == "wt-updates")
    assert card["kind"] == "worktree"
    assert card["sessionId"] == newer
    assert card["actionHint"] == "resume"

    exe = tmp_path / "grok.exe"
    exe.write_text("", encoding="utf-8")

    class FakeAcp:
        connected = True

        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def session_new(self, folder: str) -> str:
            self.calls.append(("new", folder))
            return "019f9999-9999-9999-9999-999999999999"

    class FakeHub:
        def __init__(self) -> None:
            self.acp = FakeAcp()
            self.config = type(
                "C",
                (),
                {"grok_bin": str(exe), "sessions_root": sessions},
            )()
            self.recorded = None

        def _record_hub_session(self, sid: str, folder: str) -> None:
            self.recorded = (sid, folder)

        def _stamp_origin_sync(self, sid: str, origin: str) -> bool:
            return True

        async def _ensure_hub_agent_session(self, view, folder, ws, *, notify_switch):
            return (view, False, "reuse_cwd", "reuse")

    hub = FakeHub()
    # The phone used to send restart because the card had no linked id.
    import asyncio

    resumed = asyncio.run(
        execute_build_action(
            hub,
            {
                "kind": "worktree",
                "id": "wt-updates",
                "sessionId": "",
                "cwd": cwd,
                "title": "updates",
            },
            mode="restart",
        )
    )
    assert resumed["action"] == "resume"
    assert resumed["viewSessionId"] == newer
    assert f"open={newer}" in resumed["chatPath"]
    assert "embed=chat" in resumed["chatPath"]
    assert hub.acp.calls == []
    assert hub.recorded is None


def test_worktree_with_no_session_still_starts_fresh(tmp_path: Path) -> None:
    exe = tmp_path / "grok.exe"
    exe.write_text("", encoding="utf-8")
    sessions = tmp_path / "sessions"
    sessions.mkdir()

    class FakeAcp:
        connected = True

        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def session_new(self, folder: str) -> str:
            self.calls.append(("new", folder))
            return "019f7777-7777-7777-7777-777777777777"

    class FakeHub:
        def __init__(self) -> None:
            self.acp = FakeAcp()
            self.config = type(
                "C",
                (),
                {"grok_bin": str(exe), "sessions_root": sessions},
            )()

        def _record_hub_session(self, sid: str, folder: str) -> None:
            return None

        def _stamp_origin_sync(self, sid: str, origin: str) -> bool:
            return True

        async def _ensure_hub_agent_session(self, view, folder, ws, *, notify_switch):
            raise AssertionError("resume should not run when the folder has no session")

    import asyncio

    hub = FakeHub()
    restarted = asyncio.run(
        execute_build_action(
            hub,
            {
                "kind": "worktree",
                "id": "wt-empty",
                "sessionId": "",
                "cwd": r"E:\Side\brand-new",
                "title": "brand new",
            },
            mode="auto",
        )
    )
    assert restarted["action"] == "restart"
    assert "embed=chat" in restarted["chatPath"]
    assert hub.acp.calls == [("new", r"E:\Side\brand-new")]


def test_grok_retry_window() -> None:
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    record = {
        "fingerprint": "same",
        "source": "local",
        "grokAttemptAt": (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
    }
    assert grok_due(record, "same", now) is False
    assert grok_due(record, "changed", now) is True
    assert grok_due({"fingerprint": "same", "source": "grok"}, "same", now) is False


def test_build_routes_stay_behind_hub_token() -> None:
    server = Path("hub/server.py").read_text(encoding="utf-8")
    start = server.find("async def _auth_middleware")
    end = server.find("async def _on_startup")
    middleware = server[start:end]
    assert 'request.path == "/health"' in middleware
    assert "/api/build" not in middleware
    assert server.count('add_get("/api/build/index"') == 1
    assert server.count('add_post("/api/build/resume"') == 1
    assert server.count('add_post("/api/build/archive"') == 1
    assert server.count('add_post("/api/build/restore"') == 1
    assert server.count('add_post("/api/build/worktree/remove"') == 1
    assert "handle_delete_session" not in server[server.find("def handle_build_archive") : server.find("def handle_build_worktree_remove") + 800]
    supervisor = Path("hub/agent_supervisor.py").read_text(encoding="utf-8")
    assert "--always-approve" in supervisor


def test_deep_link_hook_present() -> None:
    js = Path("static/app.js").read_text(encoding="utf-8")
    css = Path("static/app.css").read_text(encoding="utf-8")
    html = Path("static/index.html").read_text(encoding="utf-8")
    assert "function readDeepLink" in js
    assert "embed=chat" in js or 'embed") === "chat"' in js
    assert "html.embed-chat" in css
    assert "html.embed-chat #empty-main" in css
    assert "html.embed-chat #empty-main" in html
    # Deep link still opens when /api/sessions never returns ok.
    open_at = js.find("deepLink.openId || (saved && saved.sessionId)")
    fetch_at = js.find('fetch(apiUrl("/api/sessions"))')
    assert fetch_at >= 0 and open_at > fetch_at
    ws = js[js.find("function wsUrl") : js.find("function wsUrl") + 400]
    assert "token=" in ws
    assert 'encodeURIComponent(t)' in ws
