#!/usr/bin/env bash
# install.sh — M12.1 installer for agent-continuity.
#
# Given a tarball + sibling .sha256 produced by release.sh build,
# lands the substrate under $XDG_DATA_HOME/agent-continuity/v{X.Y.Z}/
# and updates an 'active' symlink, then makes 'agent-continuity'
# available on PATH via $HOME/.local/bin (or $XDG_BIN_HOME).
#
# Honest about what we verify:
#   - .sha256 detects transport corruption.
#   - v0.2.0+ release tarballs require cosign signature verification.
#   - unsigned installs are allowed only via an explicit local-build flag.
#
# Why a separate .sha256 file (not embedded in this script): an
# attacker who can rewrite the tarball over the same transport can
# also rewrite an embedded checksum next to it. A separate file is
# no better against a strong adversary, but it is at least honest
# about what 'integrity check' means here. install.sh refuses to
# proceed without a sibling .sha256.
#
# Flags:
#   --from-tarball PATH    install from a local tarball (M12.1 scope)
#   --upgrade              allow replacing an active different version
#   --allow-unsigned-local-build
#                          skip cosign requirement for a local build artifact
#
# Writes (and ONLY these locations):
#   $XDG_DATA_HOME/agent-continuity/v{X.Y.Z}/   install dir
#   $XDG_DATA_HOME/agent-continuity/active      symlink to active version dir
#   $HOME/.local/bin/agent-continuity           PATH shim (or $XDG_BIN_HOME)
# install.sh never touches the substrate's config/state/cache namespaces.

set -euo pipefail

XDG_DATA_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}"
INSTALL_BASE="${XDG_DATA_HOME}/agent-continuity"
ACTIVE_LINK="${INSTALL_BASE}/active"
# No official XDG bin spec; de facto $HOME/.local/bin. Honor
# $XDG_BIN_HOME for sandboxed installs (the M12.1 smoke uses this).
BIN_DIR="${XDG_BIN_HOME:-${HOME}/.local/bin}"
SHIM_PATH="${BIN_DIR}/agent-continuity"

# Cleanup is explicit. Bash RETURN traps interact badly with set -u
# and local-variable scope; we keep the temp dir as a script-global
# and clean it on EXIT.
STAGE_DIR=""
_cleanup() {
  if [ -n "${STAGE_DIR}" ] && [ -d "${STAGE_DIR}" ]; then
    rm -rf "${STAGE_DIR}"
  fi
}
trap _cleanup EXIT

usage() {
  cat <<EOF
install.sh — install agent-continuity from a release tarball

Usage:
  install.sh --from-tarball PATH [--upgrade] [--allow-unsigned-local-build]

Locations (XDG; respects env overrides):
  install dir:  ${INSTALL_BASE}/v{VERSION}/
  active link:  ${ACTIVE_LINK}
  PATH shim:    ${SHIM_PATH}

Verification:
  Tarball is checked against its sibling .sha256 file. v0.2.0+
  release tarballs also require sibling .sig and .crt files and are
  verified with cosign before extraction.

  --allow-unsigned-local-build is for artifacts built locally with
  scripts/release.sh build and no signing infrastructure. It downgrades
  verification to sha256-only and should not be used for downloaded
  release artifacts.

  install.sh -h | --help
EOF
}

# Cross-platform sha256 -c verify. cd into the dir so the .sha256
# file's relative basename resolves to the tarball next to it.
_verify_sha256() {
  local sha_file="$1"
  local tar_dir="$2"
  (
    cd "${tar_dir}"
    if command -v shasum >/dev/null 2>&1; then
      shasum -a 256 -c "${sha_file}"
    elif command -v sha256sum >/dev/null 2>&1; then
      sha256sum -c "${sha_file}"
    else
      echo "error: no sha256 tool found (shasum or sha256sum)" >&2
      return 1
    fi
  )
}

_version_ge_0_2_0() {
  local v="${1#v}" major=0 minor=0 patch=0
  IFS=. read -r major minor patch <<EOF
${v}
EOF
  major="${major:-0}"
  minor="${minor:-0}"
  patch="${patch:-0}"
  if [ "${major}" -gt 0 ]; then
    return 0
  fi
  if [ "${major}" -eq 0 ] && [ "${minor}" -ge 2 ]; then
    return 0
  fi
  return 1
}

# Atomic symlink update: write to .tmp.<pid>, then rename onto the
# link path. POSIX rename is atomic on the same filesystem.
#
# Footgun (caught in M12.1 smoke): plain `mv -f tmp existing-link`
# where existing-link is a symlink to a directory will FOLLOW the
# symlink and move tmp INTO that directory, leaving the link
# untouched. The fix is to refuse to follow the destination:
#   BSD/macOS: `mv -h` (do not follow symlink-to-dir target)
#   GNU/Linux: `mv -T` (no-target-directory)
# We detect once per call. If neither flag is supported we fall
# back to the non-atomic rm+mv, which has a brief window where
# the link is missing but is better than silently no-op'ing.
_atomic_symlink() {
  local target="$1"
  local link_path="$2"
  local tmp_path="${link_path}.tmp.$$"
  rm -f "${tmp_path}"
  ln -s "${target}" "${tmp_path}"
  if mv -hf "${tmp_path}" "${link_path}" 2>/dev/null; then
    return 0
  fi
  if mv -fT "${tmp_path}" "${link_path}" 2>/dev/null; then
    return 0
  fi
  # Last resort: non-atomic. Window where link is missing is brief
  # and only happens on a `mv` that supports neither -h nor -T.
  rm -f "${link_path}"
  mv -f "${tmp_path}" "${link_path}"
}

