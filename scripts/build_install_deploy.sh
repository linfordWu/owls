#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$ROOT_DIR/owls_cli/owls_web_ui"
VENV_DIR="$ROOT_DIR/venv"

HOST="127.0.0.1"
PORT="9119"
START_WEB=1
RUN_TESTS=0
STOP_ONLY=0
OPEN_BROWSER=0
INSTALL_EXTRAS="web,cli"
DEPLOY_HOME="${OWLS_HOME:-$HOME/.owls}"
RUN_DIR="$DEPLOY_HOME/run"
PID_FILE="$RUN_DIR/owls-dashboard.pid"
LOG_FILE="$RUN_DIR/owls-dashboard.log"

usage() {
  cat <<'USAGE'
Usage: scripts/build_install_deploy.sh [options]

Compile, install, and deploy OWLS CLI + bundled web terminal on Linux.

Options:
  --port <port>       Web port (default: 9119)
  --host <host>       Web host (default: 127.0.0.1)
  --with-tests        Run focused Python tests after build
  --skip-web-start    Compile/install only; do not start the web terminal
  --open              Open browser when starting the web terminal
  --stop              Stop the managed web terminal process and exit
  -h, --help          Show this help
USAGE
}

log() {
  printf '\n==> %s\n' "$*"
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      [[ $# -ge 2 ]] || fail "--port requires a value"
      PORT="$2"
      shift 2
      ;;
    --host)
      [[ $# -ge 2 ]] || fail "--host requires a value"
      HOST="$2"
      shift 2
      ;;
    --with-tests)
      RUN_TESTS=1
      INSTALL_EXTRAS="web,cli,dev"
      shift
      ;;
    --skip-web-start|--no-start)
      START_WEB=0
      shift
      ;;
    --open)
      OPEN_BROWSER=1
      shift
      ;;
    --stop)
      STOP_ONLY=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown option: $1"
      ;;
  esac
done

[[ "$(uname -s)" == "Linux" ]] || fail "this deployment script supports Linux only"
[[ -f "$ROOT_DIR/pyproject.toml" ]] || fail "project root not found: $ROOT_DIR"
[[ -f "$WEB_DIR/package.json" ]] || fail "web UI package not found: $WEB_DIR"

mkdir -p "$RUN_DIR"

