"""Tests for tools.interceptor_chain."""

import tempfile
from pathlib import Path

import pytest

from tools.interceptor_chain import (
    ApprovalInterceptor,
    FrozenTaskStore,
    InterceptorChain,
    PolicyInterceptor,
    ValidationInterceptor,
)
from agent.interfaces import ToolContext


class TestFrozenTaskStore:
    def test_add_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FrozenTaskStore(db_path=Path(tmp) / "frozen.db")
            row_id = store.add("sess_1", "task_1", "dangerous command", "use rm -i")
            unresolved = store.list_unresolved()
            assert len(unresolved) == 1
            assert unresolved[0]["reason"] == "dangerous command"

    def test_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FrozenTaskStore(db_path=Path(tmp) / "frozen.db")
            row_id = store.add("sess_1", "task_1", "dangerous command", None)
            assert store.resolve(row_id)
            assert not store.list_unresolved()

    def test_list_by_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FrozenTaskStore(db_path=Path(tmp) / "frozen.db")
            store.add("sess_a", None, "r1", None)
            store.add("sess_b", None, "r2", None)
            assert len(store.list_unresolved("sess_a")) == 1


class TestApprovalInterceptor:
    def test_non_terminal_proceeds(self):
        interceptor = ApprovalInterceptor()
        ctx: ToolContext = {"tool_name": "web_search", "command": ""}
        action = interceptor.intercept(ctx)
        assert action["type"] == "proceed"

    def test_terminal_with_dangerous_command(self):
        import os
        os.environ["OWLS_INTERACTIVE"] = "1"
        try:
            interceptor = ApprovalInterceptor()
            ctx: ToolContext = {"tool_name": "terminal", "command": "rm -rf /"}
            action = interceptor.intercept(ctx)
            assert action["type"] in ("freeze", "rollback")
        finally:
            del os.environ["OWLS_INTERACTIVE"]

    def test_terminal_safe_command(self):
        interceptor = ApprovalInterceptor()
        ctx: ToolContext = {"tool_name": "terminal", "command": "ls -la"}
        action = interceptor.intercept(ctx)
        assert action["type"] == "proceed"


class TestPolicyInterceptor:
    def test_inspect_ro_with_write_blocked(self):
        interceptor = PolicyInterceptor()
        ctx: ToolContext = {
            "tool_name": "terminal",
            "command": "echo x > /tmp/test",
            "sandbox_profile": "inspect-ro",
        }
        action = interceptor.intercept(ctx)
        assert action["type"] == "rollback"

    def test_inspect_ro_readonly_ok(self):
        interceptor = PolicyInterceptor()
        ctx: ToolContext = {
            "tool_name": "terminal",
            "command": "cat /etc/passwd",
            "sandbox_profile": "inspect-ro",
        }
        action = interceptor.intercept(ctx)
        assert action["type"] == "proceed"

    def test_non_terminal_proceeds(self):
        interceptor = PolicyInterceptor()
        ctx: ToolContext = {"tool_name": "web_search", "sandbox_profile": "inspect-ro"}
        action = interceptor.intercept(ctx)
        assert action["type"] == "proceed"


class TestValidationInterceptor:
    def test_no_task_id_proceeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            interceptor = ValidationInterceptor(state_db=Path(tmp) / "state.db")
            ctx: ToolContext = {"tool_name": "terminal"}
            action = interceptor.intercept(ctx)
            assert action["type"] == "proceed"

    def test_failed_verifier_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            interceptor = ValidationInterceptor(state_db=Path(tmp) / "state.db")
            interceptor.set_verifier_result("node_1", "sess_1", False)
            ctx: ToolContext = {"tool_name": "terminal", "task_id": "node_1"}
            action = interceptor.intercept(ctx)
            assert action["type"] == "rollback"

    def test_passed_verifier_proceeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            interceptor = ValidationInterceptor(state_db=Path(tmp) / "state.db")
            interceptor.set_verifier_result("node_1", "sess_1", True)
            ctx: ToolContext = {"tool_name": "terminal", "task_id": "node_1"}
            action = interceptor.intercept(ctx)
            assert action["type"] == "proceed"


class TestInterceptorChain:
    def test_empty_chain_proceeds(self):
        chain = InterceptorChain([])
        action = chain.intercept({"tool_name": "terminal"})
        assert action["type"] == "proceed"

    def test_first_block_short_circuits(self):
        class Blocker:
            def intercept(self, ctx):
                return {"type": "freeze", "reason": "blocked"}

        class NeverCalled:
            def intercept(self, ctx):
                return {"type": "proceed"}

        chain = InterceptorChain([Blocker(), NeverCalled()])
        action = chain.intercept({"tool_name": "terminal"})
        assert action["type"] == "freeze"
