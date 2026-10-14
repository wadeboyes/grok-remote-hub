"""Replayed permissions after session/load are shown, not auto-approved."""

from __future__ import annotations

import asyncio

from hub.acp_client import AcpClient
from hub.config import Config
from hub.pending_interaction import (
    describe_pending_interaction,
    permission_as_question,
)


def test_describe_permission_and_plan_have_no_dash() -> None:
    perm = describe_pending_interaction(
        {
            "sessionUpdate": "pending_interaction",
            "kind": "permission",
            "title": "Run Command",
            "options": [
                {"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"},
                {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
            ],
        }
    )
    assert perm is not None
    assert perm["kind"] == "permission"
    assert perm["title"] == "Run Command"
    assert perm["options"][0]["id"] == "allow-once"
    plan = describe_pending_interaction(
        {"update": {"sessionUpdate": "pending_interaction", "kind": "plan_approval"}}
    )
    assert plan is not None
    assert plan["kind"] == "plan"
    blob = str(perm) + str(plan)
    assert "\u2014" not in blob and "\u2013" not in blob


def test_permission_as_question_keeps_option_id() -> None:
    shaped = permission_as_question(
        {
            "sessionId": "abc",
            "toolCall": {"title": "Run Command"},
            "options": [{"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"}],
        }
    )
    assert shaped is not None
    assert shaped["questions"][0]["options"][0]["id"] == "allow-once"


def test_replayed_permission_is_surfaced_not_auto_approved() -> None:
    async def run() -> None:
        client = AcpClient(Config(), secret="test-permission-surface")
        sent: list[dict] = []

        async def fake_send(payload: dict) -> None:
            sent.append(payload)

        client._send = fake_send  # type: ignore[method-assign]
        seen: list[dict] = []

        async def on_question(payload: dict) -> None:
            seen.append(payload)
            client.answer_permission(str(payload["requestId"]), "allow-once")

        client.on_user_question = on_question
        client.mark_post_load("sess-1")
        await client._handle_permission(
            7,
            {
                "sessionId": "sess-1",
                "toolCall": {"title": "Run Command"},
                "options": [
                    {"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"}
                ],
            },
        )
        assert seen and seen[0]["kind"] == "permission"
        assert seen[0]["quiet"] is True
        assert sent
        result = sent[-1]["result"]
        assert result["outcome"]["optionId"] == "allow-once"
        assert "allow-always" not in str(sent)

    asyncio.run(run())


def test_live_permission_still_auto_approves() -> None:
    async def run() -> None:
        client = AcpClient(Config(), secret="test-permission-live")
        sent: list[dict] = []

        async def fake_send(payload: dict) -> None:
            sent.append(payload)

        client._send = fake_send  # type: ignore[method-assign]
        client.on_user_question = lambda _payload: (_ for _ in ()).throw(
            AssertionError("live permission must not open the UI")
        )
        await client._handle_permission(
            3,
            {
                "sessionId": "sess-live",
                "options": [
                    {"optionId": "allow-always", "kind": "allow_always", "name": "Always"}
                ],
            },
        )
        assert sent[-1]["result"]["outcome"]["optionId"] == "allow-always"

    asyncio.run(run())
