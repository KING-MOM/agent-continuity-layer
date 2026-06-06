#!/usr/bin/env python3
"""M16.0 handoff smoke — export / inspect / import + path-mismatch guard.

Builds two isolated sandbox HOMEs:
  - source: pre-populated with decisions log + trust policy + project
    registry + a fake Claude Code session at the right encoded path
  - target: empty

Then exercises:
  T1  export (state only, default)
  T2  inspect prints manifest with expected shape
  T3  import into empty target restores byte-identical state
  T4  re-import backs up existing state under XDG_DATA_HOME
  T5  --include-claude packages ~/.claude/projects/ contents
  T6  import of a claude-included bundle into a target whose HOME path
      differs from source SKIPS claude restoration and prints warning
  T7  --no-state with no --include-claude refuses (rc=64)
  T8  bundle with mismatched schema_version refuses (rc=2)
  T9  malicious bundle path traversal refuses and writes nothing outside target
"""

from __future__ import annotations

import json
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HANDOFF_SH = REPO_ROOT / "scripts" / "handoff.sh"


class SmokeError(Exception):
    pass


class _Runner:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def check(self, name: str, fn) -> None:
        print(f"── {name} ──")
        try:
            fn()
        except SmokeError as e:
            print(f"   FAIL: {e}")
            self.failed.append((name, str(e)))
        except Exception as e:  # noqa: BLE001
            print(f"   FAIL: {type(e).__name__}: {e}")
            self.failed.append((name, f"{type(e).__name__}: {e}"))
        else:
            print("   PASS")
            self.passed.append(name)


def _env(home: pathlib.Path) -> dict[str, str]:
    e = os.environ.copy()
    e["HOME"] = str(home)
    e["XDG_CONFIG_HOME"] = str(home / ".config")
    e["XDG_STATE_HOME"] = str(home / ".local/state")
    e["XDG_CACHE_HOME"] = str(home / ".cache")
    e["XDG_DATA_HOME"] = str(home / ".local/share")
    return e


def _populate_source(home: pathlib.Path) -> dict[str, str]:
    """Pre-populate a source HOME with realistic substrate state.
    Returns the canonical contents for later byte-identity checks."""
    (home / ".config/agent-continuity").mkdir(parents=True, exist_ok=True)
    (home / ".local/state/agent-continuity").mkdir(parents=True, exist_ok=True)
    (home / ".cache/agent-continuity/queue").mkdir(parents=True, exist_ok=True)

    trust = json.dumps({
        "schema_version": "1.0",
        "default": {
            "allow_kinds": ["test"],
            "trust_levels": ["low"],
            "adapters": ["human", "claude"],
        },
        "grants": [],
    })
    (home / ".config/agent-continuity/trust-policy.json").write_text(trust)

    decision = json.dumps({
        "id": "fixture-decision-abc",
        "schema_version": "1.0",
        "adapter": "human",
        "repo": "test-project",
        "decision": "Smoke fixture decision.",
        "why": "Used by the handoff smoke to verify byte-identity across roundtrip.",
        "refs": [],
        "ts": "2026-05-28T00:00:00Z",
    })
    (home / ".local/state/agent-continuity/decisions.jsonl").write_text(decision + "\n")

    return {
        "trust-policy.json": trust,
        "decisions.jsonl": decision + "\n",
    }


def _populate_claude(home: pathlib.Path) -> str:
    """Create a fake Claude Code session at an encoded path matching this HOME."""
    encoded = str(home).replace("/", "-")[1:]  # strip leading slash, replace remaining /
    encoded = "-" + encoded if not encoded.startswith("-") else encoded
    projects = home / ".claude/projects" / encoded
    projects.mkdir(parents=True, exist_ok=True)
    session_path = projects / "fixture-session.jsonl"
    session_content = '{"role":"user","content":"smoke fixture"}\n'
    session_path.write_text(session_content)
    return session_content


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(HANDOFF_SH)] + args,
        env=env,
        capture_output=True,
        text=True,
    )


# ───────────────────────────────── tests

def make_workspace() -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """Allocate sandbox dirs + return (source_home, target_home, bundle_path)."""
    base = pathlib.Path(tempfile.mkdtemp(prefix="m16-handoff-smoke."))
    src = base / "source"
    tgt = base / "target"
    src.mkdir()
    tgt.mkdir()
    return src, tgt, base / "handoff.tar.gz"


def check_export_default(src, tgt, bundle, fixtures):
    _populate_source(src)
    p = _run(["export", "--to", str(bundle)], _env(src))
    if p.returncode != 0:
        raise SmokeError(f"export failed rc={p.returncode}: {p.stderr}")
    if not bundle.exists():
        raise SmokeError("export claimed success but bundle not created")


