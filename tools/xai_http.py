"""Shared helpers for direct xAI HTTP integrations."""

from __future__ import annotations


def owls_xai_user_agent() -> str:
    """Return a stable OWLS-specific User-Agent for xAI HTTP calls."""
    try:
        from owls_cli import __version__
    except Exception:
        __version__ = "unknown"
    return f"OWLS/{__version__}"
