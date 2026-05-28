#!/usr/bin/env python3
"""M13.3 reference agent demo.

A deliberately tiny agent loop:
  read_context -> decide -> append_decision

No LLM, no worker queue, no MCP server required. This proves a local Python
agent can use the M13.2 SDK shape without learning this repo's internals.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent_continuity import Substrate


def _build_decision(context: dict[str, Any], *, repo: str) -> dict[str, Any]:
    identity = context.get("identity") if isinstance(context.get("identity"), dict) else {}
    milestone = context.get("milestone") if isinstance(context.get("milestone"), dict) else {}
    project_name = identity.get("name") or repo
    last_completed = milestone.get("last_completed") or "unknown"

    return {
        "repo": repo,
        "decision": (
            f"Reference agent read the {project_name} context snapshot and confirmed "
            "the Python SDK can append durable decisions without an LLM."
        ),
        "why": (
            "M13.3 must prove the smallest useful agent shape: read_context, "
            "make a deterministic local choice, and append_decision through the "
            "same canonical log future agents read. "
            f"The context reported last_completed={last_completed}."
        ),
        "refs": ["M13.3", "doc:docs/python-sdk.md", "doc:docs/north-star.md"],
    }


def cmd_run(args: argparse.Namespace) -> int:
    substrate = Substrate(root=REPO_ROOT, timeout=args.timeout)
    context = substrate.read_context()
    identity = context.get("identity") if isinstance(context.get("identity"), dict) else {}
    repo = args.repo or str(identity.get("name") or "unknown")
    draft = _build_decision(context, repo=repo)

    if args.dry_run:
        payload = {"dry_run": True, "adapter": args.adapter, "author": args.author, "draft": draft}
        print(json.dumps(payload, indent=2, sort_keys=True) if args.json else _render_dry_run(payload))
        return 0

    decision_id = substrate.append_decision(
        adapter=args.adapter,
        author=args.author,
        repo=draft["repo"],
        decision=draft["decision"],
        why=draft["why"],
        refs=draft["refs"],
    )
    payload = {"appended": True, "decision_id": decision_id, "adapter": args.adapter, "author": args.author, **draft}
    print(json.dumps(payload, indent=2, sort_keys=True) if args.json else _render_appended(payload))
    return 0


def _render_dry_run(payload: dict[str, Any]) -> str:
    draft = payload["draft"]
    return "\n".join([
        "reference-agent dry run",
        f"  adapter:  {payload['adapter']}",
        f"  author:   {payload['author']}",
        f"  repo:     {draft['repo']}",
        f"  decision: {draft['decision']}",
        f"  why:      {draft['why']}",
        f"  refs:     {', '.join(draft['refs'])}",
    ])


def _render_appended(payload: dict[str, Any]) -> str:
    return "\n".join([
        "reference-agent appended decision",
        f"  id:       {payload['decision_id']}",
        f"  adapter:  {payload['adapter']}",
        f"  author:   {payload['author']}",
        f"  repo:     {payload['repo']}",
        f"  decision: {payload['decision']}",
        f"  why:      {payload['why']}",
        f"  refs:     {', '.join(payload['refs'])}",
    ])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Reference agent demo: read_context -> decide -> append_decision without an LLM."
    )
    p.add_argument("--repo", help="decision repo scope; defaults to context identity.name")
    p.add_argument(
        "--adapter",
        default="codex",
        choices=["claude", "codex", "openclaw", "human", "chatgpt", "gemini", "grok", "kimi"],
        help="decision adapter token to write under (default: codex for local demo)",
    )
    p.add_argument("--author", default="reference-agent-demo", help="decision author label")
    p.add_argument("--timeout", type=int, default=30, help="SDK subprocess timeout seconds")
    p.add_argument("--dry-run", action="store_true", help="show the decision draft without appending")
    p.add_argument("--json", action="store_true", help="emit JSON")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
