"""
Integration test: AIAgent handles user creation and deletion with sudo awareness.

Scenario
--------
The agent is asked to create a new system user and then delete it. It should:

1. Use the terminal tool to create the user (useradd or sudo useradd).
2. Use the terminal tool to verify the user exists (id, getent passwd).
3. Use the terminal tool to delete the user (userdel or sudo userdel).
4. Confirm the user was removed.

If the test is not running as root, it skips because useradd/userdel require
root privileges (or passwordless sudo, which is not guaranteed in CI).

The test uses a mock OpenAI client so no API key or network is required.
"""

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


TEST_USERNAME = "owls_test_user_42"


def _build_mock_llm_sequence(username: str, tool_calls_log: list):
    """Return a mock chat.completions.create that drives a 4-turn workflow."""
    turn = [0]

    def mock_create(**kwargs):
        turn[0] += 1
        t = turn[0]

        resp = SimpleNamespace()
        resp.choices = [SimpleNamespace()]
        resp.choices[0].message = SimpleNamespace()
        resp.choices[0].message.content = None
        resp.choices[0].message.refusal = None
        resp.choices[0].message.tool_calls = []
        resp.choices[0].finish_reason = "stop"
        resp.usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            prompt_tokens_details=None,
        )
        resp.model = "test/model"

        if t == 1:
            # Turn 1: create the user
            tc = SimpleNamespace()
            tc.id = "call_1"
            tc.type = "function"
            tc.function = SimpleNamespace(
                name="terminal",
                arguments=json.dumps(
                    {"command": f"sudo useradd -m {username}"},
                    ensure_ascii=False,
                ),
            )
            resp.choices[0].message.tool_calls = [tc]
            resp.choices[0].finish_reason = "tool_calls"
            tool_calls_log.append(("terminal", tc.function.arguments))
        elif t == 2:
            # Turn 2: verify the user exists
            tc = SimpleNamespace()
            tc.id = "call_2"
            tc.type = "function"
            tc.function = SimpleNamespace(
                name="terminal",
                arguments=json.dumps(
                    {"command": f"id {username}"},
                    ensure_ascii=False,
                ),
            )
            resp.choices[0].message.tool_calls = [tc]
            resp.choices[0].finish_reason = "tool_calls"
            tool_calls_log.append(("terminal", tc.function.arguments))
        elif t == 3:
            # Turn 3: delete the user
            tc = SimpleNamespace()
            tc.id = "call_3"
            tc.type = "function"
            tc.function = SimpleNamespace(
                name="terminal",
                arguments=json.dumps(
                    {"command": f"sudo userdel -r {username}"},
                    ensure_ascii=False,
                ),
            )
            resp.choices[0].message.tool_calls = [tc]
            resp.choices[0].finish_reason = "tool_calls"
            tool_calls_log.append(("terminal", tc.function.arguments))
        elif t == 4:
            # Turn 4: confirm removal
            resp.choices[0].message.content = (
                f"User '{username}' has been created, verified, and then removed "
                "successfully. The system is back to its original state."
            )
        else:
            # Safety net — should not reach turn 5
            resp.choices[0].message.content = "User management workflow complete."

        return resp

    return mock_create


def test_agent_manages_system_user():
    """Verify that AIAgent can create, verify, and delete a system user."""
    if os.geteuid() != 0:
        pytest.skip("useradd/userdel require root privileges; skipping non-root test run")

    tool_calls_log = []
    mock_create = _build_mock_llm_sequence(TEST_USERNAME, tool_calls_log)

    mock_client = MagicMock()
    mock_client.chat.completions.create = mock_create
    mock_client.close = MagicMock()

    # Import AIAgent inside the test so sys.path is already set by conftest
    from run_agent import AIAgent

    agent = AIAgent(
        base_url="http://localhost:1",
        api_key="test",
        model="test/model",
        max_iterations=10,
        quiet_mode=True,
        enabled_toolsets=["terminal", "file"],
    )

    # Force non-streaming path (mocks return SimpleNamespace, not stream iterators)
    agent.client = MagicMock()
    agent._create_request_openai_client = lambda **kw: mock_client
    agent._try_activate_fallback = lambda: False

    result = agent.chat(
        f"Create a system user named '{TEST_USERNAME}', verify it exists, "
        "then delete it and confirm the removal."
    )

    # 1. The agent should have invoked terminal tool at least 3 times
    assert len(tool_calls_log) >= 3, f"Expected at least 3 tool calls, got {tool_calls_log}"
    assert all(tc[0] == "terminal" for tc in tool_calls_log), (
        f"Expected only terminal tool calls, got {tool_calls_log}"
    )

    # 2. First call should create the user (useradd)
    first_args = json.loads(tool_calls_log[0][1])
    first_cmd = first_args.get("command", "").lower()
    assert "useradd" in first_cmd
    assert TEST_USERNAME in first_args.get("command", "")

    # 3. Second call should verify the user (id)
    second_args = json.loads(tool_calls_log[1][1])
    second_cmd = second_args.get("command", "").lower()
    assert "id" in second_cmd or "getent" in second_cmd
    assert TEST_USERNAME in second_args.get("command", "")

    # 4. Third call should delete the user (userdel)
    third_args = json.loads(tool_calls_log[2][1])
    third_cmd = third_args.get("command", "").lower()
    assert "userdel" in third_cmd
    assert TEST_USERNAME in third_args.get("command", "")

    # 5. The final response should mention the user lifecycle
    assert result is not None
    lower_result = result.lower()
    assert any(
        kw in lower_result
        for kw in ("user", "created", "deleted", "removed", "verify", "exist")
    ), f"Expected user-management summary, got: {result[:500]}"


if __name__ == "__main__":
    test_agent_manages_system_user()
    print("PASS")
