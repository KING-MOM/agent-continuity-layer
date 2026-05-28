#!/usr/bin/env bash
# bootstrap.sh — one-line install for agent-continuity-layer.
#
# Resolves the latest GitHub release, downloads the tarball + .sha256,
# verifies integrity, extracts to a tempdir, and runs install.sh.
# Prints next steps for `connect all --apply`.
#
# Stable URL (do not break):
#   curl -fsSL https://github.com/KING-MOM/agent-continuity-layer/releases/latest/download/bootstrap.sh | bash
#
# Trade-off (documented at length in docs/install.md):
#   - This script does the SAME sha256 corruption-check as a manual
#     install: it downloads the .sha256 file alongside the tarball
#     and refuses to proceed if they don't match.
#   - It is NOT a publisher-identity guarantee. An attacker who can
#     rewrite the tarball over the same transport can rewrite this
#     script too. Same honest framing as install.sh — integrity, not
#     identity. Signed releases are a future trust milestone.
#   - For users who want manual integrity verification before any
#     code runs, docs/install.md keeps the step-by-step tarball flow.
#
# This script never asks for sudo and never writes outside:
#   - $XDG_DATA_HOME/agent-continuity/v{VERSION}/   (install dir)
#   - $XDG_DATA_HOME/agent-continuity/active        (symlink)
#   - $HOME/.local/bin/agent-continuity             (PATH shim)
# It does NOT call `agent-continuity connect` — wiring local agents
# is a separate, explicit step the operator runs after install.

set -euo pipefail

REPO="KING-MOM/agent-continuity-layer"
GITHUB_API="https://api.github.com/repos/${REPO}"
GITHUB_DL="https://github.com/${REPO}/releases/download"

# ────────────────────────────────────────────────────────────────
# Pre-flight: required tools

_need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "error: bootstrap requires '${1}' on PATH" >&2
    return 1
  fi
}
_need curl
_need tar
_need python3   # used for JSON parsing of the GitHub API response

if command -v shasum >/dev/null 2>&1; then
  SHA_TOOL=(shasum -a 256 -c)
elif command -v sha256sum >/dev/null 2>&1; then
  SHA_TOOL=(sha256sum -c)
else
  echo "error: bootstrap needs either 'shasum' or 'sha256sum' on PATH" >&2
  exit 1
fi

# ────────────────────────────────────────────────────────────────
# Resolve latest release tag via the GitHub API.

echo "==> resolving latest release of ${REPO}"
LATEST_JSON="$(curl -fsSL "${GITHUB_API}/releases/latest")" || {
  echo "error: failed to query GitHub API — is the repo public?" >&2
  exit 1
}
TAG="$(printf '%s' "${LATEST_JSON}" | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["tag_name"])')"
VERSION="${TAG#v}"   # strip leading 'v'
echo "    latest: ${TAG}"

TARBALL_NAME="agent-continuity-v${VERSION}.tar.gz"
SHA_NAME="agent-continuity-v${VERSION}.sha256"

# ────────────────────────────────────────────────────────────────
# Download into an isolated tempdir.

WORKDIR="$(mktemp -d -t agent-continuity-bootstrap.XXXXXX)"
trap 'rm -rf "${WORKDIR}"' EXIT
cd "${WORKDIR}"

echo "==> downloading ${TARBALL_NAME}"
curl -fsSL -o "${TARBALL_NAME}" "${GITHUB_DL}/${TAG}/${TARBALL_NAME}"

echo "==> downloading ${SHA_NAME}"
curl -fsSL -o "${SHA_NAME}" "${GITHUB_DL}/${TAG}/${SHA_NAME}"

echo "==> verifying sha256 (corruption check — not publisher signature)"
if ! "${SHA_TOOL[@]}" "${SHA_NAME}"; then
  echo "error: sha256 mismatch — aborting before any install write" >&2
  exit 1
fi

echo "==> extracting"
tar -xzf "${TARBALL_NAME}"

echo "==> running install.sh"
"agent-continuity-v${VERSION}/scripts/install.sh" --from-tarball "${TARBALL_NAME}"

# ────────────────────────────────────────────────────────────────
# Next-steps banner. We deliberately do NOT auto-run `connect all`
# because writing into MCP-client configs is a real side effect on
# files the operator owns. Make them type the second command.

echo
echo "✓ installed agent-continuity v${VERSION}"
echo
echo "next steps:"
echo "  1. ensure \$HOME/.local/bin is on your PATH"
echo "     (open a new shell, or run: export PATH=\"\$HOME/.local/bin:\$PATH\")"
echo
echo "  2. preview what would be wired (read-only):"
echo "     agent-continuity connect doctor"
echo
echo "  3. wire your local agents (Claude Desktop, Cursor, Zed, thin skills):"
echo "     agent-continuity connect all --apply"
echo
echo "  4. restart any running MCP clients so they pick up the new config"
echo
echo "or kick the tires without touching real config:"
echo "  agent-continuity quickstart init"
echo "  agent-continuity quickstart run-fake-worker"
echo "  agent-continuity quickstart decisions list"
echo "  agent-continuity quickstart reset"
