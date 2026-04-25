"""
Integration test: AIAgent queries process and port status.

Scenario
--------
The agent is asked to find which process is listening on a specific port.
It should:

1. Use the terminal tool to list listening ports and associated processes (ss -tlnp).
2. Use the terminal tool to inspect the identified process (ps).
3. Conclude which process owns the port and describe its status.

The test uses a mock OpenAI client so no API key or network is required.
"""

import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _start_simple_http_server(port: int = 8765) -> subprocess.Popen:
    """Start a simple HTTP server in the background to occupy a known port."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(Path(__file__).parent),
    )
    # Allow the server to start and bind the port
    time.sleep(1.5)
    if proc.poll() is not None:
        stdout, stderr = proc.communicate()
        pytest.skip(f"http.server exited early: {stderr or stdout}")
    return proc


def _build_mock_llm_sequence(port: int, tool_calls_log: list):
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
            # Turn 1: list listening sockets with process info
            tc = SimpleNamespace()
            tc.id = "call_1"
            tc.type = "function"
            tc.function = SimpleNamespace(
                name="terminal",
                arguments=json.dumps(
                    {"command": "ss -tlnp"},
                    ensure_ascii=False,
                ),
            )
            resp.choices[0].message.tool_calls = [tc]
            resp.choices[0].finish_reason = "tool_calls"
            tool_calls_log.append(("terminal", tc.function.arguments))
        elif t == 2:
            # Turn 2: inspect processes with ps
            tc = SimpleNamespace()
            tc.id = "call_2"
            tc.type = "function"
            tc.function = SimpleNamespace(
                name="terminal",
                arguments=json.dumps(
                    {"command": "ps aux | grep python"},
                    ensure_ascii=False,
                ),
            )
            resp.choices[0].message.tool_calls = [tc]
            resp.choices[0].finish_reason = "tool_calls"
            tool_calls_log.append(("terminal", tc.function.arguments))
        elif t == 3:
            # Turn 3: diagnostic conclusion
            resp.choices[0].message.content = (
                f"Port {port} is being used by a Python http.server process. "
                "The process is actively listening and serving HTTP requests. "
                "This is a development server spawned by the python -m http.server module."
            )
        else:
            # Safety net — should not reach turn 4
            resp.choices[0].message.content = "Investigation complete."

        return resp

    return mock_create


def test_agent_queries_process_and_port():
    """Verify that AIAgent can identify which process owns a listening port."""
    proc = _start_simple_http_server(port=8765)
    try:
        tool_calls_log = []
        mock_create = _build_mock_llm_sequence(8765, tool_calls_log)

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
            "Something is listening on port 8765. "
            "Find out which process it is and tell me its status."
        )

        # 1. The agent should have invoked terminal tool at least twice
        assert len(tool_calls_log) >= 2, f"Expected at least 2 tool calls, got {tool_calls_log}"
        assert tool_calls_log[0][0] == "terminal"
        assert tool_calls_log[1][0] == "terminal"

        # 2. The first terminal call should list listening ports (ss -tlnp)
        first_args = json.loads(tool_calls_log[0][1])
        first_cmd = first_args.get("command", "").lower()
        assert "ss" in first_cmd or "netstat" in first_cmd or "lsof" in first_cmd

        # 3. The second terminal call should inspect processes (ps)
        second_args = json.loads(tool_calls_log[1][1])
        second_cmd = second_args.get("command", "").lower()
        assert "ps" in second_cmd

        # 4. The final response should mention the port and process
        assert result is not None
        lower_result = result.lower()
        assert any(
            kw in lower_result
            for kw in ("port", "process", "listening", "python", "http.server", "8765")
        ), f"Expected port/process-related diagnosis, got: {result[:500]}"

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    test_agent_queries_process_and_port()
    print("PASS")
