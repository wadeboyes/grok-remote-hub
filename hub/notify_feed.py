"""Phone notification events for turn end and questions.

The Android app long-polls these over Tailscale. There is no push service.
Auto approve on the agent is unchanged: a permission the hub already answers
is not an event. A plan that is still waiting for approval is, because Wade
has to answer it.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

LINE_FINISHED = "Finished"
LINE_ERROR = "Stopped with an error"
LINE_NEEDS_ANSWER = "Needs your answer"

KIND_TURN_END = "turn_end"
KIND_PENDING = "pending_question"

# Drop a repeat of the same session, kind, and reason inside this window.
# A turn idle is sometimes broadcast twice (live id, then the unlock path).
DEDUPE_SECONDS = 3.0
MAX_WAIT_SECONDS = 25.0


def notification_line(kind: str, reason: str) -> str:
    """Short shade line. Title is the session name, sent separately."""
    if kind == KIND_PENDING or reason in {"question", "permission"}:
        return LINE_NEEDS_ANSWER
    if reason == "error":
        return LINE_ERROR
    return LINE_FINISHED


def classify_turn_notify(
    state: str,
    error: str | None,
    *,
    plan_waiting: bool = False,
    blip_text: str = "",
) -> tuple[str, str] | None:
    """Map a turn broadcast to (kind, reason), or None when it should stay quiet.

    A keepalive blip keeps the turn running. It is not a finish and not an error.
    """
    if (state or "").strip() != "idle":
        return None
    err = (error or "").strip()
    if blip_text and blip_text in err:
        return None
    if err:
        return (KIND_TURN_END, "error")
    if plan_waiting:
        return (KIND_PENDING, "permission")
    return (KIND_TURN_END, "finished")


def should_notify_session(open_session_id: str | None, event_session_id: str | None) -> bool:
    """False for a blank id, and for the session already open on that device."""
    event_id = str(event_session_id or "").strip()
    if not event_id:
        return False
    open_id = str(open_session_id or "").strip()
    if open_id and open_id == event_id:
        return False
    return True


def group_key(session_id: str) -> str:
    """One notification group per session, so two sessions do not stack together."""
    return "session:" + str(session_id or "").strip()


class NotifyFeed:
    """In-memory cursor feed. Process-local. Lost on hub restart, which is fine."""

    def __init__(self, maxlen: int = 200) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=max(1, int(maxlen)))
        self._next_id = 1
        self._cursor = 0
        self._dedupe: dict[tuple[str, str, str], float] = {}
        self._waiters: list[asyncio.Future[None]] = []

    @property
    def cursor(self) -> int:
        return self._cursor

    def publish(
        self,
        *,
        session_id: str,
        kind: str,
        reason: str,
        title: str,
        chat_path: str = "",
    ) -> dict[str, Any] | None:
        sid = str(session_id or "").strip()
        if not sid:
            return None
        kind_s = str(kind or "").strip()
        reason_s = str(reason or "").strip()
        if kind_s not in {KIND_TURN_END, KIND_PENDING}:
            return None
        now = time.monotonic()
        key = (sid, kind_s, reason_s)
        prev = self._dedupe.get(key)
        if prev is not None and (now - prev) < DEDUPE_SECONDS:
            return None
        self._dedupe[key] = now
        # Keep the dedupe map from growing without bound across many sessions.
        if len(self._dedupe) > 400:
            cutoff = now - DEDUPE_SECONDS
            self._dedupe = {k: ts for k, ts in self._dedupe.items() if ts >= cutoff}
            self._dedupe[key] = now
        event_id = self._next_id
        self._next_id += 1
        self._cursor = event_id
        event = {
            "id": event_id,
            "kind": kind_s,
            "sessionId": sid,
            "title": (title or "").strip() or "Grok session",
            "line": notification_line(kind_s, reason_s),
            "reason": reason_s,
            "chatPath": chat_path or "",
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self._events.append(event)
        self._wake()
        return dict(event)

    def after(self, since: int) -> list[dict[str, Any]]:
        cursor = int(since)
        return [dict(event) for event in self._events if int(event["id"]) > cursor]

    async def poll(self, since: int, wait: float) -> tuple[list[dict[str, Any]], int]:
        """Return events newer than ``since``.

        ``since < 0`` primes a client: cursor only, no backlog.
        ``wait`` is capped so a dropped phone does not hold a handler forever.
        """
        if int(since) < 0:
            return [], self._cursor
        found = self.after(since)
        hold = max(0.0, min(float(wait), MAX_WAIT_SECONDS))
        if found or hold <= 0:
            return found, self._cursor
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[None] = loop.create_future()
        self._waiters.append(fut)
        try:
            await asyncio.wait_for(fut, hold)
        except asyncio.TimeoutError:
            pass
        finally:
            if fut in self._waiters:
                self._waiters.remove(fut)
        return self.after(since), self._cursor

    def _wake(self) -> None:
        waiters = list(self._waiters)
        self._waiters.clear()
        for fut in waiters:
            if not fut.done():
                fut.set_result(None)
