#!/usr/bin/env bash
#
# Example Agents VM Provisioner
# ===========================
# One command to provision a fully configured Example Agents VM on GCP.
#
# Usage:
#   ./provision.sh                          # Interactive (asks for project, region)
#   ./provision.sh --project my-gcp-proj    # Non-interactive with defaults
#
# Requirements:
#   - gcloud CLI installed and authenticated (gcloud auth login)
#   - A GCP project with billing enabled
#   - Compute Engine API enabled
#
set -euo pipefail

# ── Defaults ──
DEFAULT_ZONE="us-central1-a"
DEFAULT_MACHINE="e2-small"
DEFAULT_DISK_SIZE="30"
DEFAULT_IMAGE_FAMILY="ubuntu-2204-lts"
DEFAULT_IMAGE_PROJECT="ubuntu-os-cloud"
VM_USER="claude"

BOLD='\033[1m'
BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
RESET='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${RESET}  $1"; }
log_ok()    { echo -e "${GREEN}[OK]${RESET}    $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${RESET}  $1"; }
log_err()   { echo -e "${RED}[ERROR]${RESET} $1"; }
log_step()  { echo -e "\n${BOLD}${BLUE}▸ $1${RESET}"; }

# ── Parse Args ──
GCP_PROJECT=""
ZONE="$DEFAULT_ZONE"
MACHINE="$DEFAULT_MACHINE"
DISK_SIZE="$DEFAULT_DISK_SIZE"
INSTANCE_NAME=""
CLIENT_NAME=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --project)   GCP_PROJECT="$2"; shift 2 ;;
        --zone)      ZONE="$2"; shift 2 ;;
        --machine)   MACHINE="$2"; shift 2 ;;
        --disk)      DISK_SIZE="$2"; shift 2 ;;
        --name)      INSTANCE_NAME="$2"; shift 2 ;;
        --client)    CLIENT_NAME="$2"; shift 2 ;;
        --help)
            echo "Usage: ./provision.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --project PROJECT   GCP project ID"
            echo "  --zone ZONE         GCP zone (default: us-central1-a)"
            echo "  --machine TYPE      Machine type (default: e2-small)"
            echo "  --disk SIZE         Disk size in GB (default: 30)"
            echo "  --name NAME         VM instance name"
            echo "  --client NAME       Client name (for labeling)"
            exit 0 ;;
        *) log_err "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Header ──
echo -e "${BOLD}${BLUE}"
echo "╔══════════════════════════════════════════╗"
echo "║    Example Agents VM Provisioner             ║"
echo "║    Powered by Example Agents              ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${RESET}"

# ── Preflight Checks ──
log_step "Preflight checks"

if ! command -v gcloud &>/dev/null; then
    log_err "gcloud CLI not found. Install: https://cloud.google.com/sdk/docs/install"
    exit 1
fi
log_ok "gcloud CLI found"

# Check auth
if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null | head -1 | grep -q "@"; then
    log_err "Not authenticated. Run: gcloud auth login"
    exit 1
fi
GCLOUD_ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null | head -1)
log_ok "Authenticated as ${GCLOUD_ACCOUNT}"

# Get/confirm project
if [ -z "$GCP_PROJECT" ]; then
    CURRENT_PROJECT=$(gcloud config get-value project 2>/dev/null || echo "")
    if [ -n "$CURRENT_PROJECT" ]; then
        echo ""
        read -p "GCP Project [${CURRENT_PROJECT}]: " GCP_PROJECT
        GCP_PROJECT=${GCP_PROJECT:-$CURRENT_PROJECT}
    else
        read -p "GCP Project ID: " GCP_PROJECT
    fi
fi

if [ -z "$GCP_PROJECT" ]; then
    log_err "No project specified."
    exit 1
fi

gcloud config set project "$GCP_PROJECT" --quiet
log_ok "Project: ${GCP_PROJECT}"

# Enable Compute Engine API
log_info "Ensuring Compute Engine API is enabled..."
gcloud services enable compute.googleapis.com --quiet 2>/dev/null || true
log_ok "Compute Engine API enabled"

# ── Configuration ──
log_step "Configuration"

if [ -z "$CLIENT_NAME" ]; then
    read -p "Client name (for labeling, e.g. 'operator' or 'acme-corp'): " CLIENT_NAME
fi
CLIENT_SLUG=$(echo "$CLIENT_NAME" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd 'a-z0-9-')

if [ -z "$INSTANCE_NAME" ]; then
    INSTANCE_NAME="la-${CLIENT_SLUG}-$(date +%s | tail -c 5)"
fi

