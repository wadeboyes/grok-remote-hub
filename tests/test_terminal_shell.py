"""Hub terminals use PowerShell on Windows and kill the process tree."""

from __future__ import annotations

import os

from hub.acp_terminal import shell_launch_argv, taskkill_tree_argv


def test_windows_shell_is_powershell_not_cmd() -> None:
    argv = shell_launch_argv("Get-ChildItem")
    if os.name == "nt":
        assert argv[0] == "powershell.exe"
        assert "-NoLogo" in argv
        assert "cmd.exe" not in argv
        assert argv[-1] == "Get-ChildItem"
    else:
        assert argv[0] == "sh"
        assert argv[-1] == "Get-ChildItem"


def test_taskkill_includes_child_tree() -> None:
    argv = taskkill_tree_argv(4242)
    assert argv[:3] == ["taskkill", "/PID", "4242"]
    assert "/T" in argv
    assert "/F" in argv


def test_terminal_create_uses_explicit_argv_not_cmd_shell() -> None:
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "hub" / "acp_terminal.py").read_text(
        encoding="utf-8"
    )
    create = text[text.find("async def create") :]
    assert "shell_launch_argv" in create
    assert "create_subprocess_shell" not in create
    assert "_kill_windows_tree" in text
    assert "asyncio.to_thread" in text
