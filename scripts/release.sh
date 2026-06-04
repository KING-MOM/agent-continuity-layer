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
  # M15.1 reproducible-build path: file list comes from `git ls-files -s`
  # which gives us (mode, hash, stage, path). We need mode + path; the
  # _repro_tar.py helper consumes them as TAB-separated lines.
  #
  # Exclusion filters stay the same (docs/artifacts/, dist/, *.bak).
  local file_list
  file_list="$(cd "${REPO_ROOT}" && git ls-files -s \
    | awk '{print $1 "\t" $4}' \
    | grep -v $'\t''docs/artifacts/' \
    | grep -v $'\t''dist/' \
    | grep -v '\.bak'$'\t' \
    | grep -v '\.bak'$ \
    | sort -t $'\t' -k 2
  )"
  if [ -z "${file_list}" ]; then
    echo "error: empty file list — git ls-files returned nothing" >&2
    return 1
  fi
  local file_count
  file_count="$(printf "%s\n" "${file_list}" | wc -l | tr -d '[:space:]')"

  # SOURCE_DATE_EPOCH: per https://reproducible-builds.org. Honor the
  # env var if set; otherwise derive from the current commit's
  # committer timestamp so two builds at the same SHA produce the
  # same tarball bytes.
  local epoch
  if [ -n "${SOURCE_DATE_EPOCH:-}" ]; then
    epoch="${SOURCE_DATE_EPOCH}"
  else
    epoch="$(cd "${REPO_ROOT}" && git log -1 --format=%ct HEAD)"
  fi

  # M15.1.1: surface the exact reference we're building from so an
  # operator can verify they're at the expected commit / tag. Silent
  # builds from a non-tagged HEAD are the #1 false-positive on the
  # "rebuild and compare sha256" verify recipe — a user post-tag
  # would otherwise get a different sha256 and conclude release was
  # tampered with.
  local commit_sha tag_name reference_label
  commit_sha="$(cd "${REPO_ROOT}" && git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  tag_name="$(cd "${REPO_ROOT}" && git describe --exact-match --tags HEAD 2>/dev/null || true)"
  if [ -n "${tag_name}" ]; then
    reference_label="${commit_sha} (tag: ${tag_name})"
  else
    reference_label="${commit_sha} (NOT on a release tag)"
  fi

  echo "building agent-continuity v${version}"
  echo "  source:   ${REPO_ROOT}"
  echo "  commit:   ${reference_label}"
  echo "  files:    ${file_count}"
  echo "  epoch:    ${epoch} (SOURCE_DATE_EPOCH)"
  echo "  tarball:  ${tarball_path}"

  # Loud warning when HEAD is not on a tag. Output bytes will not
  # match any published release; users following the verify-from-
  # source recipe MUST checkout a tag first.
  if [ -z "${tag_name}" ]; then
    cat >&2 <<EOF

WARNING: building from a non-tagged HEAD (${commit_sha}). The tarball's
         sha256 will NOT match any published release. If you intended
         to verify against the public v${version} release, run:
             git checkout v${version}
             scripts/release.sh build
         and compare the resulting dist/*.sha256 to the GitHub release
         download. To suppress this warning when intentionally building
         from a development commit, ignore it.

EOF
  fi

  # Deterministic build via Python helper.
  # _repro_tar.py reads the file list from stdin and produces a tarball
  # whose every byte is a function of: (file contents, file mode bits
  # from git, file path, epoch). No fs mtime, uid, gid, gzip filename,
  # or gzip mtime leaks in.
  ( cd "${REPO_ROOT}" && printf "%s\n" "${file_list}" | \
    python3 "${SCRIPT_DIR}/_repro_tar.py" \
      --source-dir "${REPO_ROOT}" \
      --output "${tarball_path}" \
      --prefix "agent-continuity-v${version}" \
      --epoch "${epoch}"
  )

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

  # M15.2: emit CycloneDX 1.5 SBOM alongside the tarball. The SBOM is
  # bound to this specific tarball's sha256 via the `metadata.component.
  # hashes[]` field, so consumers can independently verify the SBOM
  # belongs to the artifact they're auditing. SBOM itself is
  # reproducible: same version + commit + SOURCE_DATE_EPOCH → identical
  # SBOM bytes.
  local sbom_name="agent-continuity-v${version}.cdx.json"
  local sbom_path="${DIST_DIR}/${sbom_name}"
  SOURCE_DATE_EPOCH="${epoch}" python3 "${SCRIPT_DIR}/_sbom.py" \
    --output "${sbom_path}" \
    --version "${version}" \
    --tarball-sha256 "${sha}"

  local size_bytes
  size_bytes="$(wc -c < "${tarball_path}" | tr -d '[:space:]')"

  echo
  echo "release built:"
  echo "  ${tarball_path}"
  echo "  size:     ${size_bytes} bytes"
  echo "  sha256:   ${sha}"
  echo "  checksum: ${checksum_path}"
  echo "  sbom:     ${sbom_path}"
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