cmd_install() {
  local tarball="" upgrade=0 allow_unsigned_local_build=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --from-tarball)
        if [ $# -lt 2 ]; then
          echo "error: --from-tarball requires a PATH argument" >&2
          return 64
        fi
        tarball="$2"
        shift 2
        ;;
      --upgrade) upgrade=1; shift ;;
      --allow-unsigned-local-build) allow_unsigned_local_build=1; shift ;;
      -h|--help) usage; return 0 ;;
      *) echo "error: unknown flag: $1" >&2; echo >&2; usage >&2; return 64 ;;
    esac
  done

  if [ -z "${tarball}" ]; then
    echo "error: --from-tarball PATH is required" >&2
    echo >&2
    usage >&2
    return 64
  fi
  if [ ! -f "${tarball}" ]; then
    echo "error: tarball not found: ${tarball}" >&2
    return 1
  fi

  # Sibling .sha256 by convention: same dir, .tar.gz → .sha256.
  local tarball_dir tarball_base sha_file
  tarball_dir="$(cd "$(dirname "${tarball}")" && pwd)"
  tarball_base="$(basename "${tarball}")"
  sha_file="${tarball_base%.tar.gz}.sha256"
  if [ ! -f "${tarball_dir}/${sha_file}" ]; then
    echo "error: integrity file missing: ${tarball_dir}/${sha_file}" >&2
    echo "       release.sh build writes this alongside the tarball" >&2
    return 1
  fi

  echo "verifying integrity (sha256, transport corruption check)"
  if ! _verify_sha256 "${sha_file}" "${tarball_dir}"; then
    echo "error: sha256 verification failed — tarball is corrupt or .sha256 mismatched" >&2
    return 1
  fi

  # M15.3/M15.4: v0.2.0+ release tarballs require cosign verification
  # before extraction. Local release.sh builds can opt into the unsigned
  # path explicitly with --allow-unsigned-local-build; downloaded release
  # artifacts should never silently degrade to sha256-only.
  local tarball_sig="${tarball_dir}/${tarball_base}.sig"
  local tarball_crt="${tarball_dir}/${tarball_base}.crt"
  local release_version="" requires_signature=0
  if [[ "${tarball_base}" =~ ^agent-continuity-v([0-9]+[.][0-9]+[.][0-9]+)([-+._a-zA-Z0-9]*)?[.]tar[.]gz$ ]]; then
    release_version="${BASH_REMATCH[1]}"
    if _version_ge_0_2_0 "${release_version}"; then
      requires_signature=1
    fi
  fi

  if [ -f "${tarball_sig}" ] && [ -f "${tarball_crt}" ]; then
    if ! command -v cosign >/dev/null 2>&1; then
      cat >&2 <<EOF
error: cosign signatures are present (${tarball_base}.sig + .crt) but
       cosign is not installed. The release is signed and refusing to
       install without verification would be safer than skipping.

       Install cosign:
         macOS:  brew install cosign
         linux:  see https://docs.sigstore.dev/cosign/installation/
EOF
      return 1
    fi
    echo "verifying cosign signature (publisher identity via sigstore keyless OIDC)"
    local expected_identity_regex='^https://github\.com/KING-MOM/agent-continuity-layer/\.github/workflows/release\.yml@refs/tags/v.*$'
    local expected_oidc_issuer='https://token.actions.githubusercontent.com'
    if ! ( cd "${tarball_dir}" && cosign verify-blob \
            --certificate "${tarball_base}.crt" \
            --signature "${tarball_base}.sig" \
            --certificate-identity-regexp "${expected_identity_regex}" \
            --certificate-oidc-issuer "${expected_oidc_issuer}" \
            "${tarball_base}" >/dev/null 2>&1 ); then
      echo "error: cosign signature verification FAILED for ${tarball_base}" >&2
      echo "       expected identity regex: ${expected_identity_regex}" >&2
      echo "       expected OIDC issuer:    ${expected_oidc_issuer}" >&2
      echo "       refusing to install." >&2
      return 1
    fi
    echo "  signature valid"
  elif [ -f "${tarball_sig}" ] || [ -f "${tarball_crt}" ]; then
    cat >&2 <<EOF
error: incomplete cosign signature files for ${tarball_base}
       expected both:
         ${tarball_base}.sig
         ${tarball_base}.crt
EOF
    return 1
  elif [ "${requires_signature}" = "1" ] && [ "${allow_unsigned_local_build}" != "1" ]; then
    cat >&2 <<EOF