# Generate connection code
CONNECTION_CODE=$(cat /dev/urandom | LC_ALL=C tr -dc 'A-Z0-9' | head -c 6)

echo ""
echo "  Instance name:    ${INSTANCE_NAME}"
echo "  Zone:             ${ZONE}"
echo "  Machine type:     ${MACHINE}"
echo "  Disk size:        ${DISK_SIZE}GB"
echo "  Client:           ${CLIENT_NAME}"
echo "  Connection code:  ${CONNECTION_CODE}"
echo ""
read -p "Proceed? (Y/n): " confirm
if [ "$confirm" = "n" ] || [ "$confirm" = "N" ]; then
    echo "Aborted."
    exit 0
fi

# ── Create Firewall Rule (if not exists) ──
log_step "Firewall configuration"

FIREWALL_NAME="life-agents-ssh"
if gcloud compute firewall-rules describe "$FIREWALL_NAME" --project="$GCP_PROJECT" &>/dev/null; then
    log_ok "Firewall rule already exists: ${FIREWALL_NAME}"
else
    log_info "Creating firewall rule for SSH..."
    gcloud compute firewall-rules create "$FIREWALL_NAME" \
        --project="$GCP_PROJECT" \
        --direction=INGRESS \
        --priority=1000 \
        --network=default \
        --action=ALLOW \
        --rules=tcp:22 \
        --source-ranges=0.0.0.0/0 \
        --target-tags=life-agents \
        --description="Allow SSH to Example Agents VMs" \
        --quiet
    log_ok "Firewall rule created"
fi

# ── Create VM ──
log_step "Creating VM instance"

log_info "Provisioning ${INSTANCE_NAME} (${MACHINE}, ${DISK_SIZE}GB)..."

gcloud compute instances create "$INSTANCE_NAME" \
    --project="$GCP_PROJECT" \
    --zone="$ZONE" \
    --machine-type="$MACHINE" \
    --image-family="$DEFAULT_IMAGE_FAMILY" \
    --image-project="$DEFAULT_IMAGE_PROJECT" \
    --boot-disk-size="${DISK_SIZE}GB" \
    --boot-disk-type=pd-balanced \
    --tags=life-agents \
    --labels="client=${CLIENT_SLUG},product=life-agents,connection-code=${CONNECTION_CODE}" \
    --metadata=startup-script='#!/bin/bash
# Example Agents VM Startup Script
# This runs on first boot only (checked by sentinel file)

SENTINEL="/var/lib/life-agents-initialized"
if [ -f "$SENTINEL" ]; then
    exit 0
fi

set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# System updates
apt-get update -qq
apt-get upgrade -yqq

# Install essentials
apt-get install -yqq \
    git \
    curl \
    wget \
    jq \
    python3 \
    python3-pip \
    rsync \
    tmux \
    htop \
    unzip \
    ripgrep \
    ufw

# Install Node.js 20 LTS
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -yqq nodejs

# Install Claude Code
npm install -g @anthropic-ai/claude-code 2>/dev/null || true

# Create claude user
useradd -m -s /bin/bash claude
mkdir -p /home/claude/.ssh
chmod 700 /home/claude/.ssh
touch /home/claude/.ssh/authorized_keys
chmod 600 /home/claude/.ssh/authorized_keys
chown -R claude:claude /home/claude/.ssh

# Allow claude user to use sudo without password (for package installs)
echo "claude ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/claude

# Initialize Example Agents directory structure
su - claude -c "bash -s" << '"'"'CLAUDE_INIT'"'"'
mkdir -p ~/life-agents/sessions
mkdir -p ~/life-agents/scripts
mkdir -p ~/life-agents/backups
mkdir -p ~/life-agents/devices
mkdir -p ~/life-agents/channels
mkdir -p ~/life-agents/config
mkdir -p ~/.claude/skills

# Create empty registry
echo '"'"'{"projects": []}'"'"' > ~/life-agents/sessions/registry.json

# Create update_registry.py
cat > ~/life-agents/scripts/update_registry.py << '"'"'PYTHON_REG'"'"'
#!/usr/bin/env python3
import json, argparse, os

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--uuid", required=True)
    parser.add_argument("--last-active", required=True)
    parser.add_argument("--last-device", required=True)
    parser.add_argument("--last-interface", default="claude-code")
    parser.add_argument("--name", default=None)
    parser.add_argument("--description", default=None)
    parser.add_argument("--context-summary", default=None)
    args = parser.parse_args()
    registry_path = os.path.expanduser("~/life-agents/sessions/registry.json")
    with open(registry_path, "r") as f:
        registry = json.load(f)
    project = None
    for p in registry["projects"]:
        if p["uuid"] == args.uuid:
            project = p
            break
    if project is None:
        project = {"uuid": args.uuid, "name": args.name or args.uuid, "description": args.description or "", "created_at": args.last_active, "tags": []}
        registry["projects"].append(project)
    project["last_active"] = args.last_active
    project["last_device"] = args.last_device
    project["last_interface"] = args.last_interface
    if args.name: project["name"] = args.name
    if args.description: project["description"] = args.description
    if args.context_summary: project["context_summary"] = args.context_summary
    with open(registry_path, "w") as f:
        json.dump(registry, f, indent=2)
