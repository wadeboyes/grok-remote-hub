"""Resolve the Grok CLI binary. Never prefer grok.bat over grok.exe."""

from __future__ import annotations

from pathlib import Path


class GrokBinError(RuntimeError):
    """grok.exe could not be resolved, or the path is grok.bat."""

    def __init__(self, message: str, status: int = 500):
        super().__init__(message)
        self.message = message
        self.status = status


def _home_exe() -> Path:
    return Path.home() / ".grok" / "bin" / "grok.exe"


def prefer_grok_exe(path: str | None) -> str:
    """Return a grok path that is not grok.bat when an exe is available.

    Wade's install is ``C:\\Users\\WADEBOYES\\.grok\\bin\\grok.exe``. A PATH
    entry named grok.bat must not win over that exe or a sibling exe.
    """
    raw = (path or "").strip().strip('"')
    home_exe = _home_exe()

    def _exe_beside_bat(bat: Path) -> str | None:
        sibling = bat.with_suffix(".exe")
        if sibling.is_file():
            return str(sibling)
        if home_exe.is_file():
            return str(home_exe)
        # Still name the exe. Callers refuse to run a .bat.
        return str(sibling)

    if not raw or raw.lower() in {"grok", "grok.bat", "grok.exe"}:
        if home_exe.is_file():
            return str(home_exe)
        if raw.lower().endswith(".bat"):
            return _exe_beside_bat(Path(raw)) or str(home_exe)
        if raw.lower().endswith(".exe") and raw not in {"grok.exe"}:
            return raw
        return str(home_exe) if raw.lower() == "grok.exe" else (raw or "grok")

    candidate = Path(raw)
    if candidate.suffix.lower() == ".bat":
        swapped = _exe_beside_bat(candidate)
        return swapped or str(home_exe)
    if candidate.suffix.lower() == ".exe":
        return str(candidate)
    if home_exe.is_file() and candidate.name.lower() in {"grok", "grok.bat"}:
        return str(home_exe)
    return str(candidate)


def require_grok_exe(path: str | None) -> str:
    """Resolve grok.exe. Raise if the only candidate is grok.bat."""
    resolved = prefer_grok_exe(path)
    if resolved.lower().endswith(".bat"):
        raise GrokBinError(
            "Refusing grok.bat. Set hub.grok_bin to grok.exe "
            "(C:\\Users\\WADEBOYES\\.grok\\bin\\grok.exe)."
        )
    return resolved


def resume_command(grok_exe: str, cwd: str, session_id: str) -> list[str]:
    """Canonical resume argv: grok.exe --cwd <dir> --resume <session-id>."""
    exe = require_grok_exe(grok_exe)
    folder = (cwd or "").strip()
    sid = (session_id or "").strip()
    if not folder:
        raise GrokBinError("cwd required for resume", status=400)
    if not sid:
        raise GrokBinError("session id required for resume", status=400)
    return [exe, "--cwd", folder, "--resume", sid]
