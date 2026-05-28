#!/usr/bin/env bash
# life-agents setup — One-time device registration
# Usage: curl -sL https://get.example-agents.local | bash
#    or: bash life-agents-setup.sh
set -euo pipefail

BOLD='\033[1m'
BLUE='\033[0;34m'
GREEN='\033[0;32m'
RED='\033[0;31m'
RESET='\033[0m'

CONFIG_DIR="${HOME}/.claude"
CONFIG_FILE="${CONFIG_DIR}/life-agents.json"
SSH_KEY="${HOME}/.ssh/life-agents"

echo -e "${BOLD}${BLUE}"
echo "╔══════════════════════════════════════╗"
echo "║     Example Agents Unified Sessions     ║"
echo "║          Device Setup                ║"
echo "╚══════════════════════════════════════╝"
echo -e "${RESET}"

# Check if already configured
if [ -f "$CONFIG_FILE" ]; then
    echo -e "${GREEN}This device is already connected to Example Agents.${RESET}"
    VM_HOST=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['vm_host'])")
    echo "  VM: $VM_HOST"
    echo ""
    read -p "Reconfigure? (y/N): " reconfigure
    if [ "$reconfigure" != "y" ] && [ "$reconfigure" != "Y" ]; then
        echo "Keeping existing configuration."
        exit 0
    fi
fi

# Get connection details
echo ""
echo -e "${BOLD}Enter your 6-character connection code:${RESET}"
echo "(You received this when you signed up for Example Agents)"
echo ""
read -p "Connection code: " CONNECTION_CODE

