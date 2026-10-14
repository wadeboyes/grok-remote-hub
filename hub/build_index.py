"""Index of every Grok Build session and git worktree, newest first.

This is wider than the phone home's Working list and wider than a single
projects folder such as grok-builds. Sessions come from ``~/.grok/sessions``.
Worktrees come from ``~/.grok/worktrees.db`` plus directories under
``~/.grok/worktrees`` that the database does not track.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from hub.build_summaries import (
    SUMMARY_SCRATCH_MARK,
    SummaryStore,
    attach_stored_summaries,
    excerpt_for_session,
    file_fingerprint,
    local_summary_from_excerpt,
    plain,
)
from hub.session_index import scan_sessions

DEFAULT_INDEX_LIMIT = 5000

_WORKTREE_SQL = """
SELECT id, path, source_repo, repo_name, kind, creation_mode, git_ref,
       head_commit, session_id, created_at, last_accessed_at, status, metadata
FROM worktrees
"""


def grok_home_from_sessions(sessions_root: Path) -> Path:
    root = Path(sessions_root).expanduser()
    if root.name.lower() == "sessions":
        return root.parent
    return Path.home() / ".grok"


def _iso_from_unix(value: int | float | None) -> str:
    if value is None:
        return ""
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return ""
    if stamp <= 0:
        return ""
    dt = datetime.fromtimestamp(stamp, tz=timezone.utc)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sort_stamp(iso: str) -> float:
    text = (iso or "").strip()
    if not text:
        return 0.0
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _is_summary_scratch(cwd: str, path: str = "") -> bool:
    """True for headless summary runs under data/.grok-hub-summary."""
    raw = f"{cwd}\n{path}"
    blob = unquote(raw).replace("\\", "/").lower()
    mark = SUMMARY_SCRATCH_MARK.lower()
    if mark in blob or mark in raw.replace("\\", "/").lower():
        return True
    return False


def _label_from_metadata(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        meta = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(meta, dict):
        return ""
    label = meta.get("label") or meta.get("name") or ""
    return plain(str(label))


def _row_worktree(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    (
        wid,
        path,
        source_repo,
        repo_name,
        kind,
        creation_mode,
        git_ref,
        head_commit,
        session_id,
        created_at,
        last_accessed_at,
        status,
        metadata,
    ) = row
    label = _label_from_metadata(metadata if isinstance(metadata, str) else None)
    title = label or plain(str(repo_name or "")) or Path(str(path or "")).name or str(wid)
    updated = _iso_from_unix(last_accessed_at) or _iso_from_unix(created_at)
    return {
        "id": str(wid or ""),
        "path": str(path or ""),
        "sourceRepo": str(source_repo or ""),
        "repoName": str(repo_name or ""),
        "worktreeKind": str(kind or ""),
        "creationMode": str(creation_mode or ""),
        "gitRef": str(git_ref or ""),
        "headCommit": str(head_commit or ""),
        "sessionId": str(session_id or "").strip(),
        "createdAt": _iso_from_unix(created_at),
        "updatedAt": updated,
        "status": str(status or ""),
        "title": title or "Worktree",
    }


def read_worktree_db(db_path: Path) -> list[dict[str, Any]]:
    path = Path(db_path)
    if not path.is_file():
        return []
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
    except sqlite3.Error:
        return []
    try:
        try:
            cur = conn.execute(_WORKTREE_SQL)
        except sqlite3.Error:
            return []
        return [_row_worktree(row) for row in cur.fetchall()]
    finally:
        conn.close()


def scan_worktree_dirs(grok_home: Path) -> list[dict[str, Any]]:
    """Directories under ~/.grok/worktrees/<repo>/<name> not already in the db."""
    root = Path(grok_home) / "worktrees"
    if not root.is_dir():
        return []
    found: list[dict[str, Any]] = []
    try:
        repos = list(root.iterdir())
    except OSError:
        return []
    for repo in repos:
        if not repo.is_dir() or repo.name.startswith("."):
            continue
        try:
            children = list(repo.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir():
                continue
            git_marker = child / ".git"
            if not git_marker.exists():
                continue
            try:
                updated = datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc)
                updated_iso = updated.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            except OSError:
                updated_iso = ""
            found.append(
                {
                    "id": f"dir:{child.name}",
                    "path": str(child),
                    "sourceRepo": "",
                    "repoName": repo.name,
                    "worktreeKind": "untracked",
                    "creationMode": "",
                    "gitRef": "",
                    "headCommit": "",
                    "sessionId": "",
                    "createdAt": updated_iso,
                    "updatedAt": updated_iso,
                    "status": "alive",
                    "title": child.name,
                }
            )
    return found


def list_worktrees(grok_home: Path) -> list[dict[str, Any]]:
    home = Path(grok_home)
    rows = read_worktree_db(home / "worktrees.db")
    known_paths = {str(Path(row["path"])) for row in rows if row.get("path")}
    known_resolved: set[str] = set()
    for raw in known_paths:
        try:
            known_resolved.add(str(Path(raw).resolve()))
        except OSError:
            known_resolved.add(raw)
    for extra in scan_worktree_dirs(home):
        raw = extra.get("path") or ""
        try:
            resolved = str(Path(raw).resolve())
        except OSError:
            resolved = raw
        if raw in known_paths or resolved in known_resolved:
            continue
        rows.append(extra)
    return rows


def _session_fingerprint(session_dir: Path) -> str:
    names = [
        "summary.json",
        "updates.jsonl",
        "chat_history.jsonl",
        "prompt_history.jsonl",
        "events.jsonl",
        "signals.json",
    ]
    return file_fingerprint([session_dir / name for name in names])


def _worktree_fingerprint(row: dict[str, Any], session_dir: Path | None) -> str:
    parts = [
        f"id:{row.get('id')}",
        f"updated:{row.get('updatedAt')}",
        f"status:{row.get('status')}",
        f"session:{row.get('sessionId')}",
    ]
    if session_dir and session_dir.is_dir():
        parts.append(_session_fingerprint(session_dir))
    else:
        path = Path(str(row.get("path") or ""))
        parts.append(file_fingerprint([path]))
    return "|".join(parts)


def _quick_summary_for_item(item: dict[str, Any]) -> dict[str, str]:
    """Cheap summary from fields already on the item. No transcript read."""
    excerpt = {
        "title": str(item.get("title") or ""),
        "cwd": str(item.get("cwd") or ""),
        "folder": Path(str(item.get("cwd") or "")).name,
        "repoName": str(item.get("repoName") or ""),
        "sessionSummary": str(item.get("sessionSummary") or ""),
        "lastUser": "",
        "lastAssistant": "",
        "openPlan": "",
        "sessionId": str(item.get("sessionId") or ""),
    }
    path = item.get("path")
    if path:
        quick = excerpt_for_session(
            Path(str(path)),
            title=str(item.get("title") or ""),
            cwd=str(item.get("cwd") or ""),
        )
        excerpt.update(quick)
    summary = local_summary_from_excerpt(excerpt, kind=str(item.get("kind") or "session"))
    item["_displayTitle"] = str(excerpt.get("displayTitle") or "")
    item["_hasUserTurns"] = excerpt.get("hasUserTurns") == "yes"
    return summary


def build_items(
    sessions_root: Path,
    grok_home: Path,
    *,
    limit: int = DEFAULT_INDEX_LIMIT,
) -> list[dict[str, Any]]:
    """Sessions and worktrees, newest first. Not limited to one projects folder."""
    sessions = scan_sessions(Path(sessions_root), limit=max(limit, 1) * 4)
    by_session_path: dict[str, str] = {}
    items: list[dict[str, Any]] = []

    for session in sessions:
        if _is_summary_scratch(session.cwd, session.path):
            continue
        session_dir = Path(session.path)
        summary_text = ""
        summary_path = session_dir / "summary.json"
        if summary_path.is_file():
            try:
                data = json.loads(summary_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    summary_text = plain(str(data.get("session_summary") or ""))
            except (OSError, json.JSONDecodeError, UnicodeError):
                summary_text = ""
        by_session_path[session.sessionId] = session.path
        items.append(
            {
                "kind": "session",
                "id": session.sessionId,
                "title": session.title,
                "cwd": session.cwd,
                "updatedAt": session.updatedAt,
                "sortKey": _sort_stamp(session.updatedAt),
                "sessionId": session.sessionId,
                "worktreeId": "",
                "worktreePath": "",
                "sourceRepo": "",
                "repoName": Path(session.cwd).name if session.cwd else "",
                "status": "subagent" if session.isSubagent else "session",
                "isSubagent": bool(session.isSubagent),
                "modelId": session.modelId,
                "path": session.path,
                "sessionSummary": summary_text,
                "fingerprint": _session_fingerprint(session_dir),
                "actionHint": "resume",
            }
        )

    session_ids = {item["sessionId"] for item in items}
    for row in list_worktrees(Path(grok_home)):
        path = str(row.get("path") or "")
        if _is_summary_scratch(path, path):
            continue
        session_id = str(row.get("sessionId") or "").strip()
        session_dir: Path | None = None
        if session_id and session_id in by_session_path:
            session_dir = Path(by_session_path[session_id])
        title = str(row.get("title") or "Worktree")
        if session_id and session_id not in session_ids:
            title = title or session_id
        wid = str(row.get("id") or "")
        if not wid:
            wid = "dir:" + hashlib.sha1(path.encode("utf-8", errors="replace")).hexdigest()[:12]
        updated = str(row.get("updatedAt") or row.get("createdAt") or "")
        items.append(
            {
                "kind": "worktree",
                "id": wid,
                "title": title,
                "cwd": path,
                "updatedAt": updated,
                "sortKey": _sort_stamp(updated),
                "sessionId": session_id,
                "worktreeId": wid,
                "worktreePath": path,
                "sourceRepo": str(row.get("sourceRepo") or ""),
                "repoName": str(row.get("repoName") or ""),
                "status": str(row.get("status") or ""),
                "isSubagent": str(row.get("worktreeKind") or "") == "subagent",
                "modelId": "",
                "path": str(session_dir) if session_dir else "",
                "sessionSummary": "",
                "gitRef": str(row.get("gitRef") or ""),
                "fingerprint": _worktree_fingerprint(row, session_dir),
                "actionHint": "resume" if session_id else "restart",
            }
        )
        if session_id:
            for item in items:
                if item.get("kind") == "session" and item.get("sessionId") == session_id:
                    if not item.get("worktreePath"):
                        item["worktreeId"] = wid
                        item["worktreePath"] = path

    items.sort(key=lambda row: float(row.get("sortKey") or 0), reverse=True)
    if limit > 0:
        items = items[:limit]
    for item in items:
        item.pop("sortKey", None)
        item.pop("sessionSummary", None)
    return items


def build_catalog(
    sessions_root: Path,
    grok_home: Path,
    store: SummaryStore,
    *,
    limit: int = DEFAULT_INDEX_LIMIT,
) -> dict[str, Any]:
    """Index plus stored summaries. Writes a quick summary when none is stored."""
    items = build_items(sessions_root, grok_home, limit=limit)
    due = attach_stored_summaries(items, store, quick_summary=_quick_summary_for_item)
    # Drop internal path from the wire payload? The phone does not need the
    # session directory. Keep cwd. Remove path after summaries are attached.
    public: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        row.pop("path", None)
        row.pop("fingerprint", None)
        row.pop("_displayTitle", None)
        row.pop("_hasUserTurns", None)
        public.append(row)
    return {
        "items": public,
        "count": len(public),
        "due": due,
        "limit": limit,
    }


def find_catalog_item(
    sessions_root: Path,
    grok_home: Path,
    *,
    kind: str,
    item_id: str,
) -> dict[str, Any] | None:
    """Find one session or worktree, including path and fingerprint."""
    wanted_kind = (kind or "").strip().lower()
    wanted_id = (item_id or "").strip()
    if not wanted_id:
        return None
    items = build_items(sessions_root, grok_home, limit=DEFAULT_INDEX_LIMIT)
    for item in items:
        if wanted_kind and str(item.get("kind") or "") != wanted_kind:
            continue
        if str(item.get("id") or "") == wanted_id:
            return item
    if not wanted_kind:
        for item in items:
            if str(item.get("id") or "") == wanted_id:
                return item
            if str(item.get("sessionId") or "") == wanted_id:
                return item
    return None
