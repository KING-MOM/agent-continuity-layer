#!/usr/bin/env python3
"""doctor.py — read-only health report for agent-continuity-layer. Never mutates anything."""

from __future__ import annotations
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
DOCTOR_VERSION = "1.0"

# M12.0: substrate version source-of-truth. Single file at core/VERSION,
# read at doctor startup so the version surfaces in the repo block.
# release.sh build reads the same file when naming the tarball;
# scripts/version.sh cats it. No drift between in-repo --version and
# release artifact name.
def _read_substrate_version() -> str:
    vp = REPO_ROOT / "core" / "VERSION"
    if not vp.exists():
        return "unknown"
    try:
        return vp.read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"

SUBSTRATE_VERSION = _read_substrate_version()

# M11.0: honor XDG base dirs so the sandboxed quickstart (and any
# operator who relocates their config / cache) gets isolated state.
# These match the M8.0 / M10.0 / _worker M11.0 conventions.
_XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or (HOME / ".config"))
_XDG_CACHE_HOME = Path(os.environ.get("XDG_CACHE_HOME") or (HOME / ".cache"))

# Dedicated host-key pin file used by sync.sh. Doctor checks it (read-only) so
# the operator sees "unpinned" before they hit a sync failure.
KNOWN_HOSTS_FILE = _XDG_CONFIG_HOME / "agent-continuity" / "known_hosts"


def _host_in_known_hosts(file: Path, host: str) -> bool:
    if not file.exists():
        return False
    try:
        text = file.read_text()
    except Exception:
        return False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if not parts:
            continue
        for h in parts[0].split(","):
            if h.startswith("[") and "]" in h:
                h = h[1:h.index("]")]
            if h == host:
                return True
    return False


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(cmd: list[str], timeout: int = 5) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0]}"
    except PermissionError as e:
        return 126, "", f"permission denied running {cmd[0]}: {e}"
    except OSError as e:
        return 126, "", f"os error running {cmd[0]}: {e}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"


