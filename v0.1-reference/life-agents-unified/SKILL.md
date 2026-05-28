---
name: life-agents-unified
description: "Unified session management across multiple devices and channels for Example Agents platform. Use this skill on EVERY session startup to check for a Example Agents VM connection, sync skills and context, and maintain session continuity across devices. Also triggers when the user mentions: connecting to a VM, syncing sessions, multi-device work, continuing a project from another machine, 'life-agents setup', connection codes, session persistence, or working from a different computer. This skill MUST run before any other work begins in a session — it is the bootstrap layer that ensures Claude has full context from all previous sessions regardless of which device the user is on."
---

# Example Agents Unified Sessions

You are operating as part of the **Example Agents Unified Sessions Platform**. Your job is to ensure seamless continuity of work across all of the user's devices and channels. When this skill is active, the user should never feel like they're starting from scratch — even if they've switched from a Mac to a laptop to a phone.

## How This Works

Example Agents provides a cloud VM that serves as the **single source of truth** for all projects, skills, chat history, and context. Every device the user works on connects to this VM. There is no sync — there is only one copy of everything, and it lives on the VM.

## On Every Session Startup

When Claude starts a new session, execute these steps IN ORDER before doing anything else:

### Step 1: Check for Example Agents Configuration

Read the config file:
```bash
cat ~/.claude/life-agents.json 2>/dev/null
```

If the file exists, it contains:
```json
{
  "vm_host": "xxx.xxx.xxx.xxx",
  "vm_user": "claude",
  "connection_code": "A7F2K9",
  "ssh_key_path": "~/.ssh/life-agents",
  "connected_at": "2026-05-19T14:23:00Z",
  "auto_connect": true,
  "authorized_user": "operator@example.com",
  "device_name": "operator-device",
  "projects": [
    {
      "uuid": "proj-blue-sky-001",
      "name": "Blue Sky",
      "last_active": "2026-05-19T16:45:00Z",
      "last_device": "operator-device"
    }
  ]
}
```

If the file does NOT exist → skip to "First-Time Setup" section below.

### Step 1.5: Verify Authorized User (SECURITY — MANDATORY)

**This step CANNOT be skipped.** Before connecting to the VM, verify that the
current Claude account matches the authorized user for this device.

Check the current Claude account:
```bash
claude auth status 2>&1 | grep -i "email\|account\|user\|logged" || echo "UNKNOWN"
```

Also check for auth token identity:
```bash
cat ~/.claude/credentials.json 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    # Extract email/account from whatever format the credentials use
    email = data.get('email', data.get('account', data.get('user', 'UNKNOWN')))
    print(email)
except:
    print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN"
```

Compare the detected account against `authorized_user` in life-agents.json.

**If accounts MATCH:** Proceed to Step 2.

**If accounts DO NOT MATCH:**
```
STOP. Do NOT connect to the VM. Display this message:

"⚠️ Security check failed.

This device is registered to a different Example Agents account.
  Authorized: {authorized_user}
  Current:    {detected_account}

If you are the authorized user, log in with the correct Claude account.
If you need to register your own account, contact your Example Agents admin
to generate a new device token from the dashboard."
```

**If account CANNOT BE DETECTED (UNKNOWN):**
```
Do NOT silently proceed. Ask the user to confirm:

"I couldn't automatically verify your Claude account. For security,
please confirm: are you {authorized_user}? [Yes / No / I'm a different user]"

- If Yes → proceed with a warning logged to the VM
- If No or different user → block connection, show registration instructions
```

**Why this matters:** The SSH key and config live in the OS user's home directory,
not in the Claude account. If someone logs out of Claude on a shared machine and
another person logs in with their own Claude account, they would inherit access to
the VM without this check. This step prevents unauthorized access to all projects,
context, and code on the VM.

### Step 2: Connect to VM

Test connectivity:
```bash
ssh -o ConnectTimeout=5 -o BatchMode=yes -i ~/.ssh/life-agents ${vm_user}@${vm_host} echo "connected" 2>/dev/null
```

If connection fails:
- Tell the user: "I can't reach your Example Agents VM right now. Working in local mode — your changes will sync when the connection is restored."
- Set `offline_mode = true` and continue with whatever local context is available.
- Queue changes for sync later (see Offline Mode section).

