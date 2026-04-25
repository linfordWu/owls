"""Tamper-evident audit logger for OWLS.

All security-relevant events are written to JSONL files with SHA-256 chain
hashing so that offline forensic analysis can detect tampering.

Usage:
    from tools.audit_logger import get_audit_logger, AuditLogger
    logger = get_audit_logger()
    logger.log_event({
        "event_type": "command_execution",
        "session_id": "sess_abc",
        "command": "ls -la",
        "risk_level": "low",
        "description": "Listed files in /tmp",
    })
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from owls_constants import get_owls_home

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_audit_logger_instance: Optional["AuditLogger"] = None
_audit_logger_lock = threading.Lock()


def get_audit_logger(log_dir: Optional[Path] = None) -> "AuditLogger":
    """Return the process-wide AuditLogger singleton."""
    global _audit_logger_instance
    if _audit_logger_instance is None:
        with _audit_logger_lock:
            if _audit_logger_instance is None:
                _audit_logger_instance = AuditLogger(log_dir=log_dir)
    return _audit_logger_instance


# ---------------------------------------------------------------------------
# AuditLogger
# ---------------------------------------------------------------------------

class AuditLogger:
    """Append-only, chain-hashed audit log.

    Each day's events go into a separate ``audit_events.YYYY-MM-DD.jsonl``
    file under *log_dir*.  The ``prev_hash`` field links every line to the
    previous one, making undetected deletion impossible without regenerating
    every subsequent hash.
    """

    def __init__(self, log_dir: Optional[Path] = None):
        self.log_dir = log_dir or get_owls_home() / "audit"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # In-memory cache of the last hash per daily file for fast appends.
        self._last_hash_cache: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_event(self, event: Dict[str, Any]) -> None:
        """Write a single audit event to today's JSONL file.

        Missing fields are auto-populated:
        - event_id    → UUIDv4
        - timestamp   → ISO-8601 UTC now
        - prev_hash   → SHA-256 of the previous line (or "0" for first line)
        - hash        → SHA-256 of this event's canonical JSON
        """
        event = dict(event)  # shallow copy — don't mutate caller's dict

        # Auto-fill required fields
        if "event_id" not in event:
            event["event_id"] = str(uuid.uuid4())
        if "timestamp" not in event:
            event["timestamp"] = datetime.now(timezone.utc).isoformat()
        if "risk_level" not in event:
            event["risk_level"] = "low"

        file_path = self._current_file_path()
        prev_hash = self._get_prev_hash(file_path)
        event["prev_hash"] = prev_hash

        # Canonical JSON for hashing (sorted keys, no extra whitespace)
        canonical = json.dumps(event, sort_keys=True, ensure_ascii=False)
        event["hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        line = json.dumps(event, ensure_ascii=False) + "\n"

        with self._lock:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(line)
            self._last_hash_cache[str(file_path)] = event["hash"]

        logger.debug("Audit event %s written to %s", event["event_id"], file_path.name)

    def query(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        event_type: Optional[str] = None,
        session_id: Optional[str] = None,
        risk_level: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Query audit events with optional filters.

        Results are returned in chronological order (oldest first).
        """
        results: List[Dict[str, Any]] = []

        # Determine which daily files to scan
        files = sorted(self.log_dir.glob("audit_events.*.jsonl"))
        if start:
            start_str = start.strftime("%Y-%m-%d")
            files = [f for f in files if f.stem.split(".")[-1] >= start_str]
        if end:
            end_str = end.strftime("%Y-%m-%d")
            files = [f for f in files if f.stem.split(".")[-1] <= end_str]

        for file_path in files:
            for line in self._read_lines(file_path):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts_str = record.get("timestamp", "")
                if start or end:
                    try:
                        # Parse ISO-8601; truncate to datetime for comparison
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if start and ts < start:
                            continue
                        if end and ts > end:
                            continue
                    except ValueError:
                        pass

                if event_type and record.get("event_type") != event_type:
                    continue
                if session_id and record.get("session_id") != session_id:
                    continue
                if risk_level and record.get("risk_level") != risk_level:
                    continue

                results.append(record)

        # Already ordered by file name (date) then line order
        return results

    def verify_chain(self, file_path: Optional[Path] = None) -> bool:
        """Verify the hash chain integrity of a log file (or all files).

        Returns True if every line's prev_hash matches the previous line's
        hash.  Returns False on any mismatch.
        """
        files = [file_path] if file_path else sorted(self.log_dir.glob("audit_events.*.jsonl"))
        for fp in files:
            if fp is None:
                continue
            prev_hash = "0"
            for line in self._read_lines(fp):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    return False
                if record.get("prev_hash") != prev_hash:
                    logger.error("Chain break in %s at event %s", fp.name, record.get("event_id"))
                    return False
                # Recompute hash
                rec_copy = {k: v for k, v in record.items() if k != "hash"}
                canonical = json.dumps(rec_copy, sort_keys=True, ensure_ascii=False)
                expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                if record.get("hash") != expected:
                    logger.error("Hash mismatch in %s at event %s", fp.name, record.get("event_id"))
                    return False
                prev_hash = record["hash"]
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _current_file_path(self) -> Path:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.log_dir / f"audit_events.{today}.jsonl"

    def _get_prev_hash(self, file_path: Path) -> str:
        cached = self._last_hash_cache.get(str(file_path))
        if cached:
            return cached
        # Read last line
        last_line = self._read_last_line(file_path)
        if last_line:
            try:
                record = json.loads(last_line)
                h = record.get("hash", "0")
                self._last_hash_cache[str(file_path)] = h
                return h
            except json.JSONDecodeError:
                pass
        return "0"

    @staticmethod
    def _read_lines(file_path: Path):
        if not file_path.exists():
            return
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield line

    @staticmethod
    def _read_last_line(file_path: Path) -> Optional[str]:
        if not file_path.exists():
            return None
        try:
            with open(file_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                pos = f.tell()
                if pos == 0:
                    return None
                # Walk backward to find newline
                buf = b""
                while pos > 0:
                    pos -= 1
                    f.seek(pos)
                    ch = f.read(1)
                    if ch == b"\n" and buf:
                        break
                    buf = ch + buf
                return buf.decode("utf-8", errors="replace").strip() or None
        except OSError:
            return None
