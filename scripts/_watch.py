#!/usr/bin/env python3
"""_watch.py — M9.4 opt-in agent-home watcher (macOS LaunchAgent today).

Continuity primitive: project registry + adapter portability (auto-wires
new agents into the substrate the moment they appear on disk).

What it does
------------
A user-scope LaunchAgent on macOS that wakes up via fsevents when files
under `$HOME` or `$HOME/Library/Application Support` change. On wake it
runs `_watch.py --tick`, which calls `agent-continuity connect doctor`
to detect drift (a new MCP target or skill home that's wireable but not
yet wired) and runs `connect <target> --apply` for each.

The watcher is **opt-in**. The bootstrap one-liner does NOT install it
unless the operator passes `--watch`. Substrate identity stays "tool",
not "background service", unless the operator explicitly upgrades it.

Subcommands
-----------
  enable               install LaunchAgent + launchctl load + initial sync
  disable              launchctl unload + remove LaunchAgent
  status               show enabled/disabled, last tick, recent log lines
  --tick               (invoked by launchd) detect drift, run connect

Files written
-------------
  ~/Library/LaunchAgents/com.agent-continuity.watcher.plist
  ~/Library/Logs/agent-continuity/watcher.log    (rotated at 1 MB)
  $XDG_STATE_HOME/agent-continuity/watcher.state.json
                                                  (last-tick timestamp,
                                                   debounce + audit)

Privacy / trust posture
-----------------------
  - Watcher runs as the user, not root. No LaunchDaemon, no privilege
    escalation.
  - Watcher only invokes `agent-continuity connect`. It does not read
    user files outside the substrate state dir, does not phone home,
    does not auto-update.
  - Every action is appended to the log file with a UTC timestamp.
    The operator can `cat ~/Library/Logs/agent-continuity/watcher.log`
    at any time for a complete audit trail.
  - macOS may prompt for File and Folder Access permissions the first
    time WatchPaths fires on a TCC-protected directory. The operator
    grants or denies; the substrate has no control over that prompt.

Cross-platform note
-------------------
M9.4 is macOS-only. Linux/systemd parity tracked as a follow-up slice.
On non-Darwin platforms `enable` exits with a clear "not supported on
this platform yet" message; `--tick` still works (so the same logic
can be wired into a systemd user unit in the next slice).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HOME = pathlib.Path.home()
LAUNCHAGENT_LABEL = "com.agent-continuity.watcher"
LAUNCHAGENT_PATH = HOME / "Library" / "LaunchAgents" / f"{LAUNCHAGENT_LABEL}.plist"
LOG_DIR = HOME / "Library" / "Logs" / "agent-continuity"
LOG_PATH = LOG_DIR / "watcher.log"
LOG_MAX_BYTES = 1_048_576  # 1 MB before rotation

# Debounce: if the watcher fires twice within this window, the second
# tick no-ops. WatchPaths is noisy — anything writing under $HOME (which
# is constant) would trigger a wake. ThrottleInterval in the plist gives
# us a 10s floor; this is a safety belt on top.
DEBOUNCE_SECONDS = 10


def _state_dir() -> pathlib.Path:
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return pathlib.Path(xdg) / "agent-continuity"
    return HOME / ".local" / "state" / "agent-continuity"


def _state_path() -> pathlib.Path:
    return _state_dir() / "watcher.state.json"


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _agent_continuity_path() -> str:
    """Resolve the agent-continuity shim/binary that should be invoked
    by the LaunchAgent and by --tick (when shelling out to `connect`).

    Priority:
      1. AGENT_CONTINUITY_BIN env override (smoke tests use this)
      2. $HOME/.local/bin/agent-continuity (the standard PATH shim)
      3. fall back to `agent-continuity` on PATH
    """
    override = os.environ.get("AGENT_CONTINUITY_BIN")
    if override and pathlib.Path(override).exists():
        return override
    shim = HOME / ".local" / "bin" / "agent-continuity"
    if shim.exists():
        return str(shim)
    return "agent-continuity"


def _read_state() -> dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_state(state: dict[str, Any]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _rotate_log_if_needed() -> None:
    if not LOG_PATH.exists():
        return
    try:
        if LOG_PATH.stat().st_size <= LOG_MAX_BYTES:
            return
    except OSError:
        return
    rotated = LOG_PATH.with_suffix(".log.1")
    try:
        if rotated.exists():
            rotated.unlink()
        LOG_PATH.rename(rotated)
    except OSError:
        pass


def _log(line: str) -> None:
    """Append a timestamped line to the watcher log. Best-effort —
    a failed log write must not block --tick's actual work."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _rotate_log_if_needed()
    stamped = f"[{_now_iso()}] {line}\n"
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(stamped)
    except OSError as e:
        # Last resort: print to stderr so launchd captures it
        print(f"watcher: log write failed: {e}", file=sys.stderr)