error: missing cosign signature files for ${tarball_base}
       v0.2.0+ release tarballs require sibling files:
         ${tarball_base}.sig
         ${tarball_base}.crt

       If this is a local artifact you built yourself with
       scripts/release.sh build, re-run with:
         --allow-unsigned-local-build
EOF
    return 1
  elif [ "${allow_unsigned_local_build}" = "1" ]; then
    echo "warn: --allow-unsigned-local-build set; skipping cosign signature verification" >&2
    echo "      sha256 detects corruption only; do not use this for downloaded releases" >&2
  fi

  # Extract to tempdir so we can read VERSION before placing. EXIT
  # trap (above) cleans this up no matter how we exit.
  STAGE_DIR="$(mktemp -d -t agent-continuity-install.XXXXXX)"
  tar -xzf "${tarball}" -C "${STAGE_DIR}"

  # Tarball ships under agent-continuity-v{VERSION}/. Locate it by
  # the known prefix so we don't accidentally pick up some other
  # entry the OS might have stashed.
  local extracted_root=""
  for d in "${STAGE_DIR}"/agent-continuity-v*; do
    if [ -d "${d}" ]; then
      extracted_root="${d}"
      break
    fi
  done
  if [ -z "${extracted_root}" ] || [ ! -f "${extracted_root}/core/VERSION" ]; then
    echo "error: tarball does not contain expected agent-continuity-v*/core/VERSION layout" >&2
    return 1
  fi
  local version
  version="$(head -1 "${extracted_root}/core/VERSION" | tr -d '[:space:]')"
  if [ -z "${version}" ]; then
    echo "error: extracted core/VERSION is empty" >&2
    return 1
  fi

  local target_dir="${INSTALL_BASE}/v${version}"

  # Detect current active version, if any. The 'active' symlink
  # target is a relative basename (e.g. 'v0.1.0'), since install.sh
  # writes it that way to keep INSTALL_BASE movable.
  local active_target="" active_version=""
  if [ -L "${ACTIVE_LINK}" ]; then
    active_target="$(readlink "${ACTIVE_LINK}")"
    active_version="$(basename "${active_target}")"
  fi

  echo "installing agent-continuity v${version}"
  echo "  install:  ${target_dir}"
  echo "  active:   ${ACTIVE_LINK}"
  echo "  shim:     ${SHIM_PATH}"

  # Upgrade guard: replacing a different active version requires
  # explicit confirmation. Same-version re-install is idempotent
  # (no flag required); we just rewrite the install dir contents.
  if [ -n "${active_version}" ] && [ "${active_version}" != "v${version}" ]; then
    if [ "${upgrade}" -ne 1 ]; then
      echo "error: refusing to replace active ${active_version} with v${version}" >&2
      echo "       run again with --upgrade to confirm" >&2
      return 1
    fi
    echo "  upgrading from ${active_version} to v${version}"
  fi

  # Place install dir. Brief window where ${target_dir} is missing
  # during same-version reinstall, acceptable for a non-daemon tool.
  # The rm -rf guard restricts deletion to paths under INSTALL_BASE
  # (defense against a corrupted env var or surprise tilde).
  mkdir -p "${INSTALL_BASE}"
  if [ -d "${target_dir}" ]; then
    case "${target_dir}" in
      "${INSTALL_BASE}/v"*)
        rm -rf "${target_dir}"
        ;;
      *)
        echo "error: refusing rm -rf on unexpected path: ${target_dir}" >&2
        return 1
        ;;
    esac
  fi
  mv "${extracted_root}" "${target_dir}"

  # Flip active. Symlink target is the version basename so the
  # link stays relative to INSTALL_BASE.
  _atomic_symlink "v${version}" "${ACTIVE_LINK}"

  # PATH shim → active/bin/agent-continuity. Goes through active so
  # rollback is just an active-symlink re-point; the shim never
  # needs to be rewritten.
  mkdir -p "${BIN_DIR}"
  _atomic_symlink "${ACTIVE_LINK}/bin/agent-continuity" "${SHIM_PATH}"

  # Smoke: --version should work and match. A mismatch here means
  # the shim/active chain is broken, which is worth surfacing now.
  local reported
  reported="$("${SHIM_PATH}" --version 2>/dev/null | head -1 | tr -d '[:space:]' || true)"
  if [ "${reported}" != "${version}" ]; then
    echo "warn: post-install --version returned '${reported}', expected '${version}'" >&2
  fi

  echo
  echo "installed:"
  echo "  agent-continuity v${version}"
  echo "  invoke as: ${SHIM_PATH} --version"
  if ! echo ":${PATH}:" | grep -Fq ":${BIN_DIR}:"; then
    echo
    echo "note: ${BIN_DIR} is not on \$PATH. To use 'agent-continuity' directly:"
    echo "      export PATH=\"${BIN_DIR}:\$PATH\""
  fi
}

main() {
  local cmd="${1:-}"
  case "${cmd}" in
    --from-tarball|--upgrade|--allow-unsigned-local-build)
      cmd_install "$@"
      ;;
    -h|--help|help|"")
      usage
      ;;
    *)
      echo "error: unknown argument: ${cmd}" >&2
      echo >&2
      usage >&2
      return 64
      ;;
  esac
}

main "$@"