if __name__ == "__main__":
    main()
PYTHON_REG
chmod +x ~/life-agents/scripts/update_registry.py

# Create apply_update.py
cat > ~/life-agents/scripts/apply_update.py << '"'"'PYTHON_APPLY'"'"'
#!/usr/bin/env python3
import json, sys, os
def main():
    update = json.load(sys.stdin)
    action = update.get("action")
    project_uuid = update.get("project_uuid")
    session_dir = os.path.expanduser(f"~/life-agents/sessions/{project_uuid}")
    os.makedirs(session_dir, exist_ok=True)
    if action == "update_context":
        with open(f"{session_dir}/context.md", "w") as f: f.write(update["content"])
    elif action == "append_decision":
        with open(f"{session_dir}/decisions.md", "a") as f: f.write(update["content"] + "\n")
    elif action == "update_history":
        with open(f"{session_dir}/history.md", "w") as f: f.write(update["content"])
    print(f"Applied {action} to {project_uuid}")
if __name__ == "__main__":
    main()
PYTHON_APPLY
chmod +x ~/life-agents/scripts/apply_update.py

# Hourly backup cron
(crontab -l 2>/dev/null; echo "0 * * * * tar czf ~/life-agents/backups/backup-\$(date +\%Y\%m\%d-\%H).tar.gz -C ~ life-agents/sessions/ 2>/dev/null") | crontab -
# Clean old backups (keep 7 days)
(crontab -l 2>/dev/null; echo "0 3 * * * find ~/life-agents/backups/ -name '"'"'backup-*.tar.gz'"'"' -mtime +7 -delete 2>/dev/null") | crontab -

# Create initial CLAUDE.md
cat > ~/.claude/CLAUDE.md << '"'"'CLAUDE_MD'"'"'
# Example Agents VM

This is a Example Agents managed VM. All projects and sessions are stored here.

## Session Management
- Projects live in ~/life-agents/sessions/{project-uuid}/
- Each project has: context.md, decisions.md, history.md
- Registry: ~/life-agents/sessions/registry.json

## Rules
- Always read context.md and decisions.md before starting work on a project
- Always update context.md after significant changes
- Log decisions to decisions.md with timestamp and rationale
- Compress chat history when it exceeds 50 entries
CLAUDE_MD

echo "Example Agents initialized for user claude."
CLAUDE_INIT

# Configure UFW
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw --force enable

# Disable password-based SSH login (key-only auth)
sed -i "s/^#*PasswordAuthentication.*/PasswordAuthentication no/" /etc/ssh/sshd_config
sed -i "s/^#*ChallengeResponseAuthentication.*/ChallengeResponseAuthentication no/" /etc/ssh/sshd_config
sed -i "s/^#*UsePAM.*/UsePAM no/" /etc/ssh/sshd_config
systemctl restart sshd

# Initialize security log
su - claude -c "touch ~/life-agents/security.log && echo '$(date -u +%Y-%m-%dT%H:%M:%SZ) | INIT | VM provisioned, password auth disabled' >> ~/life-agents/security.log"

# Mark as initialized
touch "$SENTINEL"
echo "Example Agents VM initialization complete."
' \
    --quiet

log_ok "VM created: ${INSTANCE_NAME}"

# ── Wait for VM to be ready ──
log_step "Waiting for VM to be ready"

log_info "Waiting for VM to boot and initialize..."
sleep 10

# Wait for SSH to be available
MAX_WAIT=180
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
    if gcloud compute ssh "$INSTANCE_NAME" \
        --zone="$ZONE" \
        --project="$GCP_PROJECT" \
        --command="echo ready" \
        --quiet \
        --ssh-flag="-o ConnectTimeout=5" \
        --ssh-flag="-o StrictHostKeyChecking=no" \
        2>/dev/null; then
        break
    fi
    sleep 10
    ELAPSED=$((ELAPSED + 10))
    echo -n "."
done
echo ""

if [ $ELAPSED -ge $MAX_WAIT ]; then
    log_warn "VM may still be initializing. Continuing..."
