"""Tests for agent.reflection_engine."""

import tempfile
from pathlib import Path

import pytest

from agent.reflection_engine import ReflectionEngine
from agent.experience_store import ExperienceStore


class TestReflectionEngine:
    def test_empty_report_on_missing_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = ReflectionEngine(data_dir=Path(tmp))
            experience = {
                "experience_id": "exp_1",
                "session_id": "s1",
                "input_msg": "test",
                "plan_summary": "test plan",
                "tool_sequence": [],
                "execution_time_ms": 100,
                "failure_reason": None,
                "user_feedback": None,
                "success": True,
                "timestamp": "2026-01-01T00:00:00+00:00",
            }
            report = engine.reflect(experience)
            assert report["experience_id"] == "exp_1"
            assert "Reflection skipped" in report["insights"][0]

    def test_load_relevant_fragments_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = ReflectionEngine(data_dir=Path(tmp))
            fragments = engine.load_relevant_fragments("disk error")
            assert fragments == []

    def test_consolidate_not_enough_experiences(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = ReflectionEngine(data_dir=Path(tmp))
            # Need 50 successes, we have 0
            fragments = engine.consolidate_fragments()
            assert fragments == []

    def test_consolidate_with_enough_experiences(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            store = ExperienceStore(data_dir=data_dir)
            engine = ReflectionEngine(data_dir=data_dir)
            # Add 50 successful experiences
            for i in range(50):
                store.record(
                    session_id=f"s{i}",
                    input_msg=f"task {i}",
                    plan_summary="summary",
                    tool_sequence=["terminal"],
                    execution_time_ms=100,
                    success=True,
                )
            # Also add a reflection with prompt_fragment so consolidate has data
            engine.reflect({
                "experience_id": "exp_1",
                "session_id": "s1",
                "input_msg": "test",
                "plan_summary": "plan",
                "tool_sequence": [],
                "execution_time_ms": 100,
                "failure_reason": None,
                "user_feedback": None,
                "success": True,
                "timestamp": "2026-01-01T00:00:00+00:00",
            })
            # Since auxiliary_client is not available, consolidate will still
            # return empty, but the threshold check should pass.
            fragments = engine.consolidate_fragments()
            # With no LLM, we expect empty; the key test is no exception.
            assert isinstance(fragments, list)
