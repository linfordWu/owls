"""Experience Store — record and retrieve past task executions.

Simple keyword-based matching (no vector DB required).
Experiences are appended to a JSONL file for durability.

Usage:
    from agent.experience_store import ExperienceStore
    store = ExperienceStore()
    store.record(session_id="sess_1", input_msg="...", plan_summary="...", ...)
    similar = store.get_similar_experiences("disk full error")
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from agent.interfaces import Experience
from owls_constants import get_owls_home

logger = logging.getLogger(__name__)


class ExperienceStore:
    """Append-only store of task experiences with keyword search."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or get_owls_home() / "experience"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.experiences_file = self.data_dir / "experiences.jsonl"

    def record(
        self,
        session_id: str,
        input_msg: str,
        plan_summary: str,
        tool_sequence: List[str],
        execution_time_ms: int,
        failure_reason: Optional[str] = None,
        user_feedback: Optional[str] = None,
        success: bool = True,
    ) -> None:
        """Append one experience to the store."""
        experience: Experience = {
            "experience_id": str(uuid.uuid4()),
            "session_id": session_id,
            "input_msg": input_msg,
            "plan_summary": plan_summary,
            "tool_sequence": tool_sequence,
            "execution_time_ms": execution_time_ms,
            "failure_reason": failure_reason,
            "user_feedback": user_feedback,
            "success": success,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        line = json.dumps(experience, ensure_ascii=False) + "\n"
        with open(self.experiences_file, "a", encoding="utf-8") as f:
            f.write(line)

        logger.debug("Recorded experience %s (success=%s)", experience["experience_id"], success)

    def get_similar_experiences(self, query: str, limit: int = 5) -> List[Experience]:
        """Simple keyword matching against stored experiences.

        Scores by number of overlapping keywords between query and:
        - input_msg
        - plan_summary
        - failure_reason
        """
        query_words = set(self._tokenize(query))
        if not query_words:
            return []

        scores: List[tuple[int, Experience]] = []
        for exp in self._read_all():
            text = " ".join(filter(None, [
                exp.get("input_msg", ""),
                exp.get("plan_summary", ""),
                exp.get("failure_reason", ""),
                " ".join(exp.get("tool_sequence", [])),
            ])).lower()
            text_words = set(self._tokenize(text))
            score = len(query_words & text_words)
            if score > 0:
                scores.append((score, exp))

        scores.sort(key=lambda x: (-x[0], x[1].get("timestamp", "")))
        return [exp for _, exp in scores[:limit]]

    def list_all(self) -> List[Experience]:
        """Return all experiences (oldest first)."""
        return self._read_all()

    def count(self) -> int:
        """Total number of recorded experiences."""
        return len(self._read_all())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _read_all(self) -> List[Experience]:
        if not self.experiences_file.exists():
            return []
        results = []
        with open(self.experiences_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return results

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """Simple tokenization: lowercase, split on non-alphanumeric, filter short words."""
        import re
        return [w for w in re.findall(r"[a-z0-9一-鿿]+", text.lower()) if len(w) > 2]