def check_inspect_shape(src, tgt, bundle, fixtures):
    p = _run(["inspect", str(bundle)], _env(src))
    if p.returncode != 0:
        raise SmokeError(f"inspect failed rc={p.returncode}")
    mf = json.loads(p.stdout)
    if mf.get("schema_version") != "1.0":
        raise SmokeError(f"manifest schema_version != 1.0: {mf}")
    if not mf.get("included", {}).get("agent_continuity_config"):
        raise SmokeError("manifest doesn't claim agent_continuity_config")
    if mf.get("included", {}).get("claude_sessions"):
        raise SmokeError("manifest claims claude_sessions when --include-claude was NOT used")


def check_import_restores_byte_identical(src, tgt, bundle, fixtures):
    p = _run(["import", str(bundle)], _env(tgt))
    if p.returncode != 0:
        raise SmokeError(f"import failed rc={p.returncode}: {p.stderr}")
    restored_trust = (tgt / ".config/agent-continuity/trust-policy.json").read_text()
    if restored_trust != fixtures["trust-policy.json"]:
        raise SmokeError("trust-policy.json content drifted across handoff")
    restored_dec = (tgt / ".local/state/agent-continuity/decisions.jsonl").read_text()
    if restored_dec != fixtures["decisions.jsonl"]:
        raise SmokeError("decisions.jsonl content drifted across handoff")


def check_reimport_backs_up(src, tgt, bundle, fixtures):
    # Re-import: target already has state. Should produce a backup dir.
    before_dirs = set((tgt / ".local/share").glob("agent-continuity-handoff-backup-*"))
    p = _run(["import", str(bundle)], _env(tgt))
    if p.returncode != 0:
        raise SmokeError(f"re-import failed rc={p.returncode}: {p.stderr}")
    after_dirs = set((tgt / ".local/share").glob("agent-continuity-handoff-backup-*"))
    new_dirs = after_dirs - before_dirs
    if not new_dirs:
        raise SmokeError("re-import did not create a backup dir under XDG_DATA_HOME")


def check_include_claude_packages_transcripts(src, tgt, bundle, fixtures):
    # Refresh source with claude content
    _populate_claude(src)
    p = _run(["export", "--to", str(bundle), "--include-claude"], _env(src))
    if p.returncode != 0:
        raise SmokeError(f"export --include-claude failed rc={p.returncode}: {p.stderr}")
    # Inspect the tarball directly: must contain at least one file under handoff/claude/
    found_claude = False
    with tarfile.open(bundle, "r:gz") as tar:
        for m in tar.getmembers():
            if m.name.startswith("handoff/claude/") and m.isfile():
                found_claude = True
                break
    if not found_claude:
        raise SmokeError("bundle with --include-claude has no handoff/claude/* members")


def check_path_mismatch_skips_claude(src, tgt, bundle, fixtures):
    # Target HOME differs from source HOME by construction (different temp dirs).
    # Bundle includes claude. Import should skip claude restoration.
    # First clean target so we get a fresh import path.
    shutil.rmtree(tgt, ignore_errors=True)
    tgt.mkdir()

    p = _run(["import", str(bundle)], _env(tgt))
    if p.returncode != 0:
        raise SmokeError(f"import (with claude in bundle, path mismatch) failed rc={p.returncode}: {p.stderr}")
    # Must say SKIPPING Claude sessions in stderr
    if "SKIPPING Claude sessions" not in p.stderr:
        raise SmokeError(
            f"import did not skip Claude sessions on path mismatch; "
            f"stderr: {p.stderr[:300]}"
        )
    # No ~/.claude/ should exist on target
    if (tgt / ".claude").exists():
        raise SmokeError("path mismatch: claude restoration should have been skipped, but ~/.claude/ exists")


def check_nothing_to_export_refuses(src, tgt, bundle, fixtures):
    # --no-state without --include-claude → nothing to export → rc=64
    p = _run(["export", "--to", str(bundle), "--no-state"], _env(src))
    if p.returncode != 64:
        raise SmokeError(f"expected rc=64 for empty export, got {p.returncode}")


