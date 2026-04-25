"""Reflection Engine — self-improvement through LLM-generated insights.

After each task, the engine reflects on what went well, what went wrong,
and how to improve.  Successful experiences are periodically consolidated
into prompt fragments that improve future system prompts.

Usage:
    from agent.reflection_engine import ReflectionEngine
    engine = ReflectionEngine()
    report = engine.reflect(experience)
    fragments = engine.consolidate_fragments()
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from agent.interfaces import Experience, PromptFragment, ReflectionReport
from agent.experience_store import ExperienceStore
from owls_constants import get_owls_home

logger = logging.getLogger(__name__)


class ReflectionEngine:
    """Generates reflections from experiences and consolidates them into
    reusable prompt fragments."""

    CONSOLIDATION_THRESHOLD = 50  # experiences needed before consolidation

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or get_owls_home() / "experience"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.reflections_file = self.data_dir / "reflections.jsonl"
        self.fragments_dir = self.data_dir / "prompt_fragments"
        self.fragments_dir.mkdir(exist_ok=True)

    def reflect(self, experience: Experience) -> ReflectionReport:
        """Generate a reflection for a single experience using the auxiliary LLM."""
        try:
            from agent.auxiliary_client import call_llm
            prompt = self._build_reflection_prompt(experience)
            response = call_llm(
                task="reflection",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1000,
            )
            content = response.choices[0].message.content or ""
        except (ImportError, RuntimeError) as e:
            logger.warning("auxiliary_client not available — skipping reflection: %s", e)
            return self._empty_report(experience["experience_id"])

        # Parse reflection
        insights = []
        suggested_improvements = []
        prompt_fragment = None

        for line in content.splitlines():
            line = line.strip()
            if line.lower().startswith("insight:"):
                insights.append(line.split(":", 1)[1].strip())
            elif line.lower().startswith("improvement:"):
                suggested_improvements.append(line.split(":", 1)[1].strip())
            elif line.lower().startswith("fragment:"):
                prompt_fragment = line.split(":", 1)[1].strip()

        report: ReflectionReport = {
            "reflection_id": str(uuid.uuid4()),
            "experience_id": experience["experience_id"],
            "insights": insights or ["No specific insights generated."],
            "suggested_improvements": suggested_improvements or ["No improvements suggested."],
            "prompt_fragment": prompt_fragment,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Save to disk
        line = json.dumps(report, ensure_ascii=False) + "\n"
        with open(self.reflections_file, "a", encoding="utf-8") as f:
            f.write(line)

        logger.debug("Reflection %s saved", report["reflection_id"])
        return report

    def consolidate_fragments(self) -> List[PromptFragment]:
        """Every N successful experiences, consolidate reflections into
        reusable prompt template fragments."""
        store = ExperienceStore(data_dir=self.data_dir)
        successful_count = sum(1 for e in store.list_all() if e.get("success"))

        if successful_count < self.CONSOLIDATION_THRESHOLD:
            logger.debug("Only %d successes — consolidation needs %d", successful_count, self.CONSOLIDATION_THRESHOLD)
            return []

        # Read all reflections
        reflections = self._read_reflections()
        if len(reflections) < 10:
            return []

        try:
            from agent.auxiliary_client import call_llm
            # Group successful reflections
            fragments_text = "\n".join(
                f"- {r.get('prompt_fragment', '')}" for r in reflections if r.get("prompt_fragment")
            )

            prompt = f"""You are distilling agent self-reflections into reusable prompt instructions.

Here are {len(reflections)} reflections from successful task executions:

{fragments_text}

Create 1-3 concise prompt template fragments that capture the most valuable
lessons. Each fragment should be a standalone instruction that could be
prepended to a system prompt to improve future task execution.

Format: one fragment per paragraph, no bullet points, no numbering.
"""

            response = call_llm(
                task="reflection",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1500,
            )
            content = response.choices[0].message.content or ""
        except (ImportError, RuntimeError) as e:
            logger.warning("auxiliary_client not available — skipping consolidation: %s", e)
            return []

        fragments: List[PromptFragment] = []
        for para in content.split("\n\n"):
            para = para.strip()
            if not para or len(para) < 20:
                continue
            h = hashlib.sha256(para.encode("utf-8")).hexdigest()[:16]
            fragment: PromptFragment = {
                "fragment_id": f"frag_{h}",
                "hash": h,
                "content": para,
                "source_experience_ids": [r["experience_id"] for r in reflections],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            fragments.append(fragment)
            # Write to disk
            frag_path = self.fragments_dir / f"{h}.txt"
            frag_path.write_text(para, encoding="utf-8")

        logger.info("Consolidated %d prompt fragments from %d reflections", len(fragments), len(reflections))
        return fragments

    def load_relevant_fragments(self, query: str, limit: int = 3) -> List[str]:
        """Load prompt fragments relevant to *query* (keyword match)."""
        if not self.fragments_dir.exists():
            return []

        query_lower = query.lower()
        scored = []
        for frag_path in self.fragments_dir.glob("*.txt"):
            content = frag_path.read_text(encoding="utf-8")
            score = sum(1 for word in query_lower.split() if word in content.lower() and len(word) > 3)
            if score > 0:
                scored.append((score, content))

        scored.sort(key=lambda x: -x[0])
        return [content for _, content in scored[:limit]]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_reflection_prompt(self, experience: Experience) -> str:
        status = "SUCCESS" if experience.get("success") else "FAILURE"
        feedback = experience.get("user_feedback") or "None provided"
        failure = experience.get("failure_reason") or "None"
        tools = ", ".join(experience.get("tool_sequence", []))

        return f"""Reflect on this agent task execution:

Status: {status}
Input: {experience.get('input_msg', '')}
Plan: {experience.get('plan_summary', '')}
Tools used: {tools}
Execution time: {experience.get('execution_time_ms', 0)}ms
Failure reason: {failure}
User feedback: {feedback}

Provide:
1. Insight: What went well or what went wrong?
2. Improvement: How could this be done better next time?
3. Fragment: A concise instruction that could be added to the system prompt to prevent this issue or improve similar tasks in the future.

Format each line as:
Insight: ...
Improvement: ...
Fragment: ..."""

    def _empty_report(self, experience_id: str) -> ReflectionReport:
        return {
            "reflection_id": str(uuid.uuid4()),
            "experience_id": experience_id,
            "insights": ["Reflection skipped — auxiliary LLM unavailable"],
            "suggested_improvements": [],
            "prompt_fragment": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _read_reflections(self) -> List[ReflectionReport]:
        if not self.reflections_file.exists():
            return []
        results = []
        with open(self.reflections_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return results
