"""Detect a turn left hanging by a dropped ACP connection.

The transcript tail is enough: the wedged case ends on a tool that is still
pending, with no turn completion after it. The scan is bounded so a hot
prompt does not reread a multi-megabyte updates file.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hub.history import normalize_status, tool_label

TAIL_BYTES = 256 * 1024

_DONE_KINDS = frozenset({"turn_completed", "task_completed", "prompt_complete"})
_ACTIVITY_KINDS = frozenset(
    {
        "user_message_chunk",
        "agent_message_chunk",
        "agent_thought_chunk",
        "plan",
    }
)
_OPEN_STATUSES = frozenset({"pending", "running"})
_LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 ._+-]{0,23}$")

WEDGED_PENDING_MSG = (
    "Grok produced no output. The last tool call was still pending "
    "when the connection dropped."
)
WEDGED_INTERRUPTED_MSG = (
    "Grok produced no output. The last turn was interrupted and never finished."
)
RELOADED_MSG = "This session was reloaded. The stuck tool call is cleared."
REATTACHED_MSG = (
    "This session was reattached to a fresh Grok process. "
    "Other sessions were not running."
)
OTHER_SESSIONS_MSG = (
    "This session could not be reloaded because other sessions are still running."
)
UNRESUMABLE_RESTART_MSG = (
    "This session could not be reloaded. The session files are missing."
)
RESTART_FAILED_MSG = (
    "This session could not be reattached. The Grok process did not come back."
)
LOAD_AFTER_RESTART_MSG = (
    "The Grok process was restarted, but this session did not load."
)
RESTART_BUTTON_LABEL = "Restart this session"
RESTARTING_SESSION_MSG = "Grok produced no output. Restarting this session."
NEEDS_ANSWER_MSG = "Grok needs your answer before it can continue."


@dataclass(frozen=True)
class DanglingTurn:
    """A transcript that did not finish. reason is pending_tool or interrupted."""

    reason: str
    label: str = ""
    tool_call_id: str = ""


def wedged_no_output_message(reason: str) -> str:
    """Plain explanation after cancel-then-prompt still produced nothing."""
    if reason == "interrupted":
        return WEDGED_INTERRUPTED_MSG
    return WEDGED_PENDING_MSG


def safe_tool_label(label: str) -> str:
    """Short display name only. Long commands stay out of the sentence."""
    text = " ".join((label or "").split())
    if not text or not _LABEL_RE.match(text):
        return ""
    if text.lower() == "tool":
        return ""
    return text


def clearing_message(reason: str, label: str = "") -> str:
    """Sentence shown while session/cancel runs, before the prompt is sent."""
    safe = safe_tool_label(label)
    if reason == "pending_tool" and safe:
        return (
            f"The last turn stopped while {safe} was still pending. "
            "Clearing that, then continuing."
        )
    if reason == "pending_tool":
        return (
            "The last turn stopped while a tool was still pending. "
            "Clearing that, then continuing."
        )
    return "The last turn was interrupted. Clearing that, then continuing."


def _update_from_line(obj: dict[str, Any]) -> dict[str, Any] | None:
    method = obj.get("method") or ""
    if method not in ("session/update", "_x.ai/session/update"):
        if "sessionUpdate" in obj or "session_update" in obj:
            return obj
        update = obj.get("update")
        if isinstance(update, dict):
            return update
        return None
    params = obj.get("params") or {}
    if not isinstance(params, dict):
        return None
    update = params.get("update")
    if isinstance(update, dict):
        return update
    return None


def read_updates_tail(path: Path, max_bytes: int = TAIL_BYTES) -> str:
    """Last max_bytes of updates.jsonl, dropping a partial first line."""
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    if size <= 0:
        return ""
    try:
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                data = handle.read(max_bytes)
                newline = data.find(b"\n")
                if newline >= 0:
                    data = data[newline + 1 :]
                else:
                    # One truncated line. Nothing complete to trust.
                    return ""
            else:
                data = handle.read()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def _kind(update: dict[str, Any]) -> str:
    return str(update.get("sessionUpdate") or update.get("session_update") or "")


def _label_from_update(update: dict[str, Any]) -> str:
    label = tool_label(update)
    if label and label.lower() != "tool":
        return str(label)
    title = str(update.get("title") or "").strip()
    return title


def _status_from_update(update: dict[str, Any]) -> str | None:
    if "status" in update:
        return normalize_status(update.get("status"))
    if "state" in update:
        return normalize_status(update.get("state"))
    return None


def find_dangling_turn(
    path: Path | str | None,
    *,
    max_bytes: int = TAIL_BYTES,
) -> DanglingTurn | None:
    """Return a dangling turn when the tail is an open tool or an unfinished turn.

    A clean ending (turn completed, every tool completed or failed) is None.
    """
    if path is None:
        return None
    file_path = Path(path)
    if not file_path.is_file():
        return None
    text = read_updates_tail(file_path, max_bytes=max_bytes)
    if not text.strip():
        return None

    tools: dict[str, dict[str, Any]] = {}
    order = 0
    anon = 0
    last_tool_id = ""
    last_done = False
    activity_after_done = False
    saw_activity = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        update = _update_from_line(obj)
        if not update:
            continue
        kind = _kind(update)
        if kind in _DONE_KINDS:
            last_done = True
            activity_after_done = False
            continue
        if kind in ("tool_call", "tool_call_update"):
            saw_activity = True
            if last_done:
                activity_after_done = True
            tool_id = str(
                update.get("toolCallId") or update.get("tool_call_id") or ""
            ).strip()
            if not tool_id:
                if kind == "tool_call_update" and last_tool_id:
                    tool_id = last_tool_id
                else:
                    anon += 1
                    tool_id = f"anon-{anon}"
            status = _status_from_update(update)
            if kind == "tool_call" and status is None:
                status = "pending"
            prev = tools.get(tool_id)
            if prev is None:
                order += 1
                tools[tool_id] = {
                    "status": status or "pending",
                    "label": _label_from_update(update),
                    "order": order,
                }
            else:
                if status is not None:
                    prev["status"] = status
                label = _label_from_update(update)
                if label:
                    prev["label"] = label
            last_tool_id = tool_id
            continue
        if kind in _ACTIVITY_KINDS:
            saw_activity = True
            if last_done:
                activity_after_done = True

    open_tools = [
        item
        for item in tools.values()
        if str(item.get("status") or "") in _OPEN_STATUSES
    ]
    if open_tools:
        last = max(open_tools, key=lambda item: int(item.get("order") or 0))
        label = str(last.get("label") or "")
        tool_id = ""
        for key, item in tools.items():
            if item is last:
                tool_id = key
                break
        return DanglingTurn(reason="pending_tool", label=label, tool_call_id=tool_id)
    if saw_activity and (not last_done or activity_after_done):
        return DanglingTurn(reason="interrupted")
    return None
