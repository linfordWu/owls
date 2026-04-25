"""Linux clipboard image extraction.

Provides ``save_clipboard_image(dest)`` and ``has_clipboard_image()`` for
Wayland and X11 sessions.
"""

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def save_clipboard_image(dest: Path) -> bool:
    """Extract an image from the Linux clipboard and save it as PNG."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if os.environ.get("WAYLAND_DISPLAY") and _wayland_save(dest):
        return True
    return _xclip_save(dest)


def has_clipboard_image() -> bool:
    """Return True when the Linux clipboard currently contains an image."""
    if os.environ.get("WAYLAND_DISPLAY") and _wayland_has_image():
        return True
    return _xclip_has_image()


def _wayland_has_image() -> bool:
    try:
        r = subprocess.run(
            ["wl-paste", "--list-types"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return r.returncode == 0 and any(
            t.startswith("image/") for t in r.stdout.splitlines()
        )
    except FileNotFoundError:
        logger.debug("wl-paste not installed; Wayland clipboard unavailable")
    except Exception:
        pass
    return False


def _wayland_save(dest: Path) -> bool:
    try:
        types_r = subprocess.run(
            ["wl-paste", "--list-types"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if types_r.returncode != 0:
            return False
        types = types_r.stdout.splitlines()

        mime = None
        for preferred in ("image/png", "image/jpeg", "image/bmp", "image/gif", "image/webp"):
            if preferred in types:
                mime = preferred
                break
        if not mime:
            return False

        with open(dest, "wb") as f:
            subprocess.run(
                ["wl-paste", "--type", mime],
                stdout=f,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=True,
            )

        if not dest.exists() or dest.stat().st_size == 0:
            dest.unlink(missing_ok=True)
            return False
        if mime == "image/bmp":
            return _convert_to_png(dest)
        return True
    except FileNotFoundError:
        logger.debug("wl-paste not installed; Wayland clipboard unavailable")
    except Exception as exc:
        logger.debug("wl-paste clipboard extraction failed: %s", exc)
        dest.unlink(missing_ok=True)
    return False


def _convert_to_png(path: Path) -> bool:
    try:
        from PIL import Image

        img = Image.open(path)
        img.save(path, "PNG")
        return True
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("Pillow image conversion failed: %s", exc)

    tmp = path.with_suffix(".bmp")
    try:
        path.rename(tmp)
        r = subprocess.run(
            ["convert", str(tmp), "png:" + str(path)],
            capture_output=True,
            timeout=5,
        )
        if r.returncode == 0 and path.exists() and path.stat().st_size > 0:
            tmp.unlink(missing_ok=True)
            return True
        tmp.rename(path)
    except FileNotFoundError:
        logger.debug("ImageMagick not installed; cannot convert BMP to PNG")
        if tmp.exists() and not path.exists():
            tmp.rename(path)
    except Exception as exc:
        logger.debug("ImageMagick image conversion failed: %s", exc)
        if tmp.exists() and not path.exists():
            tmp.rename(path)
    return False


def _xclip_has_image() -> bool:
    for target in ("image/png", "image/jpeg", "image/bmp", "image/gif", "image/webp"):
        try:
            r = subprocess.run(
                ["xclip", "-selection", "clipboard", "-target", target, "-o"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            if r.returncode == 0:
                return True
        except FileNotFoundError:
            logger.debug("xclip not installed; X11 clipboard unavailable")
            return False
        except Exception:
            pass
    return False


def _xclip_save(dest: Path) -> bool:
    for target in ("image/png", "image/jpeg", "image/bmp", "image/gif", "image/webp"):
        try:
            with open(dest, "wb") as f:
                r = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-target", target, "-o"],
                    stdout=f,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            if r.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
                if target == "image/bmp":
                    return _convert_to_png(dest)
                return True
            dest.unlink(missing_ok=True)
        except FileNotFoundError:
            logger.debug("xclip not installed; X11 clipboard unavailable")
            return False
        except Exception as exc:
            logger.debug("xclip clipboard extraction failed: %s", exc)
            dest.unlink(missing_ok=True)
    return False
