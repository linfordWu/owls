"""Tests for agent.planner."""

import pytest

from agent.planner import is_complex_task, Planner, PlanStateMachine


class TestIsComplexTask:
    def test_sequencing_words_trigger(self):
        assert is_complex_task("First check disk, then restart nginx") is True
        assert is_complex_task("先检查磁盘，再重启nginx") is True
        assert is_complex_task("并且安装依赖") is True

    def test_simple_task_not_complex(self):
        assert is_complex_task("What is the weather?") is False
        assert is_complex_task("List files in /tmp") is False

    def test_tool_mentions_trigger(self):
        assert is_complex_task("Check disk, restart service, verify port, install package") is True

    def test_step_by_step_trigger(self):
        assert is_complex_task("Give me a step by step guide") is True


class TestPlanStateMachine:
    def test_empty_plan(self):
        psm = PlanStateMachine("test_1", [])
        assert psm.plan_id == "test_1"
        assert psm.get_ready_nodes() == []

    def test_single_node_ready(self):
        node = {
            "id": "n1",
            "task_desc": "test",
            "status": "pending",
            "dependencies": [],
            "allowed_tools": ["terminal"],
            "verifier_command": None,
            "max_retries": 1,
            "sandbox_profile": "full-mutate",
            "risk_level": "low",
        }
        psm = PlanStateMachine("test_2", [node])
        ready = psm.get_ready_nodes()
        assert ready == ["n1"]

    def test_dependency_ordering(self):
        nodes = [
            {"id": "n2", "task_desc": "b", "status": "pending", "dependencies": ["n1"],
             "allowed_tools": [], "verifier_command": None, "max_retries": 1,
             "sandbox_profile": "full-mutate", "risk_level": "low"},
            {"id": "n1", "task_desc": "a", "status": "pending", "dependencies": [],
             "allowed_tools": [], "verifier_command": None, "max_retries": 1,
             "sandbox_profile": "full-mutate", "risk_level": "low"},
        ]
        psm = PlanStateMachine("test_3", nodes)
        ready = psm.get_ready_nodes()
        assert ready == ["n1"]

    def test_update_node(self):
        node = {
            "id": "n1", "task_desc": "test", "status": "pending", "dependencies": [],
            "allowed_tools": [], "verifier_command": None, "max_retries": 1,
            "sandbox_profile": "full-mutate", "risk_level": "low",
        }
        psm = PlanStateMachine("test_4", [node])
        psm.update_node("n1", "success")
        assert psm.nodes["n1"]["status"] == "success"

    def test_retry_and_skip(self):
        node = {
            "id": "n1", "task_desc": "test", "status": "failed", "dependencies": [],
            "allowed_tools": [], "verifier_command": None, "max_retries": 1,
            "sandbox_profile": "full-mutate", "risk_level": "low",
        }
        psm = PlanStateMachine("test_5", [node])
        assert psm.retry_node("n1")
        assert psm.nodes["n1"]["status"] == "pending"
        assert psm.skip_node("n1")
        assert psm.nodes["n1"]["status"] == "success"

    def test_serialize(self):
        node = {
            "id": "n1", "task_desc": "test", "status": "pending", "dependencies": [],
            "allowed_tools": [], "verifier_command": None, "max_retries": 1,
            "sandbox_profile": "full-mutate", "risk_level": "low",
        }
        psm = PlanStateMachine("test_6", [node])
        serialized = psm.serialize()
        assert serialized["plan_id"] == "test_6"
        assert len(serialized["nodes"]) == 1
