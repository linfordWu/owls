"""Tests for the Nous-OWLS-3/4 non-agentic warning detector.

Prior to this check, the warning fired on any model whose name contained
``"owls"`` anywhere (case-insensitive). That false-positived on unrelated
local Modelfiles such as ``owls-brain:qwen3-14b-ctx16k`` — a tool-capable
Qwen3 wrapper that happens to live under the "owls" tag namespace.

``is_nous_owls_non_agentic`` should only match the actual Nous Research
OWLS-3 / OWLS-4 chat family.
"""

from __future__ import annotations

import pytest

from owls_cli.model_switch import (
    _OWLS_MODEL_WARNING,
    _check_owls_model_warning,
    is_nous_owls_non_agentic,
)


@pytest.mark.parametrize(
    "model_name",
    [
        "NousResearch/OWLS-3-Llama-3.1-70B",
        "NousResearch/OWLS-3-Llama-3.1-405B",
        "owls-3",
        "OWLS-3",
        "owls-4",
        "owls-4-405b",
        "owls_4_70b",
        "openrouter/ospilot3:70b",
        "openrouter/nousresearch/owls-4-405b",
        "NousResearch/OWLS3",
        "owls-3.1",
    ],
)
def test_matches_real_nous_owls_chat_models(model_name: str) -> None:
    assert is_nous_owls_non_agentic(model_name), (
        f"expected {model_name!r} to be flagged as Nous OWLS 3/4"
    )
    assert _check_owls_model_warning(model_name) == _OWLS_MODEL_WARNING


@pytest.mark.parametrize(
    "model_name",
    [
        # Kyle's local Modelfile — qwen3:14b under a custom tag
        "owls-brain:qwen3-14b-ctx16k",
        "owls-brain:qwen3-14b-ctx32k",
        "owls-honcho:qwen3-8b-ctx8k",
        # Plain unrelated models
        "qwen3:14b",
        "qwen3-coder:30b",
        "qwen2.5:14b",
        "claude-opus-4-6",
        "anthropic/claude-sonnet-4.5",
        "gpt-5",
        "openai/gpt-4o",
        "google/gemini-2.5-flash",
        "deepseek-chat",
        # Non-chat OWLS models we don't warn about
        "owls-llm-2",
        "ospilot2-pro",
        "nous-owls-2-mistral",
        # Edge cases
        "",
        "owls",  # bare "owls" isn't the 3/4 family
        "owls-brain",
        "brain-owls-3-impostor",  # "3" not preceded by /: boundary
    ],
)
def test_does_not_match_unrelated_models(model_name: str) -> None:
    assert not is_nous_owls_non_agentic(model_name), (
        f"expected {model_name!r} NOT to be flagged as Nous OWLS 3/4"
    )
    assert _check_owls_model_warning(model_name) == ""


def test_none_like_inputs_are_safe() -> None:
    assert is_nous_owls_non_agentic("") is False
    # Defensive: the helper shouldn't crash on None-ish falsy input either.
    assert _check_owls_model_warning("") == ""
