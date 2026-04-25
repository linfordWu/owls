"""
Integration test: AI Hackathon 2026 — Audit trail with chain hashing.

Scenario
--------
Verify that the real ``tools/audit_logger.py``:

1. Writes security-relevant events to JSONL files.
2. Computes SHA-256 chain hashes so each event links to the previous one.
3. Supports querying by risk level, event type, and session ID.
4. Provides ``verify_chain()`` for offline forensic integrity checks.
"""

import hashlib
import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tools.audit_logger import AuditLogger, get_audit_logger


def test_audit_logger_chain_hashing():
    """Verify SHA-256 chain hashing across sequential events."""
    with tempfile.TemporaryDirectory() as td:
        log_dir = Path(td)
        logger = AuditLogger(log_dir=log_dir)

        # Write two events
        logger.log_event({
            "event_type": "tool_call",
            "session_id": "sess_abc",
            "command": "ls -la",
            "risk_level": "low",
        })
        logger.log_event({
            "event_type": "command_execution",
            "session_id": "sess_abc",
            "command": "rm -rf /tmp/old",
            "risk_level": "high",
        })

        # Read back the daily file
        files = sorted(log_dir.glob("audit_events.*.jsonl"))
        assert len(files) == 1

        lines = [json.loads(line) for line in files[0].read_text(encoding="utf-8").strip().split("\n")]
        assert len(lines) == 2

        ev1, ev2 = lines

        # Event 1: prev_hash should be "0" (genesis)
        assert ev1["prev_hash"] == "0"
        # Event 2: prev_hash should equal event 1's hash
        assert ev2["prev_hash"] == ev1["hash"]

        # Recompute hash for event 1 manually
        ev1_copy = {k: v for k, v in ev1.items() if k != "hash"}
        canonical1 = json.dumps(ev1_copy, sort_keys=True, ensure_ascii=False)
        expected_hash1 = hashlib.sha256(canonical1.encode("utf-8")).hexdigest()
        assert ev1["hash"] == expected_hash1

        # Recompute hash for event 2 manually
        ev2_copy = {k: v for k, v in ev2.items() if k != "hash"}
        canonical2 = json.dumps(ev2_copy, sort_keys=True, ensure_ascii=False)
        expected_hash2 = hashlib.sha256(canonical2.encode("utf-8")).hexdigest()
        assert ev2["hash"] == expected_hash2


def test_audit_logger_query_filters():
    """Verify query by risk_level, event_type, and session_id."""
    with tempfile.TemporaryDirectory() as td:
        log_dir = Path(td)
        logger = AuditLogger(log_dir=log_dir)

        logger.log_event({
            "event_type": "tool_call",
            "session_id": "sess_alpha",
            "command": "ls",
            "risk_level": "low",
        })
        logger.log_event({
            "event_type": "approval_decision",
            "session_id": "sess_alpha",
            "command": "chmod 777 /tmp",
            "risk_level": "high",
            "approved": False,
        })
        logger.log_event({
            "event_type": "tool_call",
            "session_id": "sess_beta",
            "command": "cat file.txt",
            "risk_level": "low",
        })

        # Query all
        all_events = logger.query()
        assert len(all_events) == 3

        # Query by risk_level
        high_risk = logger.query(risk_level="high")
        assert len(high_risk) == 1
        assert high_risk[0]["event_type"] == "approval_decision"

        # Query by event_type
        tool_calls = logger.query(event_type="tool_call")
        assert len(tool_calls) == 2

        # Query by session_id
        alpha_events = logger.query(session_id="sess_alpha")
        assert len(alpha_events) == 2

        # Combined filter
        alpha_high = logger.query(session_id="sess_alpha", risk_level="high")
        assert len(alpha_high) == 1
        assert alpha_high[0]["approved"] is False


def test_audit_logger_verify_chain():
    """Verify ``verify_chain()`` returns True for intact logs and False after tampering."""
    with tempfile.TemporaryDirectory() as td:
        log_dir = Path(td)
        logger = AuditLogger(log_dir=log_dir)

        logger.log_event({
            "event_type": "command_execution",
            "session_id": "sess_123",
            "command": "rm -rf /tmp/test",
            "risk_level": "high",
        })
        logger.log_event({
            "event_type": "command_execution",
            "session_id": "sess_123",
            "command": "ls -la",
            "risk_level": "low",
        })

        # Should pass before tampering
        assert logger.verify_chain() is True

        # Tamper with the file: modify the first event's command field
        files = sorted(log_dir.glob("audit_events.*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text(encoding="utf-8").strip().split("\n")
        ev1 = json.loads(lines[0])
        ev1["command"] = "echo tampered"
        lines[0] = json.dumps(ev1, ensure_ascii=False)
        files[0].write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Should fail after tampering
        assert logger.verify_chain() is False


def test_audit_logger_singleton():
    """Verify ``get_audit_logger()`` returns a singleton and reuses the same instance."""
    with tempfile.TemporaryDirectory() as td:
        log_dir = Path(td)
        # Reset singleton for test isolation
        import tools.audit_logger as _al_mod
        _al_mod._audit_logger_instance = None

        logger1 = get_audit_logger(log_dir=log_dir)
        logger2 = get_audit_logger(log_dir=log_dir)
        assert logger1 is logger2

        # Write through one reference, read through the other
        logger1.log_event({
            "event_type": "tool_call",
            "session_id": "sess_singleton",
            "command": "pwd",
            "risk_level": "low",
        })
        results = logger2.query(session_id="sess_singleton")
        assert len(results) == 1
        assert results[0]["command"] == "pwd"


def test_audit_logger_auto_populated_fields():
    """Verify event_id and timestamp are auto-populated when missing."""
    with tempfile.TemporaryDirectory() as td:
        log_dir = Path(td)
        logger = AuditLogger(log_dir=log_dir)

        logger.log_event({
            "event_type": "test",
            "session_id": "sess_auto",
            "command": "echo hello",
        })

        results = logger.query(session_id="sess_auto")
        assert len(results) == 1
        ev = results[0]

        assert "event_id" in ev
        assert len(ev["event_id"]) > 0
        assert "timestamp" in ev
        # Should be parseable ISO-8601
        ts = datetime.fromisoformat(ev["timestamp"].replace("Z", "+00:00"))
        assert ts.year >= 2025
        # Default risk_level when omitted
        assert ev["risk_level"] == "low"


if __name__ == "__main__":
    test_audit_logger_chain_hashing()
    test_audit_logger_query_filters()
    test_audit_logger_verify_chain()
    test_audit_logger_singleton()
    test_audit_logger_auto_populated_fields()
    print("PASS")
