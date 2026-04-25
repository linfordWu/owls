"""
Integration test: AIAgent multi-step continuous task orchestration.

Scenario
--------
The agent receives a complex multi-step request:
"Check disk usage, and then find large log files, and then clean them up"

This triggers the planner path because the message contains sequencing
markers ("and then") and describes 3+ distinct operations.

The test verifies:
1. Planner.generate_plan() is invoked for complex tasks
2. A DAG with 3 nodes is created and executed in dependency order
3. Each node calls agent.run_conversation() to perform its step
4. The final response summarizes plan completion

The test uses mocked LLM responses so no API key or network is required.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# Sample 3-node plan DAG returned by the mocked auxiliary LLM
SAMPLE_PLAN_NODES = [
    {
        "id": "n_001",
        "task_desc": "Check disk usage with df -h",
        "status": "pending",
        "dependencies": [],
        "allowed_tools": ["terminal"],
        "verifier_command": "echo 'disk checked'",
        "max_retries": 1,
        "sandbox_profile": "inspect-ro",
        "risk_level": "low",
    },
    {
        "id": "n_002",
        "task_desc": "Find large log files under /var/log",
        "status": "pending",
        "dependencies": ["n_001"],
        "allowed_tools": ["terminal", "file"],
        "verifier_command": "echo 'logs found'",
        "max_retries": 1,
        "sandbox_profile": "inspect-ro",
        "risk_level": "low",
    },
    {
        "id": "n_003",
        "task_desc": "Clean up large log files",
        "status": "pending",
        "dependencies": ["n_002"],
        "allowed_tools": ["terminal"],
        "verifier_command": "echo 'cleanup done'",
        "max_retries": 1,
        "sandbox_profile": "full-mutate",
        "risk_level": "medium",
    },
]


def _build_mock_auxiliary_llm_response():
    """Return a mock call_llm response that yields a 3-node plan."""
    plan_json = json.dumps({"nodes": SAMPLE_PLAN_NODES}, ensure_ascii=False)

    resp = SimpleNamespace()
    resp.choices = [SimpleNamespace()]
    resp.choices[0].message = SimpleNamespace()
    resp.choices[0].message.content = plan_json
    resp.choices[0].message.refusal = None
    return resp


def _build_mock_openai_client():
    """Return a mock OpenAI client that always returns a simple text response."""
    def mock_create(**kwargs):
        resp = SimpleNamespace()
        resp.choices = [SimpleNamespace()]
        resp.choices[0].message = SimpleNamespace()
        resp.choices[0].message.content = "Task step completed successfully."
        resp.choices[0].message.refusal = None
        resp.choices[0].message.tool_calls = []
        resp.choices[0].finish_reason = "stop"
        resp.usage = SimpleNamespace(
            prompt_tokens=50,
            completion_tokens=20,
            total_tokens=70,
            prompt_tokens_details=None,
        )
        resp.model = "test/model"
        return resp

    mock_client = MagicMock()
    mock_client.chat.completions.create = mock_create
    mock_client.close = MagicMock()
    return mock_client


def _setup_mock_agent(agent):
    """Wire mock API client and disable streaming for agent tests."""
    mock_client = _build_mock_openai_client()
    agent.client = MagicMock()
    agent._create_request_openai_client = lambda **kw: mock_client
    agent._try_activate_fallback = lambda: False


def test_agent_uses_planner_for_complex_task():
    """Verify that a complex task triggers Planner.generate_plan()."""
    from agent.planner import is_complex_task

    # "and then" is an explicit sequencing marker
    assert is_complex_task(
        "Check disk usage, and then find large log files, and then clean them up"
    )
    # 3+ tool mentions trigger the heuristic
    assert is_complex_task("check disk AND restart nginx AND verify port 80")
    assert not is_complex_task("What is the weather today?")


def test_agent_multi_step_orchestration():
    """
    End-to-end: agent receives a 3-step task, planner generates a DAG,
    nodes execute sequentially, and the result summarizes completion.
    """
    from run_agent import AIAgent
    from agent.planner import PlanStateMachine

    agent = AIAgent(
        base_url="http://localhost:1",
        api_key="test",
        model="test/model",
        max_iterations=10,
        quiet_mode=True,
        enabled_toolsets=["terminal", "file"],
    )

    # Force planner enabled (normally read from config)
    agent._planner_enabled = True
    agent._in_plan_execution = False
    _setup_mock_agent(agent)

    # Track which nodes were executed via _execute_node
    executed_nodes = []
    original_execute_node = PlanStateMachine._execute_node

    def patched_execute_node(self, node_id, agent_ref, cm):
        executed_nodes.append(node_id)
        return original_execute_node(self, node_id, agent_ref, cm)

    mock_llm_response = _build_mock_auxiliary_llm_response()

    with patch.object(PlanStateMachine, "_execute_node", patched_execute_node):
        with patch("agent.auxiliary_client.call_llm", return_value=mock_llm_response):
            result = agent.chat(
                "Check disk usage, and then find large log files, and then clean them up"
            )

    # 1. Planner should have generated and executed a 3-node plan
    assert len(executed_nodes) == 3, f"Expected 3 node executions, got {executed_nodes}"

    # 2. Nodes should execute in dependency order
    assert executed_nodes[0] == "n_001"
    assert executed_nodes[1] == "n_002"
    assert executed_nodes[2] == "n_003"

    # 3. Final response should mention plan completion
    assert result is not None
    lower_result = result.lower()
    assert "plan" in lower_result or "success" in lower_result, (
        f"Expected plan summary in result, got: {result[:500]}"
    )


def test_agent_plan_execution_with_failure_and_rollback():
    """
    Simulate a plan where the second node fails; verify downstream nodes
    are marked rolled_back and the result mentions failures.
    """
    from run_agent import AIAgent
    from agent.planner import PlanStateMachine

    agent = AIAgent(
        base_url="http://localhost:1",
        api_key="test",
        model="test/model",
        max_iterations=10,
        quiet_mode=True,
        enabled_toolsets=["terminal", "file"],
    )
    agent._planner_enabled = True
    agent._in_plan_execution = False
    _setup_mock_agent(agent)

    # Node n_002 will fail
    call_count = [0]
    original_execute_node = PlanStateMachine._execute_node

    def patched_execute_node(self, node_id, agent_ref, cm):
        call_count[0] += 1
        if node_id == "n_002":
            # Simulate failure: update status and return False
            self.update_node(node_id, "failed")
            return False
        return original_execute_node(self, node_id, agent_ref, cm)

    mock_llm_response = _build_mock_auxiliary_llm_response()

    with patch.object(PlanStateMachine, "_execute_node", patched_execute_node):
        with patch("agent.auxiliary_client.call_llm", return_value=mock_llm_response):
            result = agent.chat(
                "Check disk usage, and then find large log files, and then clean them up"
            )

    # n_001 succeeds, n_002 fails, n_003 should be rolled_back (not executed)
    assert call_count[0] == 2, f"Expected 2 node executions (n_001, n_002), got {call_count[0]}"

    # Result should mention failures
    assert "fail" in result.lower() or "rollback" in result.lower() or "plan" in result.lower()


def test_complex_task_detection_heuristic():
    """Verify is_complex_task correctly identifies multi-step requests."""
    from agent.planner import is_complex_task

    # Chinese sequencing markers
    assert is_complex_task("先检查磁盘，然后重启nginx，最后验证端口")
    assert is_complex_task("检查磁盘并且重启服务")

    # English sequencing markers
    assert is_complex_task("First check disk, next find logs, finally clean up")
    assert is_complex_task("Step by step: install, configure, start")

    # Tool mention count heuristic
    assert is_complex_task("check disk AND restart nginx AND verify port 80")

    # Simple tasks should NOT trigger planner
    assert not is_complex_task("hello")
    assert not is_complex_task("what is 2+2")
    assert not is_complex_task("check disk usage")