is_running() {
  local pid="${1:-}"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

is_group_running() {
  local pid="${1:-}"
  [[ -n "$pid" ]] && kill -0 -- "-$pid" 2>/dev/null
}

stop_dashboard() {
  if [[ ! -f "$PID_FILE" ]]; then
    echo "No managed OWLS dashboard PID file found."
    return 0
  fi

  local pid
  pid="$(tr -d '[:space:]' < "$PID_FILE" || true)"
  if ! is_running "$pid" && ! is_group_running "$pid"; then
    rm -f "$PID_FILE"
    echo "Removed stale PID file."
    return 0
  fi

  echo "Stopping OWLS dashboard PID $pid..."
  kill -TERM -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
  for _ in {1..20}; do
    if ! is_running "$pid" && ! is_group_running "$pid"; then
      rm -f "$PID_FILE"
      echo "Stopped."
      return 0
    fi
    sleep 0.25
  done

  echo "Process did not stop gracefully; sending SIGKILL."
  kill -KILL -- "-$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
}

if [[ "$STOP_ONLY" -eq 1 ]]; then
  stop_dashboard
  exit 0
fi

log "Preparing Python virtual environment"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m ensurepip --upgrade >/dev/null
python -m pip install --upgrade pip >/dev/null

log "Installing OWLS CLI"
python -m pip install -e "$ROOT_DIR[$INSTALL_EXTRAS]"

log "Compiling Python modules"
mapfile -t PY_FILES < <(
  {
    printf '%s\n' \
      "$ROOT_DIR/owls_constants.py" \
      "$ROOT_DIR/toolsets.py" \
      "$ROOT_DIR/model_tools.py" \
      "$ROOT_DIR/run_agent.py" \
      "$ROOT_DIR/cli.py"
    find \
      "$ROOT_DIR/agent" \
      "$ROOT_DIR/tools" \
      "$ROOT_DIR/owls_cli" \
      "$ROOT_DIR/gateway" \
      "$ROOT_DIR/tui_gateway" \
      \( -path "$ROOT_DIR/owls_cli/owls_web_ui/node_modules" -o \
         -path "$ROOT_DIR/owls_cli/owls_web_ui/dist" \) -prune \
      -o -name '*.py' -print
  } | awk '!seen[$0]++'
)
python -m py_compile "${PY_FILES[@]}"

log "Checking CLI entry points"
owls --help >/dev/null
owls gateway --help >/dev/null
owls dashboard --help >/dev/null

log "Checking Node.js"
command -v node >/dev/null || fail "node not found; install Node.js >= 23"
command -v npm >/dev/null || fail "npm not found; install Node.js/npm"
NODE_MAJOR="$(node -p "Number(process.versions.node.split('.')[0])")"
[[ "$NODE_MAJOR" -ge 23 ]] || fail "Node.js >= 23 is required; found $(node --version)"

log "Installing web dependencies"
npm --prefix "$WEB_DIR" install --package-lock=false

log "Building web terminal"
npm --prefix "$WEB_DIR" run build
[[ -f "$WEB_DIR/dist/server/index.js" ]] || fail "missing web server bundle"
[[ -f "$WEB_DIR/dist/client/index.html" ]] || fail "missing web client bundle"

if [[ "$RUN_TESTS" -eq 1 ]]; then
  log "Running focused tests"
  "$ROOT_DIR/scripts/run_tests.sh" \
    tests/agent/test_prompt_builder.py \
    tests/tools/test_skills_tool.py \
    tests/owls_cli/test_commands.py
fi

log "Checking API server adapter"
API_SERVER_ENABLED=1 python - <<'PY'
from gateway.config import load_gateway_config, Platform
from gateway.run import GatewayRunner

cfg = load_gateway_config()
connected = [p.value for p in cfg.get_connected_platforms()]
if connected != ["api_server"]:
    raise SystemExit(f"unexpected connected endpoints: {connected}")
runner = GatewayRunner(cfg)
adapter = runner._create_adapter(Platform.API_SERVER, cfg.platforms[Platform.API_SERVER])
if adapter is None:
    raise SystemExit("API server adapter could not be created")
PY

if [[ "$START_WEB" -eq 0 ]]; then
  log "Build/install complete; web start skipped"
  exit 0
fi

log "Deploying OWLS web terminal"
stop_dashboard

if command -v lsof >/dev/null; then
  PORT_PIDS="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  [[ -z "$PORT_PIDS" ]] || fail "port $PORT is already in use by PID(s): $PORT_PIDS"
fi

OPEN_FLAG="--no-open"
if [[ "$OPEN_BROWSER" -eq 1 ]]; then
  OPEN_FLAG=""
fi

nohup setsid "$VENV_DIR/bin/owls" dashboard \
  --host "$HOST" \
  --port "$PORT" \
  $OPEN_FLAG \
  > "$LOG_FILE" 2>&1 &
PID="$!"
echo "$PID" > "$PID_FILE"

HEALTH_URL="http://127.0.0.1:$PORT/health"
for _ in {1..80}; do
  if ! is_running "$PID"; then
    tail -n 80 "$LOG_FILE" >&2 || true
    rm -f "$PID_FILE"
    fail "dashboard process exited during startup"
  fi
  if python - "$HEALTH_URL" <<'PY'
import sys
from urllib.request import urlopen

try:
    with urlopen(sys.argv[1], timeout=1) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except Exception:
    raise SystemExit(1)
PY
  then
    log "Deploy complete"
    echo "URL: http://$HOST:$PORT"
    echo "PID: $PID"
    echo "Log: $LOG_FILE"
    exit 0
  fi
  sleep 0.5
done

tail -n 120 "$LOG_FILE" >&2 || true
fail "dashboard health check failed: $HEALTH_URL"