def _plist_xml(program: str) -> str:
    """Build the LaunchAgent plist. `program` is the absolute path
    to the agent-continuity shim; we hard-code it at enable time so
    launchd doesn't have to do PATH resolution as part of wake."""
    home_lib_app = str(HOME / "Library" / "Application Support")
    log_path = str(LOG_PATH)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHAGENT_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{program}</string>
        <string>watch</string>
        <string>--tick</string>
    </array>
    <key>WatchPaths</key>
    <array>
        <string>{HOME}</string>
        <string>{home_lib_app}</string>
    </array>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{log_path}</string>
    <key>StandardErrorPath</key>
    <string>{log_path}</string>
</dict>
</plist>
"""


# ──────────────────────────────────────────────────────────────────
# Drift detection — uses `connect doctor --json` so we don't duplicate
# any knowledge about which targets exist. New connect targets shipped
# in later slices light up automatically.

def _detect_drift() -> list[str]:
    """Return list of target names that are wireable but not yet wired.

    Calls `agent-continuity connect doctor --json` and parses entries
    with state in ('missing-section', 'missing-entry', 'wrong-entry',
    'pending-write'). Connected targets and unreachable agent-homes
    are excluded.
    """
    bin_path = _agent_continuity_path()
    try:
        proc = subprocess.run(
            [bin_path, "connect", "doctor", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            # rc=2 means dry-run with pending writes (expected when drift exists)
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        _log(f"detect-drift: failed to invoke {bin_path}: {e}")
        return []
    if proc.returncode not in (0, 2):
        _log(f"detect-drift: doctor rc={proc.returncode} stderr={proc.stderr.strip()[:200]}")
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        _log(f"detect-drift: doctor output not JSON: {e}")
        return []
    drift = []
    for entry in data.get("entries", []):
        state = entry.get("state", "")
        name = entry.get("name", "")
        # Drift states per _connect.py — anything that would be written
        # on --apply but isn't yet.
        if state in ("missing-section", "missing-entry", "wrong-entry", "pending-write"):
            drift.append(name)
    return drift


def _apply_drift(targets: list[str]) -> tuple[int, int]:
    """Run `connect <target> --apply` for each. Returns (succeeded, failed)."""
    bin_path = _agent_continuity_path()
    ok = 0
    fail = 0
    for t in targets:
        try:
            proc = subprocess.run(
                [bin_path, "connect", t, "--apply"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            _log(f"apply: target={t} invocation failed: {e}")
            fail += 1
            continue
        if proc.returncode == 0:
            _log(f"apply: target={t} wired ok")
            ok += 1
        else:
            _log(f"apply: target={t} rc={proc.returncode} stderr={proc.stderr.strip()[:200]}")
            fail += 1
    return ok, fail


# ──────────────────────────────────────────────────────────────────
# Subcommand handlers

def cmd_tick(_args: argparse.Namespace) -> int:
    state = _read_state()
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    last_tick = state.get("last_tick_epoch", 0)
    if now - last_tick < DEBOUNCE_SECONDS:
        # Debounced — too soon since last tick
        return 0
    state["last_tick_epoch"] = now
    state["last_tick_iso"] = _now_iso()

    drift = _detect_drift()
    if not drift:
        # No-op tick — silent unless verbose. Don't log every fsevents
        # wake or the log fills up with noise. Update state only.
        _write_state(state)
        return 0

    _log(f"tick: drift detected: {drift}")
    ok, fail = _apply_drift(drift)
    state["last_apply_iso"] = _now_iso()
    state["last_apply_ok"] = ok
    state["last_apply_fail"] = fail
    state.setdefault("total_apply_ok", 0)
    state["total_apply_ok"] += ok
    state.setdefault("total_apply_fail", 0)
    state["total_apply_fail"] += fail
    _write_state(state)
    return 0 if fail == 0 else 1


def cmd_enable(args: argparse.Namespace) -> int:
    if sys.platform != "darwin" and not args.force:
        print(
            "watch enable: only macOS (Darwin) is supported in M9.4. "
            "Linux/systemd parity is a follow-up slice. Re-run with "
            "--force to write the plist anyway (it won't load).",
            file=sys.stderr,
        )
        return 2

    program = _agent_continuity_path()
    if not pathlib.Path(program).exists() and program != "agent-continuity":
        print(f"watch enable: shim not found at {program}", file=sys.stderr)
        return 1

    LAUNCHAGENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHAGENT_PATH.write_text(_plist_xml(program), encoding="utf-8")
    print(f"wrote LaunchAgent: {LAUNCHAGENT_PATH}")

    # Unload first in case a stale one is loaded — idempotent.
    subprocess.run(
        ["launchctl", "unload", str(LAUNCHAGENT_PATH)],
        capture_output=True,
        check=False,
    )
    rc = subprocess.run(
        ["launchctl", "load", str(LAUNCHAGENT_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )
    if rc.returncode != 0:
        print(
            f"watch enable: launchctl load failed (rc={rc.returncode}): "
            f"{rc.stderr.strip()}",
            file=sys.stderr,
        )
        return 1
    print(f"loaded into launchd as {LAUNCHAGENT_LABEL}")
    _log("enable: LaunchAgent loaded")

    # Initial sync so state is consistent at the moment of enable.
    drift = _detect_drift()
    if drift:
        print(f"initial sync: wiring {drift}")
        ok, fail = _apply_drift(drift)
        print(f"  → {ok} ok, {fail} failed")
    else:
        print("initial sync: no drift (everything already wired)")

    print()
    print("watcher enabled. agent-continuity will auto-wire new agent")
    print("homes as they appear on disk. audit trail at:")
    print(f"  {LOG_PATH}")
    print()
    print("note: macOS may prompt for File and Folder Access permissions")
    print("the first time the watcher fires on a protected directory.")
    print("disable any time with: agent-continuity watch disable")
    return 0


def cmd_disable(_args: argparse.Namespace) -> int:
    if not LAUNCHAGENT_PATH.exists():
        print(f"watch disable: no LaunchAgent at {LAUNCHAGENT_PATH} (already disabled)")
        return 0
    rc = subprocess.run(
        ["launchctl", "unload", str(LAUNCHAGENT_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )
    if rc.returncode != 0:
        # Already unloaded is fine — keep going to remove plist
        print(
            f"watch disable: launchctl unload non-zero (rc={rc.returncode}); "
            "continuing to remove plist",
            file=sys.stderr,
        )
    try:
        LAUNCHAGENT_PATH.unlink()
        print(f"removed LaunchAgent: {LAUNCHAGENT_PATH}")
    except OSError as e:
        print(f"watch disable: failed to remove plist: {e}", file=sys.stderr)
        return 1
    _log("disable: LaunchAgent unloaded and removed")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    info: dict[str, Any] = {
        "platform": sys.platform,
        "launchagent_path": str(LAUNCHAGENT_PATH),
        "launchagent_present": LAUNCHAGENT_PATH.exists(),
        "log_path": str(LOG_PATH),
        "state_path": str(_state_path()),
    }
    state = _read_state()
    info["last_tick_iso"] = state.get("last_tick_iso")
    info["last_apply_iso"] = state.get("last_apply_iso")
    info["last_apply_ok"] = state.get("last_apply_ok", 0)
    info["last_apply_fail"] = state.get("last_apply_fail", 0)
    info["total_apply_ok"] = state.get("total_apply_ok", 0)
    info["total_apply_fail"] = state.get("total_apply_fail", 0)

    # Check launchctl list to confirm loaded vs just present-on-disk
    if info["launchagent_present"]:
        rc = subprocess.run(
            ["launchctl", "list", LAUNCHAGENT_LABEL],
            capture_output=True,
            text=True,
            check=False,
        )
        info["launchctl_loaded"] = (rc.returncode == 0)
    else:
        info["launchctl_loaded"] = False

    if args.json:
        print(json.dumps(info, indent=2))
        return 0

    enabled = info["launchctl_loaded"]
    print(f"watcher:               {'ENABLED' if enabled else 'disabled'}")
    print(f"  launchagent_path:    {info['launchagent_path']}")
    print(f"  launchagent_present: {info['launchagent_present']}")
    print(f"  launchctl_loaded:    {info['launchctl_loaded']}")
    print(f"  log_path:            {info['log_path']}")
    print(f"  last_tick:           {info['last_tick_iso'] or 'never'}")
    print(f"  last_apply:          {info['last_apply_iso'] or 'never'}")
    print(f"  last_apply_ok/fail:  {info['last_apply_ok']} / {info['last_apply_fail']}")
    print(f"  total_apply_ok/fail: {info['total_apply_ok']} / {info['total_apply_fail']}")
    return 0


# ──────────────────────────────────────────────────────────────────
# CLI

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="watch",
        description="opt-in agent-home watcher (M9.4). enable/disable/status/--tick.",
    )
    sub = parser.add_subparsers(dest="cmd")

    p_enable = sub.add_parser("enable", help="install LaunchAgent + initial sync")
    p_enable.add_argument(
        "--force",
        action="store_true",
        help="write plist even on non-Darwin (smoke + cross-platform dev)",
    )
    p_enable.set_defaults(func=cmd_enable)

    p_disable = sub.add_parser("disable", help="unload LaunchAgent + remove plist")
    p_disable.set_defaults(func=cmd_disable)

    p_status = sub.add_parser("status", help="show watcher state + audit summary")
    p_status.add_argument("--json", action="store_true", help="emit JSON")
    p_status.set_defaults(func=cmd_status)

    # --tick is not a subcommand because launchd invokes it as a flag-like
    # argument; we keep both shapes for ergonomic CLI use.
    parser.add_argument(
        "--tick",
        action="store_true",
        help=argparse.SUPPRESS,  # internal — launchd invokes us this way
    )

    args = parser.parse_args()
    if args.tick:
        return cmd_tick(args)
    if not args.cmd:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
