"""Plain English summaries for Grok Build sessions and worktrees.

The list reads a JSON store so it loads without waiting on the model.
When a session file changes, the fingerprint no longer matches and a refresh
is due. Refresh tries headless ``grok.exe -p`` (never grok.bat, never
``--always-approve``). If the CLI is missing or errors, a local summary from
the session files is stored instead.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from hub.build_transcript import (
    Conversation,
    is_placeholder_summary,
    is_untitled_title,
    load_conversation,
)
from hub.session_index import delete_session

SUMMARY_VERSION = 1
# Bump when the transcript reader changes so stored filler summaries rebuild.
SUMMARY_LOGIC_VERSION = 2
# Scratch cwd marker. Sessions created by headless summary runs are excluded
# from the build index and deleted after the CLI returns.
SUMMARY_SCRATCH_MARK = ".grok-hub-summary"
GROK_RETRY_S = 30 * 60
_CLIP = 360

_EM = "\u2014"
_EN = "\u2013"


def plain(text: str) -> str:
    """Single line, no em or en dashes."""
    raw = str(text or "")
    raw = raw.replace(_EM, ", ").replace(_EN, ", ")
    raw = raw.replace("\r", " ").replace("\n", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def clip(text: str, limit: int = _CLIP) -> str:
    cleaned = plain(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def file_fingerprint(paths: list[Path]) -> str:
    parts: list[str] = []
    for path in paths:
        try:
            st = path.stat()
            parts.append(f"{path.name}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append(f"{path.name}:missing")
    return "|".join(parts)


def summary_fields(what: str, built: str, progress: str, nxt: str) -> dict[str, str]:
    fields = {
        "what": plain(what),
        "built": plain(built),
        "progress": plain(progress),
        "next": plain(nxt),
    }
    fields["text"] = " ".join(part for part in fields.values() if part)
    return fields


def normalize_summary_fields(raw: dict[str, Any]) -> dict[str, str] | None:
    fields = summary_fields(
        str(raw.get("what") or ""),
        str(raw.get("built") or ""),
        str(raw.get("progress") or ""),
        str(raw.get("next") or ""),
    )
    if not any(fields[key] for key in ("what", "built", "progress", "next")):
        return None
    return fields


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".hubtmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        path.write_text(text, encoding="utf-8")


class SummaryStore:
    """JSON map of summaries. Safe to read on every list request."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self.load()

    def load(self) -> None:
        with self._lock:
            self._items = {}
            if not self.path.is_file():
                return
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeError):
                return
            items = data.get("items") if isinstance(data, dict) else None
            if not isinstance(items, dict):
                return
            for key, row in items.items():
                if isinstance(key, str) and isinstance(row, dict):
                    self._items[key] = row

    def save(self) -> None:
        with self._lock:
            payload = {"version": SUMMARY_VERSION, "items": self._items}
            text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
            _atomic_write(self.path, text)

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._items.get(key)
            if not isinstance(row, dict):
                return None
            return dict(row)

    def put(self, key: str, row: dict[str, Any], *, save: bool = True) -> None:
        with self._lock:
            self._items[key] = row
            if save:
                payload = {"version": SUMMARY_VERSION, "items": self._items}
                text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
                _atomic_write(self.path, text)

    def keys(self) -> list[str]:
        return list(self._items.keys())


def item_key(kind: str, item_id: str) -> str:
    return f"{kind}:{item_id}"