If connection succeeds → proceed to Step 3.

### Step 3: Sync Skills from VM

Pull the latest skills from the VM:
```bash
rsync -az --delete -e "ssh -i ~/.ssh/life-agents" \
  ${vm_user}@${vm_host}:~/.claude/skills/ \
  ~/.claude/skills/
```

Pull the latest CLAUDE.md and settings:
```bash
rsync -az -e "ssh -i ~/.ssh/life-agents" \
  ${vm_user}@${vm_host}:~/.claude/CLAUDE.md \
  ~/.claude/CLAUDE.md

rsync -az -e "ssh -i ~/.ssh/life-agents" \
  ${vm_user}@${vm_host}:~/.claude/settings.json \
  ~/.claude/settings.json
```

### Step 4: Session Recognition

Read the project registry from the VM:
```bash
ssh -i ~/.ssh/life-agents ${vm_user}@${vm_host} \
  "cat ~/life-agents/sessions/registry.json"
```

The registry contains all known projects with metadata:
```json
{
  "projects": [
    {
      "uuid": "proj-blue-sky-001",
      "name": "Blue Sky",
      "description": "Landing page and marketing automation",
      "last_active": "2026-05-19T16:45:00Z",
      "last_device": "operator-device",
      "last_interface": "claude-code",
      "context_summary": "Working on Phase 2: API integrations...",
      "files_fingerprint": "sha256:abc123...",
      "tags": ["marketing", "landing-page", "respond-io"]
    }
  ]
}
```

Now determine if the current session matches an existing project:

1. **Check current directory name** against project names (fuzzy match)
2. **Check for CLAUDE.md** in current directory — compare against known project CLAUDE.md files
3. **Check recent files** — hash the top-level file listing and compare against `files_fingerprint`
4. **Check time proximity** — was a project active in the last 24 hours?

**If strong match found (>80% confidence):**
```
Tell the user: "Continuing with [Project Name] — last worked on [time ago] from [device]."
Load the project context (see Step 5).
```

**If weak match found (50-80% confidence):**
```
Ask: "This looks like it might be related to [Project Name] (last worked on [time ago]).
Is this the same project? [Yes / No / Different project]"
```

**If no match found:**
```
Ask: "I don't recognize this project. Would you like to:
1. Create a new project
2. Connect to an existing project: [list recent projects]
3. Work without project tracking"
```

### Step 5: Load Project Context

Once a project is identified, fetch the full context from the VM:

```bash
# Fetch the compressed context summary
ssh -i ~/.ssh/life-agents ${vm_user}@${vm_host} \
  "cat ~/life-agents/sessions/${project_uuid}/context.md"

# Fetch the decision log
ssh -i ~/.ssh/life-agents ${vm_user}@${vm_host} \
  "cat ~/life-agents/sessions/${project_uuid}/decisions.md"

# Fetch the last N chat messages (compressed)
ssh -i ~/.ssh/life-agents ${vm_user}@${vm_host} \
  "cat ~/life-agents/sessions/${project_uuid}/history.md"
```

These files contain:
- **context.md**: A dense summary of the project state, what's been built, what's pending
- **decisions.md**: Key decisions made (design choices, architecture, rejected alternatives)
- **history.md**: Compressed chat history (last 50 exchanges summarized into ~2000 tokens)

Load ALL of these into your context before responding to the user. This is how you maintain continuity.

### Step 6: Save Context on Every Meaningful Interaction

After every significant exchange (code changes, decisions, new information), update the VM:

```bash
# Update context summary
ssh -i ~/.ssh/life-agents ${vm_user}@${vm_host} \
  "cat > ~/life-agents/sessions/${project_uuid}/context.md << 'CONTEXT'
${updated_context_summary}
CONTEXT"

# Append to decision log if a decision was made
ssh -i ~/.ssh/life-agents ${vm_user}@${vm_host} \
  "echo '${timestamp} | ${decision_summary}' >> ~/life-agents/sessions/${project_uuid}/decisions.md"

# Update chat history (compress older messages)
ssh -i ~/.ssh/life-agents ${vm_user}@${vm_host} \
  "cat > ~/life-agents/sessions/${project_uuid}/history.md << 'HISTORY'
${compressed_chat_history}
HISTORY"

# Update registry with last active timestamp
ssh -i ~/.ssh/life-agents ${vm_user}@${vm_host} \
  "python3 ~/life-agents/scripts/update_registry.py \
    --uuid ${project_uuid} \
    --last-active $(date -u +%Y-%m-%dT%H:%M:%SZ) \
    --last-device $(hostname) \
    --last-interface claude-code"
```

