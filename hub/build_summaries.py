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

from hub.history import parse_updates_jsonl
from hub.session_index import delete_session

SUMMARY_VERSION = 1
# Scratch cwd marker. Sessions created by headless summary runs are excluded
# from the build index and deleted after the CLI returns.
SUMMARY_SCRATCH_MARK = ".grok-hub-summary"
GROK_RETRY_S = 30 * 60
_TAIL_BYTES = 180_000
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


def grok_due(record: dict[str, Any] | None, fingerprint: str, now: datetime | None = None, *, force: bool = False) -> bool:
    """True when a headless grok -p refresh should run."""
    if force:
        return True
    if not record:
        return True
    if str(record.get("fingerprint") or "") != fingerprint:
        return True
    if str(record.get("source") or "") == "grok":
        return False
    attempted = _parse_iso(str(record.get("grokAttemptAt") or ""))
    if attempted is None:
        return True
    current = now or datetime.now(timezone.utc)
    return (current - attempted).total_seconds() >= GROK_RETRY_S


def _tail_bytes(path: Path, limit: int = _TAIL_BYTES) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    try:
        with path.open("rb") as handle:
            if size > limit:
                handle.seek(size - limit)
                handle.readline()
            data = handle.read()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def _messages_from_updates(session_dir: Path) -> list[dict[str, Any]]:
    updates = session_dir / "updates.jsonl"
    if not updates.is_file():
        return []
    # parse_updates_jsonl reads the whole file. Cap by copying a tail into a
    # temp-less in-memory parse when the file is huge.
    try:
        size = updates.stat().st_size
    except OSError:
        return []
    if size <= _TAIL_BYTES:
        return parse_updates_jsonl(updates, max_messages=80)
    text = _tail_bytes(updates)
    # Reuse the parser via a sibling-free write in the same dir is risky.
    # Parse line by line with the same public function on a memory file.
    from tempfile import NamedTemporaryFile

    try:
        with NamedTemporaryFile("w", encoding="utf-8", suffix=".jsonl", delete=False) as tmp:
            tmp.write(text)
            tmp_path = Path(tmp.name)
        try:
            return parse_updates_jsonl(tmp_path, max_messages=80)
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass
    except OSError:
        return []


def _last_role(messages: list[dict[str, Any]], role: str) -> str:
    for msg in reversed(messages):
        if msg.get("role") == role:
            text = plain(str(msg.get("text") or ""))
            if text:
                return text
    return ""


