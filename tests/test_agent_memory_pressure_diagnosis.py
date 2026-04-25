"""
Integration test: AIAgent diagnoses a memory-pressure issue.

Scenario
--------
A Python program (tests/fixtures/memory_pressure_program.py) allocates a
percentage of system RAM in bytearray chunks, touches every page so RSS
tracks it, and then sleeps forever without freeing anything.  The agent is
asked to investigate high memory usage.  It should:

1. Use the terminal tool to inspect the running process (ps, /proc/pid/status).
2. Use the file tool to read the source code.
3. Conclude that the program does not release allocated memory.

The test uses a mock OpenAI client so no API key or network is required.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# Path to the in-repo memory-pressure fixture
MEMORY_PRESSURE_PROGRAM = Path(__file__).parent / "fixtures" / "memory_pressure_program.py"


def _start_memory_pressure_program(percent: int = 3, duration: int = 3) -> subprocess.Popen:
    """Start the memory-pressure fixture in the background."""
    proc = subprocess.Popen(
        [sys.executable, str(MEMORY_PRESSURE_PROGRAM), str(percent), str(duration)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # Allow the process to warm up and allocate memory
    time.sleep(1.5)
    if proc.poll() is not None:
        stdout, stderr = proc.communicate()
        pytest.skip(f"memory_pressure_program exited early: {stderr or stdout}")
    return proc


def _build_mock_llm_sequence(proc_pid: int, src_path: Path, tool_calls_log: list):
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
            # Turn 1: inspect the running process with terminal
            tc = SimpleNamespace()
            tc.id = "call_1"
            tc.type = "function"
            tc.function = SimpleNamespace(
                name="terminal",
                arguments=json.dumps(
                    {"command": f"ps -o pid,rss,comm -p {proc_pid}"},
                    ensure_ascii=False,
                ),
            )
            resp.choices[0].message.tool_calls = [tc]
            resp.choices[0].finish_reason = "tool_calls"
            tool_calls_log.append(("terminal", tc.function.arguments))
        elif t == 2:
            # Turn 2: read the source code with file tool
            tc = SimpleNamespace()
            tc.id = "call_2"
            tc.type = "function"
            tc.function = SimpleNamespace(
                name="file",
                arguments=json.dumps(
                    {"action": "read", "path": str(src_path)},
                    ensure_ascii=False,
                ),
            )
            resp.choices[0].message.tool_calls = [tc]
            resp.choices[0].finish_reason = "tool_calls"
            tool_calls_log.append(("file", tc.function.arguments))
        elif t == 3:
            # Turn 3: diagnostic conclusion
            resp.choices[0].message.content = (
                "The memory_pressure_program process is consuming a large amount of memory "
                "because it allocates system RAM in bytearray chunks, touches every page to "
                "ensure RSS tracks the allocation, and then enters an infinite sleep loop "
                "without ever freeing the allocated buffers. This is a memory retention / "
                "memory leak issue."
            )
        else:
            # Safety net — should not reach turn 4
            resp.choices[0].message.content = "Investigation complete."

        return resp

    return mock_create


def test_agent_diagnoses_memory_pressure_issue():
    """Verify that AIAgent can locate and diagnose a memory-pressure bug."""
    if not MEMORY_PRESSURE_PROGRAM.exists():
        pytest.skip(f"memory_pressure_program.py not found at {MEMORY_PRESSURE_PROGRAM}")

    proc = _start_memory_pressure_program(percent=3, duration=3)
    try:
        tool_calls_log = []
        mock_create = _build_mock_llm_sequence(proc.pid, MEMORY_PRESSURE_PROGRAM, tool_calls_log)

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
            "There is a process using too much memory. "
            "Find out why and tell me the root cause."
        )

        # 1. The agent should have invoked terminal and file tools
        assert len(tool_calls_log) >= 2, f"Expected at least 2 tool calls, got {tool_calls_log}"
        assert tool_calls_log[0][0] == "terminal"
        assert tool_calls_log[1][0] == "file"

        # 2. The final response should mention the memory issue
        assert result is not None
        lower_result = result.lower()
        assert any(
            kw in lower_result
            for kw in ("memory", "free", "泄漏", "未释放", "allocated", "retention")
        ), f"Expected memory-related diagnosis, got: {result[:500]}"

        # 3. The terminal call should reference the running PID
        assert str(proc.pid) in tool_calls_log[0][1]

        # 4. The file call should reference the source file
        assert str(MEMORY_PRESSURE_PROGRAM) in tool_calls_log[1][1]

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    test_agent_diagnoses_memory_pressure_issue()
    print("PASS")
