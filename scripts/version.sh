#!/usr/bin/env bash
# version.sh — print the substrate version from core/VERSION.
#
# Single source of truth: core/VERSION (one line, semver). M12.2's
# bin/agent-continuity --version will call this. Doctor's check_repo
# reports the same value. release.sh build reads the same file when
# naming the tarball, so a release artifact and the in-repo --version
# can never drift.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cat "${SCRIPT_DIR}/../core/VERSION"
