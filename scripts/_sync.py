#!/usr/bin/env python3
"""sync.py — read-only pull of project context from VM into local cache.

Never writes to the VM. Never mutates ~/.claude/skills/ or agent settings.
Cache lives at ~/.cache/agent-continuity/{project_uuid}/.
"""

from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
SYNC_VERSION = "1.0"

_XDG_CACHE_HOME = Path(os.environ.get("XDG_CACHE_HOME") or (HOME / ".cache"))
_XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or (HOME / ".config"))
CACHE_ROOT = _XDG_CACHE_HOME / "agent-continuity"
LIFE_AGENTS_CONFIG = HOME / ".claude" / "life-agents.json"

# Dedicated host-key pin file. NOT ~/.ssh/known_hosts — we own this one so the
# operator can rotate / nuke it without affecting other SSH usage on the box.
KNOWN_HOSTS_FILE = _XDG_CONFIG_HOME / "agent-continuity" / "known_hosts"

# VM-side paths (mirrors v0.1 layout — see v0.1-reference/life-agents-unified/SKILL.md §"Step 4").
VM_REGISTRY = "~/life-agents/sessions/registry.json"
VM_SESSION_DIR = "~/life-agents/sessions"
SESSION_FILES = ("context.md", "decisions.md", "history.md")


