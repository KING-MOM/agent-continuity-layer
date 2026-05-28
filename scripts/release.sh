#!/usr/bin/env bash
# release.sh — M12.0 release build for agent-continuity-layer.
#
# Maintainer-facing. Produces a tarball + sha256 under dist/ that
# install.sh (M12.1) can consume.
#
# Subcommands:
#   build   build the release artifact for the version in core/VERSION
#
# Future subcommands (deferred to M12.x):
#   tag     create the git tag matching core/VERSION (maintainer
#           runs `git tag` manually for M12.0)
#   verify  verify a built tarball's contents against the allowlist
#   publish push the release to a distribution endpoint
#
# Hard rule: no release from a dirty working tree. The project is
# now about trust; a release built from uncommitted changes would
# undermine that.
#
# Integrity vs security: the .sha256 produced here lets install.sh
# verify the tarball survived transport intact. It is NOT a
# supply-chain security guarantee — an attacker who can rewrite the
# tarball can also rewrite the .sha256 next to it. Signed releases
# are M-future. Docs in M12.1 will spell this out.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${REPO_ROOT}/dist"

usage() {
  cat <<EOF
release.sh — M12.0 release build for agent-continuity-layer

Usage:
  release.sh build              build tarball + sha256 from core/VERSION

The build subcommand:
  - reads core/VERSION
  - refuses to run from a dirty working tree (only 'dist/' is
    permitted in the diff)
  - assembles an allowlist of repo files
  - writes:
      dist/agent-continuity-vX.Y.Z.tar.gz
      dist/agent-continuity-vX.Y.Z.sha256

  release.sh -h | --help
EOF
}

# Read substrate version from the single source-of-truth.
_read_version() {
  local version
  version="$(cat "${REPO_ROOT}/core/VERSION" 2>/dev/null | head -1 | tr -d '[:space:]')"
  if [ -z "${version}" ]; then
    echo "error: core/VERSION missing or empty" >&2
    return 1
  fi
  echo "${version}"
}

# Enforce clean working tree. The user's M12.0 sign-off was explicit:
# "no release from dirty source ... releasing a dirty tree would be
# off-brand in the funniest/worst way." Only 'dist/' is allowed in
# the diff, since release.sh write outputs there.
_check_clean_tree() {
  local dirty
  dirty="$(cd "${REPO_ROOT}" && git status --porcelain 2>/dev/null | \
           grep -v '^.. dist/' \
        || true)"
  if [ -n "${dirty}" ]; then
    echo "error: refusing to build from a dirty working tree." >&2
    echo "       commit or stash these changes first:" >&2
    echo "${dirty}" | sed 's/^/         /' >&2
    return 1
  fi
}

cmd_build() {
  local version
  version="$(_read_version)" || return 1

  _check_clean_tree || return 1

  local tarball_name="agent-continuity-v${version}.tar.gz"
  local checksum_name="agent-continuity-v${version}.sha256"
  local tarball_path="${DIST_DIR}/${tarball_name}"
  local checksum_path="${DIST_DIR}/${checksum_name}"

  mkdir -p "${DIST_DIR}"

  # File allowlist via `git ls-files`. Reason: clean-tree gate above
  # guarantees git knows about every file we want to ship; nothing
  # untracked sneaks in. Then filter explicit excludes (per-milestone
  # evidence under docs/artifacts/, backup files, anything under dist/
  # — even though clean-tree means dist/ shouldn't have tracked files,
  # belt-and-braces).
  local file_list
  file_list="$(cd "${REPO_ROOT}" && git ls-files \
    | grep -v '^docs/artifacts/' \
    | grep -v '^dist/' \
    | grep -v '\.bak' \
    | sort
  )"
  if [ -z "${file_list}" ]; then
    echo "error: empty file list — git ls-files returned nothing" >&2
    return 1
  fi
  local file_count
  file_count="$(printf "%s\n" "${file_list}" | wc -l | tr -d '[:space:]')"

  echo "building agent-continuity v${version}"
  echo "  source:   ${REPO_ROOT}"
  echo "  files:    ${file_count}"
  echo "  tarball:  ${tarball_path}"

  # Build tarball. Files go inside a versioned top-level directory
  # so extraction lands at agent-continuity-v0.1.0/... rather than
  # polluting the user's cwd.
  local stage_dir
  stage_dir="$(mktemp -d -t agent-continuity-release.XXXXXX)"
  local stage_root="${stage_dir}/agent-continuity-v${version}"
  mkdir -p "${stage_root}"

  # Stage each file. Preserves relative paths so e.g.
  # core/schemas/decision-entry.schema.json -> stage_root/core/schemas/...
  while IFS= read -r f; do
    local dest="${stage_root}/${f}"
    mkdir -p "$(dirname "${dest}")"
    cp -p "${REPO_ROOT}/${f}" "${dest}"
  done <<< "${file_list}"

  # tar + gzip the staged directory.
  ( cd "${stage_dir}" && tar -czf "${tarball_path}" "agent-continuity-v${version}" )

  # Cleanup stage. Keep the tarball + checksum only.
  rm -rf "${stage_dir}"

  # Compute sha256. Cross-platform: shasum on macOS, sha256sum on Linux.
  local sha
  if command -v shasum >/dev/null 2>&1; then
    sha="$(shasum -a 256 "${tarball_path}" | awk '{print $1}')"
  elif command -v sha256sum >/dev/null 2>&1; then
    sha="$(sha256sum "${tarball_path}" | awk '{print $1}')"
  else
    echo "error: no sha256 tool found (shasum or sha256sum)" >&2
    return 1
  fi

  # Checksum file format: '<sha256-hex>  <tarball-name>\n' (matches
  # shasum/sha256sum -c expectations so install.sh can `shasum -c
  # checksum_file` directly in M12.1).
  printf "%s  %s\n" "${sha}" "${tarball_name}" > "${checksum_path}"

  local size_bytes
  size_bytes="$(wc -c < "${tarball_path}" | tr -d '[:space:]')"

  echo
  echo "release built:"
  echo "  ${tarball_path}"
  echo "  size:     ${size_bytes} bytes"
  echo "  sha256:   ${sha}"
  echo "  checksum: ${checksum_path}"
  echo
  echo "next:"
  echo "  - smoke-test the artifact (M12.4 will automate this)"
  echo "  - git tag v${version}  (manual for M12.0; release.sh tag in a later slice)"
}

main() {
  local cmd="${1:-}"
  case "${cmd}" in
    build) shift; cmd_build "$@" ;;
    -h|--help|help|"") usage ;;
    *)
      echo "error: unknown subcommand: ${cmd}" >&2
      echo >&2
      usage >&2
      return 64
      ;;
  esac
}

main "$@"
