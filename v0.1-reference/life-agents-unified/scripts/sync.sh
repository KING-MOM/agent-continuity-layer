#!/usr/bin/env bash
# life-agents-sync — Runs on every Claude session startup
# Called by the bootstrap skill automatically
set -euo pipefail

CONFIG_FILE="${HOME}/.claude/life-agents.json"
QUEUE_FILE="${HOME}/.claude/life-agents-queue.jsonl"

# Exit silently if not configured
if [ ! -f "$CONFIG_FILE" ]; then
    exit 0
fi

# Parse config
VM_HOST=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['vm_host'])")
VM_USER=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['vm_user'])")
SSH_KEY=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['ssh_key_path'])")
SSH_KEY="${SSH_KEY/#\~/$HOME}"

# Test connection
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes -i "$SSH_KEY" "${VM_USER}@${VM_HOST}" echo "ok" &>/dev/null; then
    echo '{"status": "offline", "message": "VM unreachable. Working in local mode."}'
    exit 0
fi

# Replay any queued updates from offline mode
if [ -f "$QUEUE_FILE" ] && [ -s "$QUEUE_FILE" ]; then
    while IFS= read -r line; do
        echo "$line" | ssh -i "$SSH_KEY" "${VM_USER}@${VM_HOST}" \
            "python3 ~/life-agents/scripts/apply_update.py" 2>/dev/null || true
    done < "$QUEUE_FILE"
    > "$QUEUE_FILE"
fi

# Sync skills
rsync -az --delete -e "ssh -i ${SSH_KEY}" \
    "${VM_USER}@${VM_HOST}:~/.claude/skills/" \
    "${HOME}/.claude/skills/" 2>/dev/null || true

# Sync CLAUDE.md
rsync -az -e "ssh -i ${SSH_KEY}" \
    "${VM_USER}@${VM_HOST}:~/.claude/CLAUDE.md" \
    "${HOME}/.claude/CLAUDE.md" 2>/dev/null || true

# Sync settings
rsync -az -e "ssh -i ${SSH_KEY}" \
    "${VM_USER}@${VM_HOST}:~/.claude/settings.json" \
    "${HOME}/.claude/settings.json" 2>/dev/null || true

# Get project registry
REGISTRY=$(ssh -i "$SSH_KEY" "${VM_USER}@${VM_HOST}" \
    "cat ~/life-agents/sessions/registry.json" 2>/dev/null || echo '{"projects":[]}')

# Update device last_seen
DEVICE_NAME=$(hostname | tr '[:upper:]' '[:lower:]' | tr ' ' '-')
ssh -i "$SSH_KEY" "${VM_USER}@${VM_HOST}" \
    "python3 -c \"
import json, os
path = os.path.expanduser('~/life-agents/devices/${DEVICE_NAME}.json')
if os.path.exists(path):
    d = json.load(open(path))
    d['last_seen'] = '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
    json.dump(d, open(path, 'w'), indent=2)
\"" 2>/dev/null || true

# Output status for the skill to read
echo "{\"status\": \"connected\", \"vm_host\": \"${VM_HOST}\", \"device\": \"${DEVICE_NAME}\", \"registry\": ${REGISTRY}}"