def host_in_known_hosts(file: Path, host: str) -> bool:
    """True if `host` appears in the first-field hostlist of any non-comment line."""
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
            # strip [host]:port form
            if h.startswith("[") and "]" in h:
                h = h[1:h.index("]")]
            if h == host:
                return True
    return False


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ssh_cat(user: str, host: str, key: Path, remote_path: str, timeout: int = 10) -> tuple[int, str, str]:
    """ssh ... cat <remote_path>. Read-only — never writes anything on the VM.

    Strict host-key checking against the dedicated KNOWN_HOSTS_FILE. Will refuse
    to connect if the host isn't pinned (run `sync.sh --trust-host` first) or if
    the server's key doesn't match the pinned entry (rotation or MITM)."""
    cmd = [
        "ssh",
        "-o", "ConnectTimeout=5",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={KNOWN_HOSTS_FILE}",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-i", str(key),
        f"{user}@{host}",
        "cat", remote_path,
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", "ssh not on PATH"
    except PermissionError as e:
        return 126, "", f"permission denied running ssh: {e}"
    except OSError as e:
        return 126, "", f"os error running ssh: {e}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"


def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """Generic subprocess helper for ssh-keyscan / ssh-keygen invocations."""
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


def trust_host(cfg: dict[str, Any], args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """Bootstrap or display host-key pin. Never fetches session data.

    Two modes:
      --trust-host                                  preview: ssh-keyscan the host,
                                                    display fingerprints + accept instructions
      --trust-host --confirm-fingerprint SHA256:..  commit: verify live fingerprint matches
                                                    the operator-supplied value, write known_hosts
    """
    host = cfg.get("vm_host")
    port = int(cfg.get("vm_port", 22))
    report: dict[str, Any] = {
        "version": SYNC_VERSION,
        "ran_at": _now(),
        "device": socket.gethostname(),
        "mode": "trust-host",
        "host": host,
        "port": port,
        "known_hosts_file": str(KNOWN_HOSTS_FILE),
    }
    if not host:
        report["status"] = "error"
        report["error"] = "no vm_host in life-agents.json"
        return 1, report

    if host_in_known_hosts(KNOWN_HOSTS_FILE, host):
        report["status"] = "error"
        report["error"] = (
            f"host '{host}' already pinned in {KNOWN_HOSTS_FILE}. "
            f"To re-pin (e.g. after VM key rotation), remove the existing entry first: "
            f"  sed -i.bak '/^{host}/d' {KNOWN_HOSTS_FILE}"
        )
        return 1, report

    # Live keyscan
    rc, scan_out, scan_err = _run(["ssh-keyscan", "-T", "5", "-p", str(port), host], timeout=12)
    if rc != 0 or not scan_out.strip():
        report["status"] = "error"
        report["error"] = f"ssh-keyscan failed (rc {rc}): {(scan_err or 'no output').strip()[:200]}"
        return 1, report

    # Compute fingerprints via ssh-keygen -lf <tmp>
    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".kh", delete=False)
    try:
        tmp.write(scan_out)
        tmp.close()
        rc2, fp_out, fp_err = _run(["ssh-keygen", "-lf", tmp.name], timeout=5)
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
    if rc2 != 0:
        report["status"] = "error"
        report["error"] = f"ssh-keygen failed (rc {rc2}): {(fp_err or 'no output').strip()[:200]}"
        return 1, report

    # Pair each ssh-keyscan line with its ssh-keygen fingerprint by index.
    # ssh-keygen -lf preserves input order, so zipping the filtered lists is
    # safe — but the length-mismatch check below is a paranoid guard against
    # a malformed or partially-rejected key set (refuse rather than mispair).
    keyscan_lines = [l.rstrip() for l in scan_out.splitlines()
                     if l.strip() and not l.lstrip().startswith("#")]
    keygen_lines = [l for l in fp_out.splitlines()
                    if l.strip() and not l.lstrip().startswith("#")]
    if len(keyscan_lines) != len(keygen_lines):
        report["status"] = "error"
        report["error"] = (
            f"ssh-keyscan returned {len(keyscan_lines)} keys but ssh-keygen "
            f"returned {len(keygen_lines)} fingerprint(s); refusing to pin "
            f"because lines can't be reliably paired."
        )
        return 1, report

    keys: list[dict[str, str]] = []
    for ks_line, kg_line in zip(keyscan_lines, keygen_lines):
        parts = kg_line.strip().split()
        if len(parts) < 4:
            continue
        keys.append({
            "bits": parts[0],
            "fingerprint": parts[1],
            "algorithm": parts[-1].strip("()"),
            "_known_hosts_line": ks_line,
        })
    # The report exposes fingerprints + algorithms (operator-visible info) but
    # NOT the raw known_hosts lines (avoid bloating JSON output with base64 keys).
    report["fingerprints"] = [
        {"bits": k["bits"], "fingerprint": k["fingerprint"], "algorithm": k["algorithm"]}
        for k in keys
    ]

    if not args.confirm_fingerprint:
        report["status"] = "preview"
        report["hint"] = (
            "verify ONE fingerprint out-of-band (gcloud compute ssh --command='ssh-keygen -lf /etc/ssh/ssh_host_*_key.pub', "
            "web console, etc.) then re-run with --confirm-fingerprint <SHA256:...>. "
            "Only the line whose fingerprint matches will be written — other algorithms "
            "the server presented are NOT trusted."
        )
        return 0, report

    # Commit path: find the exact key whose fingerprint matches what the
    # operator confirmed, write ONLY that line. This prevents an attacker who
    # controls one algorithm slot from getting a second malicious algorithm
    # silently pinned alongside.
    confirm = args.confirm_fingerprint.strip()
    confirm_norm = confirm[7:] if confirm.lower().startswith("sha256:") else confirm
    matched: dict[str, str] | None = None
    for entry in keys:
        live = entry["fingerprint"]
        live_norm = live[7:] if live.lower().startswith("sha256:") else live
        if live_norm == confirm_norm:
            matched = entry
            break
    if matched is None:
        report["status"] = "error"
        report["error"] = (
            f"fingerprint mismatch — server presented "
            f"{[k['fingerprint'] for k in keys]}, you confirmed {confirm}. "
            f"Possible MITM, key rotation, or typo. Refusing to pin."
        )
        return 1, report

    try:
        KNOWN_HOSTS_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except Exception as e:
        report["status"] = "error"
        report["error"] = f"could not create {KNOWN_HOSTS_FILE.parent}: {e}"
        return 1, report
    try:
        with KNOWN_HOSTS_FILE.open("a") as f:
            f.write(matched["_known_hosts_line"] + "\n")
        os.chmod(KNOWN_HOSTS_FILE, 0o600)
    except Exception as e:
        report["status"] = "error"
        report["error"] = f"could not write {KNOWN_HOSTS_FILE}: {e}"
        return 1, report

    report["status"] = "pinned"
    report["pinned_algorithm"] = matched["algorithm"]
    report["pinned_fingerprint"] = matched["fingerprint"]
    report["keys_written"] = 1
    report["note"] = (
        f"only the {matched['algorithm']} key was pinned. Other algorithms the "
        f"server presented were NOT trusted. To add another algorithm, remove "
        f"this entry and re-run --trust-host with a different --confirm-fingerprint."
    )
    return 0, report


def _validate_registry(data: Any) -> list[str]:
    """Lightweight schema-shape validation against project-registry.schema.json v1.
    Returns a list of error messages (empty = valid)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["registry root is not an object"]
    if data.get("schema_version") != "1.0":
        errors.append(f"schema_version is {data.get('schema_version')!r}, expected '1.0'")
    projects = data.get("projects")
    if not isinstance(projects, list):
        errors.append("projects is not a list")
        return errors
    uuid_pat = re.compile(r"^proj-[a-z0-9-]+$")
    for i, p in enumerate(projects):
        if not isinstance(p, dict):
            errors.append(f"projects[{i}] is not an object")
            continue
        for req in ("uuid", "name", "created_at"):
            if req not in p:
                errors.append(f"projects[{i}] missing required field: {req}")
        uuid = p.get("uuid", "")
        if not isinstance(uuid, str) or not uuid_pat.match(uuid):
            errors.append(f"projects[{i}].uuid invalid format: {uuid!r}")
    return errors


def load_config() -> dict[str, Any] | None:
    if not LIFE_AGENTS_CONFIG.exists():
        return None
    try:
        return json.loads(LIFE_AGENTS_CONFIG.read_text())
    except Exception:
        return None


def fetch_registry(cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    user = cfg.get("vm_user", "claude")
    host = cfg.get("vm_host")
    key_str = cfg.get("ssh_key_path", "~/.ssh/life-agents")
    key = Path(os.path.expanduser(key_str))
    if not host:
        return None, "no vm_host in life-agents.json"
    if not key.exists():
        return None, f"ssh key missing: {key}"
    rc, stdout, stderr = _ssh_cat(user, host, key, VM_REGISTRY)
    if rc != 0:
        return None, f"ssh fetch failed (rc {rc}): {stderr.strip()[:200]}"
    try:
        return json.loads(stdout), None
    except json.JSONDecodeError as e:
        return None, f"registry JSON parse error: {e}"


def fetch_project(cfg: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    user = cfg.get("vm_user", "claude")
    host = cfg.get("vm_host")
    key = Path(os.path.expanduser(cfg.get("ssh_key_path", "~/.ssh/life-agents")))
    uuid = project["uuid"]
    target_dir = CACHE_ROOT / uuid
    entry: dict[str, Any] = {
        "uuid": uuid,
        "name": project.get("name"),
        "target_dir": str(target_dir),
        "files": {},
        "status": None,
    }
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        entry["status"] = "failed"
        entry["error"] = f"could not mkdir cache dir: {e}"
        return entry

    for fname in SESSION_FILES:
        remote = f"{VM_SESSION_DIR}/{uuid}/{fname}"
        rc, stdout, stderr = _ssh_cat(user, host, key, remote)
        if rc != 0:
            entry["files"][fname] = {
                "status": "error",
                "rc": rc,
                "stderr": stderr.strip()[:200],
            }
            continue
        if not stdout.strip():
            entry["files"][fname] = {"status": "empty"}
            continue
        target = target_dir / fname
        tmp = target.with_name(target.name + f".tmp-{os.getpid()}")
        try:
            tmp.write_text(stdout)
            os.replace(tmp, target)
            entry["files"][fname] = {
                "status": "synced",
                "size": len(stdout),
                "sha256": _sha256_text(stdout),
                "path": str(target),
            }
        except Exception as e:
            entry["files"][fname] = {"status": "write_error", "error": str(e)}
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    # write metadata sidecar (useful for "when did I last sync this?" answers)
    meta = {
        "uuid": uuid,
        "name": project.get("name"),
        "synced_at": _now(),
        "source_vm": host,
        "files": entry["files"],
    }
    try:
        (target_dir / ".meta.json").write_text(json.dumps(meta, indent=2))
    except Exception as e:
        entry["meta_warning"] = f"could not write .meta.json: {e}"

    statuses = [f["status"] for f in entry["files"].values()]
    if all(s == "synced" for s in statuses):
        entry["status"] = "synced"
    elif any(s == "synced" for s in statuses):
        entry["status"] = "partial"
    else:
        entry["status"] = "failed"
    return entry


def _print_report(report: dict[str, Any], args: argparse.Namespace, human_lines: list[str]) -> None:
    if not args.human:
        print(json.dumps(report, indent=2))
    if not args.json:
        if not args.human:
            print()
            print("---")
            print()
        print("\n".join(human_lines))


def render_human_noop(report: dict[str, Any]) -> list[str]:
    return [
        f"agent-continuity sync v{report['version']} - {report['ran_at']}",
        "mode: no-op (no VM configured)",
        "",
        report["message"],
        report["hint"],
    ]


def render_human_trust(report: dict[str, Any]) -> list[str]:
    L = [
        f"agent-continuity sync v{report['version']} - {report['ran_at']}",
        f"mode: trust-host  host: {report.get('host')}:{report.get('port')}  status: {report.get('status')}",
    ]
    if report.get("status") == "preview":
        L.append("")
        L.append("host presented these keys:")
        for fp in report.get("fingerprints", []):
            L.append(f"  {fp['algorithm']:10s}  {fp['fingerprint']}  ({fp['bits']} bits)")
        L.append("")
        L.append(report.get("hint", ""))
    elif report.get("status") == "pinned":
        L.append(f"  algorithm:    {report.get('pinned_algorithm')}")
        L.append(f"  fingerprint:  {report.get('pinned_fingerprint')}")
        L.append(f"  wrote 1 host key entry ({report.get('pinned_algorithm')} only) to {report.get('known_hosts_file')}")
        if report.get("note"):
            L.append(f"  note: {report['note']}")
    else:
        L.append(f"error: {report.get('error','')}")
    return L


def render_human_synced(report: dict[str, Any]) -> list[str]:
    L = [
        f"agent-continuity sync v{report['version']} - {report['ran_at']}",
        f"mode: {report['mode']}  vm: {report.get('vm_host','?')}  cache: {CACHE_ROOT}",
    ]
    if report["mode"] == "list":
        L.append("")
        for p in report.get("projects", []):
            L.append(f"  {p['uuid']}  {p.get('name','')}  last_active={p.get('last_active','-')}")
        L.append(f"\ntotal: {len(report.get('projects', []))} project(s)")
        return L

    s = report["summary"]
    L.append(f"summary: synced={s['synced']}  partial={s['partial']}  failed={s['failed']}")
    L.append("")
    for r in report["results"]:
        tag = {"synced": "[OK    ]", "partial": "[PARTIAL]", "failed": "[FAILED]"}.get(r["status"], "[?]")
        L.append(f"{tag} {r['uuid']}  {r.get('name','')}")
        for fname, info in r["files"].items():
            st = info["status"]
            if st == "synced":
                L.append(f"          {fname}: synced ({info['size']} bytes)")
            elif st == "empty":
                L.append(f"          {fname}: empty on VM (skipped)")
            elif st == "error":
                L.append(f"          {fname}: error rc={info.get('rc')} {info.get('stderr','')[:80]}")
            else:
                L.append(f"          {fname}: {st}")
    L.append("")
    L.append("(read-only — sync.sh never writes back to the VM; cache lives at ~/.cache/agent-continuity/)")
    return L


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only pull of project context from VM into local cache.",
    )
    parser.add_argument("--project", help="Only sync this project UUID")
    parser.add_argument("--list", action="store_true", help="List projects on VM, don't pull files")
    parser.add_argument("--trust-host", action="store_true",
                        help="Bootstrap or display VM host-key pin. Never fetches session data.")
    parser.add_argument("--confirm-fingerprint", metavar="SHA256",
                        help="Used with --trust-host: write known_hosts only if live fingerprint matches this value.")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    parser.add_argument("--human", action="store_true", help="Print human summary only")
    args = parser.parse_args()

    if args.json and args.human:
        print("error: --json and --human are mutually exclusive", file=sys.stderr)
        return 64
    if args.confirm_fingerprint and not args.trust_host:
        print("error: --confirm-fingerprint requires --trust-host", file=sys.stderr)
        return 64

    cfg = load_config()

    # --trust-host is an explicit security operation — silent no-op would hide
    # the fact that there's no VM to pin. Error loud instead.
    if args.trust_host and cfg is None:
        report = {
            "version": SYNC_VERSION,
            "ran_at": _now(),
            "device": socket.gethostname(),
            "mode": "error",
            "error": "cannot --trust-host: no ~/.claude/life-agents.json",
            "hint": "set up the VM config first (v0.1 connection-code flow), then re-run --trust-host",
        }
        human = [
            f"agent-continuity sync v{report['version']} - {report['ran_at']}",
            "mode: error  --trust-host requested but no VM config present",
            f"  error: {report['error']}",
            f"  hint:  {report['hint']}",
        ]
        _print_report(report, args, human)
        return 1

    if cfg is None:
        report = {
            "version": SYNC_VERSION,
            "ran_at": _now(),
            "device": socket.gethostname(),
            "mode": "no-op",
            "message": "no ~/.claude/life-agents.json — local-only mode, nothing to sync.",
            "hint": "see docs/install.md or scripts/doctor.sh; first-time VM setup is the v0.1 connection-code flow.",
        }
        _print_report(report, args, render_human_noop(report))
        return 0

    # Trust-host bootstrap: never fetches data, only manages pin state.
    if args.trust_host:
        rc, report = trust_host(cfg, args)
        _print_report(report, args, render_human_trust(report))
        return rc

    # Pre-fetch bootstrap check: refuse to pull data without a host pin.
    host = cfg.get("vm_host")
    if host and not host_in_known_hosts(KNOWN_HOSTS_FILE, host):
        report = {
            "version": SYNC_VERSION,
            "ran_at": _now(),
            "device": socket.gethostname(),
            "mode": "error",
            "vm_host": host,
            "error": (
                f"host '{host}' is not pinned in {KNOWN_HOSTS_FILE}. "
                f"Bootstrap first: scripts/sync.sh --trust-host"
            ),
            "known_hosts_file": str(KNOWN_HOSTS_FILE),
            "hint": "run scripts/sync.sh --trust-host to capture and pin the VM's host key",
        }
        human = [
            f"agent-continuity sync v{report['version']} - {report['ran_at']}",
            f"mode: error  vm: {host}  status: unpinned",
            f"  host key not pinned in {KNOWN_HOSTS_FILE}",
            f"  bootstrap first:  scripts/sync.sh --trust-host",
        ]
        _print_report(report, args, human)
        return 1

    registry, err = fetch_registry(cfg)
    if err is not None:
        report = {
            "version": SYNC_VERSION,
            "ran_at": _now(),
            "device": socket.gethostname(),
            "mode": "error",
            "vm_host": cfg.get("vm_host"),
            "error": err,
        }
        human = [
            f"agent-continuity sync v{report['version']} - {report['ran_at']}",
            f"mode: error  vm: {report['vm_host']}",
            f"error: {err}",
        ]
        _print_report(report, args, human)
        return 1

    validation_errors = _validate_registry(registry)
    if validation_errors:
        report = {
            "version": SYNC_VERSION,
            "ran_at": _now(),
            "device": socket.gethostname(),
            "mode": "error",
            "vm_host": cfg.get("vm_host"),
            "error": "registry failed schema validation",
            "validation_errors": validation_errors,
        }
        human = [
            f"agent-continuity sync v{report['version']} - {report['ran_at']}",
            f"mode: error  vm: {report['vm_host']}",
            "registry failed schema validation:",
            *[f"  - {e}" for e in validation_errors],
        ]
        _print_report(report, args, human)
        return 1

    projects = registry.get("projects", [])
    if args.project:
        projects = [p for p in projects if p.get("uuid") == args.project]
        if not projects:
            report = {
                "version": SYNC_VERSION,
                "ran_at": _now(),
                "mode": "error",
                "error": f"project not found in registry: {args.project}",
            }
            _print_report(report, args, [f"error: project not found: {args.project}"])
            return 1

    if args.list:
        # --list does not mutate the local cache. It reports what's on the VM.
        report = {
            "version": SYNC_VERSION,
            "ran_at": _now(),
            "device": socket.gethostname(),
            "mode": "list",
            "vm_host": cfg.get("vm_host"),
            "projects": [
                {
                    "uuid": p.get("uuid"),
                    "name": p.get("name"),
                    "last_active": p.get("last_active"),
                }
                for p in projects
            ],
        }
        _print_report(report, args, render_human_synced(report))
        return 0

    # Pull path: mirror registry into cache, then fetch each project's files.
    try:
        CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        (CACHE_ROOT / "registry.json").write_text(json.dumps(registry, indent=2))
    except Exception as e:
        report = {
            "version": SYNC_VERSION,
            "ran_at": _now(),
            "mode": "error",
            "error": f"could not write cache registry: {e}",
        }
        _print_report(report, args, [f"error: {e}"])
        return 1

    results = [fetch_project(cfg, p) for p in projects]
    summary = {
        "synced":  sum(1 for r in results if r["status"] == "synced"),
        "partial": sum(1 for r in results if r["status"] == "partial"),
        "failed":  sum(1 for r in results if r["status"] == "failed"),
    }
    report = {
        "version": SYNC_VERSION,
        "ran_at": _now(),
        "device": socket.gethostname(),
        "mode": "pull",
        "vm_host": cfg.get("vm_host"),
        "cache_root": str(CACHE_ROOT),
        "results": results,
        "summary": summary,
    }
    _print_report(report, args, render_human_synced(report))

    if summary["failed"] > 0:
        return 1
    if summary["partial"] > 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
