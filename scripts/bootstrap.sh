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
# Argument parsing
#
# --connect-all  Opt-in: after install completes, also run
#                `agent-continuity connect all --apply`. This writes
#                into Claude Desktop / Cursor / Zed config files and
#                installs thin skills into Claude/Codex/OpenClaw
#                homes. Off by default to keep `curl … | bash` from
#                silently touching third-party app configs.

CONNECT_ALL=0
NO_VERIFY=0
UPGRADE=0
for arg in "$@"; do
  case "${arg}" in
    --connect-all) CONNECT_ALL=1 ;;
    --no-verify) NO_VERIFY=1 ;;
    --upgrade) UPGRADE=1 ;;
    -h|--help)
      cat <<'EOF'
bootstrap.sh — one-line install for agent-continuity-layer.

Usage:
  curl -fsSL https://github.com/KING-MOM/agent-continuity-layer/releases/latest/download/bootstrap.sh | bash
  curl -fsSL https://github.com/KING-MOM/agent-continuity-layer/releases/latest/download/bootstrap.sh | bash -s -- --connect-all

Flags:
  --connect-all   After install, run `agent-continuity connect all --apply`
                  to wire Claude Desktop, Cursor, Zed, and thin skills.
                  Without this flag, install is purely substrate-local;
                  wiring is a separate explicit step the operator runs.
  --no-verify     Skip cosign signature verification of the downloaded
                  artifacts. STRONGLY DISCOURAGED — provided only as
                  an emergency escape if the signature infrastructure
                  is temporarily broken. Use of this flag downgrades
                  the install integrity story to sha256-only (corruption
                  detection but no publisher identity verification).
  --upgrade       Allow replacing an existing active install with a
                  different version. Without this flag, install.sh
                  refuses to overwrite an already-active version (the
                  default protects against silent downgrades). Required
                  when re-running bootstrap on a machine that already
                  has any version installed.
  -h, --help      This message.
EOF
      exit 0
      ;;
    *)
      echo "warning: ignoring unknown argument: ${arg}" >&2
      ;;
  esac
done

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

echo "==> verifying sha256 (transport corruption check)"
if ! "${SHA_TOOL[@]}" "${SHA_NAME}"; then
  echo "error: sha256 mismatch — aborting before any install write" >&2
  exit 1
fi

# ────────────────────────────────────────────────────────────────
# M15.3: cosign signature verification.
#
# v0.2.0+ releases are signed via GitHub Actions keyless OIDC. The
# signature ties the artifact to THIS repo's release workflow.
# Without verification an attacker who compromises the release
# infrastructure (or anyone with write access to the repo) could
# substitute a malicious tarball + matching sha256.
#
# We require cosign by default. Operators who genuinely need to
# bypass (cosign unavailable, infrastructure broken) can pass
# --no-verify, which downgrades to sha256-only with a loud warning.
#
# Pre-v0.2.0 tags are NOT signed; if the resolved latest tag is
# below v0.2.0 we skip verification with a note (backward compat).

# Crude semver compare: extract major.minor and refuse on < 0.2.
_version_at_least_020() {
  local v="$1"
  # Strip pre-release suffixes if any (-alpha, -rc, etc.)
  v="${v%%-*}"
  IFS='.' read -r major minor _patch <<<"${v}"
  if [ "${major:-0}" -gt 0 ]; then return 0; fi
  if [ "${major:-0}" -eq 0 ] && [ "${minor:-0}" -ge 2 ]; then return 0; fi
  return 1
}

if _version_at_least_020 "${VERSION}"; then
  if [ "${NO_VERIFY}" = "1" ]; then
    cat >&2 <<EOF
==> WARNING: --no-verify is set; SKIPPING cosign signature verification.
    Integrity story is downgraded to sha256-only (transport corruption
    detection but no publisher identity). DO NOT use --no-verify in
    production. This flag exists only for emergency escapes.
EOF
  elif ! command -v cosign >/dev/null 2>&1; then
    cat >&2 <<EOF
