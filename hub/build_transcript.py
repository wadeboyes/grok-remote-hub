"""Read Grok session transcripts for the phone list.

Titles and summaries come from the conversation, not from an empty
summary.json. Large files contribute the opening user requests and the
latest turns. A session is empty only when those files have no user turn.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from hub.history import _extract_text, _message_from_update, _update_from_line

_HEAD_BYTES = 64_000
_TAIL_BYTES = 128_000
_TURN_CLIP = 600
_TITLE_MAX = 80

_PLACEHOLDER_MARKS = (
    "no saved summary",
    "start a concrete task",
    "not written down",
    "does not name anything",
    "no assistant reply stored",
    "open plan item on file",
    "untitled grok build",
    "no linked session notes",
    "no user request is stored",
    "no conversation stored yet",
    "summary is not ready yet",
)

_WRAPPER_TAGS = (
    "user_query",
    "user_info",
    "system_reminder",
    "open_and_recently_viewed_files",
    "agent_skills",
    "mcp_file_system",
)


def plain_line(text: str) -> str:
    raw = str(text or "")
    raw = raw.replace("\u2014", ", ").replace("\u2013", ", ")
    raw = raw.replace("\r", " ").replace("\n", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def is_untitled_title(title: str) -> bool:
    cleaned = plain_line(title).lower().strip(" .")
    return cleaned in {"", "untitled", "untitled session", "new session", "session"}


def is_placeholder_summary(summary: dict) -> bool:
    parts = []
    if isinstance(summary, dict):
        for key in ("what", "built", "progress", "next", "text"):
            parts.append(str(summary.get(key) or ""))
    text = plain_line(" ".join(parts)).lower()
    if not text:
        return True
    return any(mark in text for mark in _PLACEHOLDER_MARKS)


def title_from_user_text(text: str) -> str:
    """Short list title from one user request. Empty when the line is too thin."""
    cleaned = _unwrap_user(text)
    if not cleaned:
        return ""
    sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0]
    title = sentence if len(sentence) <= _TITLE_MAX else _clip_words(cleaned, _TITLE_MAX)
    title = title.strip("\"'` ")
    lowered = title.lower().strip(" .")
    if lowered in {"hi", "hey", "hello", "ok", "okay", "yes", "no", "thanks", "thank you"}:
        return ""
    if len(title) < 8:
        return ""
    return title


@dataclass
class Conversation:
    first_users: list[str] = field(default_factory=list)
    recent: list[tuple[str, str]] = field(default_factory=list)
    open_plan: str = ""

    @property
    def has_user_turns(self) -> bool:
        if any(self.first_users):
            return True
        return any(role == "user" and text for role, text in self.recent)

    def display_title(self) -> str:
        seen: list[str] = []
        for text in self.first_users:
            seen.append(text)
        for role, text in self.recent:
            if role == "user":
                seen.append(text)
        for text in seen:
            title = title_from_user_text(text)
            if title:
                return title
        return ""


def load_conversation(session_dir: Path) -> Conversation:
    """User and assistant turns from the session directory."""
    folder = Path(session_dir)
    chat = _turns_in_file(folder / "chat_history.jsonl")
    prompts = _user_prompts(folder / "prompt_history.jsonl")
    turns = _merge_prompts(chat, prompts)
    plan = ""
    if not any(role == "user" and text for role, text in turns):
        extra, plan = _turns_and_plan(folder / "updates.jsonl")
        if not any(role == "user" and text for role, text in extra):
            event_turns, event_plan = _turns_and_plan(folder / "events.jsonl")
            extra = _dedupe(extra + event_turns)
            plan = plan or event_plan
        else:
            plan = plan or _plan_only(folder / "events.jsonl")
        turns = _dedupe(turns + extra)
    else:
        plan = _plan_only(folder / "updates.jsonl") or _plan_only(folder / "events.jsonl")
    return _pack(turns, plan)


def _pack(turns: list[tuple[str, str]], plan: str) -> Conversation:
    users = [text for role, text in turns if role == "user" and text]
    first = users[:3]
    recent = [(role, text) for role, text in turns if role in {"user", "assistant"} and text][-6:]
    return Conversation(first_users=first, recent=recent, open_plan=plain_line(plan)[:400])


def _turns_in_file(path: Path) -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []
    for obj in _iter_json_objects(path):
        for role, text in _turns_from_obj(obj):
            if role in {"user", "assistant"} and text:
                turns.append((role, text[:_TURN_CLIP]))
    return _dedupe(turns)


def _user_prompts(path: Path) -> list[str]:
    """Lines in prompt_history are user requests, even when they have no role field."""
    found: list[str] = []
    for obj in _iter_json_objects(path):
        turns = _turns_from_obj(obj)
        if not turns:
            text = _payload_text(obj)
            if text:
                turns = [("user", text)]
        for role, text in turns:
            if role == "user" and text:
                found.append(text[:_TURN_CLIP])
    return found


def _turns_and_plan(path: Path) -> tuple[list[tuple[str, str]], str]:
    turns: list[tuple[str, str]] = []
    plan = ""
    for obj in _iter_json_objects(path):
        update = _update_from_line(obj)
        if isinstance(update, dict) and update.get("sessionUpdate") == "plan":
            msg = _message_from_update(update)
            if msg:
                plan = _plan_text(msg) or plan
        for role, text in _turns_from_obj(obj):
            if role in {"user", "assistant"} and text:
                turns.append((role, text[:_TURN_CLIP]))
    return _dedupe(turns), plan


def _plan_only(path: Path) -> str:
    _turns, plan = _turns_and_plan(path)
    return plan


def _merge_prompts(turns: list[tuple[str, str]], prompts: list[str]) -> list[tuple[str, str]]:
    if not prompts:
        return turns
    known = {text[:80] for role, text in turns if role == "user"}
    prefix: list[tuple[str, str]] = []
    for text in prompts:
        if text[:80] in known:
            continue
        prefix.append(("user", text))
        known.add(text[:80])
    return _dedupe(prefix + turns)


def _dedupe(turns: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for turn in turns:
        if out and out[-1] == turn:
            continue
        out.append(turn)
    return out


def _iter_json_objects(path: Path, *, head_bytes: int = _HEAD_BYTES, tail_bytes: int = _TAIL_BYTES):
    file_path = Path(path)
    try:
        size = file_path.stat().st_size
    except OSError:
        return
    if size <= 0:
        return
    try:
        with file_path.open("rb") as handle:
            if size <= head_bytes + tail_bytes:
                chunks = [handle.read()]
            else:
                head = handle.read(head_bytes)
                handle.seek(size - tail_bytes)
                tail = handle.read()
                chunks = [_trim_head(head), _trim_tail(tail)]
    except OSError:
        return
    for chunk in chunks:
        if not chunk:
            continue
        for line in chunk.decode("utf-8", errors="replace").splitlines():
            text = line.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def _trim_head(raw: bytes) -> bytes:
    if b"\n" not in raw:
        return raw
    # Drop a partial last line. The first line is complete.
    return raw[: raw.rfind(b"\n")]


def _trim_tail(raw: bytes) -> bytes:
    if b"\n" not in raw:
        return b""
    # Drop a partial first line. The last line is complete when the file ends in a newline.
    return raw[raw.find(b"\n") + 1 :]


def _turns_from_obj(obj: dict) -> list[tuple[str, str]]:
    update = _update_from_line(obj)
    if isinstance(update, dict) and update.get("sessionUpdate"):
        msg = _message_from_update(update)
        if not msg:
            return []
        role = str(msg.get("role") or "")
        text = plain_line(str(msg.get("text") or ""))
        if role in {"user", "assistant"} and text:
            return [(role, text)]
        return []

    prompt = obj.get("prompt")
    role_hint = _role_of(
        str(obj.get("role") or obj.get("type") or obj.get("kind") or obj.get("author") or "")
    )
    if isinstance(prompt, str) and plain_line(prompt) and role_hint != "assistant":
        return [("user", plain_line(prompt))]

    sources = [obj]
    message = obj.get("message")
    if isinstance(message, dict):
        sources.append(message)
    for src in sources:
        role = _role_of(
            str(
                src.get("role")
                or src.get("type")
                or src.get("kind")
                or src.get("event")
                or src.get("author")
                or src.get("sender")
                or ""
            )
        )
        text = _payload_text(src)
        if role and text:
            return [(role, text)]
    return []


def _payload_text(src: dict) -> str:
    for key in ("content", "text", "body", "parts", "items", "input"):
        if key not in src or src[key] in (None, ""):
            continue
        text = plain_line(_extract_text(src[key]))
        if text:
            return text
    message = src.get("message")
    if isinstance(message, str):
        return plain_line(message)
    return ""


def _role_of(value: str) -> str:
    role = value.strip().lower().replace("-", "_")
    if not role or "thought" in role or "tool" in role:
        return ""
    if role in {"user", "human", "prompt", "customer", "request", "query"} or role.startswith("user"):
        return "user"
    if (
        role in {"assistant", "model", "grok", "bot", "response", "result", "reply"}
        or "assistant" in role
        or role.startswith("agent")
    ):
        return "assistant"
    return ""


def _unwrap_user(text: str) -> str:
    cleaned = str(text or "")
    for tag in _WRAPPER_TAGS:
        cleaned = re.sub(rf"</?{tag}[^>]*>", " ", cleaned, flags=re.IGNORECASE)
    return plain_line(cleaned)


def _clip_words(text: str, limit: int) -> str:
    cleaned = plain_line(text)
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[:limit].rsplit(" ", 1)[0].rstrip(".,;:")
    if len(cut) < max(8, limit // 2):
        cut = cleaned[:limit].rstrip()
    return cut


def _plan_text(msg: dict) -> str:
    entries = (msg.get("meta") or {}).get("entries") or []
    if not isinstance(entries, list):
        return ""
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "").lower()
        if status in {"completed", "complete", "done", "cancelled", "canceled"}:
            continue
        content = plain_line(str(entry.get("content") or ""))
        if content:
            return content
    return ""
