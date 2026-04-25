"""Launch the bundled OWLS Web UI.

Handles Node.js detection, build, and server startup.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _resolve_web_ui_dir() -> Path | None:
    """Find the bundled or explicitly configured web UI directory."""
    # 1. Bundled package data. This is the default path for installed OWLS.
    bundled = Path(__file__).parent / "owls_web_ui"
    if (bundled / "package.json").exists():
        return bundled

    # 2. Explicit override, only used if bundled assets are not available.
    if "OWLS_WEB_UI_DIR" in os.environ:
        p = Path(os.environ["OWLS_WEB_UI_DIR"])
        if p.exists():
            return p

    # 3. Source-tree fallbacks for developers who keep the UI as a sibling
    # inside this repository. Do not walk to the parent directory; that would
    # reintroduce a dependency on a separately checked out web UI.
    project_root = Path(__file__).parent.parent.resolve()
    candidates = [
        project_root / "owls-web-ui",
        project_root / "web-ui",
    ]
    for c in candidates:
        if (c / "package.json").exists():
            return c

    return None


def _ensure_runtime_dependencies(ui_dir: Path) -> bool:
    """Install runtime-only Node dependencies when the bundled dist is present."""
    package_json = ui_dir / "package.json"
    if not package_json.exists():
        return True

    # The server bundle intentionally leaves node-pty external because it ships
    # native helpers. If it is missing the app still starts, but the terminal
    # websocket is disabled, so install production dependencies when possible.
    node_pty = ui_dir / "node_modules" / "node-pty" / "package.json"
    if node_pty.exists():
        return True

    npm = shutil.which("npm")
    if not npm:
        print("npm not found; terminal support may be disabled until dependencies are installed.")
        return True

    print("→ Installing OWLS Web UI runtime dependencies...")
    result = subprocess.run([npm, "install", "--omit=dev"], cwd=ui_dir, capture_output=True)
    if result.returncode != 0:
        print("  ✗ Runtime dependency install failed")
        print(result.stderr.decode())
        return False

    print("  ✓ Runtime dependencies ready")
    return True


def _build_web_ui(ui_dir: Path) -> bool:
    """Build the web UI if needed."""
    dist_server = ui_dir / "dist" / "server" / "index.js"
    if dist_server.exists():
        return _ensure_runtime_dependencies(ui_dir)

    npm = shutil.which("npm")
    if not npm:
        print("npm not found. Install Node.js >= 23: https://nodejs.org/")
        return False

    print("→ Building OWLS Web UI...")
    r1 = subprocess.run([npm, "install"], cwd=ui_dir, capture_output=True)
    if r1.returncode != 0:
        print("  ✗ npm install failed")
        print(r1.stderr.decode())
        return False

    r2 = subprocess.run([npm, "run", "build"], cwd=ui_dir, capture_output=True)
    if r2.returncode != 0:
        print("  ✗ Build failed")
        print(r2.stderr.decode())
        return False

    print("  ✓ Build complete")
    return True


def _wait_for_port(host: str, port: int, timeout: float = 30.0) -> bool:
    """Wait until a TCP port accepts connections."""
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def start_web_ui(
    host: str = "127.0.0.1",
    port: int = 8648,
    open_browser: bool = True,
    allow_public: bool = False,
) -> None:
    """Start the OWLS Web UI server."""
    node = shutil.which("node")
    if not node:
        print("Node.js not found. Install Node.js >= 23: https://nodejs.org/")
        sys.exit(1)

    ui_dir = _resolve_web_ui_dir()
    if not ui_dir:
        print("OWLS Web UI directory not found.")
        print("Set OWLS_WEB_UI_DIR or reinstall OWLS with bundled web UI assets.")
        sys.exit(1)

    if not _build_web_ui(ui_dir):
        sys.exit(1)

    # ── Start OWLS API server (gateway with api_server platform) ──
    owls_bin = shutil.which("owls") or sys.executable
    gateway_proc = None
    try:
        gateway_env = os.environ.copy()
        gateway_env["API_SERVER_ENABLED"] = "true"
        gateway_env["API_SERVER_HOST"] = "127.0.0.1"
        gateway_env["API_SERVER_PORT"] = "8642"
        gateway_env["API_SERVER_CORS_ORIGINS"] = f"http://{host}:{port}"

        print("→ Starting OWLS API server (gateway)...")
        gateway_proc = subprocess.Popen(
            [owls_bin, "gateway", "run"],
            env=gateway_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if not _wait_for_port("127.0.0.1", 8642, timeout=30.0):
            print("  ✗ OWLS API server did not start on port 8642 within 30s")
            gateway_proc.terminate()
            try:
                gateway_proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                gateway_proc.kill()
            sys.exit(1)
        print("  ✓ OWLS API server ready on http://127.0.0.1:8642")
    except Exception as e:
        print(f"  ✗ Failed to start OWLS API server: {e}")
        if gateway_proc is not None:
            gateway_proc.terminate()
        sys.exit(1)

    env = os.environ.copy()
    env["OWLS_BIN"] = owls_bin
    env["PORT"] = str(port)
    env["HOST"] = host
    if allow_public:
        env["CORS_ORIGINS"] = "*"

    server_js = ui_dir / "dist" / "server" / "index.js"
    if not server_js.exists():
        print(f"Server bundle not found: {server_js}")
        print("Build may have failed. Try: cd <web-ui-dir> && npm run build")
        if gateway_proc is not None:
            gateway_proc.terminate()
        sys.exit(1)

    if open_browser:
        import threading
        import webbrowser

        def _open():
            import time as _t
            _t.sleep(2.0)
            webbrowser.open(f"http://{host}:{port}")

        threading.Thread(target=_open, daemon=True).start()

    print(f"  OWLS Web UI → http://{host}:{port}")
    try:
        subprocess.run([node, str(server_js)], cwd=ui_dir, env=env)
    finally:
        if gateway_proc is not None:
            print("→ Stopping OWLS API server...")
            gateway_proc.terminate()
            try:
                gateway_proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                gateway_proc.kill()