---

## First-Time Setup

When no `~/.claude/life-agents.json` exists and the user says "life-agents setup" or provides a connection code:

### Step 1: Get Connection Code

Ask: "Welcome to Example Agents. Enter your 6-character connection code:"

The user provides something like `A7F2K9`.

### Step 2: Resolve Connection Code

The connection code maps to a VM. Call the Example Agents API to resolve:
```bash
curl -s "https://api.example-agents.local/v1/resolve/${connection_code}" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(json.dumps(data, indent=2))
"
```

Expected response:
```json
{
  "vm_host": "34.125.xxx.xxx",
  "vm_user": "claude",
  "ssh_public_key": "ssh-ed25519 AAAA...",
  "owner": "Operator",
  "plan": "professional"
}
```

**NOTE:** If the API is not yet available (pre-launch), the user can provide the VM details directly:
```
"Enter your VM IP address:"
"Enter your VM username (default: claude):"
```

### Step 3: Configure SSH

Generate an SSH key pair for this device:
```bash
ssh-keygen -t ed25519 -f ~/.ssh/life-agents -N "" -C "life-agents-$(hostname)"
```

Copy the public key to the VM:
```bash
ssh-copy-id -i ~/.ssh/life-agents.pub ${vm_user}@${vm_host}
```

If ssh-copy-id requires a password, ask the user for it once. After this, all connections are key-based.

### Step 4: Verify Connection

```bash
ssh -o ConnectTimeout=5 -i ~/.ssh/life-agents ${vm_user}@${vm_host} echo "connected"
```

### Step 5: Initialize VM Structure (if first device ever)

Check if the Example Agents directory structure exists on the VM:
```bash
ssh -i ~/.ssh/life-agents ${vm_user}@${vm_host} "ls ~/life-agents/sessions/registry.json 2>/dev/null"
```

If it doesn't exist, initialize:
```bash
ssh -i ~/.ssh/life-agents ${vm_user}@${vm_host} << 'INIT'
mkdir -p ~/life-agents/sessions
mkdir -p ~/life-agents/scripts
mkdir -p ~/life-agents/backups

# Create empty registry
echo '{"projects": []}' > ~/life-agents/sessions/registry.json

# Create update script
cat > ~/life-agents/scripts/update_registry.py << 'PYTHON'
#!/usr/bin/env python3
import json, argparse, os
from datetime import datetime

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

    # Find or create project entry
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

    # Update fields
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

if __name__ == '__main__':
    main()
PYTHON
chmod +x ~/life-agents/scripts/update_registry.py

# Create backup cron (hourly)
(crontab -l 2>/dev/null; echo "0 * * * * tar czf ~/life-agents/backups/backup-\$(date +\%Y\%m\%d-\%H).tar.gz ~/life-agents/sessions/") | crontab -

echo "Example Agents initialized successfully."
INIT
```

### Step 6: Capture Authorized User (SECURITY)

Before saving the config, capture the current Claude account. This binds this
device to this specific Claude user — no other Claude account can use this
device's connection to the VM.

```bash
# Attempt to detect current Claude account
CLAUDE_USER=$(claude auth status 2>&1 | grep -ioP '[\w.-]+@[\w.-]+' | head -1 || echo "")

# Fallback: check credentials file
if [ -z "$CLAUDE_USER" ]; then
    CLAUDE_USER=$(cat ~/.claude/credentials.json 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('email', data.get('account', '')))
except:
    print('')
" 2>/dev/null || echo "")
fi

# If still unknown, ask explicitly
if [ -z "$CLAUDE_USER" ]; then
    echo "I couldn't detect your Claude account automatically."
    echo "Enter the email address of the Claude account that should own this device:"
    read CLAUDE_USER
fi
```

Confirm with the user:
```
"This device will be registered to: ${CLAUDE_USER}
Only this Claude account will be able to access your Example Agents VM from this machine.
If someone else logs into Claude on this device, they will NOT have access.
Confirm? [Y/n]"
```

