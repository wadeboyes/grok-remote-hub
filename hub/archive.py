"""Archive flags for the phone and tablet lists.

State lives in ``data/archive.json`` on the hub. Session files under
``~/.grok/sessions`` are never read or written here. A session is keyed by
session id. A worktree is keyed by its folder path. Removing a worktree also
records that path so a leftover database row does not come back after the
folder is gone.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ArchiveError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def session_key(session_id: str) -> str:
    return str(session_id or "").strip().casefold()


def worktree_key(path: str) -> str:
    raw = str(path or "").strip()
    if raw.startswith("\\\\?\\"):
        raw = raw[4:]
    elif raw.startswith("//?/"):
        raw = raw[4:]
    raw = raw.replace("\\", "/")
    while len(raw) > 1 and raw.endswith("/"):
        raw = raw[:-1]
    return raw.casefold()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _empty() -> dict[str, dict[str, Any]]:
    return {"sessions": {}, "worktrees": {}, "removedWorktrees": {}}


class ArchiveStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return _empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            return _empty()
        if not isinstance(data, dict):
            return _empty()
        out = _empty()
        for name in out:
            raw = data.get(name)
            if isinstance(raw, dict):
                out[name] = {str(key): value for key, value in raw.items() if isinstance(value, dict)}
        return out

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "sessions": data.get("sessions") or {},
                "worktrees": data.get("worktrees") or {},
                "removedWorktrees": data.get("removedWorktrees") or {},
            },
            indent=2,
            sort_keys=True,
        )
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(payload + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def archive_session(self, session_id: str) -> None:
        key = session_key(session_id)
        if not key:
            raise ArchiveError("session id required", 400)
        with self._lock:
            data = self._load()
            data["sessions"][key] = {"id": str(session_id).strip(), "archivedAt": _now()}
            self._save(data)

    def archive_worktree(self, path: str) -> None:
        key = worktree_key(path)
        if not key:
            raise ArchiveError("worktree path required", 400)
        with self._lock:
            data = self._load()
            data["worktrees"][key] = {"path": str(path).strip(), "archivedAt": _now()}
            self._save(data)

    def restore_session(self, session_id: str) -> None:
        key = session_key(session_id)
        if not key:
            raise ArchiveError("session id required", 400)
        with self._lock:
            data = self._load()
            data["sessions"].pop(key, None)
            self._save(data)

    def restore_worktree(self, path: str) -> None:
        key = worktree_key(path)
        if not key:
            raise ArchiveError("worktree path required", 400)
        with self._lock:
            data = self._load()
            data["worktrees"].pop(key, None)
            self._save(data)

    def is_session_archived(self, session_id: str) -> bool:
        key = session_key(session_id)
        if not key:
            return False
        with self._lock:
            return key in self._load()["sessions"]

    def is_worktree_archived(self, path: str) -> bool:
        key = worktree_key(path)
        if not key:
            return False
        with self._lock:
            return key in self._load()["worktrees"]

    def is_worktree_removed(self, path: str) -> bool:
        key = worktree_key(path)
        if not key:
            return False
        with self._lock:
            return key in self._load()["removedWorktrees"]

    def note_removed_worktree(self, path: str, session_id: str = "") -> None:
        """Hide a deleted worktree and archive its session. One file write."""
        path_key = worktree_key(path)
        if not path_key:
            raise ArchiveError("worktree path required", 400)
        sid = session_key(session_id)
        with self._lock:
            data = self._load()
            data["removedWorktrees"][path_key] = {
                "path": str(path).strip(),
                "removedAt": _now(),
            }
            data["worktrees"].pop(path_key, None)
            if sid:
                data["sessions"][sid] = {
                    "id": str(session_id).strip(),
                    "archivedAt": _now(),
                }
            self._save(data)

    def forget_removed_worktree(self, path: str) -> None:
        key = worktree_key(path)
        if not key:
            return
        with self._lock:
            data = self._load()
            if key not in data["removedWorktrees"]:
                return
            data["removedWorktrees"].pop(key, None)
            self._save(data)


def _path_is_dir(path: str) -> bool:
    if not str(path or "").strip():
        return False
    try:
        return Path(path).is_dir()
    except OSError:
        return False


def annotate_items(items: list[dict[str, Any]], store: ArchiveStore | None) -> list[dict[str, Any]]:
    """Set ``archived`` and drop worktrees this hub has already removed."""
    if store is None:
        for item in items:
            item["archived"] = False
        return items
    kept: list[dict[str, Any]] = []
    for item in items:
        kind = str(item.get("kind") or "")
        if kind == "worktree":
            path = str(item.get("worktreePath") or item.get("cwd") or "")
            if store.is_worktree_removed(path):
                if _path_is_dir(path):
                    store.forget_removed_worktree(path)
                else:
                    continue
            item["archived"] = store.is_worktree_archived(path)
        else:
            sid = str(item.get("sessionId") or item.get("id") or "")
            item["archived"] = store.is_session_archived(sid)
        kept.append(item)
    return kept


def archive_catalog_item(store: ArchiveStore, item: dict[str, Any]) -> dict[str, Any]:
    kind = str(item.get("kind") or "").strip().lower()
    item_id = str(item.get("id") or "").strip()
    if kind == "session":
        sid = str(item.get("sessionId") or item_id).strip()
        store.archive_session(sid)
        return {"ok": True, "archived": True, "kind": "session", "id": item_id}
    if kind == "worktree":
        path = str(item.get("worktreePath") or item.get("cwd") or "").strip()
        store.archive_worktree(path)
        return {"ok": True, "archived": True, "kind": "worktree", "id": item_id}
    raise ArchiveError("kind must be session or worktree", 400)


def restore_catalog_item(store: ArchiveStore, item: dict[str, Any]) -> dict[str, Any]:
    kind = str(item.get("kind") or "").strip().lower()
    item_id = str(item.get("id") or "").strip()
    if kind == "session":
        sid = str(item.get("sessionId") or item_id).strip()
        store.restore_session(sid)
        return {"ok": True, "archived": False, "kind": "session", "id": item_id}
    if kind == "worktree":
        path = str(item.get("worktreePath") or item.get("cwd") or "").strip()
        store.restore_worktree(path)
        return {"ok": True, "archived": False, "kind": "worktree", "id": item_id}
    raise ArchiveError("kind must be session or worktree", 400)
