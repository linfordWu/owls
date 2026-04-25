"""Tests for agent.experience_store."""

import tempfile
from pathlib import Path

import pytest

from agent.experience_store import ExperienceStore


class TestExperienceStore:
    def test_record_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceStore(data_dir=Path(tmp))
            store.record(
                session_id="sess_1",
                input_msg="Check disk space",
                plan_summary="df -h",
                tool_sequence=["terminal"],
                execution_time_ms=1200,
                success=True,
            )
            all_exp = store.list_all()
            assert len(all_exp) == 1
            assert all_exp[0]["input_msg"] == "Check disk space"

    def test_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceStore(data_dir=Path(tmp))
            assert store.count() == 0
            store.record(
                session_id="s", input_msg="m", plan_summary="p",
                tool_sequence=[], execution_time_ms=0, success=True,
            )
            assert store.count() == 1

    def test_similar_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceStore(data_dir=Path(tmp))
            store.record(
                session_id="s1", input_msg="disk full error on /var",
                plan_summary="check disk", tool_sequence=["terminal"],
                execution_time_ms=100, success=False, failure_reason="disk full",
            )
            store.record(
                session_id="s2", input_msg="how is the weather",
                plan_summary="weather", tool_sequence=["web_search"],
                execution_time_ms=50, success=True,
            )
            results = store.get_similar_experiences("disk space problem")
            assert len(results) >= 1
            assert "disk" in results[0]["input_msg"].lower()

    def test_similar_search_no_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceStore(data_dir=Path(tmp))
            store.record(
                session_id="s1", input_msg="hello world",
                plan_summary="greet", tool_sequence=[],
                execution_time_ms=10, success=True,
            )
            results = store.get_similar_experiences("quantum physics")
            assert len(results) == 0
