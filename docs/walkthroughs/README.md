# Walkthroughs

One adapter per file. Each walkthrough answers the same seven questions, so a new user can pick the host they actually have and skip the rest.

Read [`../m9-adapter-pattern.md`](../m9-adapter-pattern.md) first if you want the contract before the recipe; come back here when you want to use it.

## Pick your adapter

| Walkthrough | When to read it |
|---|---|
| [chatgpt-web-bundle.md](chatgpt-web-bundle.md) | You're using ChatGPT, Gemini, Grok, or Kimi in a web chat with no filesystem or shell. |
| [claude-web-bundle.md](claude-web-bundle.md) | You're using Claude.ai web. Same bundle pattern as ChatGPT, different adapter brand. |
| [codex-local-shell.md](codex-local-shell.md) | You're running Codex (or any local LLM CLI) on a host with shell access. |
| [mika-openclaw-bridge.md](mika-openclaw-bridge.md) | You're using OpenClaw / Mika. The bridge is one adapter among many — not the center of the system. |
| [read-only-auditor.md](read-only-auditor.md) | You want to inspect continuity state (context, decisions, queue) without write authority. CI, monitoring, or a curious human. |
| [troubleshooting.md](troubleshooting.md) | Something rejected your bundle, your submit, or your ingest. Diagnostics table. |

## The seven questions every walkthrough answers

1. **What adapter am I?** (adapter_type, brand, transport, capabilities)
2. **What can I read?**
3. **What can I write?**
4. **What command does the operator run?**
5. **What artifact comes back?**
6. **What trust boundary applies?**
7. **How do I verify with doctor?**

## One thing to internalize

Bundles, MCP, shell, and the OpenClaw bridge are all *transports* over the same six-operation contract. The decision log, the worker queue, and the context snapshot are the same artifacts no matter which transport you use. Pick the transport that matches your host; the continuity stays the same.

See also:

- [`../m9-adapter-pattern.md`](../m9-adapter-pattern.md) — canonical adapter contract
- [`../../CHARTER.md`](../../CHARTER.md) — what this project is
- [`../roadmap.md`](../roadmap.md) — what comes next
