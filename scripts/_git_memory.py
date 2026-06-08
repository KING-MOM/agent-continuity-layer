#!/usr/bin/env python3
"""Git-backed durable memory repo helper.

Continuity primitive: context recovery + decision log + artifact memory.

This is the low-risk sync path: Git stores curated, durable memory records;
host-local state keeps raw sessions, credentials, queues, and trust policy.
No symlinks. No network. No automatic commits.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
VERSION_PATH = REPO_ROOT / "core" / "VERSION"

_XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or (HOME / ".config"))
_XDG_STATE_HOME = Path(os.environ.get("XDG_STATE_HOME") or (HOME / ".local" / "state"))

CONFIG_ROOT = _XDG_CONFIG_HOME / "agent-continuity"
STATE_ROOT = _XDG_STATE_HOME / "agent-continuity"

DEFAULT_MEMORY_REPO = Path(os.environ.get("AGENT_CONTINUITY_MEMORY_REPO", "")).expanduser()
if not str(DEFAULT_MEMORY_REPO):
    DEFAULT_MEMORY_REPO = Path.cwd() / "agent-memory"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _version() -> str:
    try:
        return VERSION_PATH.read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", "git not found on PATH"
    except subprocess.TimeoutExpired:
        return 124, "", "git command timed out"
    except OSError as e:
        return 126, "", f"could not run git: {e}"


def _write_if_missing(path: Path, content: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _copy_if_exists(src: Path, dst: Path) -> bool:
    if not src.exists() or not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _git_porcelain(root: Path) -> str:
    rc, out, err = _run_git(["status", "--porcelain"], root)
    if rc != 0:
        raise RuntimeError(f"git status failed: {err.strip()}")
    return out


def _git_head(root: Path, ref: str = "HEAD") -> str | None:
    rc, out, _ = _run_git(["rev-parse", "--verify", ref], root)
    if rc != 0:
        return None
    return out.strip() or None


def _secret_findings(root: Path) -> list[dict[str, Any]]:
    """High-confidence denylist scan for files about to enter memory Git.

    The memory repo intentionally contains policy text mentioning words like
    "token" or "private key". Those are not secrets. This scanner flags only
    credential-shaped values and PEM blocks.
    """
    patterns: list[tuple[str, re.Pattern[str]]] = [
        ("pem-private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
        ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
        ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
        ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
        (
            "assigned-secret",
            re.compile(
                r"(?i)\b(api[_-]?key|secret|token|password|client_secret)\b"
                r"\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{16,}"
            ),
        ),
    ]
    findings: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append({"path": str(path.relative_to(root)), "kind": "binary-file"})
            continue
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for kind, pattern in patterns:
                if pattern.search(line):
                    findings.append({
                        "path": str(path.relative_to(root)),
                        "line": lineno,
                        "kind": kind,
                    })
    return findings


def _memory_repo_readme() -> str:
    return """# Agent Memory

Private Git-backed continuity memory.

This repo stores curated records that should survive across machines and agent
sessions:

- `projects/` — registered projects and related repo identities
- `contexts/` — compact project context snapshots and hand-curated summaries
- `decisions/` — append-only decision logs
- `handoffs/` — durable handoff/result summaries
- `artifacts/` — artifact index entries, not raw secret-bearing blobs
- `devices/` — descriptive device identities only
- `metadata/` — export manifests and sync notes

Do not store credentials, e.firma material, OAuth tokens, raw chat/session
dumps, browser profiles, worker queue spools, or machine-local trust grants.
New devices should start read-only until their write authority is explicitly
approved outside this repo.
"""


def _memory_gitignore() -> str:
    return """# Secrets and raw runtime stores must never enter memory Git.
.env
.env.*
*.pem
*.key
*.p12
*.cer
*.crt
*.der
*.pfx
*.sqlite
*.db
*.db-*

