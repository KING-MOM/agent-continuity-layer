# Multi-Channel Integration Guide

## Overview

Example Agents Unified Sessions can extend beyond Claude Code/Cowork to messaging
channels like messaging channel, Telegram, and custom webhooks. All channels share the
same session memory on the VM, ensuring consistent context across interfaces.

## Architecture

```
[messaging channel]  ─┐
[Telegram]  ─┤──→  [Channel Router (VM)]  ──→  [Session Memory]  ←──  [Claude Code/Cowork]
[Webhook]   ─┘          │                        ~/life-agents/sessions/{uuid}/
                        │                            ├── context.md
                        ↓                            ├── decisions.md
                  [Anthropic API]                    └── history.md
                  (for reasoning)
```

## How It Works

1. **Incoming message** arrives on any channel (messaging channel, Telegram, etc.)
2. **Channel Router** identifies the project UUID from the message context
   - By explicit project mention ("about Blue Sky")
   - By sender identity (mapped to project in config)
   - By keyword matching against known project names/tags
3. **Router reads** `context.md` and `decisions.md` from the session directory
4. **Anthropic API** generates a response with full project context
5. **Response sent** back through the originating channel
6. **Interaction logged** to `history.md` with channel identifier

## Channel Router Setup

The channel router runs as a lightweight Python service on the VM:

```
~/life-agents/
├── channels/
│   ├── router.py          # Main routing service
│   ├── whatsapp.py        # messaging channel adapter (via whatsmeow/wacli)
│   ├── telegram.py        # Telegram adapter (via python-telegram-bot)
│   └── webhook.py         # Generic webhook adapter
├── config/
│   └── channels.json      # Channel configuration
```

### channels.json

```json
{
  "channels": [
    {
      "type": "whatsapp",
      "enabled": true,
      "phone": "+15550000000",
      "default_project": null,
      "contact_project_map": {
        "+15551111111": "proj-blue-sky-001"
      }
    },
    {
      "type": "telegram",
      "enabled": true,
      "bot_token": "BOT_TOKEN_HERE",
      "default_project": null,
      "chat_project_map": {
        "12345678": "proj-blue-sky-001"
      }
    },
    {
      "type": "webhook",
      "enabled": true,
      "port": 8080,
      "auth_token": "WEBHOOK_SECRET"
    }
  ]
}
```

## Session Memory Format

All channels read and write to the same session files. The format includes
a channel identifier so Claude knows the origin:

### history.md entry from messaging channel:
```markdown
## [2026-05-19T16:45:00Z] User (via messaging channel)
¿Cómo va Blue Sky?

## [2026-05-19T16:45:02Z] Agent (via messaging channel)
Completamos la Fase 1. Ahora estamos trabajando en las integraciones de API...
```

### history.md entry from Claude Code:
```markdown
## [2026-05-19T14:30:00Z] User (via Claude Code on operator-device)
Let's refactor the API integration module.

## [2026-05-19T14:30:15Z] Claude (via Claude Code)
I've restructured the module into three files: auth.py, endpoints.py, and models.py...
```

## Consistency Rules

1. **Read before respond**: Every channel adapter MUST read `context.md` and
   `decisions.md` before generating a response.
2. **Write after respond**: Every interaction MUST be appended to `history.md`.
3. **Decision capture**: If a decision is made via any channel, it MUST be
   appended to `decisions.md`.
4. **No contradictions**: If a decision was already made (logged in decisions.md),
   the agent must respect it regardless of which channel the question comes from.
5. **Channel attribution**: Every history entry includes the channel identifier
   so the full interaction timeline is reconstructable.

## Running the Router

```bash
# Start the channel router (runs as systemd service)
sudo systemctl start life-agents-router

# Or manually for testing
cd ~/life-agents/channels
python3 router.py --config ../config/channels.json
```

## Adding a New Channel

1. Create an adapter file in `~/life-agents/channels/` that implements:
   - `receive_message(raw_message) -> ParsedMessage`
   - `send_response(channel_id, text) -> bool`
2. Register it in `channels.json`
3. The router handles project matching and session memory automatically
