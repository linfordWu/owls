"""
Integration test: AIAgent diagnoses disk usage issues.

Scenario
--------
The agent is asked to investigate high disk usage. It should:

1. Use the terminal tool to check overall disk usage (df -h).
2. Use the terminal tool to inspect directory sizes (du -sh).
3. Conclude which directories or mount points are consuming the most space.

The test uses a mock OpenAI client so no API key or network is required.
"""

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _build_mock_llm_sequence(tool_calls_log: list):
    """Return a mock chat.completions.create that drives a 3-turn investigation."""
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
            # Turn 1: check overall disk usage
            tc = SimpleNamespace()
            tc.id = "call_1"
            tc.type = "function"
            tc.function = SimpleNamespace(
                name="terminal",
                arguments=json.dumps(
                    {"command": "df -h"},
                    ensure_ascii=False,
                ),
            )
            resp.choices[0].message.tool_calls = [tc]
            resp.choices[0].finish_reason = "tool_calls"
            tool_calls_log.append(("terminal", tc.function.arguments))
        elif t == 2:
            # Turn 2: inspect top-level directory sizes
            tc = SimpleNamespace()
            tc.id = "call_2"
            tc.type = "function"
            tc.function = SimpleNamespace(
                name="terminal",
                arguments=json.dumps(
                    {"command": "du -sh /var/log /tmp /home 2>/dev/null | sort -rh"},
                    ensure_ascii=False,
                ),
            )
            resp.choices[0].message.tool_calls = [tc]
            resp.choices[0].finish_reason = "tool_calls"
            tool_calls_log.append(("terminal", tc.function.arguments))
        elif t == 3:
            # Turn 3: diagnostic conclusion
            resp.choices[0].message.content = (
                "The disk usage investigation shows that /var/log is consuming "
                "the most space due to large uncompressed log files. "
                "Overall disk usage on the root filesystem is at 87%."
            )
        else:
            # Safety net — should not reach turn 4
            resp.choices[0].message.content = "Investigation complete."

        return resp

    return mock_create


def test_agent_diagnoses_disk_usage_issue():
    """Verify that AIAgent can investigate and diagnose disk usage problems."""
    tool_calls_log = []
    mock_create = _build_mock_llm_sequence(tool_calls_log)

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
        "The server is running low on disk space. "
        "Investigate which directories or mount points are using the most space."
    )

    # 1. The agent should have invoked terminal tool at least twice
    assert len(tool_calls_log) >= 2, f"Expected at least 2 tool calls, got {tool_calls_log}"
    assert tool_calls_log[0][0] == "terminal"
    assert tool_calls_log[1][0] == "terminal"

    # 2. The first terminal call should check overall disk usage (df -h)
    first_args = json.loads(tool_calls_log[0][1])
    assert "df" in first_args.get("command", "").lower()

    # 3. The second terminal call should inspect directory sizes (du -sh)
    second_args = json.loads(tool_calls_log[1][1])
    assert "du" in second_args.get("command", "").lower()

    # 4. The final response should mention disk usage findings
    assert result is not None
    lower_result = result.lower()
    assert any(
        kw in lower_result
        for kw in ("disk", "space", "usage", "filesystem", "mount", "directory", "log")
    ), f"Expected disk-related diagnosis, got: {result[:500]}"


if __name__ == "__main__":
    test_agent_diagnoses_disk_usage_issue()
    print("PASS")