def _worse(a: str, b: str) -> str:
    order = ["ok", "info", "warn", "error"]
    return a if order.index(a) >= order.index(b) else b


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Minimal YAML-frontmatter parser. Flat key:value only — no nested, no lists."""
    if not text.startswith("---"):
        return {}
    lines = text.split("\n")
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return {}
    fm: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line:
            k, _, v = line.partition(":")
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
                v = v[1:-1]
            fm[k.strip()] = v
    return fm


def _parse_version(v: str | None) -> tuple[int, ...] | None:
    """Parse 'X.Y.Z' into a tuple of ints. Returns None if unparseable or empty."""
    if not v:
        return None
    try:
        return tuple(int(p) for p in v.split("."))
    except ValueError:
        return None


def check_repo() -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": "ok",
        "issues": [],
        "substrate_version": SUBSTRATE_VERSION,
    }

    rc, stdout, _ = _run(["git", "-C", str(REPO_ROOT), "status", "--porcelain"])
    if rc != 0:
        # M12.0: an installed substrate (extracted release tarball) has
        # no .git/ at the repo root. That's not an error — git fields
        # are a development convenience. Mark "not a git repo" and let
        # the schemas + scripts checks below decide the overall status.
        out["git"] = {
            "is_repo": False,
            "note": "not a git repo (installed substrate, extracted tarball, or copied tree)",
        }
    else:
        clean = stdout.strip() == ""
        out["git"] = {"is_repo": True, "clean": clean}
        if not clean:
            out["git"]["dirty_files"] = stdout.strip().split("\n")
            out["status"] = _worse(out["status"], "warn")
        rc, head, _ = _run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"])
        if rc == 0:
            out["git"]["head"] = head.strip()
        rc, branch, _ = _run(["git", "-C", str(REPO_ROOT), "rev-parse", "--abbrev-ref", "HEAD"])
        if rc == 0:
            out["git"]["branch"] = branch.strip()

    schemas_dir = REPO_ROOT / "core" / "schemas"
    schemas: dict[str, Any] = {}
    for sf in sorted(schemas_dir.glob("*.schema.json")):
        try:
            data = json.loads(sf.read_text())
            sv = (data.get("properties", {}).get("schema_version", {}).get("const"))
            schemas[sf.name] = {
                "parse": "ok",
                "id": data.get("$id"),
                "schema_version": sv,
                "sha256": _sha256(sf),
            }
        except Exception as e:
            schemas[sf.name] = {"parse": "error", "error": str(e)}
            out["status"] = "error"
            out["issues"].append(f"schema parse failed: {sf.name}: {e}")
    out["schemas"] = schemas
    if not schemas:
        out["status"] = "error"
        out["issues"].append("no schemas found in core/schemas/")

    scripts_dir = REPO_ROOT / "scripts"
    scripts: dict[str, Any] = {}
    expected = ["doctor.sh", "install-thin-skills.sh", "sync.sh", "worker.sh", "migrate.sh"]
    for name in expected:
        p = scripts_dir / name
        if not p.exists():
            scripts[name] = {"present": False}
            out["status"] = "error"
            out["issues"].append(f"missing script: {name}")
            continue
        scripts[name] = {
            "present": True,
            "executable": os.access(p, os.X_OK),
            "size": p.stat().st_size,
        }
        if not scripts[name]["executable"]:
            out["status"] = _worse(out["status"], "warn")
            out["issues"].append(f"not executable: {name}")
    out["scripts"] = scripts
    return out


def check_agent_homes() -> dict[str, Any]:
    homes: dict[str, Any] = {}
    for name in ("openclaw", "claude", "codex"):
        p = HOME / f".{name}"
        if p.is_dir():
            items = 0
            try:
                items = sum(1 for _ in p.iterdir())
            except Exception:
                items = -1
            homes[name] = {"present": True, "path": str(p), "items": items}
        else:
            homes[name] = {"present": False, "path": str(p)}
    return {"status": "ok", "homes": homes}


def check_installed_skills() -> dict[str, Any]:
    # Canonical install targets — one per agent, unified name "agent-continuity".
    targets = {
        "claude":   HOME / ".claude" / "skills" / "agent-continuity" / "SKILL.md",
        "codex":    HOME / ".codex" / "skills" / "agent-continuity" / "SKILL.md",
        "openclaw": HOME / ".openclaw" / "workspace" / "skills" / "agent-continuity" / "SKILL.md",
    }
    out: dict[str, Any] = {"status": "ok", "agents": {}}
    for agent, target in targets.items():
        repo_skill = REPO_ROOT / "skills" / agent / "SKILL.md"
        if not repo_skill.exists():
            out["agents"][agent] = {"status": "error", "error": "missing source in repo"}
            out["status"] = "error"
            continue
        src_text = repo_skill.read_text()
        src_hash = _sha256(repo_skill)
        src_version = _parse_frontmatter(src_text).get("version")
        if not target.exists():
            out["agents"][agent] = {
                "status": "not_installed",
                "target": str(target),
                "source_hash": src_hash,
                "source_version": src_version,
            }
            continue
        inst_text = target.read_text()
        inst_hash = _sha256(target)
        inst_version = _parse_frontmatter(inst_text).get("version")
        entry: dict[str, Any] = {
            "target": str(target),
            "source_hash": src_hash,
            "installed_hash": inst_hash,
            "source_version": src_version,
            "installed_version": inst_version,
        }
        if inst_hash == src_hash:
            entry["status"] = "installed_matching"
        else:
            sv = _parse_version(src_version)
            iv = _parse_version(inst_version)
            if sv and iv:
                if iv > sv:
                    entry["status"] = "installed_newer"
                elif iv < sv:
                    entry["status"] = "installed_older"
                else:
                    entry["status"] = "installed_drifted"
            else:
                entry["status"] = "installed_drifted"
        out["agents"][agent] = entry

    # Roll up
    statuses = [a["status"] for a in out["agents"].values()]
    if any(s in ("installed_drifted", "installed_older", "installed_newer") for s in statuses):
        out["status"] = _worse(out["status"], "warn")
    elif any(s == "not_installed" for s in statuses) and out["status"] == "ok":
        out["status"] = "info"
    return out


def check_vm_config() -> dict[str, Any]:
    cfg_path = HOME / ".claude" / "life-agents.json"
    if not cfg_path.exists():
        return {"status": "info", "configured": False, "message": "no life-agents.json - local-only mode"}
    try:
        cfg = json.loads(cfg_path.read_text())
    except Exception as e:
        return {"status": "error", "configured": True, "parse_error": str(e), "path": str(cfg_path)}

    safe_cfg = {
        "vm_host": cfg.get("vm_host"),
        "vm_user": cfg.get("vm_user"),
        "authorized_user": cfg.get("authorized_user"),
        "device_name": cfg.get("device_name"),
        "connected_at": cfg.get("connected_at"),
        "auto_connect": cfg.get("auto_connect"),
        "connection_code_present": bool(cfg.get("connection_code")),
        "ssh_key_path": cfg.get("ssh_key_path"),
        "projects_count": len(cfg.get("projects", [])),
    }
    out: dict[str, Any] = {"status": "ok", "configured": True, "config": safe_cfg}

    host = cfg.get("vm_host")
    user = cfg.get("vm_user", "claude")
    key = cfg.get("ssh_key_path", "~/.ssh/life-agents")
    key_expanded = Path(os.path.expanduser(key))

    if not key_expanded.exists():
        out["reachability"] = {"status": "skipped", "reason": f"ssh key not found at {key_expanded}"}
        out["status"] = "warn"
        return out
    if not host:
        out["reachability"] = {"status": "skipped", "reason": "no vm_host in config"}
        out["status"] = "warn"
        return out
    if not shutil.which("ssh"):
        out["reachability"] = {"status": "skipped", "reason": "ssh not on PATH"}
        out["status"] = "warn"
        return out

    # Strict host-key check against the dedicated known_hosts file. If the host
    # isn't pinned yet, report "unpinned" without contacting the VM — telling
    # the operator to run sync.sh --trust-host is more useful than a misleading
    # "reachable" that would die at the first real data fetch.
    if not _host_in_known_hosts(KNOWN_HOSTS_FILE, host):
        out["reachability"] = {
            "status": "unpinned",
            "known_hosts_file": str(KNOWN_HOSTS_FILE),
            "note": "host key not pinned — run scripts/sync.sh --trust-host before any data sync",
        }
        out["status"] = _worse(out["status"], "warn")
        return out

    rc, stdout, stderr = _run([
        "ssh",
        "-o", "ConnectTimeout=5",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={KNOWN_HOSTS_FILE}",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-i", str(key_expanded),
        f"{user}@{host}",
        "echo READY",
    ], timeout=8)

    if rc == 0 and "READY" in stdout:
        out["reachability"] = {"status": "reachable"}
    elif "REMOTE HOST IDENTIFICATION HAS CHANGED" in stderr:
        # Key mismatch — possible MITM or VM key rotation. This is loud on purpose.
        out["reachability"] = {
            "status": "key_mismatch",
            "exit": rc,
            "stderr": stderr.strip()[:200],
            "note": "pinned host key does not match what the server presented. Investigate before re-pinning.",
        }
        out["status"] = _worse(out["status"], "error")
    else:
        out["reachability"] = {"status": "unreachable", "exit": rc, "stderr": stderr.strip()[:200]}
        out["status"] = _worse(out["status"], "warn")
    return out


def check_worker_bridge() -> dict[str, Any]:
    """Canonical paths only. Source is in this repo; installed extension lives
    under ~/.openclaw/workspace/.openclaw/extensions/agent-continuity/ (M3/M4)."""
    out: dict[str, Any] = {"status": "ok"}

    # Source adapter must exist in repo — sanity check.
    src = REPO_ROOT / "adapters" / "openclaw"
    src_exists = src.is_dir()
    out["source_adapter"] = {
        "path": str(src),
        "present": src_exists,
        "items": sum(1 for _ in src.iterdir()) if src_exists else 0,
    }
    if not src_exists:
        out["status"] = "error"
        out["message"] = "source adapter missing in repo — repo is broken"

    # Installed extension — info until M3/M4.
    ext = HOME / ".openclaw" / "workspace" / ".openclaw" / "extensions" / "agent-continuity"
    ext_exists = ext.exists()
    out["installed_extension"] = {"path": str(ext), "present": ext_exists}
    if not ext_exists:
        out["installed_extension"]["note"] = "extension not yet installed — wire-up is M3/M4"
        if out["status"] == "ok":
            out["status"] = "info"

    # Bonus: report openclaw CLI on PATH (not load-bearing).
    openclaw = shutil.which("openclaw")
    out["openclaw_cli"] = {"present": bool(openclaw), "path": openclaw}
    return out


def check_trust_policy() -> dict[str, Any]:
    policy_path = _XDG_CONFIG_HOME / "agent-continuity" / "trust-policy.json"
    if not policy_path.exists():
        return {
            "status": "warn",
            "present": False,
            "expected_path": str(policy_path),
            "message": "no trust policy - every worker task will fail policy resolution. See core/schemas/trust-policy.example.json",
        }
    try:
        policy = json.loads(policy_path.read_text())
    except Exception as e:
        return {"status": "error", "present": True, "path": str(policy_path), "parse_error": str(e)}

    out: dict[str, Any] = {"status": "ok", "present": True, "path": str(policy_path)}

    if policy.get("schema_version") != "1.0":
        out["status"] = "warn"
        out["schema_version_mismatch"] = policy.get("schema_version")

    repos = policy.get("repos", [])
    out["grants_count"] = len(repos)
    out["has_default"] = "default" in policy

    now_iso = _now()
    expired: list[dict[str, Any]] = []
    for repo_entry in repos:
        rp = repo_entry.get("policy", {})
        exp = rp.get("expires_at")
        if exp and exp < now_iso:
            expired.append({"origin": repo_entry.get("origin"), "expired_at": exp})
    out["expired_grants_count"] = len(expired)
    if expired:
        out["expired_grants"] = expired
        out["status"] = _worse(out["status"], "warn")
    return out


# Charter primitives (must match memory-inventory.schema.json's primitive enum
# AND CHARTER.md's "Continuity Primitives" section).
_CHARTER_PRIMITIVES: tuple[str, ...] = (
    "project registry",
    "context recovery",
    "decision log",
    "history",
    "trust policy",
    "handoff ledger",
    "artifact memory",
    "adapter portability",
)


def _validate_against_schema(
    data: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any] | None = None,
    path: str = "$",
) -> list[str]:
    """Minimal stdlib JSON Schema validator. Supports the subset this repo's
    schemas actually use:
      - type (object, array, string, integer, boolean, null, or list-union)
      - required, properties, additionalProperties (true / false / schema)
      - enum, const
      - $defs + $ref (local refs only: #/$defs/Name)
      - items (single schema), minItems
      - format (informational only — not enforced; would need date-time parser)

    Adding here rather than adding a jsonschema dep — stays stdlib-only and
    is sufficient for the inventory shape M6 enforces. Future schemas can
    reuse this; if features grow beyond what's listed above, add them
    explicitly rather than silently passing them through."""
    if root_schema is None:
        root_schema = schema
    errors: list[str] = []

    # $ref resolution (local only)
    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return [f"{path}: only local $ref supported, got {ref!r}"]
        node: Any = root_schema
        for part in ref[2:].split("/"):
            if not isinstance(node, dict) or part not in node:
                return [f"{path}: $ref {ref!r} could not be resolved"]
            node = node[part]
        return _validate_against_schema(data, node, root_schema, path)

    # const
    if "const" in schema and data != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {data!r}")

    # enum
    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{path}: {data!r} not in enum {schema['enum']!r}")

    # type
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        type_ok = False
        for t in types:
            if t == "object" and isinstance(data, dict): type_ok = True
            elif t == "array" and isinstance(data, list): type_ok = True
            elif t == "string" and isinstance(data, str): type_ok = True
            elif t == "integer" and isinstance(data, int) and not isinstance(data, bool): type_ok = True
            elif t == "boolean" and isinstance(data, bool): type_ok = True
            elif t == "null" and data is None: type_ok = True
            elif t == "number" and isinstance(data, (int, float)) and not isinstance(data, bool): type_ok = True
        if not type_ok:
            errors.append(f"{path}: expected type {schema['type']!r}, got {type(data).__name__}")
            return errors  # downstream checks would compound the error

    # object
    if isinstance(data, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in data:
                errors.append(f"{path}: missing required property {req!r}")
        for k, v in data.items():
            if k in props:
                errors.extend(_validate_against_schema(v, props[k], root_schema, f"{path}.{k}"))
            else:
                ap = schema.get("additionalProperties")
                if ap is False:
                    errors.append(f"{path}: additional property {k!r} not allowed")
                elif isinstance(ap, dict):
                    errors.extend(_validate_against_schema(v, ap, root_schema, f"{path}.{k}"))

    # array
    if isinstance(data, list):
        if "minItems" in schema and len(data) < schema["minItems"]:
            errors.append(f"{path}: length {len(data)} < minItems {schema['minItems']}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(data):
                errors.extend(_validate_against_schema(item, item_schema, root_schema, f"{path}[{i}]"))

    return errors


def check_charter() -> dict[str, Any]:
    """M6.1: verify the continuity-first charter is present and load-bearing.

    Checks:
      - CHARTER.md exists at repo root (ERROR if missing)
      - README.md, docs/architecture.md, each skills/*/SKILL.md reference
        CHARTER.md (WARN per-file if missing)
      - core/memory-inventory.json exists + parses (ERROR if not)
      - inventory validates against core/schemas/memory-inventory.schema.json
        with real schema-driven validation (catches enum violations like
        bad writers/readers tokens — M6.0.1's tightening is now enforced)
      - inventory covers all 8 charter primitives (WARN if any missing)"""
    out: dict[str, Any] = {"status": "ok"}
    charter = REPO_ROOT / "CHARTER.md"
    out["charter_present"] = charter.exists()
    if not charter.exists():
        out["status"] = "error"
        out["error"] = f"CHARTER.md missing at {charter}"
        return out

    # Reference checks. We use a simple substring match for "CHARTER.md" — the
    # charter commit (a555121) standardized on that exact filename.
    ref_targets = [
        ("README.md", REPO_ROOT / "README.md"),
        ("docs/architecture.md", REPO_ROOT / "docs" / "architecture.md"),
        ("skills/claude/SKILL.md", REPO_ROOT / "skills" / "claude" / "SKILL.md"),
        ("skills/codex/SKILL.md", REPO_ROOT / "skills" / "codex" / "SKILL.md"),
        ("skills/openclaw/SKILL.md", REPO_ROOT / "skills" / "openclaw" / "SKILL.md"),
    ]
    refs: dict[str, bool] = {}
    for name, p in ref_targets:
        if not p.exists():
            refs[name] = False
            out["status"] = _worse(out["status"], "warn")
            continue
        refs[name] = "CHARTER.md" in p.read_text(errors="ignore")
        if not refs[name]:
            out["status"] = _worse(out["status"], "warn")
    out["references"] = refs

    # Memory inventory
    inv_path = REPO_ROOT / "core" / "memory-inventory.json"
    inv_schema_path = REPO_ROOT / "core" / "schemas" / "memory-inventory.schema.json"
    inv_block: dict[str, Any] = {"path": str(inv_path), "present": inv_path.exists()}
    out["inventory"] = inv_block
    if not inv_path.exists():
        inv_block["error"] = "core/memory-inventory.json missing"
        out["status"] = "error"
        return out
    if not inv_schema_path.exists():
        inv_block["error"] = "core/schemas/memory-inventory.schema.json missing"
        out["status"] = "error"
        return out
    try:
        inv = json.loads(inv_path.read_text())
        inv_schema = json.loads(inv_schema_path.read_text())
    except Exception as e:
        inv_block["error"] = f"parse error: {e}"
        out["status"] = "error"
        return out

    # Real schema validation (M6.0.1's enum tightening is now enforced here).
    # M6.1.1: if schema validation fails, return immediately. The coverage
    # check below assumes inv['memory_files'] is a list of dicts — which the
    # schema enforces. Running coverage on schema-invalid data would crash on
    # .get() calls when, e.g., inv is a bare list or memory_files is a string.
    # Doctor reports the schema errors and stops cleanly.
    schema_errors = _validate_against_schema(inv, inv_schema)
    inv_block["schema_valid"] = not schema_errors
    if schema_errors:
        inv_block["schema_errors"] = schema_errors[:10]  # cap to keep doctor output reasonable
        inv_block["schema_errors_truncated"] = len(schema_errors) > 10
        out["status"] = _worse(out["status"], "error")
        return out

    # Primitive coverage. Defensive isinstance guards are belt-and-braces:
    # by this point the schema has already accepted the shape, but guarding
    # the iteration costs nothing and protects against future validator bugs.
    memory_files = inv.get("memory_files", []) if isinstance(inv, dict) else []
    memory_files = memory_files if isinstance(memory_files, list) else []
    covered = sorted({
        f.get("primitive")
        for f in memory_files
        if isinstance(f, dict) and f.get("primitive")
    })
    missing = sorted(set(_CHARTER_PRIMITIVES) - set(covered))
    inv_block["primitives_covered"] = covered
    inv_block["primitives_missing"] = missing
    inv_block["entries_count"] = len(memory_files)
    if missing:
        out["status"] = _worse(out["status"], "warn")

    return out


def check_context_snapshot() -> dict[str, Any]:
    """M7.1: report the project context snapshot's status.

    Checks:
      - core/context-snapshot.json exists (ERROR if missing)
      - parses as JSON (ERROR if not)
      - validates against core/schemas/context-snapshot.schema.json
        with the same _validate_against_schema used by check_charter
        (ERROR if invalid)
      - source_head_sha matches `git rev-parse HEAD` (WARN if stale)
      - reports last_completed, next_major_milestone, truncated
        next_safe_action so a fresh agent reading doctor's output
        sees the snapshot's headline answers without opening the file

    Doctor never mutates. Refresh is `scripts/context.sh --write`.

    Staleness check: the snapshot file naturally goes stale on the next
    non-snapshot commit. That's expected; the warning prompts a refresh
    when continuity actually matters (next handoff to a fresh agent),
    not on every commit. The operator decides when to regenerate."""
    out: dict[str, Any] = {"status": "ok"}
    snap_path = REPO_ROOT / "core" / "context-snapshot.json"
    schema_path = REPO_ROOT / "core" / "schemas" / "context-snapshot.schema.json"

    out["path"] = str(snap_path)
    out["snapshot_present"] = snap_path.exists()

    if not snap_path.exists():
        out["status"] = "error"
        out["message"] = "missing — run `scripts/context.sh --write` to generate"
        return out

    try:
        snap = json.loads(snap_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        out["status"] = "error"
        out["message"] = f"parse error: {e}"
        return out

    if not schema_path.exists():
        out["status"] = "error"
        out["message"] = f"schema missing at {schema_path}"
        return out

    try:
        schema = json.loads(schema_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        out["status"] = "error"
        out["message"] = f"schema parse error: {e}"
        return out

    schema_errors = _validate_against_schema(snap, schema)
    out["schema_valid"] = not schema_errors
    if schema_errors:
        out["schema_errors"] = schema_errors[:10]
        out["schema_errors_truncated"] = len(schema_errors) > 10
        out["status"] = "error"
        out["message"] = f"schema validation failed ({len(schema_errors)} errors)"
        return out

    # Snapshot is valid; surface its headline answers for the human render.
    source_sha = snap.get("source_head_sha")
    out["source_head_sha"] = source_sha
    out["source_head_short"] = source_sha[:7] if isinstance(source_sha, str) else None

    rc, head_out, _ = _run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"])
    current_sha = head_out.strip() if rc == 0 and head_out.strip() else None
    out["current_head_sha"] = current_sha
    out["current_head_short"] = current_sha[:7] if current_sha else None

    ms = snap.get("milestone") or {}
    out["last_completed"] = ms.get("last_completed") if isinstance(ms, dict) else None
    nm = (ms.get("next_major_milestone") or {}) if isinstance(ms, dict) else {}
    out["next_major_milestone"] = {
        "tag": nm.get("tag", "") if isinstance(nm, dict) else "",
        "label": nm.get("label", "") if isinstance(nm, dict) else "",
        "present": bool(nm.get("present")) if isinstance(nm, dict) else False,
    }

    nsa = snap.get("next_safe_action") or ""
    if not isinstance(nsa, str):
        nsa = ""
    nsa_one = " ".join(nsa.split())  # collapse any whitespace runs
    if len(nsa_one) > 100:
        nsa_one = nsa_one[:100].rstrip() + "…"
    out["next_safe_action_truncated"] = nsa_one

    if source_sha is None or current_sha is None:
        out["stale"] = None
        out["message"] = "freshness check skipped (snapshot or git lacks HEAD info)"
        return out

    if source_sha == current_sha:
        out["stale"] = False
        return out

    out["stale"] = True
    out["status"] = "warn"
    out["message"] = (
        f"snapshot is stale (snapshot: {source_sha[:7]}, HEAD: {current_sha[:7]}). "
        "Run `scripts/context.sh --write` to refresh."
    )
    return out


# M-tag regex used by check_context_pin to detect milestone references.
# DUPLICATED INTENTIONALLY from scripts/_context.py:M_TAG_RE. Doctor stays
# free of intra-scripts/ imports so that a missing/broken _context.py
# doesn't break the health check itself. If the M-tag schema ever changes,
# update BOTH places.
_DOCTOR_M_TAG_RE = re.compile(r"\bM(\d+)(?:\.(\d+)([a-z]\d*)?(?:\.(\d+))?)?\b")


def _m_tags_in(text: str) -> list[str]:
    """Return canonical M-tag strings found in text (e.g. ['M7.1', 'M8'])."""
    out: list[str] = []
    for m in _DOCTOR_M_TAG_RE.finditer(text):
        major = m.group(1)
        minor = m.group(2)
        suffix = m.group(3) or ""
        patch = m.group(4)
        tag = f"M{major}"
        if minor is not None:
            tag += f".{minor}{suffix}"
            if patch is not None:
                tag += f".{patch}"
        out.append(tag)
    return out


def _leading_milestone_tag(subject: str) -> str | None:
    """Return the M-tag from the subject's prefix (before the first ':'), or
    None. Matches both shapes we use in this repo:
        'M7.0: project context recovery ...'      -> 'M7.0'
        'fix(M7.0.1): next_proposed semantics ...' -> 'M7.0.1'
        'docs: update README'                      -> None  (no M-tag in prefix)

    The leading tag is the milestone the commit is *about*. Tags that appear
    only in the subject body (e.g. M8.2's 'pin forward to M8.3/M8.4') are
    *references*, not completions. M8.2.1 introduced this distinction after
    check_context_pin falsely flagged the pin as referencing completed M8.3
    and M8.4."""
    head, sep, _ = subject.partition(":")
    if not sep:
        return None
    tags = _m_tags_in(head)
    return tags[0] if tags else None


def check_context_pin() -> dict[str, Any]:
    """M7.2: detect a stale next_safe_action without auto-guessing intent.

    core/context-pinned.json carries the one piece of operator judgment
    in the context snapshot (next_safe_action). When the action it
    describes is already done, the snapshot points fresh agents at
    completed work. Doctor's job here is to flag that the pin is stale,
    not to write a new one — the operator decides what comes next.

    Detection:
      - pin missing                                        -> WARN
      - parse error                                        -> ERROR
      - next_safe_action empty/absent                      -> INFO
      - next_safe_action references an M-tag that appears
        as a LEADING tag in any git log commit subject     -> WARN
      - no M-tag references, or only references to tags
        not yet shipped as leading tags                    -> OK

    'Leading tag' is the milestone the commit is *about* (e.g. 'M7.1: ...'
    or 'fix(M7.0.1): ...'), not a tag mentioned anywhere else in the
    subject line. M8.2.1 introduced this distinction after the M8.2 commit
    subject ('... pin forward to M8.3/M8.4') falsely marked M8.3 and M8.4
    as completed.

    This is intentionally conservative: it only flags 'pin mentions
    completed milestone'. It won't catch a pin written in free-form
    prose ('finish the smoke test') after the smoke test ran — that
    failure mode is deferred."""
    out: dict[str, Any] = {"status": "ok"}
    pin_path = REPO_ROOT / "core" / "context-pinned.json"
    out["path"] = str(pin_path)
    out["pin_present"] = pin_path.exists()

    if not pin_path.exists():
        out["status"] = "warn"
        out["message"] = (
            "missing — operator should create with a next_safe_action "
            "(see core/context-pinned.json schema in memory-inventory.json)"
        )
        return out

    try:
        data = json.loads(pin_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        out["status"] = "error"
        out["message"] = f"parse error: {e}"
        return out

    nsa = data.get("next_safe_action") if isinstance(data, dict) else None
    if not isinstance(nsa, str) or not nsa.strip():
        out["status"] = "info"
        out["next_safe_action_present"] = False
        out["message"] = "next_safe_action empty — operator hasn't pinned a next step"
        return out

    out["next_safe_action_present"] = True
    # Dedupe + sort: the check is set-semantic; multiplicity in the pin
    # text isn't meaningful and just clutters the human render.
    referenced = sorted(set(_m_tags_in(nsa)))
    out["referenced_tags"] = referenced

    if not referenced:
        out["message"] = "pin has no M-tag references to verify"
        return out

    rc, log_out, _ = _run(["git", "-C", str(REPO_ROOT), "log", "--format=%s"])
    if rc != 0:
        out["status"] = "info"
        out["message"] = "git log unavailable; cannot verify pin against history"
        return out

    completed: set[str] = set()
    for line in log_out.splitlines():
        tag = _leading_milestone_tag(line)
        if tag is not None:
            completed.add(tag)
    out["completed_tags_in_log_count"] = len(completed)

    stale_refs = sorted({t for t in referenced if t in completed})
    out["stale_references"] = stale_refs

    if stale_refs:
        out["status"] = "warn"
        out["message"] = (
            f"pin references completed milestone(s) {', '.join(stale_refs)} — "
            "edit core/context-pinned.json to point at the next step"
        )
    return out


def check_decisions_log() -> dict[str, Any]:
    """M8.1: validate the cross-agent decision log + summarize its content.

    Path: $XDG_STATE_HOME/agent-continuity/decisions.jsonl
    (defaults to ~/.local/state/agent-continuity/decisions.jsonl)

    States:
      - file missing                                          -> INFO
        (empty log is valid before any add; not a defect)
      - file present but contains only blank lines             -> INFO
      - any line is malformed JSON                             -> ERROR
      - any entry fails schema validation against the canonical
        core/schemas/decision-entry.schema.json                -> ERROR
      - all entries parse + validate                           -> OK
        with summary: count, newest ts, adapters present, repos present

    Validates against the CANONICAL schema (not scripts/_decisions.py's
    inline validator) by design — catches drift between the writer's
    fast inline check and the schema file. If they diverge, doctor
    reports the schema-side truth.

    Doctor never mutates; append is scripts/decisions.sh add."""
    out: dict[str, Any] = {"status": "ok"}

    # XDG_STATE_HOME path resolution. Duplicated from scripts/_decisions.py
    # to keep doctor free of intra-scripts/ imports (same pattern as M7.2's
    # M_TAG_RE duplication). If the path scheme changes, update both.
    xdg_state = Path(os.environ.get("XDG_STATE_HOME") or (HOME / ".local" / "state"))
    log_path = xdg_state / "agent-continuity" / "decisions.jsonl"
    schema_path = REPO_ROOT / "core" / "schemas" / "decision-entry.schema.json"

    out["path"] = str(log_path)
    out["log_present"] = log_path.exists()

    if not log_path.exists():
        out["status"] = "info"
        out["message"] = "decisions log not yet created — empty is valid before any add"
        return out

    if not schema_path.exists():
        out["status"] = "error"
        out["message"] = f"canonical schema missing at {schema_path}"
        return out

    try:
        schema = json.loads(schema_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        out["status"] = "error"
        out["message"] = f"schema parse error: {e}"
        return out

    parse_errors: list[str] = []
    schema_errors: list[str] = []
    entry_count = 0
    newest_ts = ""
    adapters_present: set[str] = set()
    repos_present: set[str] = set()
    ERR_CAP = 5  # collect up to N of each error class; iteration continues so counts stay accurate

    try:
        with log_path.open(encoding="utf-8") as f:
            for lineno, raw in enumerate(f, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as e:
                    if len(parse_errors) < ERR_CAP:
                        parse_errors.append(f"line {lineno}: {e}")
                    continue
                errs = _validate_against_schema(entry, schema)
                if errs:
                    for err in errs[:3]:
                        if len(schema_errors) < ERR_CAP:
                            schema_errors.append(f"line {lineno}: {err}")
                    continue
                entry_count += 1
                ts = entry.get("ts")
                if isinstance(ts, str) and ts > newest_ts:
                    newest_ts = ts
                adapter = entry.get("adapter")
                if isinstance(adapter, str):
                    adapters_present.add(adapter)
                repo = entry.get("repo")
                if isinstance(repo, str):
                    repos_present.add(repo)
    except OSError as e:
        out["status"] = "error"
        out["message"] = f"could not read log: {e}"
        return out

    out["entry_count"] = entry_count
    out["newest_ts"] = newest_ts or None
    out["adapters_present"] = sorted(adapters_present)
    out["repos_present"] = sorted(repos_present)
    out["parse_error_count"] = len(parse_errors)
    out["schema_error_count"] = len(schema_errors)
    # M8.4: report file size alongside the count so the operator can decide
    # when to run scripts/decisions.sh compact. No threshold/auto-compaction
    # in v1; both numbers are informational.
    try:
        out["log_size_bytes"] = log_path.stat().st_size
    except OSError:
        out["log_size_bytes"] = None

    # Parse errors take priority — a malformed line means the rest of the
    # file's validity is uncertain (we kept iterating to tally, but the
    # operator should fix the malformed line first).
    if parse_errors:
        out["status"] = "error"
        out["parse_errors"] = parse_errors
        out["message"] = f"{len(parse_errors)} malformed JSONL line(s) (capped at {ERR_CAP})"
        return out

    if schema_errors:
        out["status"] = "error"
        out["schema_errors"] = schema_errors
        out["message"] = f"{len(schema_errors)} schema validation error(s) (capped at {ERR_CAP})"
        return out

    if entry_count == 0:
        out["status"] = "info"
        out["message"] = "decisions log exists but is empty — valid before any add"
        return out

    return out


_M9_OPERATIONS: tuple[str, ...] = (
    "whoami", "read_context", "read_decisions",
    "append_decision", "claim_task", "submit_result",
)

# Embedded minimal-valid examples used for positive + unknown-field
# rejection probes against the M9 schemas. Kept here (not loaded from
# disk) so doctor's check_m9_adapter_portability is self-contained: it
# doesn't depend on docs/artifacts/M9.* fixtures that an OSS user
# might not yet have generated.
_M9_IDENTITY_EXAMPLE: dict[str, Any] = {
    "schema_version": "1.0",
    "adapter_id": "doctor-probe-identity",
    "adapter_type": "local-cli",
    "adapter": "human",
    "display_name": "Doctor probe identity",
    "transport": ["shell"],
    "capabilities": {
        "whoami": True, "read_context": True, "read_decisions": True,
        "append_decision": True, "claim_task": True, "submit_result": True,
    },
    "created_at": "2026-01-01T00:00:00Z",
}
_M9_BUNDLE_EXAMPLE: dict[str, Any] = {
    "schema_version": "1.0",
    "bundle_id": "doctor-probe-bundle",
    "direction": "layer-to-adapter",
    "created_at": "2026-01-01T00:00:00Z",
}


def check_m9_adapter_portability() -> dict[str, Any]:
    """M9.3: verify adapter portability surface is operationally available.

    Five sub-blocks:
      contract           docs/m9-adapter-pattern.md exists + names all six operations
      schemas            adapter-identity + adapter-bundle parse, validate a minimal
                         positive example, and reject an unknown root-level field
      transports         shell / bundle / mcp / openclaw-bridge entry points detected
      transport_summary  list of available transports (handy for OSS adapter authors)

    Severity model (per M9.3 sign-off):
      - Contract doc missing                                ERROR
      - Contract doc missing some operations                WARN
      - Schema missing or parse error                       ERROR
      - Schema does NOT reject unknown fields               ERROR
      - Schema's positive example fails validation          ERROR
      - Core shell scripts (context/decisions/worker)
        missing                                             ERROR
      - bundle.sh missing (M9.1 has shipped)                ERROR
      - mcp.sh or mcp/tools.json missing (M9.2 has shipped) ERROR
      - mcp manifest missing operations                     ERROR
      - OpenClaw bridge missing                             INFO

    Doctor is read-only here: never writes, never invokes the
    transports' write paths (no real bundle ingest, no real MCP write
    tool, no real worker.sh claim)."""
    out: dict[str, Any] = {"status": "ok"}

    # ---- 1. Contract doc ----
    contract_path = REPO_ROOT / "docs" / "m9-adapter-pattern.md"
    contract: dict[str, Any] = {"path": str(contract_path), "present": contract_path.exists()}
    if not contract_path.exists():
        contract["status"] = "error"
        contract["message"] = "M9 spec doc missing"
        out["status"] = _worse(out["status"], "error")
    else:
        try:
            text = contract_path.read_text(errors="ignore")
        except OSError as e:
            contract["status"] = "error"
            contract["message"] = f"could not read: {e}"
            out["status"] = _worse(out["status"], "error")
        else:
            missing_ops = [op for op in _M9_OPERATIONS if op not in text]
            contract["operations_present"] = [op for op in _M9_OPERATIONS if op in text]
            contract["operations_missing"] = missing_ops
            if missing_ops:
                contract["status"] = "warn"
                contract["message"] = f"spec missing operation names: {missing_ops}"
                out["status"] = _worse(out["status"], "warn")
            else:
                contract["status"] = "ok"
    out["contract"] = contract

    # ---- 2. Schemas (parse + positive + unknown-field rejection) ----
    schemas: dict[str, Any] = {}
    schema_targets = [
        ("adapter_identity", "adapter-identity.schema.json", _M9_IDENTITY_EXAMPLE),
        ("adapter_bundle", "adapter-bundle.schema.json", _M9_BUNDLE_EXAMPLE),
    ]
    for name, filename, example in schema_targets:
        spath = REPO_ROOT / "core" / "schemas" / filename
        entry: dict[str, Any] = {"path": str(spath), "present": spath.exists()}
        if not spath.exists():
            entry["status"] = "error"
            entry["message"] = "schema file missing"
            out["status"] = _worse(out["status"], "error")
            schemas[name] = entry
            continue
        try:
            schema = json.loads(spath.read_text())
        except Exception as e:
            entry["status"] = "error"
            entry["message"] = f"parse error: {e}"
            out["status"] = _worse(out["status"], "error")
            schemas[name] = entry
            continue

        entry["parse"] = "ok"
        # Root additionalProperties: false is the M9 strictness contract.
        entry["additional_properties_false"] = schema.get("additionalProperties") is False

        pos_errs = _validate_against_schema(example, schema)
        entry["positive_valid"] = not pos_errs
        if pos_errs:
            entry["positive_errors"] = pos_errs[:3]

        # Probe rejection: add a known-unknown field, expect rejection.
        bad = {**example, "_doctor_probe_unknown": True}
        neg_errs = _validate_against_schema(bad, schema)
        entry["rejects_unknown_fields"] = bool(neg_errs)

        # Severity rollup for this schema entry
        if not entry["positive_valid"]:
            entry["status"] = "error"
            entry["message"] = "schema rejects the embedded positive example — schema or example drift"
            out["status"] = _worse(out["status"], "error")
        elif not entry["additional_properties_false"]:
            entry["status"] = "error"
            entry["message"] = "schema root additionalProperties is not false"
            out["status"] = _worse(out["status"], "error")
        elif not entry["rejects_unknown_fields"]:
            entry["status"] = "error"
            entry["message"] = "schema accepted an unknown root field"
            out["status"] = _worse(out["status"], "error")
        else:
            entry["status"] = "ok"
        schemas[name] = entry
    out["schemas"] = schemas

    # ---- 3. Transports ----
    transports: dict[str, Any] = {}

    # 3a. Shell — three canonical scripts that everything else wraps.
    shell_scripts = [
        REPO_ROOT / "scripts" / "context.sh",
        REPO_ROOT / "scripts" / "decisions.sh",
        REPO_ROOT / "scripts" / "worker.sh",
    ]
    shell_missing = [str(p.relative_to(REPO_ROOT)) for p in shell_scripts if not p.exists()]
    shell_present = [str(p.relative_to(REPO_ROOT)) for p in shell_scripts if p.exists()]
    transports["shell"] = {
        "available": not shell_missing,
        "present": shell_present,
        "missing": shell_missing,
    }
    if shell_missing:
        transports["shell"]["status"] = "error"
        transports["shell"]["message"] = f"core shell scripts missing: {shell_missing}"
        out["status"] = _worse(out["status"], "error")
    else:
        transports["shell"]["status"] = "ok"

    # 3b. Bundle (M9.1)
    bundle_path = REPO_ROOT / "scripts" / "bundle.sh"
    transports["bundle"] = {
        "path": str(bundle_path.relative_to(REPO_ROOT)),
        "available": bundle_path.exists(),
    }
    if not bundle_path.exists():
        transports["bundle"]["status"] = "error"
        transports["bundle"]["message"] = "scripts/bundle.sh expected (M9.1 has shipped)"
        out["status"] = _worse(out["status"], "error")
    else:
        transports["bundle"]["status"] = "ok"

    # 3c. MCP (M9.2)
    mcp_script = REPO_ROOT / "scripts" / "mcp.sh"
    mcp_manifest = REPO_ROOT / "core" / "mcp" / "tools.json"
    mcp_entry: dict[str, Any] = {
        "path": str(mcp_script.relative_to(REPO_ROOT)),
        "manifest": str(mcp_manifest.relative_to(REPO_ROOT)),
        "available": mcp_script.exists(),
    }
    if not mcp_script.exists():
        mcp_entry["status"] = "error"
        mcp_entry["message"] = "scripts/mcp.sh expected (M9.2 has shipped)"
        out["status"] = _worse(out["status"], "error")
    elif not mcp_manifest.exists():
        mcp_entry["status"] = "error"
        mcp_entry["message"] = f"manifest missing at {mcp_manifest}"
        out["status"] = _worse(out["status"], "error")
    else:
        try:
            manifest = json.loads(mcp_manifest.read_text())
            tools = manifest.get("tools", [])
            tool_names = [t.get("name") for t in tools if isinstance(t, dict)]
            mcp_entry["tool_count"] = len(tool_names)
            mcp_entry["tool_names"] = tool_names
            missing_tools = [op for op in _M9_OPERATIONS if op not in tool_names]
            mcp_entry["operations_missing"] = missing_tools
            if missing_tools:
                mcp_entry["status"] = "error"
                mcp_entry["message"] = f"manifest missing operations: {missing_tools}"
                out["status"] = _worse(out["status"], "error")
            else:
                mcp_entry["status"] = "ok"
        except Exception as e:
            mcp_entry["status"] = "error"
            mcp_entry["message"] = f"manifest parse error: {e}"
            out["status"] = _worse(out["status"], "error")
    transports["mcp"] = mcp_entry

    # 3d. OpenClaw bridge (M5) — INFO if missing (not required for OSS).
    bridge_path = HOME / ".openclaw" / "workspace" / "scripts" / "agent-worker.mjs"
    bridge_entry: dict[str, Any] = {
        "path": str(bridge_path),
        "available": bridge_path.exists(),
    }
    if not bridge_path.exists():
        bridge_entry["status"] = "info"
        bridge_entry["message"] = "OpenClaw bridge not installed; not required for non-OpenClaw use"
        # Don't worsen overall status past INFO for a missing optional transport.
        if out["status"] == "ok":
            out["status"] = "info"
    else:
        bridge_entry["status"] = "ok"
    transports["openclaw_bridge"] = bridge_entry

    out["transports"] = transports

    # ---- 4. Transport summary ----
    # `available` on each transport entry tracks entry-point presence;
    # the summary lists transports that are FULLY operational (status==ok),
    # i.e. usable end-to-end without the operator having to fix something.
    # MCP with a missing manifest counts as broken-not-usable, not usable.
    summary: list[str] = []
    if transports["shell"].get("status") == "ok":
        summary.append("shell")
    if transports["bundle"].get("status") == "ok":
        summary.append("bundle")
    if transports["mcp"].get("status") == "ok":
        summary.append("mcp")
    if transports["openclaw_bridge"].get("status") == "ok":
        summary.append("openclaw-bridge")
    out["transport_summary"] = summary

    return out


def check_sync() -> dict[str, Any]:
    """M10.2: multi-device sync visibility.

    Reports, in one block:
      - device identity present/missing (+ device_id, display_name)
      - backend configured: fake-vm-local (M10.0/1/2) or unconfigured
        (real SSH-pinned VM is M10.3)
      - per-artifact status for decisions, project-registry, context-pinned:
          state ∈ {current, local-ahead, remote-ahead, diverged, unknown}
          + counts / last_writer attribution where available

    Read-only — never invokes a sync write. The check uses the same
    `_vm_root` abstraction sync.sh uses, so M10.3's SSH backend will
    swap in without rewriting doctor. Imports from _sync_artifact are
    deferred to inside this function so a broken sync subsystem
    degrades to an error block rather than crashing the whole doctor."""
    out: dict[str, Any] = {"status": "ok"}

    # Defer import so other doctor checks survive even if _sync_artifact.py
    # is broken (matches M7.2 / M8.1 robustness pattern but inverted —
    # here doctor depends on sync, so wrap the dependency).
    try:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from _sync_artifact import (
            _vm_root,
            DEVICE_IDENTITY_PATH,
            DECISIONS_PATH,
            VM_DECISIONS_REL,
            LOCAL_PIN_PATH,
            VM_PIN_REL,
            VM_SESSIONS_DIR_REL,
            VM_REGISTRY_ENTRY_FILENAME,
            LOCAL_REGISTRY_DIR,
            _list_local_uuids,
            _list_vm_uuids,
            _read_registry_entry,
            _local_registry_path,
            _vm_registry_path,
            _read_pin,
            _read_jsonl,
        )
    except Exception as e:
        return {
            "status": "error",
            "message": f"sync subsystem unavailable: {type(e).__name__}: {e}",
        }

    # ---- device identity ----
    ident_block: dict[str, Any] = {
        "path": str(DEVICE_IDENTITY_PATH),
        "present": DEVICE_IDENTITY_PATH.exists(),
    }
    if not DEVICE_IDENTITY_PATH.exists():
        ident_block["status"] = "info"
        ident_block["message"] = "no device identity yet — generated on first sync push/pull"
        if out["status"] == "ok":
            out["status"] = "info"
    else:
        try:
            ident_data = json.loads(DEVICE_IDENTITY_PATH.read_text(errors="ignore"))
            ident_block["device_id"] = ident_data.get("device_id")
            ident_block["display_name"] = ident_data.get("display_name")
            ident_block["status"] = "ok"
        except Exception as e:
            ident_block["status"] = "error"
            ident_block["message"] = f"identity parse error: {e}"
            out["status"] = _worse(out["status"], "error")
    out["device_identity"] = ident_block

    # ---- backend ----
    vm_root = _vm_root()
    backend_block: dict[str, Any] = {
        "configured": vm_root is not None,
        "path": str(vm_root) if vm_root else None,
        "kind": ("fake-vm-local" if vm_root else "unconfigured"),
    }
    if vm_root is None:
        backend_block["status"] = "info"
        backend_block["message"] = (
            "no AGENT_CONTINUITY_VM_PATH; set for fake-VM mode. "
            "Real SSH-pinned VM is M10.3 scope."
        )
        out["backend"] = backend_block
        # Without a backend, per-artifact status is meaningless.
        if out["status"] == "ok":
            out["status"] = "info"
        return out
    backend_block["status"] = "ok"
    out["backend"] = backend_block

    # ---- per-artifact status ----

    # decisions: append-only set-semantics
    local_ids: set[str] = set()
    vm_ids: set[str] = set()
    if DECISIONS_PATH.exists():
        local_ids = {e.get("id") for e in _read_jsonl(DECISIONS_PATH) if isinstance(e.get("id"), str)}
    vm_decisions_path = vm_root / VM_DECISIONS_REL
    if vm_decisions_path.exists():
        vm_ids = {e.get("id") for e in _read_jsonl(vm_decisions_path) if isinstance(e.get("id"), str)}
    local_only_d = local_ids - vm_ids
    remote_only_d = vm_ids - local_ids
    if not local_ids and not vm_ids:
        d_state = "current"  # both empty
    elif not local_only_d and not remote_only_d:
        d_state = "current"
    elif local_only_d and not remote_only_d:
        d_state = "local-ahead"
    elif remote_only_d and not local_only_d:
        d_state = "remote-ahead"
    else:
        d_state = "diverged"
    out["artifacts"] = {
        "decisions": {
            "state": d_state,
            "local_count": len(local_ids),
            "vm_count": len(vm_ids),
            "local_only": len(local_only_d),
            "remote_only": len(remote_only_d),
        },
    }

    # project-registry: per-project entries
    local_uuids = set(_list_local_uuids())
    vm_uuids = set(_list_vm_uuids(vm_root))
    local_only_r = local_uuids - vm_uuids
    remote_only_r = vm_uuids - local_uuids
    both_r = local_uuids & vm_uuids
    r_local_ahead = 0
    r_remote_ahead = 0
    r_current = 0
    r_diverged_same_ts = 0
    for uuid in both_r:
        l_entry = _read_registry_entry(_local_registry_path(uuid))
        r_entry = _read_registry_entry(_vm_registry_path(vm_root, uuid))
        if not l_entry or not r_entry:
            continue
        l_ts = l_entry.get("last_writer_ts") or ""
        r_ts = r_entry.get("last_writer_ts") or ""
        if l_ts == r_ts:
            # M10.2.1 P2: same ts can hide content divergence (same writer at
            # same instant should produce identical bytes; if they don't,
            # something happened that the LWW tiebreak alone won't surface).
            # Compare canonical body. Equal -> current; different -> diverged.
            if l_entry == r_entry:
                r_current += 1
            else:
                r_diverged_same_ts += 1
        elif l_ts > r_ts:
            r_local_ahead += 1
        else:
            r_remote_ahead += 1
    if not local_uuids and not vm_uuids:
        r_state = "current"
    elif r_diverged_same_ts:
        # Equal-ts content divergence is a strong "needs operator attention"
        # signal — both sides have unique state with matching attribution.
        r_state = "diverged"
    elif not local_only_r and not remote_only_r and not r_local_ahead and not r_remote_ahead:
        r_state = "current"
    elif (local_only_r or r_local_ahead) and not (remote_only_r or r_remote_ahead):
        r_state = "local-ahead"
    elif (remote_only_r or r_remote_ahead) and not (local_only_r or r_local_ahead):
        r_state = "remote-ahead"
    else:
        r_state = "diverged"
    out["artifacts"]["project-registry"] = {
        "state": r_state,
        "local_count": len(local_uuids),
        "vm_count": len(vm_uuids),
        "local_only": len(local_only_r),
        "remote_only": len(remote_only_r),
        "both": len(both_r),
        "current_in_both": r_current,
        "local_ahead_in_both": r_local_ahead,
        "remote_ahead_in_both": r_remote_ahead,
        "diverged_same_ts_in_both": r_diverged_same_ts,
    }

    # context-pinned: single file LWW
    vm_pin_path = vm_root / VM_PIN_REL
    local_pin = _read_pin(LOCAL_PIN_PATH)
    vm_pin = _read_pin(vm_pin_path)
    pin_block: dict[str, Any] = {}
    if local_pin is None and vm_pin is None:
        pin_block["state"] = "current"
        pin_block["note"] = "no pin on either side"
    elif local_pin is None:
        pin_block["state"] = "remote-ahead"
        pin_block["note"] = "local pin missing; VM has one"
        pin_block["vm_last_writer_device"] = vm_pin.get("last_writer_device")
        pin_block["vm_last_writer_ts"] = vm_pin.get("last_writer_ts")
    elif vm_pin is None:
        pin_block["state"] = "local-ahead"
        pin_block["note"] = "local pin present; VM has none"
        pin_block["local_last_writer_device"] = local_pin.get("last_writer_device")
        pin_block["local_last_writer_ts"] = local_pin.get("last_writer_ts")
    else:
        l_ts = local_pin.get("last_writer_ts") or ""
        r_ts = vm_pin.get("last_writer_ts") or ""
        if not l_ts and not r_ts:
            # M10.2.1 P2: even without timestamps, content equality is
            # decidable. If the prose matches byte-for-byte, the pins are
            # functionally current (both sides hold the same operator
            # intent); otherwise unknown is the honest answer.
            if local_pin == vm_pin:
                pin_block["state"] = "current"
            else:
                pin_block["state"] = "unknown"
                pin_block["note"] = "neither side has last_writer_ts and content differs"
        elif l_ts == r_ts:
            # M10.2.1 P2: equal ts can hide content divergence (concurrent
            # writes at the same instant from two devices that happen to
            # share clock). Compare canonical content. Equal -> current;
            # different -> diverged (both sides hold unique intent that
            # LWW alone won't resolve).
            if local_pin == vm_pin:
                pin_block["state"] = "current"
            else:
                pin_block["state"] = "diverged"
                pin_block["note"] = "equal last_writer_ts but content differs — concurrent write collision"
        elif l_ts > r_ts:
            pin_block["state"] = "local-ahead"
        else:
            pin_block["state"] = "remote-ahead"
        pin_block["local_last_writer_device"] = local_pin.get("last_writer_device")
        pin_block["local_last_writer_ts"] = l_ts or None
        pin_block["vm_last_writer_device"] = vm_pin.get("last_writer_device")
        pin_block["vm_last_writer_ts"] = r_ts or None
    out["artifacts"]["context-pinned"] = pin_block

    # M10.2.1 P1: aggregate per-artifact states into block status.
    # Sync drift should be ACTIONABLE via the check status, not buried
    # in the detail lines.
    #   current               -> ok
    #   local-ahead, remote-
    #     ahead, diverged     -> warn (operator should push, pull, or
    #                            reconcile; depending which side is
    #                            authoritative)
    #   unknown               -> info (we don't know enough to classify
    #                            — typically pre-M10.2 pins without
    #                            last_writer_ts; not actionable, just
    #                            surfaced)
    state_to_status = {
        "current": "ok",
        "local-ahead": "warn",
        "remote-ahead": "warn",
        "diverged": "warn",
        "unknown": "info",
    }
    for art_name, art_block in out["artifacts"].items():
        art_status = state_to_status.get(art_block.get("state"), "info")
        art_block["status"] = art_status
        out["status"] = _worse(out["status"], art_status)

    return out


def check_queue() -> dict[str, Any]:
    """Worker-task queue depth, per state. INFO if no queue yet; WARN if
    anything has landed in rejected/ or failed/ — those want operator eyes."""
    queue_root = _XDG_CACHE_HOME / "agent-continuity" / "queue"
    out: dict[str, Any] = {"status": "ok", "queue_root": str(queue_root)}
    if not queue_root.exists():
        out["status"] = "info"
        out["message"] = "no queue directory yet — nothing has been enqueued"
        return out
    out["depth"] = {}
    states = ("queued", "claimed", "running", "completed", "awaiting-approval",
              "rejected", "failed", "cancelled")
    for state in states:
        d = queue_root / state
        out["depth"][state] = len(list(d.glob("task-*.json"))) if d.exists() else 0
    if out["depth"]["rejected"] > 0 or out["depth"]["failed"] > 0:
        out["status"] = "warn"
    return out


def check_quickstart() -> dict[str, Any]:
    """M11.3: M11 quickstart sandbox integrity check.

    Diagnostic only — surfaces whether the quickstart sandbox is runnable,
    half-installed, completed, or polluting the real namespace. Never
    mutates anything; quickstart.sh init (M11.0) and quickstart.sh reset
    (M11.4) are the only paths that should change sandbox state.

    Stays at INFO when no sandbox dirs exist — quickstart isn't part of
    every operator's daily workflow. Goes to OK once initialized and
    healthy. WARN for recoverable drift (expired grant, stuck task);
    ERROR for broken state (half-initialized, missing critical files,
    real-namespace pollution).

    Uses HARDCODED ~/.config/agent-continuity-quickstart/... paths rather
    than XDG-derived paths: the sandbox location is fixed by
    quickstart.sh's design (the dir name is deliberately distinct from
    the real 'agent-continuity' namespace), so the check answers the
    same question regardless of how doctor is invoked."""
    out: dict[str, Any] = {"status": "ok"}

    qs_config = HOME / ".config" / "agent-continuity-quickstart"
    qs_state = HOME / ".local" / "state" / "agent-continuity-quickstart"
    qs_cache = HOME / ".cache" / "agent-continuity-quickstart"

    qs_workspace = qs_state / "workspace" / "quickstart-project"
    qs_policy = qs_config / "agent-continuity" / "trust-policy.json"
    qs_state_file = qs_state / "quickstart-state.json"
    qs_decisions = qs_state / "agent-continuity" / "decisions.jsonl"
    qs_queue_root = qs_cache / "agent-continuity" / "queue"

    dirs_present = {
        "config": qs_config.is_dir(),
        "state": qs_state.is_dir(),
        "cache": qs_cache.is_dir(),
    }

    if not any(dirs_present.values()):
        # Pristine: operator hasn't run init yet.
        out["state"] = "not-initialized"
        out["status"] = "info"
        out["message"] = "quickstart not initialized; run scripts/quickstart.sh init to try it"
        return out

    if not all(dirs_present.values()):
        out["state"] = "half-initialized"
        out["status"] = "error"
        out["dirs"] = dirs_present
        out["message"] = (
            "some sandbox dirs missing; reset with "
            "`rm -rf ~/.{config,local/state,cache}/agent-continuity-quickstart` and re-init"
        )
        return out

    # All three sandbox dirs present — check the contents.
    out["state"] = "initialized"

    # ---- trust policy ----
    tp: dict[str, Any] = {"path": str(qs_policy), "present": qs_policy.exists()}
    if not qs_policy.exists():
        tp["status"] = "error"
        tp["message"] = "trust policy missing after init — broken state"
        out["status"] = _worse(out["status"], "error")
    else:
        try:
            policy = json.loads(qs_policy.read_text(errors="ignore"))
            qs_repos = [r for r in policy.get("repos", []) if r.get("origin") == "quickstart-project"]
            if not qs_repos:
                tp["status"] = "error"
                tp["message"] = "trust policy present but missing 'quickstart-project' repo grant"
                out["status"] = _worse(out["status"], "error")
            else:
                repo_pol = qs_repos[0].get("policy", {})
                expires = repo_pol.get("expires_at", "")
                tp["expires_at"] = expires or None
                if expires and expires < _now():
                    tp["status"] = "warn"
                    tp["message"] = (
                        f"trust grant expired at {expires}; reset and re-init "
                        "for a fresh 30-day grant"
                    )
                    out["status"] = _worse(out["status"], "warn")
                else:
                    tp["status"] = "ok"
        except Exception as e:
            tp["status"] = "error"
            tp["message"] = f"trust policy unparseable: {e}"
            out["status"] = _worse(out["status"], "error")
    out["trust_policy"] = tp

    # ---- fixture workspace ----
    ws: dict[str, Any] = {"path": str(qs_workspace), "present": qs_workspace.is_dir()}
    if not qs_workspace.is_dir():
        ws["status"] = "error"
        ws["message"] = "fixture workspace missing — broken state"
        out["status"] = _worse(out["status"], "error")
    else:
        ws["status"] = "ok"
    out["workspace"] = ws

    # ---- fixture task ----
    ft: dict[str, Any] = {"state_file": str(qs_state_file), "state_file_present": qs_state_file.exists()}
    fixture_task_id: str | None = None
    if qs_state_file.exists():
        try:
            sdata = json.loads(qs_state_file.read_text(errors="ignore"))
            fixture_task_id = sdata.get("fixture_task_id") or None
            ft["fixture_task_id"] = fixture_task_id
        except Exception as e:
            ft["status"] = "error"
            ft["message"] = f"quickstart-state.json unparseable: {e}"
            out["status"] = _worse(out["status"], "error")

    if "status" not in ft:
        if not fixture_task_id:
            ft["task_state"] = "no-task"
            ft["status"] = "ok"
            ft["message"] = "ready for `quickstart.sh enqueue`"
        else:
            task_state: str | None = None
            if qs_queue_root.is_dir():
                for state_dir in qs_queue_root.iterdir():
                    if state_dir.is_dir() and (state_dir / f"{fixture_task_id}.json").exists():
                        task_state = state_dir.name
                        break
            ft["task_state"] = task_state or "missing-from-queue"
            if task_state is None:
                ft["status"] = "warn"
                ft["message"] = f"task id {fixture_task_id} not found in queue — corrupted state file?"
                out["status"] = _worse(out["status"], "warn")
            elif task_state == "queued":
                ft["status"] = "ok"
                ft["message"] = "ready for `quickstart.sh run-fake-worker`"
            elif task_state == "completed":
                ft["status"] = "ok"
                ft["message"] = "fixture task completed; `quickstart.sh decisions list` shows the decision"
            elif task_state in ("claimed", "running"):
                ft["status"] = "warn"
                ft["message"] = f"fixture task stuck in '{task_state}' — claimed but not finished"
                out["status"] = _worse(out["status"], "warn")
            elif task_state in ("failed", "rejected", "cancelled"):
                ft["status"] = "warn"
                ft["message"] = f"fixture task ended in '{task_state}'; reset and re-init for a fresh attempt"
                out["status"] = _worse(out["status"], "warn")
            else:
                ft["status"] = "warn"
                ft["message"] = f"fixture task in unexpected state '{task_state}'"
                out["status"] = _worse(out["status"], "warn")
    out["fixture_task"] = ft

    # ---- decisions ----
    dec: dict[str, Any] = {"path": str(qs_decisions), "present": qs_decisions.exists()}
    if not qs_decisions.exists():
        dec["status"] = "ok"
        dec["entry_count"] = 0
        dec["has_fixture_decision"] = False
    else:
        try:
            count = 0
            has_fixture = False
            with qs_decisions.open(errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    count += 1
                    refs = e.get("refs") or []
                    if isinstance(refs, list) and "M11.1" in refs:
                        has_fixture = True
            dec["status"] = "ok"
            dec["entry_count"] = count
            dec["has_fixture_decision"] = has_fixture
        except Exception as e:
            dec["status"] = "warn"
            dec["message"] = f"could not read decisions log: {e}"
            out["status"] = _worse(out["status"], "warn")
    out["decisions"] = dec

    # ---- real-namespace pollution (best-effort, cheap) ----
    pollution: list[str] = []
    real_policy = HOME / ".config" / "agent-continuity" / "trust-policy.json"
    if real_policy.exists():
        try:
            rp = json.loads(real_policy.read_text(errors="ignore"))
            for r in rp.get("repos") or []:
                if r.get("origin") == "quickstart-project":
                    pollution.append("real trust-policy.json has a 'quickstart-project' repo entry")
                    break
        except Exception:
            pass
    real_decisions = HOME / ".local" / "state" / "agent-continuity" / "decisions.jsonl"
    if real_decisions.exists():
        try:
            with real_decisions.open(errors="ignore") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if e.get("repo") == "quickstart-project":
                        pollution.append("real decisions.jsonl has 'repo: quickstart-project' entry")
                        break
        except Exception:
            pass
    if pollution:
        out["real_namespace_pollution"] = pollution
        out["status"] = _worse(out["status"], "error")

    return out


def check_project_registry_health() -> dict[str, Any]:
    """M14.0: local project-registry health.

    Reports per-device registry state at
    $XDG_CONFIG_HOME/agent-continuity/projects/*.json. Status ladder:
      info  — no projects registered yet (fresh install)
      ok    — entries valid, no duplicates, no stale paths
      warn  — duplicate origins or names, or stale repo paths
              (recoverable; surface to operator)
      error — parse failures or schema-invalid entries
    """
    base = Path(os.environ.get("XDG_CONFIG_HOME") or str(HOME / ".config"))
    projects_dir = base / "agent-continuity" / "projects"
    out: dict[str, Any] = {
        "status": "info",
        "projects_dir": str(projects_dir),
        "entries_count": 0,
        "issues": [],
    }
    if not projects_dir.is_dir():
        out["message"] = "no projects/ dir yet — fresh install or no projects registered"
        return out

    schema_path = REPO_ROOT / "core" / "schemas" / "project-registry-entry.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        out["status"] = "error"
        out["issues"].append(f"cannot load project-registry-entry schema: {e}")
        return out

    parsed: list[tuple[Path, dict[str, Any]]] = []
    parse_errors: list[str] = []
    schema_errors: list[str] = []
    for p in sorted(projects_dir.glob("*.json")):
        try:
            entry = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            parse_errors.append(f"{p.name}: {e}")
            continue
        if not isinstance(entry, dict):
            schema_errors.append(f"{p.name}: top-level must be an object")
            continue
        errs = _validate_against_schema(entry, schema)
        if errs:
            schema_errors.append(f"{p.name}: {'; '.join(errs[:3])}")
            continue
        parsed.append((p, entry))

    out["entries_count"] = len(parsed)
    if parse_errors:
        out["status"] = "error"
        out["issues"].extend([f"parse_error:{e}" for e in parse_errors])
    if schema_errors:
        out["status"] = "error"
        out["issues"].extend([f"schema_error:{e}" for e in schema_errors])

    # Duplicate detection. We surface as warn; an operator may legitimately
    # want two projects with the same name in rare cases (different orgs),
    # but two entries with the same origin URL are almost always a mistake.
    names_seen: dict[str, list[str]] = {}
    origins_seen: dict[str, list[str]] = {}
    stale_paths: list[str] = []
    for path, entry in parsed:
        name = entry.get("name", "")
        names_seen.setdefault(name.lower(), []).append(entry["uuid"][:8])
        for repo in entry.get("repos", []):
            origin = repo.get("origin")
            if origin:
                origins_seen.setdefault(origin, []).append(entry["uuid"][:8])
            for obs in repo.get("observed_paths", []):
                rec_path = obs.get("path")
                rec_device = obs.get("device_id")
                if rec_path and not Path(rec_path).exists():
                    stale_paths.append(
                        f"{entry['uuid'][:8]} ({entry.get('name')}): "
                        f"device={rec_device} path={rec_path} no longer exists"
                    )

    dup_names = {n: uids for n, uids in names_seen.items() if len(uids) > 1}
    dup_origins = {o: uids for o, uids in origins_seen.items() if len(uids) > 1}

    if dup_origins or dup_names or stale_paths:
        out["status"] = _worse(out["status"], "warn")
    if dup_origins:
        for o, uids in dup_origins.items():
            out["issues"].append(f"duplicate_origin:{o}:{','.join(uids)}")
    if dup_names:
        for n, uids in dup_names.items():
            out["issues"].append(f"duplicate_name:{n}:{','.join(uids)}")
    if stale_paths:
        out["issues"].extend([f"stale_path:{s}" for s in stale_paths])

    if not parsed and not parse_errors and not schema_errors:
        out["message"] = "projects/ dir exists but empty"
        return out

    if out["status"] == "info" and parsed:
        out["status"] = "ok"

    return out


def check_v0_1_residue() -> dict[str, Any]:
    issues: list[str] = []
    findings: dict[str, Any] = {}

    v01_skill = HOME / ".claude" / "skills" / "life-agents-unified"
    if v01_skill.exists():
        findings["v0_1_skill_installed"] = {
            "path": str(v01_skill),
            "severity": "danger",
            "note": "v0.1 skill is active. It auto-rsyncs ~/.claude/skills/ from VM with --delete on every session. Will fight v0.2.",
        }
        issues.append("danger:v0_1_skill_installed")
    else:
        findings["v0_1_skill_installed"] = {"installed": False}

    queue = HOME / ".claude" / "life-agents-queue.jsonl"
    if queue.exists():
        findings["v0_1_offline_queue"] = {
            "path": str(queue),
            "size": queue.stat().st_size,
            "severity": "warn",
            "note": "v0.1 offline queue. Will silently drain to VM on next v0.1 connect.",
        }
        issues.append("warn:v0_1_offline_queue")

    rc, stdout, stderr = _run(["crontab", "-l"], timeout=3)
    # `crontab -l` exits 1 with "no crontab for X" when none is installed.
    # Treat that as "empty", not "error".
    if rc == 0 or "no crontab" in (stderr or "").lower():
        suspect = [l for l in stdout.splitlines()
                   if re.search(r"life-agents|rsync.*\.claude|rsync.*life-agents", l)
                   and not l.lstrip().startswith("#")]
        if suspect:
            findings["crontab"] = {"matches": suspect, "severity": "danger"}
            issues.append("danger:crontab_auto_sync")
        else:
            findings["crontab"] = {"matches": []}
    else:
        findings["crontab"] = {"error": stderr.strip()[:200] or "could not read crontab", "severity": "warn"}
        issues.append("warn:crontab_unreadable")

    launchd_dir = HOME / "Library" / "LaunchAgents"
    launchd_hits: list[str] = []
    if launchd_dir.exists():
        for plist in launchd_dir.glob("*.plist"):
            try:
                text = plist.read_text(errors="ignore")
                if re.search(r"life-agents|agent-continuity", text):
                    launchd_hits.append(str(plist))
            except Exception:
                pass
    if launchd_hits:
        findings["launchd"] = {"matches": launchd_hits, "severity": "warn"}
        issues.append("warn:launchd_entries")
    else:
        findings["launchd"] = {"matches": []}

    rc_findings: dict[str, list[str]] = {}
    for rcfile in (HOME / ".zshrc", HOME / ".bashrc", HOME / ".zshenv", HOME / ".bash_profile"):
        if not rcfile.exists():
            continue
        try:
            text = rcfile.read_text(errors="ignore")
            hits = [l.strip() for l in text.splitlines()
                    if re.search(r"life-agents|rsync.*\.claude/skills", l)
                    and not l.lstrip().startswith("#")]
            if hits:
                rc_findings[rcfile.name] = hits
        except Exception:
            pass
    if rc_findings:
        findings["shell_rc"] = {"matches": rc_findings, "severity": "warn"}
        issues.append("warn:shell_rc_entries")
    else:
        findings["shell_rc"] = {"matches": {}}

    settings_path = HOME / ".claude" / "settings.json"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
            hooks = settings.get("hooks", {})
            hook_text = json.dumps(hooks)
            if re.search(r"life-agents|rsync", hook_text):
                findings["claude_hooks"] = {"matches": hooks, "severity": "danger"}
                issues.append("danger:claude_hooks_auto_sync")
            else:
                findings["claude_hooks"] = {"matches": "none"}
        except Exception as e:
            findings["claude_hooks"] = {"error": str(e)}

    if any(i.startswith("danger") for i in issues):
        status = "error"
    elif issues:
        status = "warn"
    else:
        status = "ok"
    return {"status": status, "issues": issues, "findings": findings}


def summarize(checks: dict[str, Any]) -> dict[str, int]:
    counts = {"ok": 0, "warn": 0, "error": 0, "info": 0}
    for v in checks.values():
        s = v.get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1
    return counts


def render_human(report: dict[str, Any]) -> str:
    L: list[str] = []
    s = report["summary"]
    L.append(f"agent-continuity-layer doctor v{report['doctor_version']} - {report['ran_at']} on {report['device']}")
    L.append(f"checks: {s.get('ok',0)} ok, {s.get('warn',0)} warn, {s.get('error',0)} error, {s.get('info',0)} info")
    L.append("")

    def tag(st: str) -> str:
        return f"[{st.upper():5}]"

    cs = report["checks"]

    r = cs["repo"]
    L.append(f"{tag(r['status'])} repo")
    L.append(f"         substrate v{r.get('substrate_version', '?')}")
    git = r.get("git", {})
    if git.get("is_repo"):
        L.append(f"         git {git.get('branch','?')} @ {(git.get('head','?') or '?')[:7]} {'clean' if git.get('clean') else 'DIRTY'}")
    schemas = r.get("schemas", {})
    parse_ok = all(v.get("parse") == "ok" for v in schemas.values())
    L.append(f"         schemas: {len(schemas)} present, parse_all_ok={parse_ok}")
    scripts = r.get("scripts", {})
    execs = sum(1 for v in scripts.values() if v.get("executable"))
    L.append(f"         scripts: {execs}/{len(scripts)} executable")
    for issue in r.get("issues", []):
        L.append(f"         - {issue}")

    r = cs["charter"]
    L.append(f"{tag(r['status'])} charter")
    if r.get("charter_present"):
        L.append(f"         CHARTER.md present at repo root")
        refs = r.get("references", {})
        missing_refs = [k for k, v in refs.items() if not v]
        if missing_refs:
            L.append(f"         missing CHARTER.md references in: {', '.join(missing_refs)}")
        else:
            L.append(f"         all {len(refs)} required files reference CHARTER.md")
        inv = r.get("inventory", {})
        if inv.get("present"):
            ok = "ok" if inv.get("schema_valid") else f"INVALID ({len(inv.get('schema_errors', []))} errors)"
            L.append(f"         inventory: {inv.get('entries_count')} entries, schema validation {ok}")
            if inv.get("schema_errors"):
                for e in inv["schema_errors"][:3]:
                    L.append(f"           - {e}")
                if inv.get("schema_errors_truncated"):
                    L.append(f"           (more errors truncated)")
            if inv.get("primitives_missing"):
                L.append(f"         primitives MISSING: {inv['primitives_missing']}")
            else:
                L.append(f"         all 8 charter primitives covered")
        else:
            L.append(f"         inventory: {inv.get('error','missing')}")
    else:
        L.append(f"         {r.get('error','CHARTER.md missing')}")

    r = cs["context_snapshot"]
    L.append(f"{tag(r['status'])} context snapshot")
    if not r.get("snapshot_present"):
        L.append(f"         {r.get('message','missing')}")
    elif r.get("schema_errors"):
        L.append(f"         {r.get('message','schema invalid')}")
        for e in r["schema_errors"][:3]:
            L.append(f"           - {e}")
        if r.get("schema_errors_truncated"):
            L.append(f"           (more errors truncated)")
    elif r.get("status") == "error":
        L.append(f"         {r.get('message','error')}")
    else:
        src = r.get("source_head_short") or "?"
        cur = r.get("current_head_short") or "?"
        stale = r.get("stale")
        if stale is True:
            L.append(f"         STALE (snapshot: {src}, HEAD: {cur}) — run `scripts/context.sh --write` to refresh")
        elif stale is False:
            L.append(f"         fresh (HEAD: {cur})")
        else:
            L.append(f"         schema valid; HEAD comparison skipped")
        lc = r.get("last_completed") or "?"
        nm = r.get("next_major_milestone") or {}
        nm_tag = nm.get("tag") or "?"
        L.append(f"         last_completed: {lc}  next_major_milestone: {nm_tag}")
        nsa = r.get("next_safe_action_truncated") or ""
        if nsa:
            L.append(f"         next_safe_action: {nsa}")

    r = cs["context_pin"]
    L.append(f"{tag(r['status'])} context pin")
    if not r.get("pin_present"):
        L.append(f"         {r.get('message','missing')}")
    elif r.get("status") == "error":
        L.append(f"         {r.get('message','error')}")
    elif r.get("stale_references"):
        L.append(f"         STALE: references completed milestone(s) {', '.join(r['stale_references'])}")
        L.append(f"         edit core/context-pinned.json to point at the next step")
        refs = r.get("referenced_tags") or []
        if refs and set(refs) != set(r["stale_references"]):
            future = sorted(set(refs) - set(r["stale_references"]))
            L.append(f"         (also references not-yet-completed: {', '.join(future)})")
    elif r.get("status") == "info":
        L.append(f"         {r.get('message','info')}")
    else:
        refs = r.get("referenced_tags") or []
        if refs:
            L.append(f"         pin references {', '.join(refs)} — none completed")
        else:
            L.append(f"         pin present; no M-tag references to verify")

    r = cs["decisions_log"]
    L.append(f"{tag(r['status'])} decisions log")
    if not r.get("log_present"):
        L.append(f"         {r.get('message','missing')}")
    elif r.get("parse_errors"):
        L.append(f"         {r.get('message','parse errors')}")
        for e in r["parse_errors"][:3]:
            L.append(f"           - {e}")
        if len(r["parse_errors"]) > 3:
            L.append(f"           ({len(r['parse_errors']) - 3} more)")
    elif r.get("schema_errors"):
        L.append(f"         {r.get('message','schema errors')}")
        for e in r["schema_errors"][:3]:
            L.append(f"           - {e}")
        if len(r["schema_errors"]) > 3:
            L.append(f"           ({len(r['schema_errors']) - 3} more)")
    elif r.get("status") == "error":
        L.append(f"         {r.get('message','error')}")
    elif r.get("entry_count", 0) == 0:
        L.append(f"         {r.get('message','empty')}")
    else:
        newest = r.get("newest_ts") or "—"
        n = r["entry_count"]
        size = r.get("log_size_bytes")
        size_str = ""
        if isinstance(size, int):
            if size < 1024:
                size_str = f", {size} B"
            elif size < 1024 * 1024:
                size_str = f", {size / 1024:.1f} KiB"
            else:
                size_str = f", {size / (1024 * 1024):.1f} MiB"
        L.append(f"         {n} {'entry' if n == 1 else 'entries'}{size_str}; newest: {newest}")
        adapters = r.get("adapters_present") or []
        repos = r.get("repos_present") or []
        if adapters:
            L.append(f"         adapters: {', '.join(adapters)}")
        if repos:
            L.append(f"         repos: {', '.join(repos)}")

    r = cs["sync"]
    L.append(f"{tag(r['status'])} sync (M10)")
    if "message" in r and not r.get("artifacts"):
        # Top-level sync-subsystem failure (import broke) or no backend
        L.append(f"         {r['message']}")
    ident = r.get("device_identity", {})
    if ident.get("present"):
        L.append(f"         device: {ident.get('device_id','?')} ({ident.get('display_name','?')})")
    elif "device_identity" in r:
        L.append(f"         device identity: {ident.get('message','missing')}")
    backend = r.get("backend", {})
    if backend.get("configured"):
        L.append(f"         backend: {backend.get('kind','?')} at {backend.get('path','?')}")
    elif "backend" in r:
        L.append(f"         backend: {backend.get('message','unconfigured')}")
    artifacts = r.get("artifacts", {})
    for name in ("decisions", "project-registry", "context-pinned"):
        a = artifacts.get(name)
        if a is None:
            continue
        state = a.get("state", "?")
        if name == "decisions":
            detail = f"local: {a.get('local_count',0)}, vm: {a.get('vm_count',0)}"
            if a.get("local_only") or a.get("remote_only"):
                detail += f", local_only: {a.get('local_only',0)}, remote_only: {a.get('remote_only',0)}"
        elif name == "project-registry":
            detail = f"local: {a.get('local_count',0)}, vm: {a.get('vm_count',0)}, both: {a.get('both',0)}"
            extras = []
            if a.get("local_only"): extras.append(f"local_only={a['local_only']}")
            if a.get("remote_only"): extras.append(f"remote_only={a['remote_only']}")
            if a.get("local_ahead_in_both"): extras.append(f"local_ahead={a['local_ahead_in_both']}")
            if a.get("remote_ahead_in_both"): extras.append(f"remote_ahead={a['remote_ahead_in_both']}")
            if extras:
                detail += " (" + ", ".join(extras) + ")"
        else:  # context-pinned
            parts = []
            lwd = a.get("local_last_writer_device")
            lts = a.get("local_last_writer_ts")
            if lwd or lts:
                parts.append(f"local: {lwd or '?'} @ {lts or '?'}")
            vwd = a.get("vm_last_writer_device")
            vts = a.get("vm_last_writer_ts")
            if vwd or vts:
                parts.append(f"vm: {vwd or '?'} @ {vts or '?'}")
            if a.get("note"):
                parts.append(a["note"])
            detail = "  ".join(parts) if parts else ""
        suffix = f" ({detail})" if detail else ""
        L.append(f"         {name:18s} {state}{suffix}")

    r = cs["m9_adapter_portability"]
    L.append(f"{tag(r['status'])} m9 adapter portability")
    contract = r.get("contract", {})
    if not contract.get("present"):
        L.append(f"         contract: docs/m9-adapter-pattern.md MISSING")
    else:
        ops_missing = contract.get("operations_missing") or []
        if ops_missing:
            L.append(f"         contract: missing op names in spec: {', '.join(ops_missing)}")
        else:
            L.append(f"         contract: all 6 operations named in spec")
    schemas = r.get("schemas", {})
    schema_states = []
    for n, e in schemas.items():
        st = e.get("status", "?")
        if st == "ok":
            schema_states.append(f"{n}: ok")
        else:
            schema_states.append(f"{n}: {st} ({e.get('message','')})")
    if schema_states:
        L.append(f"         schemas: " + " | ".join(schema_states))
    transports = r.get("transports", {})
    parts = []
    for tname in ("shell", "bundle", "mcp", "openclaw_bridge"):
        t = transports.get(tname, {})
        label = tname.replace("_", "-")
        st = t.get("status", "missing")
        if st == "ok":
            if tname == "mcp" and isinstance(t.get("tool_count"), int):
                parts.append(f"{label} ok ({t['tool_count']} tools)")
            else:
                parts.append(f"{label} ok")
        else:
            parts.append(f"{label} {st.upper()}")
    L.append(f"         transports: " + " | ".join(parts))
    summary = r.get("transport_summary") or []
    L.append(f"         available: {', '.join(summary) if summary else 'none'}")

    r = cs["agent_homes"]
    L.append(f"{tag(r['status'])} agent homes")
    for name, h in r["homes"].items():
        if h["present"]:
            L.append(f"         + .{name} ({h.get('items','?')} items)")
        else:
            L.append(f"         - .{name} (missing)")

    r = cs["installed_skills"]
    L.append(f"{tag(r['status'])} installed thin skills")
    for agent, info in r["agents"].items():
        s = info["status"]
        sv = info.get("source_version") or "?"
        iv = info.get("installed_version") or "?"
        if s == "not_installed":
            L.append(f"         {agent}: not_installed (source v{sv})")
        elif s == "installed_matching":
            L.append(f"         {agent}: installed_matching v{iv}")
        elif s in ("installed_older", "installed_newer"):
            L.append(f"         {agent}: {s} (installed v{iv} vs source v{sv})")
        elif s == "installed_drifted":
            if iv != "?" and sv != "?" and iv == sv:
                L.append(f"         {agent}: installed_drifted (v{iv} but hash differs - manual edit?)")
            else:
                L.append(f"         {agent}: installed_drifted (no version info, hash differs)")
        else:
            L.append(f"         {agent}: {s}")

    r = cs["vm_config"]
    L.append(f"{tag(r['status'])} VM config")
    if r.get("configured"):
        cfg = r.get("config", {})
        L.append(f"         host: {cfg.get('vm_host')}  user: {cfg.get('vm_user')}  authorized: {cfg.get('authorized_user')}")
        reach = r.get("reachability", {})
        L.append(f"         reachability: {reach.get('status')}")
        if reach.get("status") in ("unreachable", "skipped", "unpinned", "key_mismatch"):
            note = reach.get("note") or reach.get("stderr") or reach.get("reason") or ""
            if note:
                L.append(f"           note: {note[:200]}")
    else:
        L.append(f"         not configured ({r.get('message','')})")

    r = cs["worker_bridge"]
    L.append(f"{tag(r['status'])} worker bridge")
    src = r.get("source_adapter") or {}
    src_state = "present" if src.get("present") else "MISSING"
    L.append(f"         source adapter: {src_state} ({src.get('items', 0)} items) {src.get('path','')}")
    ext = r.get("installed_extension") or {}
    ext_state = "present" if ext.get("present") else "not installed"
    L.append(f"         installed extension: {ext_state}")
    L.append(f"           {ext.get('path','')}")
    if ext.get("note"):
        L.append(f"           note: {ext['note']}")
    cli = r.get("openclaw_cli") or {}
    L.append(f"         openclaw cli: {'found' if cli.get('present') else 'not on PATH'}")

    r = cs["trust_policy"]
    L.append(f"{tag(r['status'])} trust policy")
    if r.get("present"):
        L.append(f"         grants: {r.get('grants_count', 0)}  expired: {r.get('expired_grants_count', 0)}  has_default: {r.get('has_default')}")
        for e in r.get("expired_grants", []):
            L.append(f"         expired: {e.get('origin')} at {e.get('expired_at')}")
    else:
        L.append(f"         missing ({r.get('message','')[:120]})")

    r = cs["queue"]
    L.append(f"{tag(r['status'])} worker queue")
    if "depth" in r:
        nonzero = {k: v for k, v in r["depth"].items() if v > 0}
        if nonzero:
            for state, count in nonzero.items():
                L.append(f"         {state}: {count}")
        else:
            L.append(f"         empty")
    else:
        L.append(f"         {r.get('message','no queue')}")

    r = cs["quickstart"]
    L.append(f"{tag(r['status'])} quickstart (M11)")
    state = r.get("state", "?")
    if state == "not-initialized":
        L.append(f"         {r.get('message','not initialized')}")
    elif state == "half-initialized":
        L.append(f"         half-initialized — missing dirs:")
        for k, v in (r.get("dirs") or {}).items():
            if not v:
                L.append(f"           - {k}")
        if r.get("message"):
            L.append(f"         {r['message']}")
    else:
        tp = r.get("trust_policy", {})
        tp_status = tp.get("status", "?")
        if tp_status == "ok":
            exp = tp.get("expires_at") or "?"
            L.append(f"         trust policy: ok (expires {exp})")
        else:
            L.append(f"         trust policy: {tp_status.upper()} — {tp.get('message','')}")
        ws = r.get("workspace", {})
        ws_status = ws.get("status", "?")
        if ws_status == "ok":
            L.append(f"         workspace: ok")
        else:
            L.append(f"         workspace: {ws_status.upper()} — {ws.get('message','')}")
        ft = r.get("fixture_task", {})
        ts = ft.get("task_state", "?")
        L.append(f"         fixture task: {ts}  ({ft.get('message','')})")
        dec = r.get("decisions", {})
        if dec.get("present"):
            ec = dec.get("entry_count", 0)
            has = dec.get("has_fixture_decision", False)
            extra = ", fixture decision present" if has else ""
            L.append(f"         decisions log: {ec} entr{'y' if ec == 1 else 'ies'}{extra}")
        pollution = r.get("real_namespace_pollution") or []
        if pollution:
            L.append(f"         REAL NAMESPACE POLLUTION:")
            for p in pollution:
                L.append(f"           - {p}")

    r = cs["project_registry"]
    L.append(f"{tag(r['status'])} project registry")
    if r["status"] == "info":
        L.append(f"         {r.get('message', '(no projects)')}")
    else:
        L.append(f"         entries: {r.get('entries_count', 0)}")
        issues = r.get("issues", [])
        for issue in issues[:5]:
            # issues are tagged: parse_error:..., schema_error:..., duplicate_origin:..., duplicate_name:..., stale_path:...
            L.append(f"         - {issue}")
        if len(issues) > 5:
            L.append(f"         ({len(issues) - 5} more)")

    r = cs["v0_1_residue"]
    L.append(f"{tag(r['status'])} v0.1 residue")
    issues = r.get("issues", [])
    if not issues:
        L.append(f"         clean")
    else:
        for issue in issues:
            sev, key = issue.split(":", 1)
            L.append(f"         {sev.upper()}: {key}")

    L.append("")
    L.append("(default = JSON + human; --json for JSON only; --human for human only)")
    return "\n".join(L)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only health report for agent-continuity-layer. Never mutates.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    parser.add_argument("--human", action="store_true", help="Print human summary only")
    args = parser.parse_args()

    if args.json and args.human:
        print("error: --json and --human are mutually exclusive", file=sys.stderr)
        return 64

    checks = {
        "repo": check_repo(),
        "charter": check_charter(),
        "context_snapshot": check_context_snapshot(),
        "context_pin": check_context_pin(),
        "decisions_log": check_decisions_log(),
        "sync": check_sync(),
        "m9_adapter_portability": check_m9_adapter_portability(),
        "agent_homes": check_agent_homes(),
        "installed_skills": check_installed_skills(),
        "vm_config": check_vm_config(),
        "worker_bridge": check_worker_bridge(),
        "trust_policy": check_trust_policy(),
        "queue": check_queue(),
        "quickstart": check_quickstart(),
        "project_registry": check_project_registry_health(),
        "v0_1_residue": check_v0_1_residue(),
    }
    report = {
        "doctor_version": DOCTOR_VERSION,
        "ran_at": _now(),
        "device": socket.gethostname(),
        "platform": platform.system(),
        "repo_root": str(REPO_ROOT),
        "checks": checks,
        "summary": summarize(checks),
    }

    if not args.human:
        print(json.dumps(report, indent=2))
    if not args.json:
        if not args.human:
            print()
            print("---")
            print()
        print(render_human(report))

    if report["summary"].get("error", 0) > 0:
        return 1
    if report["summary"].get("warn", 0) > 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
