"""Tests for tools.audit_logger."""

import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from tools.audit_logger import AuditLogger, get_audit_logger


class TestAuditLogger:
    def test_log_event_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            logger = AuditLogger(log_dir=log_dir)
            logger.log_event({
                "event_type": "command_execution",
                "session_id": "sess_1",
                "command": "ls -la",
                "risk_level": "low",
                "description": "test",
            })
            files = list(log_dir.glob("*.jsonl"))
            assert len(files) == 1

    def test_log_event_has_required_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = AuditLogger(log_dir=Path(tmp))
            logger.log_event({"event_type": "test", "description": "d"})
            files = list(Path(tmp).glob("*.jsonl"))
            line = json.loads(files[0].read_text().strip())
            assert "event_id" in line
            assert "timestamp" in line
            assert "hash" in line
            assert "prev_hash" in line
            assert line["event_type"] == "test"

    def test_chain_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = AuditLogger(log_dir=Path(tmp))
            logger.log_event({"event_type": "a", "description": "1"})
            logger.log_event({"event_type": "b", "description": "2"})
            assert logger.verify_chain()

    def test_query_by_event_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = AuditLogger(log_dir=Path(tmp))
            logger.log_event({"event_type": "tool_call", "description": "t1"})
            logger.log_event({"event_type": "tool_result", "description": "t2"})
            results = logger.query(event_type="tool_call")
            assert len(results) == 1
            assert results[0]["event_type"] == "tool_call"

    def test_query_by_session_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = AuditLogger(log_dir=Path(tmp))
            logger.log_event({"event_type": "test", "session_id": "s1", "description": "d"})
            logger.log_event({"event_type": "test", "session_id": "s2", "description": "d"})
            results = logger.query(session_id="s1")
            assert len(results) == 1

    def test_query_by_date_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = AuditLogger(log_dir=Path(tmp))
            now = datetime.now(timezone.utc)
            logger.log_event({"event_type": "test", "description": "d"})
            results = logger.query(start=now - timedelta(hours=1), end=now + timedelta(hours=1))
            assert len(results) == 1
            results = logger.query(start=now + timedelta(hours=1))
            assert len(results) == 0

    def test_singleton(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            a = get_audit_logger(log_dir=log_dir)
            b = get_audit_logger(log_dir=log_dir)
            assert a is b
