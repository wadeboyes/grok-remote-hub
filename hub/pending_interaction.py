"""Orphaned grok interactions replayed after session/load.

grok serve keeps a turn alive across a client disconnect. The stuck case is
often a permission, an ask-user question, or a plan approval still held in
memory. After session/load the agent re-sends it. The hub shows that to the
person at the phone. It does not answer it with --always-approve.
"""

from __future__ import annotations

from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _options(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        option_id = str(item.get("optionId") or item.get("option_id") or item.get("id") or "").strip()
        if not option_id:
            continue
        label = str(item.get("name") or item.get("label") or item.get("title") or option_id).strip()
        kind = str(item.get("kind") or "").strip()
        out.append({"id": option_id, "label": label or option_id, "kind": kind})
    return out


def _kind_of(params: dict[str, Any], update: dict[str, Any]) -> str:
    for source in (update, params):
        for key in ("kind", "interactionKind", "interaction_kind", "type"):
            raw = source.get(key)
            if raw is not None and str(raw).strip():
                return str(raw).strip().lower().replace("-", "_")
    if _options(update.get("options") or params.get("options")):
        return "permission"
    questions = update.get("questions") or params.get("questions")
    if isinstance(questions, list) and questions:
        return "question"
    return "permission"


def describe_pending_interaction(params: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a UI payload for a replayed pending_interaction, or None.

    The payload is plain data: kind, title, options, questions. No dash characters.
    """
    if not isinstance(params, dict):
        return None
    update = _as_dict(params.get("update"))
    kind_name = ""
    for source in (update, params):
        for key in ("sessionUpdate", "session_update", "kind"):
            raw = source.get(key)
            if raw is not None and str(raw).strip():
                kind_name = str(raw).strip().lower()
                break
        if kind_name:
            break
    if kind_name and kind_name not in ("pending_interaction", "permission", "plan", "question"):
        if "pending" not in kind_name and "permission" not in kind_name:
            return None
    title = str(
        update.get("title")
        or params.get("title")
        or update.get("message")
        or params.get("message")
        or ""
    ).strip()
    tool = _as_dict(update.get("toolCall") or params.get("toolCall") or update.get("tool_call"))
    if not title and tool:
        title = str(tool.get("title") or tool.get("kind") or "").strip()
    options = _options(update.get("options") or params.get("options"))
    questions = update.get("questions") or params.get("questions") or []
    if not isinstance(questions, list):
        questions = []
    kind = _kind_of(params, update)
    if "plan" in kind:
        kind = "plan"
    elif "question" in kind or "ask" in kind:
        kind = "question"
    elif options or "permission" in kind:
        kind = "permission"
    else:
        kind = "permission"
    if not title and not options and not questions and kind != "plan":
        return None
    if not title:
        if kind == "plan":
            title = "A plan is waiting for approval."
        elif kind == "question":
            title = "Grok needs your answer."
        else:
            title = "Grok needs your answer before it can continue."
    return {
        "kind": kind,
        "title": title,
        "options": options,
        "questions": questions,
    }


def permission_as_question(params: dict[str, Any] | None) -> dict[str, Any] | None:
    """Shape a session/request_permission as one ask-user question.

    The phone already answers that shape. The option id is what grok expects.
    """
    if not isinstance(params, dict):
        return None
    options = _options(params.get("options"))
    if not options:
        return None
    tool = _as_dict(params.get("toolCall") or params.get("tool_call"))
    title = str(
        params.get("title")
        or tool.get("title")
        or tool.get("kind")
        or "Grok needs your answer before it can continue."
    ).strip()
    return {
        "kind": "permission",
        "title": title,
        "questions": [
            {
                "id": "permission",
                "text": title,
                "multiSelect": False,
                "options": [
                    {
                        "id": item["id"],
                        "label": item["label"],
                        "description": item["kind"],
                    }
                    for item in options
                ],
            }
        ],
    }