def check_schema_version_mismatch_refuses(src, tgt, bundle, fixtures):
    # Rewrite bundle manifest to have a bad schema_version, expect rc=2
    bad_bundle = bundle.parent / "bad.tar.gz"
    with tarfile.open(bundle, "r:gz") as src_tar:
        with tarfile.open(bad_bundle, "w:gz") as dst_tar:
            for m in src_tar.getmembers():
                if m.name == "handoff/manifest.json":
                    fh = src_tar.extractfile(m)
                    data = json.loads(fh.read())
                    data["schema_version"] = "99.0"
                    new_bytes = json.dumps(data).encode()
                    import io as _io
                    new_m = tarfile.TarInfo(m.name)
                    new_m.size = len(new_bytes)
                    new_m.mtime = m.mtime
                    new_m.mode = m.mode
                    dst_tar.addfile(new_m, _io.BytesIO(new_bytes))
                else:
                    fh = src_tar.extractfile(m)
                    if fh:
                        dst_tar.addfile(m, fh)
    shutil.rmtree(tgt, ignore_errors=True)
    tgt.mkdir()
    p = _run(["import", str(bad_bundle)], _env(tgt))
    if p.returncode != 2:
        raise SmokeError(f"expected rc=2 for schema_version mismatch, got {p.returncode}")


def check_path_traversal_refuses(src, tgt, bundle, fixtures):
    evil_bundle = bundle.parent / "evil-traversal.tar.gz"
    escaped = tgt.parent / "escaped-by-handoff.txt"
    escaped.unlink(missing_ok=True)
    shutil.rmtree(tgt, ignore_errors=True)
    tgt.mkdir()

    manifest = {
        "schema_version": "1.0",
        "source": {
            "device_hostname": "evil-fixture",
            "home": str(tgt),
            "substrate_version": "0.0.0-smoke",
        },
        "included": {
            "agent_continuity_config": True,
            "agent_continuity_state": False,
            "agent_continuity_queue": False,
            "claude_sessions": False,
        },
    }
    with tarfile.open(evil_bundle, "w:gz") as tar:
        manifest_bytes = json.dumps(manifest).encode()
        manifest_info = tarfile.TarInfo("handoff/manifest.json")
        manifest_info.size = len(manifest_bytes)
        tar.addfile(manifest_info, io.BytesIO(manifest_bytes))

        payload = b"should not land outside target\n"
        evil_info = tarfile.TarInfo(
            "handoff/agent-continuity/config/../../../../escaped-by-handoff.txt"
        )
        evil_info.size = len(payload)
        evil_info.mode = 0o600
        tar.addfile(evil_info, io.BytesIO(payload))

    p = _run(["import", str(evil_bundle), "--no-backup"], _env(tgt))
    if p.returncode == 0:
        raise SmokeError("malicious traversal bundle imported successfully")
    if escaped.exists():
        raise SmokeError(f"path traversal wrote outside target: {escaped}")


def main() -> int:
    if not HANDOFF_SH.exists():
        print(f"error: {HANDOFF_SH} not found", file=sys.stderr)
        return 1

    runner = _Runner()
    src, tgt, bundle = make_workspace()
    print(f"sandbox: {src.parent}")
    print()
    fixtures = _populate_source(src)

    try:
        runner.check("T1: export default (state only)",
                     lambda: check_export_default(src, tgt, bundle, fixtures))
        runner.check("T2: inspect shows correct manifest shape",
                     lambda: check_inspect_shape(src, tgt, bundle, fixtures))
        runner.check("T3: import restores byte-identical state",
                     lambda: check_import_restores_byte_identical(src, tgt, bundle, fixtures))
        runner.check("T4: re-import backs up existing state",
                     lambda: check_reimport_backs_up(src, tgt, bundle, fixtures))
        runner.check("T5: --include-claude packages transcripts",
                     lambda: check_include_claude_packages_transcripts(src, tgt, bundle, fixtures))
        runner.check("T6: target HOME mismatch skips claude restoration",
                     lambda: check_path_mismatch_skips_claude(src, tgt, bundle, fixtures))
        runner.check("T7: --no-state without --include-claude refuses (rc=64)",
                     lambda: check_nothing_to_export_refuses(src, tgt, bundle, fixtures))
        runner.check("T8: schema_version mismatch refuses import (rc=2)",
                     lambda: check_schema_version_mismatch_refuses(src, tgt, bundle, fixtures))
        runner.check("T9: path traversal bundle refuses import",
                     lambda: check_path_traversal_refuses(src, tgt, bundle, fixtures))
    finally:
        if not runner.failed:
            shutil.rmtree(src.parent, ignore_errors=True)

    total = len(runner.passed) + len(runner.failed)
    print()
    print(f"handoff smoke: {len(runner.passed)}/{total} passed, {len(runner.failed)} failed")
    for name in runner.passed:
        print(f"  PASS  {name}")
    for name, msg in runner.failed:
        print(f"  FAIL  {name}  —  {msg}")
    if runner.failed:
        print(f"  sandbox preserved: {src.parent}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
