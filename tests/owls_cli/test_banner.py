"""Tests for minimal startup banner rendering."""

from rich.console import Console

import owls_cli.banner as banner


def test_display_toolset_name_strips_legacy_suffix():
    assert banner._display_toolset_name("homeassistant_tools") == "homeassistant"
    assert banner._display_toolset_name("honcho_tools") == "honcho"
    assert banner._display_toolset_name("web_tools") == "web"


def test_display_toolset_name_preserves_clean_names():
    assert banner._display_toolset_name("browser") == "browser"
    assert banner._display_toolset_name("file") == "file"
    assert banner._display_toolset_name("terminal") == "terminal"


def test_display_toolset_name_handles_empty():
    assert banner._display_toolset_name("") == "unknown"
    assert banner._display_toolset_name(None) == "unknown"


def test_build_welcome_banner_shows_only_minimal_runtime_info():
    console = Console(record=True, force_terminal=False, color_system=None, width=120)

    banner.build_welcome_banner(
        console=console,
        model="anthropic/test-model",
        cwd="/tmp/project",
    )

    output = console.export_text()
    assert "OWLS Shell" in output
    assert "Model   | test-model" in output
    assert "Workdir | /tmp/project" in output
    assert "Available Tools" not in output
    assert "Available Skills" not in output
