"""Delete one linked git worktree from disk.

The only mutating git command is ``git worktree remove`` run in the parent
repository. ``--force`` is added only when the caller passed force. The branch
is never deleted. The main checkout is never removed. The target must be a
folder at least two levels under ``~/.grok/worktrees``.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from hub.archive import ArchiveStore

log = logging.getLogger("hub.worktree")

_DIRTY = "This worktree has uncommitted changes."
_UNPUSHED = "This worktree has unpushed commits."
_BOTH = "This worktree has uncommitted changes and unpushed commits."
_OUTSIDE = "Refusing to remove a folder outside .grok/worktrees."
_MAIN = "Refusing to remove the main repo checkout."


class WorktreeRemoveError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "error"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


@dataclass
class GitResult:
    returncode: int
    stdout: str
    stderr: str


GitRunner = Callable[[Path, list[str]], GitResult]


def parse_force(value: object) -> bool:
    if value is None or value is False:
        return False
    if value is True:
        return True
    raise WorktreeRemoveError("force must be true or false", 400, "force")


def default_git(cwd: Path, args: list[str]) -> GitResult:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorktreeRemoveError("git timed out.", 504, "git") from exc
    except FileNotFoundError as exc:
        raise WorktreeRemoveError("git was not found on the PC.", 500, "git") from exc
    except OSError as exc:
        raise WorktreeRemoveError("Could not run git.", 500, "git") from exc
    return GitResult(proc.returncode, proc.stdout or "", proc.stderr or "")


def assert_removable_worktree(path: str, grok_home: Path) -> Path:
    """Resolved worktree folder, or raise before any git command."""
    raw = str(path or "").strip()
    if not raw:
        raise WorktreeRemoveError("Worktree path is missing.", 400, "unsafe")
    if ".." in Path(raw).parts:
        raise WorktreeRemoveError(_OUTSIDE, 400, "unsafe")
    root = (Path(grok_home).expanduser() / "worktrees").resolve(strict=False)
    candidate = Path(raw).expanduser()
    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:
        raise WorktreeRemoveError("That worktree path is not usable.", 400, "unsafe") from exc
    if not resolved.is_dir():
        raise WorktreeRemoveError("Worktree folder was not found.", 404, "missing")
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        raise WorktreeRemoveError(_OUTSIDE, 400, "unsafe") from None
    parts = [part for part in rel.parts if part not in {"", "."}]
    if any(part == ".." for part in parts) or len(parts) < 2:
        raise WorktreeRemoveError(_OUTSIDE, 400, "unsafe")
    return resolved


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve(strict=False) == right.resolve(strict=False)
    except OSError:
        return str(left) == str(right)


def _resolve_git_path(cwd: Path, text: str) -> Path:
    raw = (text or "").strip()
    if not raw:
        raise WorktreeRemoveError("Could not read this worktree from git.", 502, "git")
    path = Path(raw)
    if not path.is_absolute():
        path = Path(cwd) / path
    try:
        return path.resolve(strict=False)
    except OSError as exc:
        raise WorktreeRemoveError("Could not read this worktree from git.", 502, "git") from exc


def _git_text(git: GitRunner, cwd: Path, args: list[str]) -> GitResult:
    return git(cwd, args)


def _count(text: str) -> int:
    try:
        return int((text or "").strip() or "0")
    except ValueError:
        return 0


def _parent_repo(worktree: Path, git: GitRunner) -> Path:
    git_dir = _git_text(git, worktree, ["rev-parse", "--git-dir"])
    common = _git_text(git, worktree, ["rev-parse", "--git-common-dir"])
    if git_dir.returncode != 0 or common.returncode != 0:
        raise WorktreeRemoveError("Could not read this worktree from git.", 502, "git")
    git_dir_path = _resolve_git_path(worktree, git_dir.stdout)
    common_path = _resolve_git_path(worktree, common.stdout)
    if git_dir_path == common_path:
        raise WorktreeRemoveError(_MAIN, 400, "unsafe")
    parent = common_path.parent if common_path.name == ".git" else common_path
    try:
        parent = parent.resolve(strict=False)
    except OSError as exc:
        raise WorktreeRemoveError("Could not find the parent repo.", 502, "git") from exc
    if _same_path(parent, worktree):
        raise WorktreeRemoveError(_MAIN, 400, "unsafe")
    try:
        worktree.relative_to(parent)
        # A worktree lives outside the main checkout. If the main checkout is
        # inside the worktree, this target is not a linked worktree we can remove.
        if parent != worktree and _is_relative_to(parent, worktree):
            raise WorktreeRemoveError(_MAIN, 400, "unsafe")
    except ValueError:
        pass
    if not parent.is_dir():
        raise WorktreeRemoveError("Could not find the parent repo.", 502, "git")
    return parent


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _dirty(git: GitRunner, worktree: Path) -> bool:
    status = _git_text(git, worktree, ["status", "--porcelain"])
    if status.returncode != 0:
        raise WorktreeRemoveError("Could not read worktree status.", 502, "git")
    return bool(status.stdout.strip())


def _unpushed(git: GitRunner, worktree: Path, parent: Path) -> bool:
    upstream = _git_text(git, worktree, ["rev-parse", "--abbrev-ref", "@{upstream}"])
    if upstream.returncode == 0 and upstream.stdout.strip():
        counted = _git_text(
            git,
            worktree,
            ["rev-list", "--count", f"{upstream.stdout.strip()}..HEAD"],
        )
        if counted.returncode != 0:
            return False
        return _count(counted.stdout) > 0
    exclude = ["--not", "--remotes"]
    parent_branch = _git_text(git, parent, ["rev-parse", "--abbrev-ref", "HEAD"])
    name = parent_branch.stdout.strip() if parent_branch.returncode == 0 else ""
    if name and name != "HEAD":
        exclude.append(name)
    counted = _git_text(git, worktree, ["rev-list", "--count", "HEAD", *exclude])
    if counted.returncode != 0:
        return False
    return _count(counted.stdout) > 0


def _branch_name(git: GitRunner, worktree: Path) -> str:
    found = _git_text(git, worktree, ["rev-parse", "--abbrev-ref", "HEAD"])
    if found.returncode != 0:
        return ""
    name = found.stdout.strip()
    if name == "HEAD":
        return ""
    return name


def _block(dirty: bool, unpushed: bool) -> WorktreeRemoveError:
    if dirty and unpushed:
        return WorktreeRemoveError(_BOTH, 409, "dirty_unpushed")
    if dirty:
        return WorktreeRemoveError(_DIRTY, 409, "dirty")
    return WorktreeRemoveError(_UNPUSHED, 409, "unpushed")


def _assert_remove_argv(args: list[str]) -> None:
    if len(args) < 3 or args[0] != "worktree" or args[1] != "remove":
        raise WorktreeRemoveError("Refusing an unexpected git command.", 500, "unsafe")
    flags = [arg for arg in args[2:-1] if str(arg).startswith("-")]
    if any(flag != "--force" for flag in flags) or flags.count("--force") > 1:
        raise WorktreeRemoveError("Refusing an unexpected git command.", 500, "unsafe")
    if args[-1].startswith("-"):
        raise WorktreeRemoveError("Refusing an unexpected git command.", 500, "unsafe")


def _plain_git_error(text: str) -> str:
    line = ""
    for raw in (text or "").splitlines():
        if raw.strip():
            line = raw.strip()
            break
    line = line.replace("\u2014", ", ").replace("\u2013", ", ")
    return line[:240] or "git worktree remove failed."


def remove_catalog_worktree(
    item: dict[str, Any],
    grok_home: Path,
    archive: ArchiveStore,
    *,
    force: bool,
    git: GitRunner = default_git,
) -> dict[str, Any]:
    """Remove ``item`` if it is a worktree under ``~/.grok/worktrees``."""
    if str(item.get("kind") or "").strip().lower() != "worktree":
        raise WorktreeRemoveError("Remove is only for worktrees.", 400, "kind")
    raw_path = str(item.get("worktreePath") or item.get("cwd") or "").strip()
    resolved = assert_removable_worktree(raw_path, grok_home)
    source = str(item.get("sourceRepo") or "").strip()
    if source and _same_path(Path(source), resolved):
        raise WorktreeRemoveError(_MAIN, 400, "unsafe")
    parent = _parent_repo(resolved, git)
    if _same_path(parent, resolved):
        raise WorktreeRemoveError(_MAIN, 400, "unsafe")
    dirty = _dirty(git, resolved)
    unpushed = _unpushed(git, resolved, parent)
    branch = _branch_name(git, resolved)
    if (dirty or unpushed) and not force:
        raise _block(dirty, unpushed)
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(resolved))
    _assert_remove_argv(args)
    log.info("worktree remove path=%s force=%s", resolved, bool(force))
    removed = git(parent, args)
    if removed.returncode != 0:
        detail = _plain_git_error(removed.stderr or removed.stdout)
        low = detail.lower()
        if "modified" in low or "untracked" in low:
            raise WorktreeRemoveError(detail or _DIRTY, 409, "dirty")
        raise WorktreeRemoveError(detail, 502, "git")
    session_id = str(item.get("sessionId") or "").strip()
    archive.note_removed_worktree(str(resolved), session_id)
    return {
        "ok": True,
        "removed": True,
        "path": str(resolved),
        "folder": resolved.name,
        "branch": branch,
        "forced": bool(force),
        "archivedSessionId": session_id,
    }
