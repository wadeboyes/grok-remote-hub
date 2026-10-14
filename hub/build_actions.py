"""Resume or restart a session or worktree into the hub chat.

Resume uses the same arguments as::

    grok.exe --cwd <dir> --resume <session-id>

The phone then opens the hub chat. This does not launch a second interactive
TUI, because that process would fight the hub agent and would not show on
the phone. When the folder already has a session, that latest session is
resumed. ``session/new`` runs only when the folder has none.
``--always-approve`` on the agent serve is left unchanged.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from hub.build_index import latest_session_id_for_cwd
from hub.grok_bin import GrokBinError, require_grok_exe, resume_command

log = logging.getLogger("hub.build")


class BuildActionError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def chat_path(session_id: str, cwd: str) -> str:
    sid = quote(session_id or "", safe="")
    folder = quote(cwd or "", safe="")
    return f"/?open={sid}&cwd={folder}&embed=chat"


def _public_command(argv: list[str]) -> list[str]:
    return list(argv)


async def execute_build_action(
    hub: Any,
    item: dict[str, Any],
    *,
    mode: str,
) -> dict[str, Any]:
    """Resume or restart ``item`` through the hub agent.

    ``mode`` is ``resume``, ``restart``, or ``auto`` (uses the item hint).
    """
    try:
        grok_exe = require_grok_exe(getattr(hub.config, "grok_bin", ""))
    except GrokBinError as exc:
        raise BuildActionError(exc.message, exc.status) from exc

    cwd = str(item.get("cwd") or "").strip()
    session_id = str(item.get("sessionId") or "").strip()
    if not cwd:
        raise BuildActionError("cwd required", 400)

    requested = (mode or "auto").strip().lower()
    if requested not in {"auto", "resume", "restart"}:
        raise BuildActionError("mode must be auto, resume, or restart", 400)

    if not session_id:
        root = getattr(getattr(hub, "config", None), "sessions_root", None)
        if root:
            try:
                session_id = latest_session_id_for_cwd(root, cwd)
            except Exception:
                log.debug("latest session lookup failed", exc_info=True)
                session_id = ""

    # A worktree card used to ask for restart whenever the database had no
    # linked id. Resume the latest session in that folder when one exists.
    # Start fresh only when the folder has no session.
    if session_id:
        requested = "resume"
    elif requested == "auto":
        requested = "restart"

    if not getattr(hub.acp, "connected", False):
        raise BuildActionError("agent not connected", 503)

    if requested == "restart" or not session_id:
        new_id = await hub.acp.session_new(cwd)
        new_id = str(new_id or "").strip()
        if not new_id:
            raise BuildActionError("session/new did not return an id", 502)
        hub._record_hub_session(new_id, cwd)
        try:
            hub._stamp_origin_sync(new_id, "user")
        except Exception:
            log.debug("stamp origin after restart failed", exc_info=True)
        command = resume_command(grok_exe, cwd, new_id)
        log.info("build restart cwd=%s live=%s", cwd, new_id)
        return {
            "ok": True,
            "action": "restart",
            "viewSessionId": new_id,
            "liveSessionId": new_id,
            "switched": False,
            "reason": "restart",
            "cwd": cwd,
            "command": _public_command(command),
            "chatPath": chat_path(new_id, cwd),
            "title": str(item.get("title") or ""),
        }

    command = resume_command(grok_exe, cwd, session_id)
    log.info(
        "build resume command: %s --cwd %s --resume %s",
        command[0],
        cwd,
        session_id,
    )
    live_id, switched, reason, ensure_action = await hub._ensure_hub_agent_session(
        session_id,
        cwd,
        None,
        notify_switch=True,
    )
    live_id = str(live_id or "").strip()
    if not live_id:
        raise BuildActionError("resume did not return a live session", 502)
    action = "restart" if ensure_action == "new" else "resume"
    # Open the original id so the chat shows its history, then the hub
    # attaches the live session for that folder.
    open_id = session_id or live_id
    return {
        "ok": True,
        "action": action,
        "viewSessionId": session_id,
        "liveSessionId": live_id,
        "switched": bool(switched),
        "reason": str(reason or ""),
        "ensureAction": str(ensure_action or ""),
        "cwd": cwd,
        "command": _public_command(command),
        "chatPath": chat_path(open_id, cwd),
        "title": str(item.get("title") or ""),
    }