def _fingerprint_map(fingerprint: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for part in (fingerprint or "").split("|"):
        if ":" not in part:
            continue
        name, rest = part.split(":", 1)
        if name:
            found[name] = rest
    return found


def _tracked_files_unchanged(stored: str, current: str) -> bool:
    """True when every present transcript file still matches the stored fingerprint."""
    old = _fingerprint_map(stored)
    new = _fingerprint_map(current)
    if not old or not new:
        return False
    for name, rest in new.items():
        if rest == "missing":
            continue
        if old.get(name) != rest:
            return False
    return True


def record_is_current(record: dict[str, Any] | None, fingerprint: str) -> bool:
    """False when the stored summary is filler or the transcript files changed."""
    if not record or not isinstance(record.get("summary"), dict):
        return False
    summary = record.get("summary") or {}
    if not str(summary.get("text") or "").strip():
        return False
    if is_placeholder_summary(summary):
        return False
    stored = str(record.get("fingerprint") or "")
    if stored == fingerprint:
        return True
    # A real grok summary can survive a fingerprint shape change.
    # Filler text is already rejected above. File edits still miss.
    return str(record.get("source") or "") == "grok" and _tracked_files_unchanged(stored, fingerprint)


def grok_due(record: dict[str, Any] | None, fingerprint: str, now: datetime | None = None, *, force: bool = False) -> bool:
    """True when a headless grok -p refresh should run."""
    if force:
        return True
    if not record:
        return True
    stored = str(record.get("fingerprint") or "")
    if stored != fingerprint and not _tracked_files_unchanged(stored, fingerprint):
        return True
    summary = record.get("summary") if isinstance(record.get("summary"), dict) else None
    if summary is not None and is_placeholder_summary(summary):
        return True
    if str(record.get("source") or "") == "grok":
        return False
    attempted = _parse_iso(str(record.get("grokAttemptAt") or ""))
    if attempted is None:
        return True
    current = now or datetime.now(timezone.utc)
    return (current - attempted).total_seconds() >= GROK_RETRY_S


def _saved_session_summary(session_dir: Path) -> str:
    summary_path = session_dir / "summary.json"
    if not summary_path.is_file():
        return ""
    try:
        loaded = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return ""
    if not isinstance(loaded, dict):
        return ""
    return plain(str(loaded.get("session_summary") or ""))


def _format_turns(turns: list[tuple[str, str]], limit: int = 4) -> str:
    lines: list[str] = []
    for role, text in turns[-limit:]:
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {clip(text, 400)}")
    return "\n".join(lines)


def excerpt_for_session(session_dir: Path, *, title: str, cwd: str, quick: bool = False) -> dict[str, str]:
    """Transcript excerpt. ``quick`` is ignored: the list needs the real conversation."""
    del quick
    conversation = load_conversation(session_dir) if session_dir.is_dir() else Conversation()
    saved = _saved_session_summary(session_dir) if session_dir.is_dir() else ""
    derived = conversation.display_title()
    shown = plain(title)
    if is_untitled_title(shown):
        shown = derived
    folder = Path(cwd).name if cwd else ""
    first_users = [clip(text, 400) for text in conversation.first_users[:3]]
    latest_user = ""
    latest_assistant = ""
    for role, text in reversed(conversation.recent):
        if role == "assistant" and not latest_assistant:
            latest_assistant = text
        if role == "user" and not latest_user:
            latest_user = text
    opening = first_users[0] if first_users else ""
    return {
        "title": shown,
        "displayTitle": derived or shown,
        "cwd": plain(cwd),
        "folder": plain(folder),
        "sessionSummary": saved,
        "hasUserTurns": "yes" if conversation.has_user_turns else "no",
        "firstUsers": "\n".join(first_users),
        "recentTurns": _format_turns(conversation.recent),
        "lastUser": clip(opening or latest_user, 500),
        "lastAssistant": clip(latest_assistant, 500),
        "openPlan": clip(conversation.open_plan, 240),
    }


def local_summary_from_excerpt(excerpt: dict[str, str], *, kind: str) -> dict[str, str]:
    title = excerpt.get("title") or ""
    folder = excerpt.get("folder") or excerpt.get("repoName") or "a project"
    has_user = excerpt.get("hasUserTurns") == "yes"
    if kind == "worktree" and not excerpt.get("sessionId") and not has_user:
        repo = excerpt.get("repoName") or folder or "a repository"
        path = excerpt.get("cwd") or "an isolated checkout"
        return summary_fields(
            f"This is a Grok Build worktree of {repo}.",
            f"It is an isolated checkout at {path}.",
            "No linked session notes are stored on this worktree yet.",
            "Next, restart a session in this worktree and keep working from the phone.",
        )
    noun = "worktree session" if kind == "worktree" else "session"
    place = f" in {folder}" if folder else ""
    if not has_user:
        named = f" called {title}" if title and not is_untitled_title(title) else ""
        return summary_fields(
            f"This is a Grok Build {noun}{named}{place}.",
            "No user request is stored in this session yet.",
            "There is no conversation stored yet.",
            "Next, resume the session and send the first request.",
        )
    label = title if title and not is_untitled_title(title) else "this work"
    what = f"This is a Grok Build {noun} about {label}{place}."
    opening = excerpt.get("lastUser") or excerpt.get("sessionSummary") or ""
    if opening:
        built = f"The work started with: {clip(opening, 320)}"
    else:
        built = "The session has user requests, and the opening line could not be read."
    assistant = excerpt.get("lastAssistant") or ""
    if assistant:
        progress = f"The latest reply: {clip(assistant, 320)}"
    elif excerpt.get("sessionSummary"):
        progress = f"The saved note of progress: {clip(excerpt.get('sessionSummary') or '', 320)}"
    else:
        progress = "A user request is stored, and there is no assistant reply after it yet."
    plan = excerpt.get("openPlan") or ""
    if plan:
        nxt = f"Next: {clip(plan, 240)}"
    else:
        nxt = "Next, resume the session and continue this work."
    return summary_fields(what, built, progress, nxt)


def summary_prompt(excerpt: dict[str, str], *, kind: str) -> str:
    """Headless prompt. Asks for JSON only and forbids tools and dash characters."""
    lines = [
        "Write a short plain English status for one Grok Build coding session.",
        "Do not use tools. Do not browse. Do not edit files.",
        "Do not use em dashes or en dashes. Use commas or periods.",
        "Reply with one JSON object only, no markdown fence.",
        'Keys: "what", "built", "progress", "next".',
        "what: what this session or worktree is, one sentence.",
        "built: what was being built, one sentence.",
        "progress: where the work got to, one sentence.",
        "next: what is next, one sentence.",
        "",
        "If the transcript has a user request, describe that work.",
        "Do not claim the session is empty when a user request is included below.",
        "",
        f"Kind: {kind}",
        f"Title: {excerpt.get('title') or ''}",
        f"Folder: {excerpt.get('folder') or ''}",
        f"Path: {excerpt.get('cwd') or ''}",
        f"Saved summary: {excerpt.get('sessionSummary') or ''}",
        f"First user requests:\n{excerpt.get('firstUsers') or ''}",
        f"Latest turns:\n{excerpt.get('recentTurns') or ''}",
        f"Open plan item: {excerpt.get('openPlan') or ''}",
    ]
    text = "\n".join(lines)
    if len(text) > 6000:
        text = text[:6000]
    return text


def summary_argv(grok_exe: str, scratch_cwd: str, prompt: str) -> list[str]:
    """Headless grok -p argv. No --always-approve. No grok.bat."""
    from hub.grok_bin import require_grok_exe

    exe = require_grok_exe(grok_exe)
    return [
        exe,
        "--no-auto-update",
        "--cwd",
        scratch_cwd,
        "--output-format",
        "json",
        "--no-plan",
        "--no-subagents",
        "--disable-web-search",
        "-p",
        prompt,
    ]


def extract_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        val = json.loads(raw)
        if isinstance(val, dict):
            return val
    except json.JSONDecodeError:
        pass
    start = raw.rfind("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            val = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
        if isinstance(val, dict):
            return val
    return None


def parse_grok_summary_output(stdout: str) -> tuple[dict[str, str] | None, str]:
    """Return (summary fields, session id created by the CLI if any)."""
    outer = extract_json_object(stdout)
    if not outer:
        return None, ""
    session_id = str(outer.get("sessionId") or outer.get("session_id") or "").strip()
    inner: dict[str, Any] | None
    if all(key in outer for key in ("what", "built", "progress", "next")):
        inner = outer
    else:
        text = outer.get("text") or outer.get("message") or ""
        if isinstance(text, dict):
            inner = text
        else:
            inner = extract_json_object(str(text))
            if inner is None:
                # Model may have returned the four fields as the only JSON inside text
                # after a fence. extract_json_object already scanned.
                inner = None
    if not isinstance(inner, dict):
        return None, session_id
    fields = normalize_summary_fields(inner)
    return fields, session_id


def make_record(
    *,
    key: str,
    kind: str,
    item_id: str,
    fingerprint: str,
    source: str,
    summary: dict[str, str],
    grok_error: str = "",
    grok_attempt: bool = False,
    display_title: str = "",
    has_user_turns: bool = False,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "key": key,
        "kind": kind,
        "id": item_id,
        "fingerprint": fingerprint,
        "source": source,
        "summaryVersion": SUMMARY_LOGIC_VERSION,
        "displayTitle": plain(display_title)[:120],
        "hasUserTurns": bool(has_user_turns),
        "generatedAt": _now_iso(),
        "summary": summary,
        "grokError": plain(grok_error)[:300],
    }
    if grok_attempt or source == "grok" or grok_error:
        row["grokAttemptAt"] = row["generatedAt"]
    return row


def public_summary(record: dict[str, Any] | None, *, stale: bool) -> dict[str, Any]:
    if not record:
        return {
            "what": "",
            "built": "",
            "progress": "",
            "next": "",
            "text": "Summary is not ready yet.",
            "source": "pending",
            "generatedAt": "",
            "stale": True,
        }
    summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
    text = plain(str(summary.get("text") or ""))
    if not text:
        text = "Summary is not ready yet."
    return {
        "what": plain(str(summary.get("what") or "")),
        "built": plain(str(summary.get("built") or "")),
        "progress": plain(str(summary.get("progress") or "")),
        "next": plain(str(summary.get("next") or "")),
        "text": text,
        "source": str(record.get("source") or "local"),
        "generatedAt": str(record.get("generatedAt") or ""),
        "stale": stale,
    }


def attach_stored_summaries(
    items: list[dict[str, Any]],
    store: SummaryStore,
    *,
    quick_summary: Callable[[dict[str, Any]], dict[str, str]],
) -> list[str]:
    """Fill item['summary'] from the store. Persist a quick local summary if missing.

    Returns keys that still need a deeper refresh (fingerprint changed, or
    the stored text did not come from grok yet).
    """
    due: list[str] = []
    dirty = False
    for item in items:
        key = item_key(str(item.get("kind") or ""), str(item.get("id") or ""))
        fingerprint = str(item.get("fingerprint") or "")
        record = store.get(key)
        if record_is_current(record, fingerprint):
            record = record or {}
            if is_untitled_title(str(item.get("title") or "")) and is_untitled_title(
                str(record.get("displayTitle") or "")
            ):
                path = item.get("path")
                derived = load_conversation(Path(str(path))).display_title() if path else ""
                if derived:
                    record = dict(record)
                    record["displayTitle"] = derived
                    store.put(key, record, save=False)
                    dirty = True
            _apply_display_title(item, record)
            item["summary"] = public_summary(record, stale=False)
            if grok_due(record, fingerprint):
                due.append(key)
            continue
        summary = quick_summary(item)
        row = make_record(
            key=key,
            kind=str(item.get("kind") or ""),
            item_id=str(item.get("id") or ""),
            fingerprint=fingerprint,
            source="local",
            summary=summary,
            display_title=str(item.get("_displayTitle") or ""),
            has_user_turns=bool(item.get("_hasUserTurns")),
        )
        _apply_display_title(item, row)
        store.put(key, row, save=False)
        dirty = True
        item["summary"] = public_summary(row, stale=False)
        due.append(key)
    if dirty:
        store.save()
    return due


def _apply_display_title(item: dict[str, Any], record: dict[str, Any]) -> None:
    if not is_untitled_title(str(item.get("title") or "")):
        return
    shown = plain(str(record.get("displayTitle") or ""))
    if shown and not is_untitled_title(shown):
        item["title"] = shown


def scratch_cwd(root: Path) -> Path:
    path = Path(root) / "data" / SUMMARY_SCRATCH_MARK
    path.mkdir(parents=True, exist_ok=True)
    return path


def cleanup_summary_session(sessions_root: Path, session_id: str) -> None:
    if session_id:
        delete_session(sessions_root, session_id)


Runner = Callable[[list[str], Path], str]


def run_grok_summary_subprocess(argv: list[str], scratch: Path, timeout: float = 90.0) -> str:
    import subprocess

    proc = subprocess.run(
        argv,
        cwd=str(scratch),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0 and not out:
        raise RuntimeError(clip(err or f"grok exited {proc.returncode}", 400))
    return out or err


def refresh_item_summary(
    item: dict[str, Any],
    store: SummaryStore,
    *,
    grok_exe: str,
    sessions_root: Path,
    scratch_root: Path,
    runner: Runner | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Write a local summary, then try grok -p. Always returns the stored row."""
    kind = str(item.get("kind") or "session")
    item_id = str(item.get("id") or "")
    key = item_key(kind, item_id)
    fingerprint = str(item.get("fingerprint") or "")
    session_dir = Path(str(item.get("path") or "")) if item.get("path") else None
    excerpt = {
        "title": plain(str(item.get("title") or "")),
        "cwd": plain(str(item.get("cwd") or "")),
        "folder": Path(str(item.get("cwd") or "")).name,
        "repoName": plain(str(item.get("repoName") or "")),
        "sessionSummary": "",
        "lastUser": "",
        "lastAssistant": "",
        "openPlan": "",
        "sessionId": str(item.get("sessionId") or ""),
    }
    if session_dir and session_dir.is_dir():
        deep = excerpt_for_session(
            session_dir,
            title=str(item.get("title") or ""),
            cwd=str(item.get("cwd") or ""),
            quick=False,
        )
        excerpt.update(deep)
    local = local_summary_from_excerpt(excerpt, kind=kind)
    has_user = excerpt.get("hasUserTurns") == "yes"
    display_title = str(excerpt.get("displayTitle") or "")
    existing = store.get(key)
    if record_is_current(existing, fingerprint) and not grok_due(existing, fingerprint, force=force):
        return existing or {}

    def _store(summary: dict[str, str], *, source: str, grok_error: str = "", grok_attempt: bool = False) -> dict[str, Any]:
        row = make_record(
            key=key,
            kind=kind,
            item_id=item_id,
            fingerprint=fingerprint,
            source=source,
            summary=summary,
            grok_error=grok_error,
            grok_attempt=grok_attempt,
            display_title=display_title,
            has_user_turns=has_user,
        )
        store.put(key, row)
        return row

    # Store the file based summary first so a list read during grok -p is useful.
    _store(local, source="local")

    if runner is None and not _exe_exists(grok_exe):
        return _store(local, source="local", grok_error="grok.exe not found", grok_attempt=True)

    prompt = summary_prompt(excerpt, kind=kind)
    scratch = scratch_cwd(scratch_root)
    try:
        argv = summary_argv(grok_exe, str(scratch), prompt)
    except Exception as exc:
        return _store(local, source="local", grok_error=str(exc), grok_attempt=True)

    call = runner or (lambda args, cwd: run_grok_summary_subprocess(args, cwd))
    try:
        stdout = call(argv, scratch)
    except Exception as exc:
        return _store(local, source="local", grok_error=str(exc), grok_attempt=True)

    fields, spawned = parse_grok_summary_output(stdout)
    if spawned:
        cleanup_summary_session(sessions_root, spawned)
    if fields and has_user and is_placeholder_summary(fields):
        fields = None
        grok_error = "grok -p ignored the transcript"
    else:
        grok_error = ""
    if not fields:
        return _store(
            local,
            source="local",
            grok_error=grok_error or "grok -p did not return summary JSON",
            grok_attempt=True,
        )
    return _store(fields, source="grok", grok_attempt=True)


def _exe_exists(grok_exe: str) -> bool:
    from hub.grok_bin import prefer_grok_exe

    try:
        resolved = prefer_grok_exe(grok_exe)
    except Exception:
        return False
    if not resolved or resolved.lower().endswith(".bat"):
        return False
    return Path(resolved).is_file()