# Raw agent/session/cache stores are too broad for curated memory.
raw/
sessions/
credentials/
browser-profiles/
queue/
spool/
*.lock
*.tmp
.DS_Store
"""


def _ensure_layout(root: Path) -> list[str]:
    created: list[str] = []
    root.mkdir(parents=True, exist_ok=True)
    for rel in (
        "projects",
        "contexts",
        "decisions",
        "handoffs",
        "artifacts",
        "devices",
        "metadata",
    ):
        p = root / rel / ".gitkeep"
        if _write_if_missing(p, ""):
            created.append(str(p.relative_to(root)))
    if _write_if_missing(root / "README.md", _memory_repo_readme()):
        created.append("README.md")
    if _write_if_missing(root / ".gitignore", _memory_gitignore()):
        created.append(".gitignore")
    return created


def _require_memory_repo(path: Path) -> Path:
    root = path.expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"error: memory repo does not exist: {root}")
    if not (root / ".git").exists():
        raise SystemExit(f"error: not a Git repo: {root} (run git-memory init first)")
    return root


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser().resolve()
    created = _ensure_layout(root)
    git_initialized = False
    if not (root / ".git").exists():
        rc_init, _, err_init = _run_git(["init"], root)
        if rc_init != 0:
            print(f"error: git init failed: {err_init.strip()}", file=sys.stderr)
            return 1
        git_initialized = True
    report = {
        "status": "ok",
        "path": str(root),
        "git_initialized": git_initialized,
        "created": created,
        "next": [
            "git-memory export --path <repo>",
            "review the diff",
            "git -C <repo> add . && git -C <repo> commit -m 'Initial continuity memory export'",
        ],
    }
    print(json.dumps(report, indent=2))
    return 0


def _export_projects(root: Path) -> int:
    count = 0
    for base in (CONFIG_ROOT / "projects", STATE_ROOT / "registry"):
        if not base.is_dir():
            continue
        for src in sorted(base.glob("*.json")):
            data = _load_json(src)
            if not data:
                continue
            uuid = data.get("uuid") or src.stem
            dst = root / "projects" / f"{uuid}.json"
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            count += 1
    return count


def _export_device(root: Path) -> bool:
    src = CONFIG_ROOT / "device-identity.json"
    data = _load_json(src)
    if not data:
        return False
    device_id = data.get("device_id") or "device"
    dst = root / "devices" / f"{device_id}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def _export_curated(root: Path, *, write_manifest: bool) -> dict[str, Any]:
    _ensure_layout(root)

    exported: dict[str, Any] = {
        "decisions": _copy_if_exists(STATE_ROOT / "decisions.jsonl", root / "decisions" / "decisions.jsonl"),
        "decisions_compacted": _copy_if_exists(
            STATE_ROOT / "decisions.compacted.jsonl",
            root / "decisions" / "decisions.compacted.jsonl",
        ),
        "context_snapshot_json": _copy_if_exists(
            REPO_ROOT / "core" / "context-snapshot.json",
            root / "contexts" / "agent-continuity-layer.context.json",
        ),
        "context_snapshot_md": _copy_if_exists(
            REPO_ROOT / "core" / "context-snapshot.md",
            root / "contexts" / "agent-continuity-layer.context.md",
        ),
        "context_pinned": _copy_if_exists(
            REPO_ROOT / "core" / "context-pinned.json",
            root / "contexts" / "agent-continuity-layer.pinned.json",
        ),
        "project_entries": _export_projects(root),
        "device_identity": _export_device(root),
    }

    manifest = {
        "schema_version": "1.0",
        "exported_at": _now(),
        "source_host": os.uname().nodename if hasattr(os, "uname") else "",
        "source_repo": str(REPO_ROOT),
        "included": exported,
        "excluded_by_design": [
            "credentials",
            "raw Claude/Codex/OpenClaw sessions",
            "browser profiles",
            "worker queue spool",
            "machine-local trust policy",
            "e.firma or SAT signing material",
        ],
    }
    if write_manifest:
        (root / "metadata").mkdir(parents=True, exist_ok=True)
        (root / "metadata" / "last-export.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return manifest


def cmd_export(args: argparse.Namespace) -> int:
    root = _require_memory_repo(Path(args.path))
    manifest = _export_curated(root, write_manifest=True)
    print(json.dumps({"status": "ok", "path": str(root), "manifest": manifest}, indent=2, ensure_ascii=False))
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    root = _require_memory_repo(Path(args.path))
    before = _git_porcelain(root)
    if before.strip():
        print(
            "error: memory repo has uncommitted changes; review/commit/stash before sync",
            file=sys.stderr,
        )
        print(before, file=sys.stderr, end="")
        return 1

    rc_remote, remote_url, remote_err = _run_git(["remote", "get-url", "origin"], root)
    if rc_remote != 0 or not remote_url.strip():
        print(f"error: no origin remote configured: {remote_err.strip()}", file=sys.stderr)
        return 1

    rc_pull, pull_out, pull_err = _run_git(["pull", "--rebase", "origin", "main"], root)
    if rc_pull != 0:
        print(f"error: git pull --rebase failed: {(pull_err or pull_out).strip()}", file=sys.stderr)
        print(
            "hint: another device may have changed the same memory files; resolve the "
            "Git conflict in the memory repo, then rerun sync.",
            file=sys.stderr,
        )
        return 1

    manifest = _export_curated(root, write_manifest=False)
    if not _git_porcelain(root).strip():
        report = {
            "status": "ok",
            "changed": False,
            "path": str(root),
            "pulled": pull_out.strip(),
            "head": _git_head(root),
        }
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    findings = _secret_findings(root)
    if findings:
        print(
            json.dumps(
                {"status": "blocked", "reason": "secret-scan", "findings": findings[:20]},
                indent=2,
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2

    sync_meta = {
        "schema_version": "1.0",
        "synced_at": _now(),
        "device": os.uname().nodename if hasattr(os, "uname") else "",
        "source_repo": str(REPO_ROOT),
        "substrate_version": _version(),
        "remote": remote_url.strip(),
        "included": manifest["included"],
    }
    (root / "metadata").mkdir(parents=True, exist_ok=True)
    (root / "metadata" / "last-export.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (root / "metadata" / "last-sync.json").write_text(
        json.dumps(sync_meta, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    rc_add, _, add_err = _run_git(["add", "."], root)
    if rc_add != 0:
        print(f"error: git add failed: {add_err.strip()}", file=sys.stderr)
        return 1

    title = "auto-sync memory snapshot"
    body = "\n".join([
        f"device: {sync_meta['device']}",
        f"substrate: v{sync_meta['substrate_version']}",
        f"exported: {manifest['exported_at']}",
        f"remote: {sync_meta['remote']}",
    ])
    rc_commit, commit_out, commit_err = _run_git(["commit", "-m", title, "-m", body], root)
    if rc_commit != 0:
        print(f"error: git commit failed: {(commit_err or commit_out).strip()}", file=sys.stderr)
        return 1

    rc_push, push_out, push_err = _run_git(["push", "origin", "main"], root)
    if rc_push != 0:
        # Common two-Mac race: another device pushed after our initial pull.
        # Rebase our generated commit once, then retry the push. Real
        # conflicts still stop loudly for operator resolution.
        rc_rebase, rebase_out, rebase_err = _run_git(["pull", "--rebase", "origin", "main"], root)
        if rc_rebase != 0:
            print(
                f"error: git push failed and rebase recovery failed: "
                f"{(rebase_err or rebase_out or push_err or push_out).strip()}",
                file=sys.stderr,
            )
            return 1
        rc_push2, push2_out, push2_err = _run_git(["push", "origin", "main"], root)
        if rc_push2 != 0:
            print(f"error: git push retry failed: {(push2_err or push2_out).strip()}", file=sys.stderr)
            return 1

    report = {
        "status": "ok",
        "changed": True,
        "path": str(root),
        "commit": _git_head(root),
        "commit_message": title,
        "device": sync_meta["device"],
        "substrate_version": sync_meta["substrate_version"],
        "pushed": True,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    root = _require_memory_repo(Path(args.path))
    rc, out, err = _run_git(["status", "--short", "--branch"], root)
    if rc != 0:
        print(f"error: git status failed: {err.strip()}", file=sys.stderr)
        return 1
    print(out, end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="git-memory",
        description="Scaffold and export curated continuity memory into a private Git repo.",
    )
    p.add_argument(
        "--path",
        default=str(DEFAULT_MEMORY_REPO),
        help="memory repo path (default: AGENT_CONTINUITY_MEMORY_REPO or ./agent-memory)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="create the memory repo layout and git init it")
    init.set_defaults(func=cmd_init)

    export = sub.add_parser("export", help="export curated local continuity state")
    export.set_defaults(func=cmd_export)

    sync = sub.add_parser("sync", help="pull, export, scan, commit, and push changed memory")
    sync.set_defaults(func=cmd_sync)

    status = sub.add_parser("status", help="show Git status for the memory repo")
    status.set_defaults(func=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
