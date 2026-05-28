#!/usr/bin/env python3
"""_sbom.py — M15.2 CycloneDX 1.5 SBOM generator.

Emits a CycloneDX 1.5 JSON Software Bill of Materials describing this
release. Reproducible: same VERSION + same git commit → byte-identical
SBOM. Timestamps come from SOURCE_DATE_EPOCH, the serial number is a
deterministic UUIDv5 derived from version + commit.

Why CycloneDX 1.5 (not SPDX, not SWID):
  - Current industry default for OSS SBOMs (k8s, npm, container ecosystem).
  - JSON-native: easier to validate and diff than SPDX tag-value.
  - Supported by all major SBOM tooling (Syft, Trivy, Grype, Anchore).
  - 1.5 is the most recent fully-implemented spec as of this writing.

Components in this SBOM:
  - The substrate itself (application, MIT-licensed, purl pkg:github/...)
  - bash (runtime requirement)
  - python3 ≥ 3.9 (runtime requirement)

The substrate has zero PyPI / npm / system-package dependencies beyond
the two above, so the component list is genuinely complete (not a
truncated snapshot of a larger dependency graph).

Output format:
  agent-continuity-vX.Y.Z.cdx.json — CycloneDX JSON, alongside the
  tarball + .sha256 in dist/. The .cdx.json extension is the
  CycloneDX-recommended file convention for JSON SBOMs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import sys
import uuid

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSION_FILE = REPO_ROOT / "core" / "VERSION"

# Fixed namespace UUID for deterministic UUIDv5 generation. The value
# is arbitrary but must be stable across builds of this project so the
# SBOM serial-number is reproducible at a given (version, commit).
# Chosen as the nil UUID for clarity; this is internal to the project
# and has no security significance.
_SBOM_NAMESPACE = uuid.UUID("00000000-0000-0000-0000-000000000000")


def _read_version() -> str:
    return VERSION_FILE.read_text().strip().splitlines()[0]


def _read_commit_sha() -> str:
    """Best-effort: get the current git commit SHA. Falls back to
    'unknown' if not in a git repo (e.g., running from an installed
    substrate without .git/). Reproducibility-impacting: when unknown,
    the serialNumber falls back to a fixed value so the SBOM stays
    deterministic for non-git callers."""
    import subprocess
    try:
        p = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return "unknown"


def _epoch() -> int:
    """Honor SOURCE_DATE_EPOCH for reproducible-builds compliance.
    Falls back to the current commit's timestamp; finally to 0."""
    val = os.environ.get("SOURCE_DATE_EPOCH")
    if val:
        try:
            return int(val)
        except ValueError:
            pass
    import subprocess
    try:
        p = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "log", "-1", "--format=%ct", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if p.returncode == 0 and p.stdout.strip():
            return int(p.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError, ValueError):
        pass
    return 0


def _iso(epoch: int) -> str:
    return dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _serial_number(version: str, commit: str) -> str:
    """Deterministic UUIDv5 from version + commit → stable serialNumber.
    Two SBOMs at the same release point get the same serial."""
    name = f"agent-continuity-layer@v{version}@{commit}"
    return f"urn:uuid:{uuid.uuid5(_SBOM_NAMESPACE, name)}"


