"""OCR Tool — extract text from images using multiple backends.

Backends (in order of preference):
    1. pytesseract   (local, fast for clean images)
    2. easyocr       (local, better for handwriting / mixed scripts)
    3. vision_analyze_tool  (cloud LLM, fallback)

Usage:
    from tools.ocr_tool import ocr_extract_tool
    result = ocr_extract_tool("/path/to/image.png", lang="eng+chi_sim")
    # result is JSON: {"text": "...", "regions": [...]}
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)


def ocr_extract_tool(
    image_path: str,
    lang: str = "eng+chi_sim",
    task_id: Optional[str] = None,
) -> str:
    """Extract text from an image using OCR.

    Args:
        image_path: Path to the image file.
        lang: Tesseract language code(s), e.g. "eng", "chi_sim", "eng+chi_sim".
        task_id: Optional task identifier for tracing.

    Returns:
        JSON string: {"text": "extracted text", "regions": [...]}
    """
    path = Path(os.path.expanduser(image_path))
    if not path.exists():
        return tool_error(f"Image not found: {image_path}")

    text = ""
    regions: List[Dict] = []
    backend_used = "none"

    # 1. Try pytesseract
    try:
        import pytesseract
        from PIL import Image

        img = Image.open(path)
        text = pytesseract.image_to_string(img, lang=lang)
        data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
        for i in range(len(data["text"])):
            if int(data["conf"][i]) > 30:
                regions.append({
                    "text": data["text"][i],
                    "x": data["left"][i],
                    "y": data["top"][i],
                    "width": data["width"][i],
                    "height": data["height"][i],
                    "conf": data["conf"][i],
                })
        backend_used = "pytesseract"
        logger.info("OCR via pytesseract: %d chars, %d regions", len(text), len(regions))
    except ImportError:
        logger.debug("pytesseract not installed, trying easyocr")
    except Exception as e:
        logger.warning("pytesseract failed: %s", e)

    # 2. Try easyocr if pytesseract didn't work or produced no text
    if not text.strip():
        try:
            import easyocr as eo
            reader = eo.Reader(lang.replace("+", ",").split(","))
            results = reader.readtext(str(path))
            lines = []
            for bbox, txt, conf in results:
                lines.append(txt)
                regions.append({
                    "text": txt,
                    "bbox": bbox,
                    "conf": round(conf * 100, 2),
                })
            text = "\n".join(lines)
            backend_used = "easyocr"
            logger.info("OCR via easyocr: %d chars, %d regions", len(text), len(regions))
        except ImportError:
            logger.debug("easyocr not installed, trying vision fallback")
        except Exception as e:
            logger.warning("easyocr failed: %s", e)

    # 3. Fallback to vision_analyze_tool (cloud LLM)
    if not text.strip():
        try:
            from tools.vision_tools import vision_analyze_tool
            import asyncio
            prompt = "Extract all visible text from this image. Return only the text, no commentary."
            result = asyncio.get_event_loop().run_until_complete(
                vision_analyze_tool(str(path), user_prompt=prompt)
            )
            parsed = json.loads(result)
            text = parsed.get("analysis", "")
            backend_used = "vision_llm"
            logger.info("OCR via vision LLM: %d chars", len(text))
        except Exception as e:
            logger.warning("Vision fallback failed: %s", e)

    if not text.strip():
        return tool_error("No text could be extracted from the image")

    return tool_result({
        "text": text.strip(),
        "regions": regions,
        "backend": backend_used,
        "lang": lang,
    })


def _is_text_dense_image(image_path: str) -> bool:
    """Heuristic: detect if an image is text-dense (screenshot, document scan).

    Uses pytesseract's OSD (orientation and script detection) or file size
    vs dimensions as a proxy.
    """
    try:
        from PIL import Image
        img = Image.open(image_path)
        w, h = img.size
        # Screenshots / document scans are usually large in pixels but small in file size
        # compared to photos.  This is a coarse heuristic.
        file_size = Path(image_path).stat().st_size
        pixels = w * h
        if pixels == 0:
            return False
        bpp = (file_size * 8) / pixels
        # Text images typically have low bpp (lots of solid backgrounds)
        if bpp < 2.0 and w > 400 and h > 300:
            return True
        # Try pytesseract OSD if available
        try:
            import pytesseract
            osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
            if osd.get("orientation_conf") and osd["orientation_conf"] > 50:
                return True
        except Exception:
            pass
    except Exception:
        pass
    return False


# Register the tool
registry.register(
    name="ocr_extract",
    toolset="vision",
    schema={
        "type": "object",
        "description": "Extract text from an image using OCR (Optical Character Recognition). Supports pytesseract, easyocr, and vision LLM fallback.",
        "parameters": {
            "type": "object",
            "properties": {
                "image_path": {
                    "type": "string",
                    "description": "Path to the image file to extract text from",
                },
                "lang": {
                    "type": "string",
                    "description": "OCR language code(s), e.g. 'eng', 'chi_sim', 'eng+chi_sim'",
                    "default": "eng+chi_sim",
                },
                "task_id": {
                    "type": "string",
                    "description": "Optional task identifier",
                },
            },
            "required": ["image_path"],
        },
    },
    handler=lambda args, **kw: ocr_extract_tool(
        image_path=args["image_path"],
        lang=args.get("lang", "eng+chi_sim"),
        task_id=args.get("task_id"),
    ),
    check_fn=lambda: True,
    description="Extract text from images using OCR",
    emoji="🔍",
)