# Validate code format
if [ ${#CONNECTION_CODE} -ne 6 ]; then
    echo -e "${RED}Error: Connection code must be 6 characters.${RESET}"
    exit 1
fi

CONNECTION_CODE=$(echo "$CONNECTION_CODE" | tr '[:lower:]' '[:upper:]')

# Try to resolve via API (graceful fallback to manual)
echo ""
echo "Resolving connection code..."

API_RESPONSE=$(curl -sf "https://api.example-agents.local/v1/resolve/${CONNECTION_CODE}" 2>/dev/null || echo "")

if [ -n "$API_RESPONSE" ]; then
    VM_HOST=$(echo "$API_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['vm_host'])")
    VM_USER=$(echo "$API_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('vm_user','claude'))")
    echo -e "${GREEN}Resolved: ${VM_HOST}${RESET}"
else
    echo "API not available. Enter VM details manually:"
    read -p "VM IP address: " VM_HOST
    read -p "VM username (default: claude): " VM_USER
    VM_USER=${VM_USER:-claude}
fi

# Generate SSH key
echo ""
echo "Generating SSH key for this device..."
DEVICE_NAME=$(hostname | tr '[:upper:]' '[:lower:]' | tr ' ' '-')

if [ -f "$SSH_KEY" ]; then
    echo "SSH key already exists at ${SSH_KEY}"
    read -p "Regenerate? (y/N): " regen
    if [ "$regen" = "y" ] || [ "$regen" = "Y" ]; then
        rm -f "${SSH_KEY}" "${SSH_KEY}.pub"
        ssh-keygen -t ed25519 -f "$SSH_KEY" -N "" -C "life-agents-${DEVICE_NAME}" -q
        echo -e "${GREEN}New key generated.${RESET}"
    fi
else
    mkdir -p "$(dirname $SSH_KEY)"
    ssh-keygen -t ed25519 -f "$SSH_KEY" -N "" -C "life-agents-${DEVICE_NAME}" -q
    echo -e "${GREEN}Key generated: ${SSH_KEY}${RESET}"
fi

# Copy key to VM
echo ""
echo -e "${BOLD}Registering this device with the VM...${RESET}"
echo "You may be asked for the VM password (this is the last time)."
echo ""

ssh-copy-id -i "${SSH_KEY}.pub" -o StrictHostKeyChecking=accept-new "${VM_USER}@${VM_HOST}" 2>/dev/null || {
    echo ""
    echo -e "${RED}Could not auto-register. Trying manual method...${RESET}"
    echo ""
    echo "Copy this key to your VM's authorized_keys:"
    echo ""
    cat "${SSH_KEY}.pub"
    echo ""
    echo "On the VM, run:"
    echo "  echo '$(cat ${SSH_KEY}.pub)' >> ~/.ssh/authorized_keys"
    echo ""
    read -p "Press Enter once you've added the key..."
}

# Test connection
echo ""
echo "Testing connection..."
if ssh -o ConnectTimeout=10 -o BatchMode=yes -i "$SSH_KEY" "${VM_USER}@${VM_HOST}" echo "OK" 2>/dev/null; then
    echo -e "${GREEN}Connection successful!${RESET}"
else
    echo -e "${RED}Connection failed. Check your VM address and try again.${RESET}"
    exit 1
fi

# Initialize VM structure if needed
echo ""
echo "Checking VM setup..."

VM_INITIALIZED=$(ssh -i "$SSH_KEY" "${VM_USER}@${VM_HOST}" \
    "test -f ~/life-agents/sessions/registry.json && echo 'yes' || echo 'no'")

if [ "$VM_INITIALIZED" = "no" ]; then
    echo "First device ever — initializing Example Agents on VM..."
    ssh -i "$SSH_KEY" "${VM_USER}@${VM_HOST}" << 'REMOTE_INIT'
    mkdir -p ~/life-agents/sessions
    mkdir -p ~/life-agents/scripts
    mkdir -p ~/life-agents/backups
    mkdir -p ~/.claude/skills

    # Create empty registry
    echo '{"projects": []}' > ~/life-agents/sessions/registry.json

    # Create update script
    cat > ~/life-agents/scripts/update_registry.py << 'PYTHON_SCRIPT'
#!/usr/bin/env python3
import json, argparse, os

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--uuid', required=True)
    parser.add_argument('--last-active', required=True)
    parser.add_argument('--last-device', required=True)
    parser.add_argument('--last-interface', default='claude-code')
    parser.add_argument('--name', default=None)
    parser.add_argument('--description', default=None)
    parser.add_argument('--context-summary', default=None)
    args = parser.parse_args()

    registry_path = os.path.expanduser('~/life-agents/sessions/registry.json')
    with open(registry_path, 'r') as f:
        registry = json.load(f)

    project = None
    for p in registry['projects']:
        if p['uuid'] == args.uuid:
            project = p
            break

    if project is None:
        project = {
            'uuid': args.uuid,
            'name': args.name or args.uuid,
            'description': args.description or '',
            'created_at': args.last_active,
            'tags': []
        }
        registry['projects'].append(project)

    project['last_active'] = args.last_active
    project['last_device'] = args.last_device
    project['last_interface'] = args.last_interface
    if args.name:
        project['name'] = args.name
    if args.description:
        project['description'] = args.description
    if args.context_summary:
        project['context_summary'] = args.context_summary

    with open(registry_path, 'w') as f:
        json.dump(registry, f, indent=2)

    print(f"Updated project {args.uuid}")

if __name__ == '__main__':
    main()
PYTHON_SCRIPT
    chmod +x ~/life-agents/scripts/update_registry.py

    # Create apply_update script for offline queue replay
    cat > ~/life-agents/scripts/apply_update.py << 'APPLY_SCRIPT'
#!/usr/bin/env python3
import json, sys, os

def main():
    update = json.load(sys.stdin)
    action = update.get('action')
    project_uuid = update.get('project_uuid')
    
    session_dir = os.path.expanduser(f'~/life-agents/sessions/{project_uuid}')
    os.makedirs(session_dir, exist_ok=True)

    if action == 'update_context':
        with open(f'{session_dir}/context.md', 'w') as f:
            f.write(update['content'])
    elif action == 'append_decision':
        with open(f'{session_dir}/decisions.md', 'a') as f:
            f.write(update['content'] + '\n')
    elif action == 'update_history':
        with open(f'{session_dir}/history.md', 'w') as f:
            f.write(update['content'])
    
    print(f"Applied {action} to {project_uuid}")

if __name__ == '__main__':
    main()
APPLY_SCRIPT
    chmod +x ~/life-agents/scripts/apply_update.py

    # Hourly backup cron
    (crontab -l 2>/dev/null; echo "0 * * * * tar czf ~/life-agents/backups/backup-\$(date +\%Y\%m\%d-\%H).tar.gz -C ~ life-agents/sessions/ 2>/dev/null") | crontab -

    # Clean old backups (keep 7 days)
    (crontab -l 2>/dev/null; echo "0 3 * * * find ~/life-agents/backups/ -name 'backup-*.tar.gz' -mtime +7 -delete 2>/dev/null") | crontab -

    echo "VM initialized."
REMOTE_INIT
    echo -e "${GREEN}VM initialized successfully.${RESET}"
else
    echo "VM already initialized. Syncing..."
fi

# Save local config
mkdir -p "$CONFIG_DIR"
cat > "$CONFIG_FILE" << CONFIG_JSON
{
  "vm_host": "${VM_HOST}",
  "vm_user": "${VM_USER}",
  "connection_code": "${CONNECTION_CODE}",
  "ssh_key_path": "${SSH_KEY}",
  "connected_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "auto_connect": true,
  "device_name": "${DEVICE_NAME}",
  "projects": []
}
CONFIG_JSON

# Initial sync — pull skills and CLAUDE.md from VM
echo ""
echo "Syncing skills and configuration from VM..."
rsync -az -e "ssh -i ${SSH_KEY}" \
    "${VM_USER}@${VM_HOST}:~/.claude/skills/" \
    "${CONFIG_DIR}/skills/" 2>/dev/null || true

rsync -az -e "ssh -i ${SSH_KEY}" \
    "${VM_USER}@${VM_HOST}:~/.claude/CLAUDE.md" \
    "${CONFIG_DIR}/CLAUDE.md" 2>/dev/null || true

# Register device on VM
ssh -i "$SSH_KEY" "${VM_USER}@${VM_HOST}" << REGISTER_DEVICE
mkdir -p ~/life-agents/devices
cat > ~/life-agents/devices/${DEVICE_NAME}.json << DEVICE_JSON
{
  "device_name": "${DEVICE_NAME}",
  "registered_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "ssh_key_fingerprint": "$(ssh-keygen -lf ${SSH_KEY}.pub | awk '{print $2}')",
  "last_seen": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
DEVICE_JSON
REGISTER_DEVICE

# Done
echo ""
echo -e "${BOLD}${GREEN}"
echo "╔══════════════════════════════════════╗"
echo "║       Setup Complete!                ║"
echo "╚══════════════════════════════════════╝"
echo -e "${RESET}"
echo ""
echo "  Connection code:  ${CONNECTION_CODE}"
echo "  VM address:       ${VM_HOST}"
echo "  Device:           ${DEVICE_NAME}"
echo "  SSH key:          ${SSH_KEY}"
echo ""
echo -e "${BOLD}What happens now:${RESET}"
echo "  • Every time you open Claude, your skills and context sync automatically"
echo "  • Switch to another device → enter the same code → same environment"
echo "  • All your work is backed up hourly on the VM"
echo ""
echo -e "${BOLD}To set up another device:${RESET}"
echo "  Run this same script and enter code: ${CONNECTION_CODE}"
echo ""
echo -e "${BOLD}To start working:${RESET}"
echo "  Just open Claude Code or Cowork as usual."
echo ""
