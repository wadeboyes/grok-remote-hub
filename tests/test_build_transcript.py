"""Transcript titles and summaries for the phone list."""

from __future__ import annotations

import json
from pathlib import Path

from hub.build_index import build_catalog, build_items
from hub.build_summaries import SummaryStore, file_fingerprint, refresh_item_summary
from hub.build_transcript import is_placeholder_summary, load_conversation

SID = "019f5555-5555-5555-5555-555555555555"
SID_EMPTY = "019f6666-6666-6666-6666-666666666666"
SID_PROMPT = "019f7777-7777-7777-7777-777777777777"
SID_EVENT = "019f8888-8888-8888-8888-888888888888"
SID_BIG = "019f9999-9999-9999-9999-999999999999"
SID_SCRATCH = "019faaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _summary(folder: Path, sid: str, cwd: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": sid, "cwd": cwd},
                "generated_title": "",
                "session_summary": "",
                "updated_at": "2026-10-12T12:00:00Z",
                "num_chat_messages": 0,
            }
        ),
        encoding="utf-8",
    )


def _chat(folder: Path, rows: list[dict]) -> None:
    lines = [json.dumps(row) for row in rows]
    (folder / "chat_history.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_chat_history_sets_title_and_four_parts(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    folder = sessions / "C%3A%5CUsers%5CWADEBOYES" / SID
    _summary(folder, SID, r"C:\Users\WADEBOYES")
    _chat(
        folder,
        [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Add a dark mode toggle to the settings screen."}],
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "I added the toggle and wired it to the theme store."}],
            },
        ],
    )
    store = SummaryStore(tmp_path / "data" / "build_summaries.json")
    # Cached filler from the old reader must not stick.
    store.put(
        f"session:{SID}",
        {
            "fingerprint": "stale",
            "summaryVersion": 1,
            "source": "grok",
            "hasUserTurns": False,
            "summary": {
                "what": "This is an untitled Grok Build coding session in C:\\Users\\WADEBOYES.",
                "built": "The session record does not name anything that was being built.",
                "progress": "There is no saved summary, last request, assistant reply, or open plan item on file.",
                "next": "Start a concrete task in this session so the next status has work to report.",
                "text": "Start a concrete task in this session so the next status has work to report.",
            },
        },
    )
    catalog = build_catalog(sessions, tmp_path, store, limit=20)
    item = catalog["items"][0]
    assert item["title"] != "Untitled session"
    assert "dark mode" in item["title"].lower()
    summary = item["summary"]
    blob = " ".join(summary[key] for key in ("what", "built", "progress", "next", "text")).lower()
    assert "dark mode" in blob
    assert "theme store" in blob
    assert "concrete task" not in blob
    assert "no saved summary" not in blob
    for key in ("what", "built", "progress", "next", "text", "title"):
        value = item["title"] if key == "title" else summary[key]
        assert "\u2014" not in value
        assert "\u2013" not in value
    assert "_displayTitle" not in item
    assert is_placeholder_summary(summary) is False