def _open_plan(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if msg.get("role") != "plan":
            continue
        entries = (msg.get("meta") or {}).get("entries") or []
        if not isinstance(entries, list):
            continue
        pending: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            status = str(entry.get("status") or "").lower()
            if status in {"completed", "complete", "done", "cancelled", "canceled"}:
                continue
            content = plain(str(entry.get("content") or ""))
            if content:
                pending.append(content)
        if pending:
            return pending[0]
    return ""


def excerpt_for_session(session_dir: Path, *, title: str, cwd: str, quick: bool) -> dict[str, str]:
    summary: dict[str, Any] = {}
    summary_path = session_dir / "summary.json"
    if summary_path.is_file():
        try:
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                summary = loaded
        except (OSError, json.JSONDecodeError, UnicodeError):
            summary = {}
    session_summary = plain(str(summary.get("session_summary") or ""))
    last_user = ""
    last_assistant = ""
    plan = ""
    if not quick:
        messages = _messages_from_updates(session_dir)
        last_user = _last_role(messages, "user")
        last_assistant = _last_role(messages, "assistant")
        plan = _open_plan(messages)
    folder = Path(cwd).name if cwd else ""
    return {
        "title": plain(title),
        "cwd": plain(cwd),
        "folder": plain(folder),
        "sessionSummary": session_summary,
        "lastUser": clip(last_user, 500),
        "lastAssistant": clip(last_assistant, 500),
        "openPlan": clip(plan, 240),
    }


def local_summary_from_excerpt(excerpt: dict[str, str], *, kind: str) -> dict[str, str]:
    title = excerpt.get("title") or ""
    folder = excerpt.get("folder") or excerpt.get("repoName") or "a project"
    if kind == "worktree" and not excerpt.get("sessionId"):
        repo = excerpt.get("repoName") or folder or "a repository"
        path = excerpt.get("cwd") or "an isolated checkout"
        return summary_fields(
            f"This is a Grok Build worktree of {repo}.",
            f"It is an isolated checkout at {path}.",
            "No linked session notes are stored on this worktree yet.",
            "Next, restart a session in this worktree and keep working from the phone.",
        )
    label = f" called {title}" if title and title.lower() not in {"untitled session", "untitled"} else ""
    noun = "worktree session" if kind == "worktree" else "session"
    what = f"This is a Grok Build {noun}{label} in {folder}."
    built_src = excerpt.get("sessionSummary") or excerpt.get("lastUser") or ""
    if built_src:
        built = f"It was building: {clip(built_src, 320)}"
    else:
        built = "What it was building was not written down in the session notes yet."
    assistant = excerpt.get("lastAssistant") or ""
    if assistant:
        progress = f"It got as far as: {clip(assistant, 320)}"
    elif excerpt.get("sessionSummary"):
        progress = "The saved session summary is the latest note of progress."
    else:
        progress = "There is no assistant reply stored yet."
    plan = excerpt.get("openPlan") or ""
    if plan:
        nxt = f"Next: {clip(plan, 240)}"
    else:
        nxt = "Next, resume the session and continue from the last request."
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
        f"Kind: {kind}",
        f"Title: {excerpt.get('title') or ''}",
        f"Folder: {excerpt.get('folder') or ''}",
        f"Path: {excerpt.get('cwd') or ''}",
        f"Saved summary: {excerpt.get('sessionSummary') or ''}",
        f"Last user request: {excerpt.get('lastUser') or ''}",
        f"Last assistant reply: {excerpt.get('lastAssistant') or ''}",
        f"Open plan item: {excerpt.get('openPlan') or ''}",
    ]
    return "\n".join(lines)


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
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "key": key,
        "kind": kind,
        "id": item_id,
        "fingerprint": fingerprint,
        "source": source,
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
        if record and isinstance(record.get("summary"), dict) and record["summary"].get("text"):
            stale = str(record.get("fingerprint") or "") != fingerprint
            item["summary"] = public_summary(record, stale=stale)
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
        )
        store.put(key, row, save=False)
        dirty = True
        item["summary"] = public_summary(row, stale=False)
        due.append(key)
    if dirty:
        store.save()
    return due


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
    existing = store.get(key)
    if not grok_due(existing, fingerprint, force=force):
        return existing or {}

    # Store the file based summary first so a list read during grok -p is useful.
    local_row = make_record(
        key=key,
        kind=kind,
        item_id=item_id,
        fingerprint=fingerprint,
        source="local",
        summary=local,
    )
    if existing and existing.get("grokAttemptAt") and not force:
        local_row["grokAttemptAt"] = existing.get("grokAttemptAt")
    store.put(key, local_row)

    if runner is None and not _exe_exists(grok_exe):
        failed = make_record(
            key=key,
            kind=kind,
            item_id=item_id,
            fingerprint=fingerprint,
            source="local",
            summary=local,
            grok_error="grok.exe not found",
            grok_attempt=True,
        )
        store.put(key, failed)
        return failed

    prompt = summary_prompt(excerpt, kind=kind)
    scratch = scratch_cwd(scratch_root)
    try:
        argv = summary_argv(grok_exe, str(scratch), prompt)
    except Exception as exc:
        failed = make_record(
            key=key,
            kind=kind,
            item_id=item_id,
            fingerprint=fingerprint,
            source="local",
            summary=local,
            grok_error=str(exc),
            grok_attempt=True,
        )
        store.put(key, failed)
        return failed

    call = runner or (lambda args, cwd: run_grok_summary_subprocess(args, cwd))
    try:
        stdout = call(argv, scratch)
    except Exception as exc:
        failed = make_record(
            key=key,
            kind=kind,
            item_id=item_id,
            fingerprint=fingerprint,
            source="local",
            summary=local,
            grok_error=str(exc),
            grok_attempt=True,
        )
        store.put(key, failed)
        return failed

    fields, spawned = parse_grok_summary_output(stdout)
    if spawned:
        cleanup_summary_session(sessions_root, spawned)
    if not fields:
        failed = make_record(
            key=key,
            kind=kind,
            item_id=item_id,
            fingerprint=fingerprint,
            source="local",
            summary=local,
            grok_error="grok -p did not return summary JSON",
            grok_attempt=True,
        )
        store.put(key, failed)
        return failed
    row = make_record(
        key=key,
        kind=kind,
        item_id=item_id,
        fingerprint=fingerprint,
        source="grok",
        summary=fields,
        grok_attempt=True,
    )
    store.put(key, row)
    return row


def _exe_exists(grok_exe: str) -> bool:
    from hub.grok_bin import prefer_grok_exe

    try:
        resolved = prefer_grok_exe(grok_exe)
    except Exception:
        return False
    if not resolved or resolved.lower().endswith(".bat"):
        return False
    return Path(resolved).is_file()