fi

# Wait for startup script to finish
log_info "Waiting for initialization to complete..."
for i in $(seq 1 30); do
    if gcloud compute ssh "$INSTANCE_NAME" \
        --zone="$ZONE" \
        --project="$GCP_PROJECT" \
        --command="test -f /var/lib/life-agents-initialized && echo 'done'" \
        --quiet \
        --ssh-flag="-o ConnectTimeout=5" \
        2>/dev/null | grep -q "done"; then
        break
    fi
    sleep 10
    echo -n "."
done
echo ""
log_ok "VM initialized"

# ── Get External IP ──
EXTERNAL_IP=$(gcloud compute instances describe "$INSTANCE_NAME" \
    --zone="$ZONE" \
    --project="$GCP_PROJECT" \
    --format="get(networkInterfaces[0].accessConfigs[0].natIP)")

log_ok "External IP: ${EXTERNAL_IP}"

# ── Save Connection Manifest ──
log_step "Saving connection manifest"

MANIFEST_DIR="${HOME}/.life-agents/instances"
mkdir -p "$MANIFEST_DIR"

MANIFEST_FILE="${MANIFEST_DIR}/${INSTANCE_NAME}.json"
cat > "$MANIFEST_FILE" << MANIFEST
{
  "instance_name": "${INSTANCE_NAME}",
  "project": "${GCP_PROJECT}",
  "zone": "${ZONE}",
  "machine_type": "${MACHINE}",
  "disk_size_gb": ${DISK_SIZE},
  "external_ip": "${EXTERNAL_IP}",
  "vm_user": "${VM_USER}",
  "connection_code": "${CONNECTION_CODE}",
  "client_name": "${CLIENT_NAME}",
  "client_slug": "${CLIENT_SLUG}",
  "provisioned_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "status": "active"
}
MANIFEST

log_ok "Manifest saved: ${MANIFEST_FILE}"

# ── Generate Client Setup Script ──
log_step "Generating client setup script"

CLIENT_SETUP_DIR="${HOME}/.life-agents/client-packages"
mkdir -p "$CLIENT_SETUP_DIR"

CLIENT_SETUP="${CLIENT_SETUP_DIR}/${CLIENT_SLUG}-setup.sh"
cat > "$CLIENT_SETUP" << CLIENT_SCRIPT
#!/usr/bin/env bash
# Example Agents Setup for: ${CLIENT_NAME}
# Connection Code: ${CONNECTION_CODE}
# Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)
set -euo pipefail

BOLD='\033[1m'
BLUE='\033[0;34m'
GREEN='\033[0;32m'
RESET='\033[0m'

VM_HOST="${EXTERNAL_IP}"
VM_USER="${VM_USER}"
CONNECTION_CODE="${CONNECTION_CODE}"
SSH_KEY="\${HOME}/.ssh/life-agents"
CONFIG_DIR="\${HOME}/.claude"
CONFIG_FILE="\${CONFIG_DIR}/life-agents.json"

echo -e "\${BOLD}\${BLUE}"
echo "╔══════════════════════════════════════╗"
echo "║     Example Agents · Device Setup       ║"
echo "╚══════════════════════════════════════╝"
echo -e "\${RESET}"
echo ""
echo "Client: ${CLIENT_NAME}"
echo "Connection code: ${CONNECTION_CODE}"
echo ""

# Generate SSH key
DEVICE_NAME=\$(hostname | tr '[:upper:]' '[:lower:]' | tr ' ' '-')

if [ ! -f "\${SSH_KEY}" ]; then
    echo "Generating SSH key..."
    mkdir -p "\$(dirname \${SSH_KEY})"
    ssh-keygen -t ed25519 -f "\${SSH_KEY}" -N "" -C "life-agents-\${DEVICE_NAME}" -q
    echo -e "\${GREEN}Key generated.\${RESET}"
else
    echo "SSH key already exists."
fi

# Register key on VM
echo ""
echo "Registering device with Example Agents VM..."
echo "You may be asked for a password (this is the last time)."
echo ""
ssh-copy-id -i "\${SSH_KEY}.pub" -o StrictHostKeyChecking=accept-new "\${VM_USER}@\${VM_HOST}" 2>/dev/null || {
    echo ""
    echo "Auto-register failed. Add this key manually:"
    echo ""
    cat "\${SSH_KEY}.pub"
    echo ""
    echo "On the VM: echo '<key>' >> /home/${VM_USER}/.ssh/authorized_keys"
    read -p "Press Enter once done..."
}