error: cosign is not installed. v0.2.0+ releases require cosign for
       signature verification.

       Install cosign:
         macOS:  brew install cosign
         linux:  see https://docs.sigstore.dev/cosign/installation/

       Or use --no-verify to skip verification (downgrades integrity
       to sha256-only; not recommended).
EOF
    exit 1
  else
    echo "==> verifying cosign signatures (publisher identity)"
    # Identity that must match the cert in the .crt file: this repo's
    # release workflow on a v* tag. The regex pins all three: repo,
    # workflow file path, ref pattern.
    EXPECTED_IDENTITY_REGEX='^https://github\.com/KING-MOM/agent-continuity-layer/\.github/workflows/release\.yml@refs/tags/v.*$'
    EXPECTED_OIDC_ISSUER='https://token.actions.githubusercontent.com'

    # Download sig + cert for the tarball
    for asset in "${TARBALL_NAME}" "${SHA_NAME}"; do
      curl -fsSL -o "${asset}.sig" "${GITHUB_DL}/${TAG}/${asset}.sig"
      curl -fsSL -o "${asset}.crt" "${GITHUB_DL}/${TAG}/${asset}.crt"
      if ! cosign verify-blob \
            --certificate "${asset}.crt" \
            --signature "${asset}.sig" \
            --certificate-identity-regexp "${EXPECTED_IDENTITY_REGEX}" \
            --certificate-oidc-issuer "${EXPECTED_OIDC_ISSUER}" \
            "${asset}" >/dev/null 2>&1; then
        echo "error: cosign signature verification FAILED for ${asset}" >&2
        echo "       expected identity regex: ${EXPECTED_IDENTITY_REGEX}" >&2
        echo "       expected OIDC issuer:    ${EXPECTED_OIDC_ISSUER}" >&2
        echo "       refusing to install. Use --no-verify ONLY in an emergency." >&2
        exit 1
      fi
      echo "    ${asset}: cosign signature valid"
    done
  fi
else
  echo "==> skipping cosign verification (${TAG} predates signed releases)"
fi

echo "==> extracting"
tar -xzf "${TARBALL_NAME}"

echo "==> running install.sh"
install_args=( --from-tarball "${TARBALL_NAME}" )
if [ "${UPGRADE}" = "1" ]; then
  install_args+=( --upgrade )
fi
"agent-continuity-v${VERSION}/scripts/install.sh" "${install_args[@]}"

# ────────────────────────────────────────────────────────────────
# Optional auto-connect. Off by default — wiring third-party app
# configs (Claude Desktop, Cursor, Zed, thin skills) should be an
# explicit operator action, not a silent side effect of `curl | bash`.
# The user opts in via `--connect-all` if they want one-command UX.
#
# If connect fails the install itself is still complete and usable;
# we warn and point at `connect doctor` rather than failing the
# whole bootstrap.

echo
echo "✓ installed agent-continuity v${VERSION}"

if [ "${CONNECT_ALL}" = "1" ]; then
  BIN_HOME="${XDG_BIN_HOME:-${HOME}/.local/bin}"
  SHIM="${BIN_HOME}/agent-continuity"
  echo
  echo "==> wiring local agents (Claude Desktop / Cursor / Zed / thin skills)"
  if [ ! -x "${SHIM}" ]; then
    cat >&2 <<EOF
warn: cannot locate installed CLI at ${SHIM}
      install is complete, but auto-connect was skipped.
      diagnose: ls -la ${BIN_HOME}
EOF
  elif "${SHIM}" connect all --apply; then
    echo
    echo "✓ wiring complete"
    echo
    echo "next step:"
    echo "  restart any running MCP clients so they pick up the new config"
  else
    cat >&2 <<EOF

warn: \`connect all --apply\` exited non-zero.
      install itself is complete; diagnose with:
          agent-continuity connect doctor
EOF
  fi
  exit 0
fi

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
echo
echo "tip: to bootstrap + wire in one shot next time, append --connect-all:"
echo "  curl -fsSL .../bootstrap.sh | bash -s -- --connect-all"