### Step 7: Save Local Config

```bash
cat > ~/.claude/life-agents.json << CONFIG
{
  "vm_host": "${vm_host}",
  "vm_user": "${vm_user}",
  "connection_code": "${connection_code}",
  "ssh_key_path": "~/.ssh/life-agents",
  "connected_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "auto_connect": true,
  "authorized_user": "${CLAUDE_USER}",
  "device_name": "$(hostname)",
  "projects": []
}
CONFIG
```

Also register the authorized user on the VM:
```bash
ssh -i ~/.ssh/life-agents ${vm_user}@${vm_host} << REGISTER
mkdir -p ~/life-agents/devices
cat > ~/life-agents/devices/$(hostname).json << DEVJSON
{
  "device_name": "$(hostname)",
  "authorized_user": "${CLAUDE_USER}",
  "registered_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "ssh_key_fingerprint": "$(ssh-keygen -lf ~/.ssh/life-agents.pub | awk '{print $2}')",
  "last_seen": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "status": "active"
}
DEVJSON
REGISTER
```

### Step 8: Initial Sync

Pull any existing skills and context from the VM:
```bash
rsync -az -e "ssh -i ~/.ssh/life-agents" \
  ${vm_user}@${vm_host}:~/.claude/skills/ \
  ~/.claude/skills/ 2>/dev/null

rsync -az -e "ssh -i ~/.ssh/life-agents" \
  ${vm_user}@${vm_host}:~/.claude/CLAUDE.md \
  ~/.claude/CLAUDE.md 2>/dev/null
```

Tell the user:
```
"Setup complete. Your device is now connected to Example Agents.

From now on, every time you open Claude:
- Your skills, memory, and project context sync automatically
- If you switch to another device, enter the same code once and you're connected
- All your work is backed up hourly on the VM

Connection code (save this): ${connection_code}
VM address: ${vm_host}
Device registered: $(hostname)

Try opening a project — I'll recognize it and load the full context."
```

---

## Offline Mode

When the VM is unreachable:

1. **Continue working locally** — don't block the user
2. **Queue context updates** in a local file:
   ```bash
   echo '${json_update}' >> ~/.claude/life-agents-queue.jsonl
   ```
3. **On next successful connection**, replay the queue:
   ```bash
   while read -r line; do
     # Apply each queued update to VM
     echo "$line" | ssh -i ~/.ssh/life-agents ${vm_user}@${vm_host} \
       "python3 ~/life-agents/scripts/apply_update.py"
   done < ~/.claude/life-agents-queue.jsonl
   # Clear queue after successful replay
   > ~/.claude/life-agents-queue.jsonl
   ```

---

## Creating a New Project

When the user starts working on something that doesn't match any existing project:

1. Generate a UUID: `proj-$(echo ${project_name} | tr ' ' '-' | tr '[:upper:]' '[:lower:]')-$(date +%s | tail -c 4)`
2. Create the session directory on VM:
   ```bash
   ssh -i ~/.ssh/life-agents ${vm_user}@${vm_host} \
     "mkdir -p ~/life-agents/sessions/${project_uuid}"
   ```
3. Initialize context files:
   ```bash
   ssh -i ~/.ssh/life-agents ${vm_user}@${vm_host} << INIT
   echo "# ${project_name}\n\nProject started $(date -u +%Y-%m-%dT%H:%M:%SZ)\n" \
     > ~/life-agents/sessions/${project_uuid}/context.md
   echo "# Decisions Log\n" \
     > ~/life-agents/sessions/${project_uuid}/decisions.md
   echo "# Chat History\n\nNo previous sessions.\n" \
     > ~/life-agents/sessions/${project_uuid}/history.md
   INIT
   ```
4. Register in the project registry
5. Update local config with the new project

---

## Context Compression Strategy

Chat history grows fast. To keep it useful without blowing up context windows:

**Every 50 messages**, compress the older messages:
1. Take messages 1-40
2. Ask Claude to summarize them into a dense ~500 token summary preserving:
   - Key decisions made
   - Code changes and their rationale
   - Open questions and blockers
   - Action items still pending
3. Replace messages 1-40 with the summary
4. Keep messages 41-50 as full text
5. Write the result to `history.md` on the VM