# Test
if ssh -o ConnectTimeout=10 -o BatchMode=yes -i "\${SSH_KEY}" "\${VM_USER}@\${VM_HOST}" echo "ok" 2>/dev/null; then
    echo -e "\${GREEN}Connected!\${RESET}"
else
    echo "Connection failed. Check your network and try again."
    exit 1
fi

# Save config
mkdir -p "\${CONFIG_DIR}"
cat > "\${CONFIG_FILE}" << CONFIG
{
  "vm_host": "\${VM_HOST}",
  "vm_user": "\${VM_USER}",
  "connection_code": "\${CONNECTION_CODE}",
  "ssh_key_path": "\${SSH_KEY}",
  "connected_at": "\$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "auto_connect": true,
  "device_name": "\${DEVICE_NAME}",
  "projects": []
}
CONFIG

# Sync
rsync -az -e "ssh -i \${SSH_KEY}" "\${VM_USER}@\${VM_HOST}:~/.claude/skills/" "\${CONFIG_DIR}/skills/" 2>/dev/null || true
rsync -az -e "ssh -i \${SSH_KEY}" "\${VM_USER}@\${VM_HOST}:~/.claude/CLAUDE.md" "\${CONFIG_DIR}/CLAUDE.md" 2>/dev/null || true

# Register device
ssh -i "\${SSH_KEY}" "\${VM_USER}@\${VM_HOST}" "
mkdir -p ~/life-agents/devices
cat > ~/life-agents/devices/\${DEVICE_NAME}.json << DEVJSON
{
  \"device_name\": \"\${DEVICE_NAME}\",
  \"registered_at\": \"\$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
  \"last_seen\": \"\$(date -u +%Y-%m-%dT%H:%M:%SZ)\"
}
DEVJSON
"

echo ""
echo -e "\${BOLD}\${GREEN}"
echo "╔══════════════════════════════════════╗"
echo "║         Setup Complete!              ║"
echo "╚══════════════════════════════════════╝"
echo -e "\${RESET}"
echo ""
echo "  Device:  \${DEVICE_NAME}"
echo "  VM:      \${VM_HOST}"
echo "  Code:    \${CONNECTION_CODE}"
echo ""
echo "Open Claude Code or Cowork — your skills and context"
echo "will sync automatically from the VM."
echo ""
echo "To set up another device, run this script again."
echo ""
CLIENT_SCRIPT

chmod +x "$CLIENT_SETUP"
log_ok "Client setup script: ${CLIENT_SETUP}"

# ── Summary ──
echo ""
echo -e "${BOLD}${GREEN}"
echo "╔══════════════════════════════════════════════════════╗"
echo "║              Provisioning Complete!                  ║"
echo "╚══════════════════════════════════════════════════════╝"
echo -e "${RESET}"
echo ""
echo -e "${BOLD}VM Details:${RESET}"
echo "  Instance:         ${INSTANCE_NAME}"
echo "  IP:               ${EXTERNAL_IP}"
echo "  Zone:             ${ZONE}"
echo "  Machine:          ${MACHINE}"
echo "  Disk:             ${DISK_SIZE}GB"
echo "  User:             ${VM_USER}"
echo ""
echo -e "${BOLD}Client Details:${RESET}"
echo "  Client:           ${CLIENT_NAME}"
echo "  Connection code:  ${CONNECTION_CODE}"
echo ""
echo -e "${BOLD}Monthly Cost Estimate:${RESET}"
if [ "$MACHINE" = "e2-micro" ]; then
    echo "  ~\$7 USD / ~\$140 MXN per month"
elif [ "$MACHINE" = "e2-small" ]; then
    echo "  ~\$15 USD / ~\$300 MXN per month"
elif [ "$MACHINE" = "e2-medium" ]; then
    echo "  ~\$25 USD / ~\$500 MXN per month"
else
    echo "  Check GCP pricing for ${MACHINE}"
fi
echo ""
echo -e "${BOLD}For the client:${RESET}"
echo "  Send them: ${CLIENT_SETUP}"
echo "  They run it on each device they want to connect."
echo "  That's it — no other steps needed."
echo ""
echo -e "${BOLD}To manage this VM:${RESET}"
echo "  SSH:    gcloud compute ssh ${INSTANCE_NAME} --zone=${ZONE}"
echo "  Stop:   gcloud compute instances stop ${INSTANCE_NAME} --zone=${ZONE}"
echo "  Start:  gcloud compute instances start ${INSTANCE_NAME} --zone=${ZONE}"
echo "  Delete: gcloud compute instances delete ${INSTANCE_NAME} --zone=${ZONE}"
echo ""
echo -e "${BOLD}Manifest:${RESET} ${MANIFEST_FILE}"
echo ""
