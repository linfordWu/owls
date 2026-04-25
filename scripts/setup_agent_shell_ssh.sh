#!/usr/bin/env bash
# Setup script for AgentShell SSH access.
# Creates the owls-agent user and configures SSH ForceCommand.
#
# Usage:
#   sudo ./setup_agent_shell_ssh.sh
#   sudo ./setup_agent_shell_ssh.sh --dry-run

set -euo pipefail

DRY_RUN=false
AGENT_USER="owls-agent"
AGENT_SHELL="/usr/local/bin/agent-shell"
SSHD_CONFIG="/etc/ssh/sshd_config"

for arg in "$@"; do
    case "$arg" in
        --dry-run)
            DRY_RUN=true
            echo "[DRY-RUN] No changes will be made."
            ;;
        *)
            echo "Unknown argument: $arg"
            echo "Usage: $0 [--dry-run]"
            exit 1
            ;;
    esac
done

run_cmd() {
    if [ "$DRY_RUN" = true ]; then
        echo "[DRY-RUN] Would run: $*"
    else
        echo "+ $*"
        "$@"
    fi
}

echo "=== OWLS AgentShell SSH Setup ==="
echo ""

# 1. Create user (if not exists)
if id "$AGENT_USER" &>/dev/null; then
    echo "User '$AGENT_USER' already exists."
else
    echo "Creating user '$AGENT_USER'..."
    run_cmd useradd -r -s /bin/bash -m -d "/home/$AGENT_USER" "$AGENT_USER"
fi

# 2. Install agent-shell wrapper
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
AGENT_SHELL_PY="$PROJECT_ROOT/owls_cli/agent_shell.py"

if [ ! -f "$AGENT_SHELL_PY" ]; then
    echo "ERROR: agent_shell.py not found at $AGENT_SHELL_PY"
    exit 1
fi

cat > /tmp/agent-shell-wrapper <<'WRAPPER'
#!/bin/bash
# Wrapper: inject project root into PYTHONPATH and delegate to agent_shell.py
PROJECT_ROOT="__PROJECT_ROOT__"
export PYTHONPATH="${PROJECT_ROOT}:${PYTHONPATH:-}"
exec python3 "$PROJECT_ROOT/owls_cli/agent_shell.py" "$@"
WRAPPER

sed -i "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" /tmp/agent-shell-wrapper
run_cmd install -m 755 /tmp/agent-shell-wrapper "$AGENT_SHELL"
rm -f /tmp/agent-shell-wrapper

# 3. Set up SSH authorized_keys with command restriction
mkdir -p "/home/$AGENT_USER/.ssh"

# Generate a key pair for the agent if none exists
if [ ! -f "/home/$AGENT_USER/.ssh/id_ed25519" ]; then
    echo "Generating SSH key pair for $AGENT_USER..."
    run_cmd ssh-keygen -t ed25519 -f "/home/$AGENT_USER/.ssh/id_ed25519" -N "" -C "owls-agent@$(hostname)"
fi

# Add the public key to authorized_keys with ForceCommand
PUB_KEY="$(cat "/home/$AGENT_USER/.ssh/id_ed25519.pub")"
AUTH_KEYS="/home/$AGENT_USER/.ssh/authorized_keys"

if [ -f "$AUTH_KEYS" ] && grep -qF "$PUB_KEY" "$AUTH_KEYS"; then
    echo "Public key already in authorized_keys."
else
    echo "Adding public key to authorized_keys with ForceCommand..."
    {
        echo "command=\"$AGENT_SHELL --user %u\" $PUB_KEY"
    } >> "$AUTH_KEYS"
fi

run_cmd chown -R "$AGENT_USER:$AGENT_USER" "/home/$AGENT_USER/.ssh"
run_cmd chmod 700 "/home/$AGENT_USER/.ssh"
run_cmd chmod 600 "$AUTH_KEYS"

# 4. Update sshd_config with Match block
MATCH_BLOCK="
# OWLS AgentShell — added by setup_agent_shell_ssh.sh
Match User $AGENT_USER
    ForceCommand $AGENT_SHELL --user %u
    AllowTcpForwarding no
    X11Forwarding no
    PermitTunnel no
"

if [ -f "$SSHD_CONFIG" ] && grep -q "ForceCommand $AGENT_SHELL" "$SSHD_CONFIG"; then
    echo "sshd_config already contains AgentShell ForceCommand."
else
    echo "Adding Match block to $SSHD_CONFIG..."
    if [ "$DRY_RUN" = true ]; then
        echo "[DRY-RUN] Would append Match block to $SSHD_CONFIG"
    else
        echo "$MATCH_BLOCK" >> "$SSHD_CONFIG"
    fi
fi

echo ""
echo "=== Setup Summary ==="
echo "User:       $AGENT_USER"
echo "Shell:      $AGENT_SHELL"
echo "SSH key:    /home/$AGENT_USER/.ssh/id_ed25519"
echo "Public key: /home/$AGENT_USER/.ssh/id_ed25519.pub"
echo ""
if [ "$DRY_RUN" = true ]; then
    echo "[DRY-RUN] To apply changes, re-run without --dry-run."
else
    echo "To complete setup, restart SSH:"
    echo "  sudo systemctl restart sshd      # systemd"
    echo "  sudo service ssh restart         # SysVinit"
    echo ""
    echo "Connect with:"
    echo "  ssh -i /home/$AGENT_USER/.ssh/id_ed25519 $AGENT_USER@<host>"
fi