def build_sbom(
    version: str,
    commit: str,
    epoch: int,
    tarball_sha256: str | None = None,
) -> dict:
    """Build the CycloneDX 1.5 SBOM document.

    tarball_sha256: optional sha256 of the release tarball. When
    provided, it lands on the main component's `hashes` field, so the
    SBOM is cryptographically tied to the specific artifact.
    """
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "serialNumber": _serial_number(version, commit),
        "metadata": {
            "timestamp": _iso(epoch),
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": "agent-continuity-layer/scripts/_sbom.py",
                        "version": version,
                        "description": (
                            "CycloneDX 1.5 SBOM generator built into the substrate. "
                            "Source: scripts/_sbom.py in this repo."
                        ),
                    }
                ]
            },
            "component": {
                "type": "application",
                "bom-ref": f"pkg:github/KING-MOM/agent-continuity-layer@v{version}",
                "name": "agent-continuity-layer",
                "version": version,
                "description": (
                    "Durable memory substrate for AI-agent work — preserves "
                    "context, decisions, handoffs, and artifacts across "
                    "agents, models, tools, machines, and sessions."
                ),
                "licenses": [{"license": {"id": "MIT"}}],
                "purl": f"pkg:github/KING-MOM/agent-continuity-layer@v{version}",
                "externalReferences": [
                    {
                        "type": "vcs",
                        "url": "https://github.com/KING-MOM/agent-continuity-layer",
                    },
                    {
                        "type": "website",
                        "url": "https://github.com/KING-MOM/agent-continuity-layer",
                    },
                    {
                        "type": "documentation",
                        "url": (
                            "https://github.com/KING-MOM/agent-continuity-layer/"
                            "blob/main/README.md"
                        ),
                    },
                    {
                        "type": "vulnerability-assertion",
                        "url": (
                            "https://github.com/KING-MOM/agent-continuity-layer/"
                            "blob/main/SECURITY.md"
                        ),
                    },
                ],
            },
            "supplier": {
                "name": "KING-MOM",
                "url": ["https://github.com/KING-MOM"],
            },
        },
        "components": [
            {
                "type": "application",
                "bom-ref": "pkg:generic/bash",
                "name": "bash",
                "scope": "required",
                "description": (
                    "POSIX shell. Runtime requirement for the installer, "
                    "bootstrap, dispatcher, and wrapper scripts. Any "
                    "modern bash (≥ 4.0) is sufficient; macOS's system "
                    "bash 3.2 will not work for the parts using "
                    "associative arrays."
                ),
                "purl": "pkg:generic/bash",
            },
            {
                "type": "application",
                "bom-ref": "pkg:generic/python@3.9",
                "name": "python3",
                "version": ">=3.9",
                "scope": "required",
                "description": (
                    "Python interpreter for the core helpers "
                    "(_doctor.py, _decisions.py, _context.py, _worker.py, "
                    "_handoff.py, _project.py, _migrate.py, _repro_tar.py, "
                    "_sbom.py). Only Python standard library is used — "
                    "zero PyPI dependencies."
                ),
                "purl": "pkg:generic/python@3.9",
            },
        ],
        "dependencies": [
            {
                "ref": f"pkg:github/KING-MOM/agent-continuity-layer@v{version}",
                "dependsOn": [
                    "pkg:generic/bash",
                    "pkg:generic/python@3.9",
                ],
            }
        ],
    }
    if tarball_sha256:
        sbom["metadata"]["component"]["hashes"] = [
            {"alg": "SHA-256", "content": tarball_sha256}
        ]
    return sbom


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Generate a CycloneDX 1.5 SBOM for the current substrate. "
            "Reproducible given SOURCE_DATE_EPOCH and a fixed commit."
        )
    )
    ap.add_argument("--output", required=True, help="output path for the .cdx.json")
    ap.add_argument(
        "--tarball-sha256",
        default=None,
        help=(
            "sha256 of the release tarball to bind to the SBOM's main "
            "component. Omit if generating SBOM independently of a build."
        ),
    )
    ap.add_argument(
        "--version",
        default=None,
        help="substrate version (default: read from core/VERSION)",
    )
    args = ap.parse_args()

    version = args.version or _read_version()
    commit = _read_commit_sha()
    epoch = _epoch()
    sbom = build_sbom(version, commit, epoch, args.tarball_sha256)

    out_path = pathlib.Path(args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(sbom, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote SBOM: {out_path}")
    print(f"  version:   {version}")
    print(f"  commit:    {commit}")
    print(f"  serial:    {sbom['serialNumber']}")
    print(f"  timestamp: {sbom['metadata']['timestamp']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