def test_request_response_and_text_only_prompt(tmp_path: Path) -> None:
    folder = tmp_path / "session"
    folder.mkdir()
    (folder / "chat_history.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "kind": "request",
                        "parts": [{"type": "text", "text": "Plan the warehouse pick list."}],
                    }
                ),
                json.dumps(
                    {
                        "kind": "response",
                        "parts": [{"type": "text", "text": "The pick list is grouped by aisle."}],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (folder / "prompt_history.jsonl").write_text(
        json.dumps({"text": "Ship the tablet layout tonight."}) + "\n",
        encoding="utf-8",
    )
    conversation = load_conversation(folder)
    assert conversation.has_user_turns
    assert "warehouse" in conversation.display_title().lower() or "tablet layout" in conversation.display_title().lower()
    blob = " ".join(text for _role, text in conversation.recent).lower()
    assert "aisle" in blob
    assert "tablet layout" in " ".join(conversation.first_users).lower()


def test_prompt_history_and_events_are_enough(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    prompt_dir = sessions / "D%3A%5CReports" / SID_PROMPT
    _summary(prompt_dir, SID_PROMPT, r"D:\Reports")
    (prompt_dir / "prompt_history.jsonl").write_text(
        json.dumps({"prompt": "Rename the export button to Save copy on the report page."}) + "\n",
        encoding="utf-8",
    )
    event_dir = sessions / "D%3A%5CShop" / SID_EVENT
    _summary(event_dir, SID_EVENT, r"D:\Shop")
    (event_dir / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "method": "session/update",
                        "params": {
                            "update": {
                                "sessionUpdate": "user_message_chunk",
                                "content": {"type": "text", "text": "Wire the receipt printer in the shop."},
                            }
                        },
                    }
                ),
                json.dumps(
                    {
                        "method": "session/update",
                        "params": {
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": "The printer now prints a test receipt."},
                            }
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    store = SummaryStore(tmp_path / "summaries.json")
    catalog = build_catalog(sessions, tmp_path, store, limit=20)
    by_id = {item["id"]: item for item in catalog["items"]}
    assert "save copy" in by_id[SID_PROMPT]["title"].lower()
    assert "receipt printer" in by_id[SID_EVENT]["title"].lower()
    assert "test receipt" in by_id[SID_EVENT]["summary"]["text"].lower()


def test_large_history_keeps_opening_request_and_latest_reply(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    folder = sessions / "D%3A%5CBilling" / SID_BIG
    _summary(folder, SID_BIG, r"D:\Billing")
    first = json.dumps(
        {"role": "user", "content": "FIRST_REQUEST_ALPHA build the invoice exporter"}
    )
    last = json.dumps(
        {"role": "assistant", "content": "LATEST_REPLY_OMEGA shipped the exporter"}
    )
    middle = json.dumps({"role": "assistant", "content": "MIDDLE_NOISE pad the log"})
    blob = first + "\n" + ((middle + "\n") * 8000) + last + "\n"
    history = folder / "chat_history.jsonl"
    history.write_text(blob, encoding="utf-8")
    assert history.stat().st_size > 192_000
    conversation = load_conversation(folder)
    assert any("FIRST_REQUEST_ALPHA" in text for text in conversation.first_users)
    assert any("LATEST_REPLY_OMEGA" in text for _role, text in conversation.recent)
    store = SummaryStore(tmp_path / "summaries.json")
    catalog = build_catalog(sessions, tmp_path, store, limit=5)
    text = catalog["items"][0]["summary"]["text"]
    assert "FIRST_REQUEST_ALPHA" in text
    assert "LATEST_REPLY_OMEGA" in text
    assert catalog["items"][0]["title"] != "Untitled session"


def test_empty_session_stays_untitled_without_filler(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    folder = sessions / "D%3A%5CEmpty" / SID_EMPTY
    _summary(folder, SID_EMPTY, r"D:\Empty")
    # No chat, prompt, or event files. summary.json alone is not useful content.
    assert build_items(sessions, tmp_path, limit=10) == []
    (folder / "updates.jsonl").write_text("", encoding="utf-8")
    # A non-empty updates file with no user turn is still listed, and stays untitled.
    (folder / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    store = SummaryStore(tmp_path / "summaries.json")
    catalog = build_catalog(sessions, tmp_path, store, limit=5)
    assert len(catalog["items"]) == 1
    item = catalog["items"][0]
    assert item["title"] == "Untitled session"
    blob = item["summary"]["text"].lower()
    assert "concrete task" not in blob
    assert "no saved summary" not in blob
    assert "no user request is stored" in blob


def test_history_change_refreshes_summary(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    folder = sessions / "D%3A%5CApp" / SID
    _summary(folder, SID, r"D:\App")
    _chat(folder, [{"role": "user", "content": "Sketch the first settings layout."}])
    store = SummaryStore(tmp_path / "summaries.json")
    first = build_catalog(sessions, tmp_path, store, limit=5)
    assert "settings layout" in first["items"][0]["summary"]["text"].lower()
    _chat(
        folder,
        [
            {"role": "user", "content": "Sketch the first settings layout."},
            {"role": "assistant", "content": "Shipped the tablet two pane list."},
        ],
    )
    second = build_catalog(sessions, tmp_path, store, limit=5)
    assert "two pane list" in second["items"][0]["summary"]["text"].lower()
    assert second["items"][0]["summary"]["stale"] is False


def test_grok_empty_reply_does_not_replace_a_real_transcript(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    folder = sessions / "D%3A%5CApp" / SID
    _summary(folder, SID, r"D:\App")
    _chat(
        folder,
        [
            {"role": "user", "content": "Add a dark mode toggle to the settings screen."},
            {"role": "assistant", "content": "The toggle is on the settings screen."},
        ],
    )
    store = SummaryStore(tmp_path / "summaries.json")
    build_catalog(sessions, tmp_path, store, limit=5)
    item = build_items(sessions, tmp_path, limit=5)[0]
    seen: dict[str, str] = {}

    def runner(argv: list[str], scratch: Path) -> str:
        seen["prompt"] = argv[argv.index("-p") + 1]
        return json.dumps(
            {
                "text": json.dumps(
                    {
                        "what": "This is an untitled Grok Build coding session.",
                        "built": "The session record does not name anything that was being built.",
                        "progress": "There is no saved summary on file.",
                        "next": "Start a concrete task in this session.",
                    }
                )
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
    assert "dark mode" in seen["prompt"].lower()
    assert "--always-approve" not in seen["prompt"]
    assert row["source"] == "local"
    assert "dark mode" in row["summary"]["text"].lower()
    assert "concrete task" not in row["summary"]["text"].lower()


def test_real_grok_summary_survives_fingerprint_shape_change(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    folder = sessions / "D%3A%5CApp" / SID
    _summary(folder, SID, r"D:\App")
    _chat(
        folder,
        [
            {"role": "user", "content": "Add a dark mode toggle to the settings screen."},
            {"role": "assistant", "content": "I added the toggle and wired it to the theme store."},
        ],
    )
    old = file_fingerprint(
        [folder / name for name in ("summary.json", "updates.jsonl", "chat_history.jsonl", "signals.json")]
    )
    store = SummaryStore(tmp_path / "summaries.json")
    store.put(
        f"session:{SID}",
        {
            "fingerprint": old,
            "source": "grok",
            "summaryVersion": 1,
            "summary": {
                "what": "This session adds dark mode to settings.",
                "built": "It built a theme toggle.",
                "progress": "The toggle is wired to the theme store.",
                "next": "Resume and try it on the tablet.",
                "text": "This session adds dark mode to settings. It built a theme toggle.",
            },
        },
    )
    catalog = build_catalog(sessions, tmp_path, store, limit=5)
    assert catalog["items"][0]["summary"]["source"] == "grok"
    assert "theme toggle" in catalog["items"][0]["summary"]["text"]
    # The stored title is still untitled, so the list title comes from the transcript.
    assert "dark mode" in catalog["items"][0]["title"].lower()


def test_summary_scratch_under_repo_data_is_hidden(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    cwd = r"C:\Users\WADEBOYES\grok-remote-hub\data\.grok-hub-summary"
    encoded = "C%3A%5CUsers%5CWADEBOYES%5Cgrok-remote-hub%5Cdata%5C.grok-hub-summary"
    folder = sessions / encoded / SID_SCRATCH
    _summary(folder, SID_SCRATCH, cwd)
    _chat(folder, [{"role": "user", "content": "Summarise this session for the phone."}])
    # Percent-encoded dot, and a cwd that does not itself contain the marker.
    encoded_dot = "C%3A%5CUsers%5CWADEBOYES%5Cgrok-remote-hub%5Cdata%5C%2Egrok-hub-summary"
    other = sessions / encoded_dot / SID_EMPTY
    _summary(other, SID_EMPTY, r"C:\Users\WADEBOYES\grok-remote-hub")
    _chat(other, [{"role": "user", "content": "This scratch run should stay hidden."}])
    items = build_items(sessions, tmp_path, limit=20)
    assert items == []
