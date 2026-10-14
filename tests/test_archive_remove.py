"""Archive flags and worktree removal. Session files stay on disk."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from hub.archive import (
    ArchiveStore,
    archive_catalog_item,
    restore_catalog_item,
    worktree_key,
)
from hub.build_index import build_catalog, build_items
from hub.build_summaries import SummaryStore
from hub.worktree_remove import (
    GitResult,
    WorktreeRemoveError,
    assert_removable_worktree,
    parse_force,
    remove_catalog_worktree,
)

SID = "01a13a07-a83e-7abc-8def-0123456789ab"


def _session_file(root: Path) -> Path:
    folder = root / "encoded" / SID
    folder.mkdir(parents=True)
    target = folder / "summary.json"
    target.write_text('{"generated_title": "Updates"}\n', encoding="utf-8")
    (folder / "chat_history.jsonl").write_text('{"text": "keep me"}\n', encoding="utf-8")
    return target


def _worktree(grok_home: Path, name: str = "updates") -> Path:
    path = grok_home / "worktrees" / "wadeboyes" / name
    path.mkdir(parents=True)
    (path / ".git").write_text("gitdir: /main/.git/worktrees/updates\n", encoding="utf-8")
    (path / "keep.txt").write_text("tree\n", encoding="utf-8")
    return path


def _item(path: Path, *, session_id: str = SID, source: str = "") -> dict:
    return {
        "kind": "worktree",
        "id": "wt-updates",
        "title": "updates",
        "cwd": str(path),
        "worktreePath": str(path),
        "sessionId": session_id,
        "sourceRepo": source,
        "gitRef": "feature",
    }


class ScriptedGit:
    def __init__(self, responses: dict[tuple[str, ...], GitResult]):
        self.responses = responses
        self.calls: list[tuple[str, list[str]]] = []

    def __call__(self, cwd: Path, args: list[str]) -> GitResult:
        self.calls.append((str(Path(cwd)), list(args)))
        found = self.responses.get(tuple(args))
        if found is None:
            return GitResult(1, "", "unexpected " + " ".join(args))
        return found


def _linked_responses(worktree: Path, parent: Path, *, porcelain: str = "", ahead: str = "0") -> dict:
    return {
        ("rev-parse", "--git-dir"): GitResult(0, str(parent / ".git" / "worktrees" / "updates"), ""),
        ("rev-parse", "--git-common-dir"): GitResult(0, str(parent / ".git"), ""),
        ("status", "--porcelain"): GitResult(0, porcelain, ""),
        ("rev-parse", "--abbrev-ref", "@{upstream}"): GitResult(0, "origin/feature", ""),
        ("rev-list", "--count", "origin/feature..HEAD"): GitResult(0, ahead, ""),
        ("rev-parse", "--abbrev-ref", "HEAD"): GitResult(0, "feature", ""),
        ("worktree", "remove", str(worktree.resolve())): GitResult(0, "", ""),
        ("worktree", "remove", "--force", str(worktree.resolve())): GitResult(0, "", ""),
    }


def test_archive_session_and_worktree_round_trip(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    summary = _session_file(sessions)
    before = summary.read_bytes()
    history = (summary.parent / "chat_history.jsonl").read_bytes()
    grok_home = tmp_path / "grok"
    worktree = _worktree(grok_home)
    store = ArchiveStore(tmp_path / "data" / "archive.json")
    archive_catalog_item(
        store,
        {
            "kind": "session",
            "id": SID,
            "sessionId": SID.upper(),
        },
    )
    assert store.is_session_archived(SID)
    assert not store.is_worktree_archived(str(worktree))
    archive_catalog_item(store, _item(Path(r"C:\Users\WADEBOYES\.grok\worktrees\wadeboyes\updates")))
    assert store.is_worktree_archived("c:/users/wadeboyes/.grok/worktrees/wadeboyes/updates/")
    saved = json.loads((tmp_path / "data" / "archive.json").read_text(encoding="utf-8"))
    assert SID in saved["sessions"]
    assert worktree_key(r"C:\Users\WADEBOYES\.grok\worktrees\wadeboyes\updates") in saved["worktrees"]
    restore_catalog_item(store, {"kind": "session", "id": SID, "sessionId": SID})
    assert not store.is_session_archived(SID)
    assert summary.read_bytes() == before
    assert (summary.parent / "chat_history.jsonl").read_bytes() == history
    assert not any(sessions.rglob("archive.json"))


def test_catalog_hides_archived_flags_and_removed_paths(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    _session_file(sessions)
    grok_home = tmp_path / "grok"
    present = _worktree(grok_home, "updates")
    missing = grok_home / "worktrees" / "wadeboyes" / "gone"
    from tests.test_build_index import _worktree_db

    _worktree_db(
        grok_home / "worktrees.db",
        [
            (
                "wt-updates",
                str(present),
                str(tmp_path / "main"),
                "updates",
                "session",
                "linked",
                "feature",
                "abc",
                SID,
                None,
                10,
                20,
                "alive",
                json.dumps({"label": "updates"}),
            ),
            (
                "wt-gone",
                str(missing),
                str(tmp_path / "main"),
                "gone",
                "session",
                "linked",
                "old",
                "def",
                "",
                None,
                10,
                30,
                "alive",
                "{}",
            ),
        ],
    )
    summaries = SummaryStore(tmp_path / "data" / "build_summaries.json")
    archive = ArchiveStore(tmp_path / "data" / "archive.json")
    plain = build_catalog(sessions, grok_home, summaries, limit=20)
    ids = {item["id"] for item in plain["items"]}
    assert "wt-updates" in ids
    assert "wt-gone" in ids
    assert all(item["archived"] is False for item in plain["items"])

    archive.archive_session(SID)
    archive.archive_worktree(str(present))
    archive.note_removed_worktree(str(missing), "")
    flagged = build_catalog(sessions, grok_home, summaries, limit=20, archive=archive)
    by_id = {item["id"]: item for item in flagged["items"]}
    assert by_id[SID]["archived"] is True
    assert by_id["wt-updates"]["archived"] is True
    assert "wt-gone" not in by_id
    session_bytes = (_session_file(sessions).read_bytes() if False else (sessions / "encoded" / SID / "summary.json").read_bytes())
    assert b"Updates" in session_bytes

    missing.mkdir(parents=True)
    (missing / ".git").write_text("gitdir: x\n", encoding="utf-8")
    back = build_catalog(sessions, grok_home, summaries, limit=20, archive=archive)
    assert any(item["id"] == "wt-gone" for item in back["items"])
    assert archive.is_worktree_removed(str(missing)) is False


def test_parse_force_rejects_strings() -> None:
    assert parse_force(None) is False
    assert parse_force(False) is False
    assert parse_force(True) is True
    with pytest.raises(WorktreeRemoveError) as raised:
        parse_force("false")
    assert raised.value.code == "force"


def test_path_safety_never_calls_git(tmp_path: Path) -> None:
    grok_home = tmp_path / "grok"
    outside = tmp_path / "main-repo"
    outside.mkdir()
    (outside / "README").write_text("main\n", encoding="utf-8")
    sessions = tmp_path / "sessions"
    summary = _session_file(sessions)
    before = summary.read_bytes()
    worktree = _worktree(grok_home)
    group = grok_home / "worktrees" / "wadeboyes"
    root = grok_home / "worktrees"
    escaped = tmp_path / "escaped"
    escaped.mkdir()
    (escaped / "secret").write_text("nope\n", encoding="utf-8")
    link = grok_home / "worktrees" / "wadeboyes" / "alias"
    link.symlink_to(escaped, target_is_directory=True)

    def boom(_cwd: Path, _args: list[str]) -> GitResult:
        raise AssertionError("git must not run")

    archive = ArchiveStore(tmp_path / "data" / "archive.json")
    blocked = [
        str(outside),
        str(root),
        str(group),
        str(worktree / ".." / ".." / ".." / "escaped"),
        str(link),
        str(sessions),
    ]
    for raw in blocked:
        with pytest.raises(WorktreeRemoveError) as raised:
            remove_catalog_worktree(
                _item(Path(raw), source=str(outside)),
                grok_home,
                archive,
                force=True,
                git=boom,
            )
        assert raised.value.code in {"unsafe", "missing"}
    assert (outside / "README").read_text(encoding="utf-8") == "main\n"
    assert (escaped / "secret").read_text(encoding="utf-8") == "nope\n"
    assert summary.read_bytes() == before
    assert worktree.is_dir()
    with pytest.raises(WorktreeRemoveError) as same:
        remove_catalog_worktree(
            _item(worktree, source=str(worktree)),
            grok_home,
            archive,
            force=True,
            git=boom,
        )
    assert same.value.code == "unsafe"
    assert "main repo" in same.value.message


def test_main_checkout_under_worktrees_is_refused(tmp_path: Path) -> None:
    grok_home = tmp_path / "grok"
    worktree = _worktree(grok_home)
    archive = ArchiveStore(tmp_path / "data" / "archive.json")
    git = ScriptedGit(
        {
            ("rev-parse", "--git-dir"): GitResult(0, str(worktree / ".git"), ""),
            ("rev-parse", "--git-common-dir"): GitResult(0, str(worktree / ".git"), ""),
        }
    )
    with pytest.raises(WorktreeRemoveError) as raised:
        remove_catalog_worktree(
            _item(worktree, source=str(tmp_path / "elsewhere")),
            grok_home,
            archive,
            force=True,
            git=git,
        )
    assert raised.value.code == "unsafe"
    assert not any(call[1][:2] == ["worktree", "remove"] for call in git.calls)
    assert worktree.is_dir()


def test_clean_remove_has_no_force_and_archives_session(tmp_path: Path) -> None:
    grok_home = tmp_path / "grok"
    worktree = _worktree(grok_home)
    parent = tmp_path / "main-repo"
    parent.mkdir()
    (parent / "README").write_text("main\n", encoding="utf-8")
    sessions = tmp_path / "sessions"
    summary = _session_file(sessions)
    before = summary.read_bytes()
    archive = ArchiveStore(tmp_path / "data" / "archive.json")
    resolved = worktree.resolve()
    git = ScriptedGit(_linked_responses(worktree, parent, porcelain="", ahead="0"))
    result = remove_catalog_worktree(
        _item(worktree, source=str(parent)),
        grok_home,
        archive,
        force=False,
        git=git,
    )
    remove_calls = [args for _cwd, args in git.calls if args[:2] == ["worktree", "remove"]]
    assert remove_calls == [["worktree", "remove", str(resolved)]]
    assert git.calls[-1][0] == str(parent)
    assert all(args[0] != "branch" for _cwd, args in git.calls)
    assert all("-d" not in args and "-D" not in args for _cwd, args in git.calls)
    assert result["archivedSessionId"] == SID
    assert result["forced"] is False
    assert archive.is_session_archived(SID)
    assert archive.is_worktree_removed(str(resolved))
    assert summary.read_bytes() == before
    assert (parent / "README").read_text(encoding="utf-8") == "main\n"
    assert (worktree / "keep.txt").read_text(encoding="utf-8") == "tree\n"


def test_dirty_or_unpushed_refuses_until_force(tmp_path: Path) -> None:
    grok_home = tmp_path / "grok"
    worktree = _worktree(grok_home)
    parent = tmp_path / "main-repo"
    parent.mkdir()
    archive = ArchiveStore(tmp_path / "data" / "archive.json")
    dirty = ScriptedGit(_linked_responses(worktree, parent, porcelain=" M file\n", ahead="0"))
    with pytest.raises(WorktreeRemoveError) as raised:
        remove_catalog_worktree(_item(worktree), grok_home, archive, force=False, git=dirty)
    assert raised.value.code == "dirty"
    assert "uncommitted" in raised.value.message
    assert not any(args[:2] == ["worktree", "remove"] for _cwd, args in dirty.calls)

    ahead = ScriptedGit(_linked_responses(worktree, parent, porcelain="", ahead="2"))
    with pytest.raises(WorktreeRemoveError) as raised:
        remove_catalog_worktree(_item(worktree), grok_home, archive, force=False, git=ahead)
    assert raised.value.code == "unpushed"
    assert "unpushed" in raised.value.message
    assert not any(args[:2] == ["worktree", "remove"] for _cwd, args in ahead.calls)

    both = ScriptedGit(_linked_responses(worktree, parent, porcelain="?? new\n", ahead="1"))
    with pytest.raises(WorktreeRemoveError) as raised:
        remove_catalog_worktree(_item(worktree), grok_home, archive, force=False, git=both)
    assert raised.value.code == "dirty_unpushed"

    forced = ScriptedGit(_linked_responses(worktree, parent, porcelain=" M file\n", ahead="3"))
    result = remove_catalog_worktree(_item(worktree), grok_home, archive, force=True, git=forced)
    assert result["forced"] is True
    assert ["worktree", "remove", "--force", str(worktree.resolve())] in [args for _c, args in forced.calls]
    assert not archive.is_worktree_archived(str(worktree))
    source = Path("hub/worktree_remove.py").read_text(encoding="utf-8")
    assert "rmtree" not in source
    assert "shutil" not in source
    assert "\u2014" not in source and "\u2013" not in source


def test_assert_removable_worktree_bounds(tmp_path: Path) -> None:
    grok_home = tmp_path / "grok"
    worktree = _worktree(grok_home)
    assert assert_removable_worktree(str(worktree), grok_home) == worktree.resolve()
    with pytest.raises(WorktreeRemoveError):
        assert_removable_worktree(str(tmp_path / "nope"), grok_home)


def _run_git(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "safe.directory=*", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    assert _run_git(path, ["init", "-b", "main"]).returncode == 0
    assert _run_git(path, ["config", "user.email", "test@example.com"]).returncode == 0
    assert _run_git(path, ["config", "user.name", "Test"]).returncode == 0
    (path / "README").write_text("main\n", encoding="utf-8")
    assert _run_git(path, ["add", "README"]).returncode == 0
    assert _run_git(path, ["commit", "-m", "init"]).returncode == 0


def test_real_git_remove_keeps_branch_and_main_checkout(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "repo"
    _init_repo(repo)
    grok_home = tmp_path / "grok"
    worktree = grok_home / "worktrees" / "demo" / "feature"
    worktree.parent.mkdir(parents=True)
    added = _run_git(repo, ["worktree", "add", "-b", "feature", str(worktree)])
    assert added.returncode == 0, added.stderr
    sessions = tmp_path / "sessions"
    summary = _session_file(sessions)
    before = summary.read_bytes()
    archive = ArchiveStore(tmp_path / "data" / "archive.json")
    calls: list[list[str]] = []

    def git(cwd: Path, args: list[str]) -> GitResult:
        calls.append(list(args))
        proc = _run_git(cwd, args)
        return GitResult(proc.returncode, proc.stdout, proc.stderr)

    result = remove_catalog_worktree(
        _item(worktree, source=str(repo)),
        grok_home,
        archive,
        force=False,
        git=git,
    )
    assert result["removed"] is True
    assert result["branch"] == "feature"
    assert not worktree.exists()
    assert (repo / "README").read_text(encoding="utf-8") == "main\n"
    branches = _run_git(repo, ["branch", "--list", "feature"])
    assert "feature" in branches.stdout
    assert summary.read_bytes() == before
    remove_args = [args for args in calls if args[:2] == ["worktree", "remove"]]
    assert remove_args == [["worktree", "remove", str(worktree.resolve())]]
    assert all(args[0] != "branch" for args in calls)

    dirty_tree = grok_home / "worktrees" / "demo" / "dirty"
    added = _run_git(repo, ["worktree", "add", str(dirty_tree), "feature"])
    assert added.returncode == 0, added.stderr
    (dirty_tree / "local.txt").write_text("wip\n", encoding="utf-8")
    with pytest.raises(WorktreeRemoveError) as raised:
        remove_catalog_worktree(
            _item(dirty_tree, source=str(repo)),
            grok_home,
            archive,
            force=False,
            git=git,
        )
    assert raised.value.code == "dirty"
    assert dirty_tree.is_dir()
    forced = remove_catalog_worktree(
        _item(dirty_tree, source=str(repo)),
        grok_home,
        archive,
        force=True,
        git=git,
    )
    assert forced["forced"] is True
    assert not dirty_tree.exists()
    assert "feature" in _run_git(repo, ["branch", "--list", "feature"]).stdout
    assert (repo / "README").is_file()

    unique = grok_home / "worktrees" / "demo" / "unique"
    added = _run_git(repo, ["worktree", "add", "-b", "unique", str(unique)])
    assert added.returncode == 0, added.stderr
    (unique / "README").write_text("unique\n", encoding="utf-8")
    assert _run_git(unique, ["add", "README"]).returncode == 0
    assert _run_git(unique, ["commit", "-m", "local only"]).returncode == 0
    with pytest.raises(WorktreeRemoveError) as raised:
        remove_catalog_worktree(
            _item(unique, source=str(repo)),
            grok_home,
            archive,
            force=False,
            git=git,
        )
    assert raised.value.code == "unpushed"
    assert unique.is_dir()
    assert "unique" in _run_git(repo, ["branch", "--list", "unique"]).stdout


def test_ui_and_messages_have_no_em_dash() -> None:
    ui = Path("android/app/src/main/java/com/wadeboyes/grokremote/ui/GrokApp.kt").read_text(encoding="utf-8")
    client = Path("android/app/src/main/java/com/wadeboyes/grokremote/HubClient.kt").read_text(encoding="utf-8")
    for text in (ui, client, Path("hub/archive.py").read_text(encoding="utf-8")):
        assert "\u2014" not in text
        assert "\u2013" not in text
    assert "Remove anyway" in ui
    assert "This can't be undone." in ui
    assert "Nothing is archived." in ui
