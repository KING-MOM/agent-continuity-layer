#!/usr/bin/env python3
"""_phase1a_smoke.py — v0.5.0 cryptographic attribution smoke.

Covers the full Phase 1a surface in one suite:

  T1   key generate creates a sealed keypair at $XDG_CONFIG_HOME
  T2   key show --json omits the private key
  T3   sign + verify roundtrip via _crypto helpers
  T4   tamper detection: modifying a signed entry fails verification
  T5   regenerate without --force is refused
  T6   key rotate preserves human_actor_id, changes device_key_id
  T7   key rotate archives the old key file
  T8   decisions add WITHOUT a configured key produces unsigned entry + warning
  T9   decisions add WITH a configured key produces signed entry, no warning
  T10  signed-entry id is stable (recomputes correctly)
  T11  decisions verify path: signed entry verifies, tampered fails, unsigned reports no-signature
  T12  team init creates manifest with founding admin as the local key holder
  T13  team add-actor (admin-signed) appends a new actor binding
  T14  team add-actor by non-admin is refused
  T15  team add-actor idempotent: re-adding same device is a no-op
  T16  team verify validates the manifest signature against admin pubkeys
  T17  backward-compat: v1.0 (legacy) entries still parse and aren't rejected on read
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"


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
            self.failed.append((name, str(e)))
            print(f"   FAIL: {e}")
        except AssertionError as e:
            self.failed.append((name, f"assertion: {e}"))
            print(f"   FAIL: assertion: {e}")
        except Exception as e:
            self.failed.append((name, f"unexpected {type(e).__name__}: {e}"))
            print(f"   FAIL (unexpected): {type(e).__name__}: {e}")
        else:
            self.passed.append(name)
            print(f"   PASS")


def _env(cfg: pathlib.Path, state: pathlib.Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(cfg)
    if state is not None:
        env["XDG_STATE_HOME"] = str(state)
    return env


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, env=env, capture_output=True, text=True, timeout=30)


# ──────────────────────────────────────────────────────────────────
# T1-T7: key management

def t_key_generate_seals_file(td: pathlib.Path) -> None:
    cfg = td / "T1-cfg"
    r = _run([sys.executable, str(SCRIPTS / "_key.py"), "generate"], _env(cfg))
    if r.returncode != 0:
        raise SmokeError(f"generate rc={r.returncode}: {r.stderr}")
    key_path = cfg / "agent-continuity" / "device-key.json"
    if not key_path.exists():
        raise SmokeError(f"key file not at {key_path}")
    mode = oct(key_path.stat().st_mode & 0o777)
    if mode != "0o600":
        raise SmokeError(f"key file mode {mode}, expected 0o600")


def t_show_omits_private(td: pathlib.Path) -> None:
    cfg = td / "T2-cfg"
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate"], _env(cfg))
    r = _run([sys.executable, str(SCRIPTS / "_key.py"), "show", "--json"], _env(cfg))
    data = json.loads(r.stdout)
    if "private_key_pem" in data:
        raise SmokeError("show --json leaked private_key_pem")
    if "public_key_pem" not in data:
        raise SmokeError("show --json missing public_key_pem")


def t_sign_verify_roundtrip(td: pathlib.Path) -> None:
    cfg = td / "T3-cfg"
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate"], _env(cfg))
    # Use the crypto module directly
    os.environ["XDG_CONFIG_HOME"] = str(cfg)
    sys.path.insert(0, str(SCRIPTS))
    import importlib
    import _crypto, _key
    importlib.reload(_key)
    key = _key.load_device_key()
    priv = _crypto.private_key_from_pem(key["private_key_pem"])
    pub = _crypto.public_key_from_pem(key["public_key_pem"])
    entry = {"schema_version": "2.0", "ts": "2026-01-01T00:00:00Z",
             "decision": "test", "why": "test"}
    sig = _crypto.sign_decision_entry(entry, priv)
    entry["device_signature"] = sig
    if not _crypto.verify_decision_entry(entry, pub):
        raise SmokeError("clean entry failed verification")


def t_tamper_detection(td: pathlib.Path) -> None:
    cfg = td / "T4-cfg"
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate"], _env(cfg))
    os.environ["XDG_CONFIG_HOME"] = str(cfg)
    sys.path.insert(0, str(SCRIPTS))
    import importlib
    import _crypto, _key
    importlib.reload(_key)
    key = _key.load_device_key()
    priv = _crypto.private_key_from_pem(key["private_key_pem"])
    pub = _crypto.public_key_from_pem(key["public_key_pem"])
    entry = {"schema_version": "2.0", "ts": "2026-01-01T00:00:00Z",
             "decision": "original", "why": "test"}
    entry["device_signature"] = _crypto.sign_decision_entry(entry, priv)
    entry["decision"] = "tampered"
    if _crypto.verify_decision_entry(entry, pub):
        raise SmokeError("tampered entry verified (should have failed)")


def t_regenerate_refused(td: pathlib.Path) -> None:
    cfg = td / "T5-cfg"
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate"], _env(cfg))
    r = _run([sys.executable, str(SCRIPTS / "_key.py"), "generate"], _env(cfg))
    if r.returncode == 0:
        raise SmokeError("second generate succeeded without --force")


def t_rotate_preserves_human_id(td: pathlib.Path) -> None:
    cfg = td / "T6-cfg"
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate",
          "--human-actor-id", "human:rotate-test"], _env(cfg))
    key_path = cfg / "agent-continuity" / "device-key.json"
    old_data = json.loads(key_path.read_text())
    _run([sys.executable, str(SCRIPTS / "_key.py"), "rotate"], _env(cfg))
    new_data = json.loads(key_path.read_text())
    if new_data["human_actor_id"] != old_data["human_actor_id"]:
        raise SmokeError("rotate changed human_actor_id")
    if new_data["device_key_id"] == old_data["device_key_id"]:
        raise SmokeError("rotate did not change device_key_id")


def t_rotate_archives(td: pathlib.Path) -> None:
    cfg = td / "T7-cfg"
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate"], _env(cfg))
    _run([sys.executable, str(SCRIPTS / "_key.py"), "rotate"], _env(cfg))
    key_dir = cfg / "agent-continuity"
    archives = list(key_dir.glob("device-key-rotated-*.json"))
    if len(archives) != 1:
        raise SmokeError(f"expected 1 archive, found {len(archives)}")


# ──────────────────────────────────────────────────────────────────
# T8-T11: decisions sign / verify integration

def t_decisions_unsigned_path(td: pathlib.Path) -> None:
    cfg = td / "T8-cfg"
    state = td / "T8-state"
    r = _run([sys.executable, str(SCRIPTS / "_decisions.py"), "add",
              "--adapter", "human", "--decision", "Unsigned",
              "--why", "No key configured", "--ref", "smoke:T8",
              "--repo", "test-repo"], _env(cfg, state))
    if r.returncode != 0:
        raise SmokeError(f"add rc={r.returncode}: {r.stderr}")
    if "unsigned" not in r.stderr:
        raise SmokeError("no warning about unsigned write")
    line = (state / "agent-continuity" / "decisions.jsonl").read_text().strip()
    entry = json.loads(line)
    if "device_signature" in entry:
        raise SmokeError("unsigned entry has device_signature field")


def t_decisions_signed_path(td: pathlib.Path) -> None:
    cfg = td / "T9-cfg"
    state = td / "T9-state"
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate",
          "--human-actor-id", "human:smoke:T9"], _env(cfg, state))
    r = _run([sys.executable, str(SCRIPTS / "_decisions.py"), "add",
              "--adapter", "human", "--decision", "Signed",
              "--why", "Key configured", "--ref", "smoke:T9",
              "--repo", "test-repo"], _env(cfg, state))
    if r.returncode != 0:
        raise SmokeError(f"add rc={r.returncode}: {r.stderr}")
    if "unsigned" in r.stderr:
        raise SmokeError("unexpected unsigned warning when key was configured")
    line = (state / "agent-continuity" / "decisions.jsonl").read_text().strip()
    entry = json.loads(line)
    for f in ("human_actor_id", "device_key_id", "signer_consent", "device_signature"):
        if f not in entry:
            raise SmokeError(f"signed entry missing field {f!r}")
    if entry["signer_consent"] != "implicit":
        raise SmokeError(f"signer_consent={entry['signer_consent']!r}, expected 'implicit'")
    if entry["human_actor_id"] != "human:smoke:T9":
        raise SmokeError(f"human_actor_id mismatch: {entry['human_actor_id']!r}")


def t_signed_id_stable(td: pathlib.Path) -> None:
    cfg = td / "T10-cfg"
    state = td / "T10-state"
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate"], _env(cfg, state))
    _run([sys.executable, str(SCRIPTS / "_decisions.py"), "add",
          "--adapter", "human", "--decision", "ID stability test",
          "--why", "recompute id should match written id",
          "--ref", "smoke:T10", "--repo", "test-repo"], _env(cfg, state))
    line = (state / "agent-continuity" / "decisions.jsonl").read_text().strip()
    entry = json.loads(line)
    os.environ["XDG_CONFIG_HOME"] = str(cfg)
    os.environ["XDG_STATE_HOME"] = str(state)
    sys.path.insert(0, str(SCRIPTS))
    import importlib
    import _decisions
    importlib.reload(_decisions)
    _decisions._crypto = None; _decisions._key = None
    recomputed = _decisions._compute_id(entry)
    if recomputed != entry["id"]:
        raise SmokeError(f"id mismatch: written={entry['id'][:16]}, recomputed={recomputed[:16]}")


def t_verify_paths(td: pathlib.Path) -> None:
    cfg = td / "T11-cfg"
    state = td / "T11-state"
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate"], _env(cfg, state))
    _run([sys.executable, str(SCRIPTS / "_decisions.py"), "add",
          "--adapter", "human", "--decision", "Verify test",
          "--why", "verify roundtrip", "--ref", "smoke:T11",
          "--repo", "test-repo"], _env(cfg, state))
    line = (state / "agent-continuity" / "decisions.jsonl").read_text().strip()
    entry = json.loads(line)
    os.environ["XDG_CONFIG_HOME"] = str(cfg)
    os.environ["XDG_STATE_HOME"] = str(state)
    sys.path.insert(0, str(SCRIPTS))
    import importlib
    import _decisions
    importlib.reload(_decisions)
    _decisions._crypto = None; _decisions._key = None
    verified, reason = _decisions._verify_entry_against_local_key(entry)
    if not verified or reason != "verified":
        raise SmokeError(f"signed entry didn't verify: {reason}")
    tampered = dict(entry); tampered["decision"] = "tampered"
    verified, reason = _decisions._verify_entry_against_local_key(tampered)
    if verified or reason != "signature-invalid":
        raise SmokeError(f"tampered entry verified or wrong reason: {reason}")
    unsigned = {k: v for k, v in entry.items() if k != "device_signature"}
    verified, reason = _decisions._verify_entry_against_local_key(unsigned)
    if verified or reason != "no-signature":
        raise SmokeError(f"unsigned reported wrong: verified={verified}, reason={reason}")


# ──────────────────────────────────────────────────────────────────
# T12-T16: team manifest

def t_team_init(td: pathlib.Path) -> None:
    cfg = td / "T12-cfg"
    repo = td / "T12-repo"
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate",
          "--human-actor-id", "human:T12:admin"], _env(cfg))
    r = _run([sys.executable, str(SCRIPTS / "_team.py"),
              "--path", str(repo), "init",
              "--team-name", "T12 Team"], _env(cfg))
    if r.returncode != 0:
        raise SmokeError(f"init rc={r.returncode}: {r.stderr}")
    mp = repo / "team-manifest.json"
    if not mp.exists():
        raise SmokeError("manifest not created")
    manifest = json.loads(mp.read_text())
    if manifest["founding_admin_human_actor_id"] != "human:T12:admin":
        raise SmokeError("founding admin not set correctly")


def t_team_add_actor_admin(td: pathlib.Path) -> None:
    cfg_admin = td / "T13-admin"
    cfg_member = td / "T13-member"
    repo = td / "T13-repo"
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate",
          "--human-actor-id", "human:T13:admin"], _env(cfg_admin))
    _run([sys.executable, str(SCRIPTS / "_team.py"),
          "--path", str(repo), "init"], _env(cfg_admin))
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate",
          "--human-actor-id", "human:T13:member"], _env(cfg_member))
    pubkey_out = repo / "T13-member.pem"
    _run([sys.executable, str(SCRIPTS / "_key.py"), "export-pubkey",
          "--out", str(pubkey_out)], _env(cfg_member))
    r = _run([sys.executable, str(SCRIPTS / "_team.py"),
              "--path", str(repo), "add-actor",
              "--human-actor-id", "human:T13:member",
              "--pubkey-file", str(pubkey_out)], _env(cfg_admin))
    if r.returncode != 0:
        raise SmokeError(f"add-actor rc={r.returncode}: {r.stderr}")


def t_team_add_actor_non_admin_refused(td: pathlib.Path) -> None:
    cfg_admin = td / "T14-admin"
    cfg_member = td / "T14-member"
    repo = td / "T14-repo"
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate",
          "--human-actor-id", "human:T14:admin"], _env(cfg_admin))
    _run([sys.executable, str(SCRIPTS / "_team.py"),
          "--path", str(repo), "init"], _env(cfg_admin))
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate",
          "--human-actor-id", "human:T14:member"], _env(cfg_member))
    pubkey_out = repo / "T14-member.pem"
    _run([sys.executable, str(SCRIPTS / "_key.py"), "export-pubkey",
          "--out", str(pubkey_out)], _env(cfg_member))
    r = _run([sys.executable, str(SCRIPTS / "_team.py"),
              "--path", str(repo), "add-actor",
              "--human-actor-id", "human:T14:hostile",
              "--pubkey-file", str(pubkey_out)], _env(cfg_member))
    if r.returncode == 0:
        raise SmokeError("non-admin add-actor succeeded (should have failed)")


def t_team_add_actor_idempotent(td: pathlib.Path) -> None:
    cfg_admin = td / "T15-admin"
    cfg_member = td / "T15-member"
    repo = td / "T15-repo"
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate",
          "--human-actor-id", "human:T15:admin"], _env(cfg_admin))
    _run([sys.executable, str(SCRIPTS / "_team.py"),
          "--path", str(repo), "init"], _env(cfg_admin))
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate",
          "--human-actor-id", "human:T15:member"], _env(cfg_member))
    pubkey_out = repo / "T15-member.pem"
    _run([sys.executable, str(SCRIPTS / "_key.py"), "export-pubkey",
          "--out", str(pubkey_out)], _env(cfg_member))
    _run([sys.executable, str(SCRIPTS / "_team.py"),
          "--path", str(repo), "add-actor",
          "--human-actor-id", "human:T15:member",
          "--pubkey-file", str(pubkey_out)], _env(cfg_admin))
    r = _run([sys.executable, str(SCRIPTS / "_team.py"),
              "--path", str(repo), "add-actor",
              "--human-actor-id", "human:T15:member",
              "--pubkey-file", str(pubkey_out)], _env(cfg_admin))
    if r.returncode != 0:
        raise SmokeError(f"second add rc={r.returncode}: {r.stderr}")
    if "already present" not in r.stderr:
        raise SmokeError(f"expected idempotent skip; got: {r.stderr}")


def t_team_verify(td: pathlib.Path) -> None:
    cfg = td / "T16-cfg"
    repo = td / "T16-repo"
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate"], _env(cfg))
    _run([sys.executable, str(SCRIPTS / "_team.py"),
          "--path", str(repo), "init"], _env(cfg))
    r = _run([sys.executable, str(SCRIPTS / "_team.py"),
              "--path", str(repo), "verify"], _env(cfg))
    if r.returncode != 0:
        raise SmokeError(f"verify rc={r.returncode}: {r.stderr}")
    if "VERIFIED" not in r.stdout:
        raise SmokeError(f"verify output unexpected: {r.stdout!r}")


# ──────────────────────────────────────────────────────────────────
# T17: backward compatibility

def t_legacy_v1_entries_accepted(td: pathlib.Path) -> None:
    cfg = td / "T17-cfg"
    state = td / "T17-state"
    decisions = state / "agent-continuity" / "decisions.jsonl"
    decisions.parent.mkdir(parents=True, exist_ok=True)
    legacy = {
        "schema_version": "1.0",
        "id": "abc123",
        "ts": "2026-05-01T00:00:00Z",
        "adapter": "human",
        "repo": "legacy-test",
        "decision": "Pre-v0.5 decision",
        "why": "From an older substrate version",
        "refs": ["legacy:test"],
    }
    decisions.write_text(json.dumps(legacy) + "\n")
    os.environ["XDG_CONFIG_HOME"] = str(cfg)
    os.environ["XDG_STATE_HOME"] = str(state)
    sys.path.insert(0, str(SCRIPTS))
    import importlib
    import _decisions
    importlib.reload(_decisions)
    errors = _decisions._validate_entry(legacy)
    if errors:
        raise SmokeError(f"v1.0 entry rejected by validator: {errors}")


# ──────────────────────────────────────────────────────────────────

def main() -> int:
    td = pathlib.Path(tempfile.mkdtemp(prefix="phase1a-smoke."))
    print(f"sandbox: {td}\n")
    runner = _Runner()
    try:
        runner.check("T1: key generate seals file at $XDG_CONFIG_HOME mode 0600", lambda: t_key_generate_seals_file(td))
        runner.check("T2: key show --json omits private key", lambda: t_show_omits_private(td))
        runner.check("T3: sign + verify roundtrip", lambda: t_sign_verify_roundtrip(td))
        runner.check("T4: tamper detection rejects modified entry", lambda: t_tamper_detection(td))
        runner.check("T5: regenerate without --force is refused", lambda: t_regenerate_refused(td))
        runner.check("T6: rotate preserves human_actor_id, changes device_key_id", lambda: t_rotate_preserves_human_id(td))
        runner.check("T7: rotate archives the old key file", lambda: t_rotate_archives(td))
        runner.check("T8: decisions add unsigned (no key) emits warning", lambda: t_decisions_unsigned_path(td))
        runner.check("T9: decisions add signed (with key) writes crypto fields", lambda: t_decisions_signed_path(td))
        runner.check("T10: signed-entry id is stable across recompute", lambda: t_signed_id_stable(td))
        runner.check("T11: verify paths (signed / tampered / unsigned)", lambda: t_verify_paths(td))
        runner.check("T12: team init creates manifest with local key as admin", lambda: t_team_init(td))
        runner.check("T13: team add-actor by admin succeeds", lambda: t_team_add_actor_admin(td))
        runner.check("T14: team add-actor by non-admin is refused", lambda: t_team_add_actor_non_admin_refused(td))
        runner.check("T15: team add-actor idempotent on duplicate device", lambda: t_team_add_actor_idempotent(td))
        runner.check("T16: team verify validates manifest signature", lambda: t_team_verify(td))
        runner.check("T17: legacy v1.0 entries still accepted on read", lambda: t_legacy_v1_entries_accepted(td))
    finally:
        if not runner.failed:
            shutil.rmtree(td, ignore_errors=True)

    total = len(runner.passed) + len(runner.failed)
    print()
    print(f"phase1a smoke: {len(runner.passed)}/{total} passed, {len(runner.failed)} failed")
    for name in runner.passed:
        print(f"  PASS  {name}")
    for name, err in runner.failed:
        print(f"  FAIL  {name}: {err}")
    return 0 if not runner.failed else 1


if __name__ == "__main__":
    sys.exit(main())
