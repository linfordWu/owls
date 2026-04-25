"""Tests for tools.ocr_tool."""

import json
import tempfile
from pathlib import Path

import pytest

from tools.ocr_tool import ocr_extract_tool, _is_text_dense_image
from tools.registry import registry


class TestOcrExtractTool:
    def test_missing_file(self):
        result = ocr_extract_tool("/nonexistent/path.png")
        parsed = json.loads(result)
        assert "error" in parsed

    def test_text_dense_detection_on_text_image(self):
        # Create a simple image file for testing
        try:
            from PIL import Image
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "test.png"
                # Create a small white image
                img = Image.new("RGB", (100, 100), color="white")
                img.save(path)
                assert _is_text_dense_image(str(path)) is False  # small image
        except ImportError:
            pytest.skip("PIL not installed")

    def test_tool_registered(self):
        entry = registry.get_entry("ocr_extract")
        assert entry is not None
        assert entry.toolset == "vision"

    def test_tool_schema_has_required_params(self):
        schema = registry.get_schema("ocr_extract")
        assert schema is not None
        props = schema["parameters"]["properties"]
        assert "image_path" in props
        assert "lang" in props
