"""
Integration test: AI Hackathon 2026 — High-risk operation interception.

Scenario
--------
A mock LLM drives the agent to attempt a dangerous command
(``rm -rf /tmp/owls_test_risk_$$``).  The approval system must:

1. Detect the command as dangerous via ``detect_dangerous_command``.
2. Block execution via ``check_all_command_guards``.
3. Return an explanatory message that the agent can surface to the user.

The test uses the real ``tools/approval.py`` functions and a mock OpenAI
client so no API key or network is required.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# Path to the temp directory used for the risky command
TEST_DIR_PREFIX = "/tmp/owls_test_risk_"


def _build_mock_llm_sequence(test_dir: str, tool_calls_log: list):
    """Return a mock chat.completions.create that drives a 2-turn scenario.

    Turn 1: agent attempts a recursive delete of the test directory.
    Turn 2: agent explains that the command was blocked (mirrors real LLM
    behavior when it receives the BLOCKED tool result).
    """
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
            # Turn 1: agent attempts a recursive delete of the test directory
            tc = SimpleNamespace()
            tc.id = "call_1"
            tc.type = "function"
            tc.function = SimpleNamespace(
                name="terminal",
                arguments=json.dumps(
                    {"command": f"rm -rf {test_dir}"},
                    ensure_ascii=False,
                ),
            )
            resp.choices[0].message.tool_calls = [tc]
            resp.choices[0].finish_reason = "tool_calls"
            tool_calls_log.append(("terminal", tc.function.arguments))
        elif t == 2:
            # Turn 2: agent explains the block decision (behavior explainability)
            resp.choices[0].message.content = (
                f"I cannot delete {test_dir} because the command was blocked by the "
                "approval system. It was flagged as dangerous (delete in root path). "
                "You would need to approve it first."
            )
        else:
            # Safety net
            resp.choices[0].message.content = "No further action."

        return resp

    return mock_create


def test_high_risk_command_intercepted_and_explained():
    """Verify that a mock agent attempting ``rm -rf`` is blocked with an explanation."""
    # Create a unique temp directory for this test run
    test_dir = f"{TEST_DIR_PREFIX}{os.getpid()}"
    Path(test_dir).mkdir(parents=True, exist_ok=True)

    try:
        tool_calls_log = []
        mock_create = _build_mock_llm_sequence(test_dir, tool_calls_log)

        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create
        mock_client.close = MagicMock()

        from run_agent import AIAgent

        # Force interactive mode so the approval system engages
        old_interactive = os.environ.get("OWLS_INTERACTIVE")
        os.environ["OWLS_INTERACTIVE"] = "1"
        try:
            agent = AIAgent(
                base_url="http://localhost:1",
                api_key="test",
                model="test/model",
                max_iterations=5,
                quiet_mode=True,
                enabled_toolsets=["terminal"],
            )

            # Force non-streaming path (mocks return SimpleNamespace, not stream iterators)
            agent.client = MagicMock()
            agent._create_request_openai_client = lambda **kw: mock_client
            agent._try_activate_fallback = lambda: False

            result = agent.chat("Please delete the test directory.")
        finally:
            if old_interactive is None:
                os.environ.pop("OWLS_INTERACTIVE", None)
            else:
                os.environ["OWLS_INTERACTIVE"] = old_interactive

        # 1. The agent should have attempted the terminal tool
        assert len(tool_calls_log) >= 1, f"Expected at least 1 tool call, got {tool_calls_log}"
        assert tool_calls_log[0][0] == "terminal"
        assert test_dir in tool_calls_log[0][1]

        # 2. The final response should explain that the command was blocked
        assert result is not None
        lower_result = result.lower()
        assert any(
            kw in lower_result
            for kw in ("block", "denied", "dangerous", "risk", "approval", "not allowed")
        ), f"Expected risk-related explanation, got: {result[:500]}"

        # 3. The temp directory must still exist (command was intercepted)
        assert Path(test_dir).exists(), f"Test directory was deleted despite interception: {test_dir}"

    finally:
        # Teardown: clean up the temp directory
        if Path(test_dir).exists():
            shutil.rmtree(test_dir, ignore_errors=True)


def test_detect_dangerous_command_patterns():
    """Unit-test the real ``detect_dangerous_command`` against competition requirements."""
    from tools.approval import detect_dangerous_command

    # rm -rf /  (root path delete)
    is_dangerous, key, desc = detect_dangerous_command("rm -rf /")
    assert is_dangerous is True
    assert "delete" in desc.lower()

    # rm -rf /tmp/owls_test_risk_123 (also matches root-path pattern because of leading /)
    is_dangerous, key, desc = detect_dangerous_command("rm -rf /tmp/owls_test_risk_123")
    assert is_dangerous is True
    assert "delete" in desc.lower()

    # Dangerous chmod
    is_dangerous, key, desc = detect_dangerous_command("chmod 777 /etc/passwd")
    assert is_dangerous is True
    assert "world/other-writable" in desc.lower()

    # Safe chmod should NOT be flagged
    is_dangerous, key, desc = detect_dangerous_command("chmod 644 /tmp/file.txt")
    assert is_dangerous is False
    assert key is None
    assert desc is None

    # System core file deletion
    is_dangerous, key, desc = detect_dangerous_command("rm /etc/hosts")
    assert is_dangerous is True
    assert "delete" in desc.lower()

    # Safe ls should NOT be flagged
    is_dangerous, key, desc = detect_dangerous_command("ls -la /tmp")
    assert is_dangerous is False


if __name__ == "__main__":
    test_detect_dangerous_command_patterns()
    test_high_risk_command_intercepted_and_explained()
    print("PASS")