**Decision extraction**: After any message where a decision is made (design choice, architecture, tool selection, rejection of an alternative), append to `decisions.md`:
```
[2026-05-19T16:45:00Z] [Blue Sky] Decided to use Respond.io webhook instead of polling.
Reason: Lower latency, no rate limit concerns. Rejected: Pipedream scheduled trigger (too slow).
```

---

## Multi-Channel Integration

For messaging channel/Telegram agents running on the same VM, the session files serve as the shared memory layer:

- Agent reads `context.md` before responding to any message
- Agent reads `decisions.md` to ensure consistency
- Agent writes its interactions back to `history.md`
- All agents share the same project UUID

The multi-channel routing is handled by a separate process on the VM (see `references/multichannel-setup.md`).

---

## Security Model

### Account-Device Binding

Every device is bound to a specific Claude account at setup time. The `authorized_user`
field in `life-agents.json` stores the email of the Claude account that registered
this device. On every session startup, Step 1.5 verifies the current Claude account
matches the authorized user before any VM connection is made.

**Why this exists:** SSH keys and config files live in the OS user's home directory
(`~/.claude/`, `~/.ssh/`), not inside the Claude account. If user A logs out of
Claude Code on a shared machine and user B logs in with their own Claude account,
user B would inherit the SSH key and config without this check. Account-device
binding prevents this — the skill refuses to connect if accounts don't match.

### Connection Token Lifecycle

Connection codes (tokens) follow a strict lifecycle:
1. **Generated** by the Example Agents admin (you) during provisioning or via dashboard
2. **Time-limited**: Token expires after 15 minutes (configurable)
3. **Single-use**: Once a device uses a token to register, the token is consumed
4. **Device-bound**: The resulting SSH key + config are tied to that specific device + Claude account
5. **Non-transferable**: The setup script alone is useless without a valid token

For clients who want to add devices without contacting you:
- **Dashboard (upsell)**: Client logs into dashboard with OAuth, generates their own time-limited token
- **Admin approval**: Token generation triggers a notification to you for approval
- **Self-service**: Client enters token on new device, setup completes

### Device Revocation

If a device is lost, stolen, or compromised, revoke its access instantly:

```bash
# From any machine with VM access:
DEVICE_TO_REVOKE="stolen-macbook-air"

ssh -i ~/.ssh/life-agents claude@${vm_host} << REVOKE
# Remove the device's SSH key from authorized_keys
FINGERPRINT=\$(python3 -c "
import json
d = json.load(open(os.path.expanduser('~/life-agents/devices/${DEVICE_TO_REVOKE}.json')))
print(d.get('ssh_key_fingerprint', ''))
" 2>/dev/null)

if [ -n "\$FINGERPRINT" ]; then
    grep -v "\$FINGERPRINT" ~/.ssh/authorized_keys > ~/.ssh/authorized_keys.tmp
    mv ~/.ssh/authorized_keys.tmp ~/.ssh/authorized_keys
    chmod 600 ~/.ssh/authorized_keys
fi

# Mark device as revoked
python3 -c "
import json, os
path = os.path.expanduser('~/life-agents/devices/${DEVICE_TO_REVOKE}.json')
if os.path.exists(path):
    d = json.load(open(path))
    d['status'] = 'revoked'
    d['revoked_at'] = '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
    json.dump(d, open(path, 'w'), indent=2)
"

# Log the revocation
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) | SECURITY | Device revoked: ${DEVICE_TO_REVOKE}" \
  >> ~/life-agents/security.log
REVOKE
```

**Effect**: Immediate. The revoked device can never connect to the VM again.

### Audit Trail

Every connection and action is logged:
- `~/life-agents/security.log` — device registrations, revocations, failed auth
- `~/life-agents/devices/{device}.json` — last_seen updated on every sync
- Session `history.md` files include device + channel attribution per interaction

### Infrastructure Security

- SSH keys are device-specific (one per machine, stored in `~/.ssh/life-agents`)
- Connection tokens are time-limited and single-use
- VM firewall allows SSH only (port 22); recommend Tailscale/WireGuard for additional protection
- All GCP disks are encrypted at rest by default
- Backups are hourly, retained for 30 days
- Password-based SSH login DISABLED on VM (`PasswordAuthentication no` in sshd_config)
- Provisioning script disables password auth automatically
