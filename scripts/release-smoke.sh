#!/usr/bin/env bash
# release-smoke.sh — M12.4 release confidence gate.
#
# Exercises the full install → use → migrate path inside a
# temporary HOME, proving the substrate is consumable by a stranger
# without OpenClaw, VM, API keys, Codex, or Claude.
#
# Build-or-consume:
#   - If dist/agent-continuity-v${VERSION}.tar.gz exists, use it.
#   - Otherwise invoke scripts/release.sh build (which itself
#     refuses on a dirty working tree, propagating that error).
#
# Sandbox:
#   - HOME is set to a fresh tempdir; all XDG_* envs and XDG_BIN_HOME
#     point inside it. The installed CLI lives under HOME's .local
#     and writes to HOME's .config/.cache/.local/state subtrees.
#   - quickstart's hardcoded sandbox at ${HOME}/.config/agent-
#     continuity-quickstart automatically lands inside the temp HOME.
#
# Verification:
#   - Each CLI call's exit code (substrate's own rc; this script
#     just classifies as pass/fail).
#   - 'agent-continuity doctor' output must not leak the BUILDER's
#     repo path — that would mean install-dir resolution broke and
#     the CLI is reading from cwd or some hardcoded path.
#   - Durable quickstart decision must appear in `decisions list`
#     before reset.
#   - reset must remove the quickstart sandbox dir.
#   - Real HOME continuity-namespace fingerprint (taken before the
#     smoke runs) must match the post-smoke fingerprint — i.e. no
#     writes leaked outside the temp HOME.
#
# Output:
#   - Full log: docs/artifacts/M12.4/smoke-{TIMESTAMP}.txt
#   - Final summary on stderr + return code
#
# Exit code: 0 if all tests pass; 1 if any fail.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${REPO_ROOT}/dist"
VERSION="$(head -1 "${REPO_ROOT}/core/VERSION" | tr -d '[:space:]')"
TARBALL="${DIST_DIR}/agent-continuity-v${VERSION}.tar.gz"
SHA_FILE="${DIST_DIR}/agent-continuity-v${VERSION}.sha256"

ARTIFACT_DIR="${REPO_ROOT}/docs/artifacts/M12.4"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARTIFACT_LOG="${ARTIFACT_DIR}/smoke-${STAMP}.txt"

# ────────────────────────────────────────────────────────────────
# Real-HOME fingerprint helpers. We check before the smoke starts
# and again at the end; identical content proves no writes leaked
# outside the temp HOME.

_fingerprint() {
  local p
  for p in \
      "${HOME}/.config/agent-continuity" \
      "${HOME}/.cache/agent-continuity" \
      "${HOME}/.local/state/agent-continuity" \
      "${HOME}/.local/share/agent-continuity" \
      "${HOME}/.local/bin/agent-continuity" \
      "${HOME}/.config/agent-continuity-quickstart" \
      "${HOME}/.local/state/agent-continuity-quickstart" \
      "${HOME}/.cache/agent-continuity-quickstart"; do
    if [ -e "${p}" ] || [ -L "${p}" ]; then
      # The opt-in watcher may update its own audit state asynchronously while
      # smoke runs; ignore that volatile file so this gate only catches leaks
      # caused by the smoke/install/quickstart path under test.
      find "${p}" \( -type f -o -type l \) 2>/dev/null \
        | grep -v '/watcher\.state\.json$' \
        | sort | xargs shasum -a 256 2>/dev/null || true
    else
      echo "ABSENT  ${p}"
    fi
  done
}

REAL_HOME="${HOME}"
FINGER_BEFORE="$(_fingerprint)"

# ────────────────────────────────────────────────────────────────
# Sandbox HOME. Preserved on failure for debugging; cleaned on PASS.

SB="$(mktemp -d -t agent-continuity-smoke.XXXXXX)"
FAKE_HOME="${SB}/home"
LOG="${SB}/smoke-${STAMP}.txt"
mkdir -p \
  "${FAKE_HOME}/.config" \
  "${FAKE_HOME}/.cache" \
  "${FAKE_HOME}/.local/state" \
  "${FAKE_HOME}/.local/share" \
  "${FAKE_HOME}/.local/bin"

# All substrate invocations inherit this env. We define it once and
# splice it into every CLI call via the `env` shell builtin so a
# typo in one place can't accidentally pick up real $HOME.
_with_sandbox_env() {
  env -i \
    HOME="${FAKE_HOME}" \
    PATH="${FAKE_HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin" \
    XDG_CONFIG_HOME="${FAKE_HOME}/.config" \
    XDG_CACHE_HOME="${FAKE_HOME}/.cache" \
    XDG_STATE_HOME="${FAKE_HOME}/.local/state" \
    XDG_DATA_HOME="${FAKE_HOME}/.local/share" \
    XDG_BIN_HOME="${FAKE_HOME}/.local/bin" \
    "$@"
}

_publish_log() {
  mkdir -p "${ARTIFACT_DIR}"
  cp "${LOG}" "${ARTIFACT_LOG}"
}

_run_smoke() {
cat <<EOF
agent-continuity release-smoke — M12.4
version:   v${VERSION}
stamp:     ${STAMP}
log:       ${ARTIFACT_LOG}
sandbox:   ${SB}
fake HOME: ${FAKE_HOME}
EOF

# ────────────────────────────────────────────────────────────────
# Test runner. Each test is a function returning 0 (pass) or non-0
# (fail). The test body runs with set -e disabled (it's the body of
# an `if` predicate), so the test must use explicit `return 1` for
# its own failure cases.

PASS=0
FAIL=0
declare -a RESULTS=()

_run_test() {
  local name="$1"; shift
  local rc=0
  echo
  echo "── T$((${#RESULTS[@]} + 1)): ${name} ──"
  if "$@"; then rc=0; else rc=$?; fi
  if [ "${rc}" = "0" ]; then
    echo "    PASS"
    PASS=$((PASS + 1))
    RESULTS+=("PASS  ${name}")
  else
    echo "    FAIL (rc=${rc})"
    FAIL=$((FAIL + 1))
    RESULTS+=("FAIL  ${name} (rc=${rc})")
  fi
}

# ────────────────────────────────────────────────────────────────
# Tests.

t_artifact_present_or_buildable() {
  if [ -f "${TARBALL}" ] && [ -f "${SHA_FILE}" ]; then
    echo "tarball + .sha256 already present in ${DIST_DIR}"
    return 0
  fi
  echo "tarball missing — invoking scripts/release.sh build"
  if ! "${REPO_ROOT}/scripts/release.sh" build; then
    echo "release.sh build failed (likely dirty tree — commit changes first)"
    return 1
  fi
  [ -f "${TARBALL}" ] && [ -f "${SHA_FILE}" ]
}

t_reproducible_build() {
  # M15.1: rebuilding the tarball at the same SHA must produce a
  # byte-identical .tar.gz. Without this guarantee a signed release
  # can't be independently verified — a third party would never get
  # the same hash to compare the signature against.
  #
  # Refuse to run when the working tree is dirty: release.sh itself
  # would refuse, and we'd be comparing nothing useful.
  if [ -n "$(cd "${REPO_ROOT}" && git status --porcelain 2>/dev/null | grep -v '^.. dist/')" ]; then
    echo "skipped: working tree dirty (release.sh refuses; nothing to reproduce)"
    return 0
  fi
  if [ ! -f "${TARBALL}" ] || [ ! -f "${SHA_FILE}" ]; then
    echo "skipped: no current tarball to compare against"
    return 0
  fi
  local sha_before
  sha_before="$(awk '{print $1}' "${SHA_FILE}")"
  echo "current sha256: ${sha_before}"
  if ! "${REPO_ROOT}/scripts/release.sh" build >/dev/null 2>&1; then
    echo "FAIL: rebuild failed"
    return 1
  fi
  local sha_after
  sha_after="$(awk '{print $1}' "${SHA_FILE}")"
  echo "rebuilt sha256: ${sha_after}"
  if [ "${sha_before}" != "${sha_after}" ]; then
    echo "FAIL: rebuild produced a different sha256"
    echo "  before: ${sha_before}"
    echo "  after:  ${sha_after}"
    return 1
  fi
  echo "→ byte-identical across rebuilds at the same SHA"
  return 0
}

t_sbom_present_and_valid() {
  # M15.2: every release ships a CycloneDX 1.5 SBOM alongside the
  # tarball + sha256. Verify it exists, is valid CycloneDX, and
  # binds to this tarball's hash.
  local sbom_path="${DIST_DIR}/agent-continuity-v${VERSION}.cdx.json"
  if [ ! -f "${sbom_path}" ]; then
    echo "FAIL: SBOM missing: ${sbom_path}"
    return 1
  fi
  if ! python3 -c "
import json, sys
sbom = json.load(open('${sbom_path}'))
assert sbom['bomFormat'] == 'CycloneDX', f'wrong bomFormat: {sbom[\"bomFormat\"]}'
assert sbom['specVersion'] == '1.5', f'wrong specVersion: {sbom[\"specVersion\"]}'
assert sbom['metadata']['component']['name'] == 'agent-continuity-layer'
assert sbom['metadata']['component']['version'] == '${VERSION}', \
    f'sbom version {sbom[\"metadata\"][\"component\"][\"version\"]} != tarball version ${VERSION}'
hashes = sbom['metadata']['component'].get('hashes', [])
assert hashes, 'SBOM has no tarball hash binding'
assert hashes[0]['alg'] == 'SHA-256', f'expected SHA-256, got {hashes[0][\"alg\"]}'
# Verify the SBOM's claimed hash matches the actual tarball
import hashlib
with open('${TARBALL}', 'rb') as f:
    actual = hashlib.sha256(f.read()).hexdigest()
assert hashes[0]['content'] == actual, \
    f'sbom claims tarball hash {hashes[0][\"content\"]} but actual is {actual}'
deps = sbom['dependencies'][0]['dependsOn']
assert 'pkg:generic/bash' in deps and 'pkg:generic/python@3.9' in deps, \
    f'unexpected deps: {deps}'
print('  CycloneDX 1.5 valid, hash binding matches tarball')
" 2>&1; then
    echo "FAIL: SBOM validation failed"
    return 1
  fi
  return 0
}

t_local_matches_published_release() {
  # M15.1.1: when HEAD is exactly on a release tag, the locally-built
  # tarball's sha256 MUST match the published release's sha256. This
  # is the cross-machine reproducibility guarantee — same tag + same
  # build recipe yields the same bytes whether you build on macOS
  # locally or on the CI workflow.
  #
  # Soft-skip when HEAD is not on a tag (development build) or when
  # there's no network reachability to fetch the published sha256.
  local tag
  tag="$(cd "${REPO_ROOT}" && git describe --exact-match --tags HEAD 2>/dev/null || true)"
  if [ -z "${tag}" ]; then
    echo "skipped: HEAD not on a release tag (development build)"
    return 0
  fi
  local local_sha
  local_sha="$(awk '{print $1}' "${SHA_FILE}")"
  if [ -z "${local_sha}" ]; then
    echo "FAIL: cannot read local sha256 from ${SHA_FILE}"
    return 1
  fi
  local public_sha_url="https://github.com/KING-MOM/agent-continuity-layer/releases/download/${tag}/agent-continuity-${tag}.sha256"
  local public_sha
  public_sha="$(curl -fsSL --max-time 10 "${public_sha_url}" 2>/dev/null | awk '{print $1}')"
  if [ -z "${public_sha}" ]; then
    echo "skipped: cannot fetch published sha256 for ${tag} (no network, or release not yet public)"
    return 0
  fi
  if [ "${local_sha}" = "${public_sha}" ]; then
    echo "  local @ ${tag}: ${local_sha}"
    echo "  published:    ${public_sha}"
    echo "  → cross-machine reproducibility holds"
    return 0
  fi
  echo "FAIL: local build at tag ${tag} does NOT match published release"
  echo "  local:     ${local_sha}"
  echo "  published: ${public_sha}"
  echo "  this means reproducibility broke between your environment and CI."
  echo "  likely causes: HEAD past the tag, modified working tree, or"
  echo "  toolchain divergence (Python version, tar implementation)."
  return 1
}

t_install_from_tarball() {
  # Invoke install.sh from the in-repo copy. (For a hostile audit we
  # could extract the tarball's own install.sh and use that, but the
  # acceptance criterion is "Installs via scripts/install.sh
  # --from-tarball" — the script under test.)
  if ! _with_sandbox_env "${REPO_ROOT}/scripts/install.sh" \
       --from-tarball "${TARBALL}"; then
    return 1
  fi
  [ -x "${FAKE_HOME}/.local/bin/agent-continuity" ]
}

t_cli_version() {
  local out
  if ! out="$(_with_sandbox_env "${FAKE_HOME}/.local/bin/agent-continuity" --version)"; then
    return 1
  fi
  echo "output: ${out}"
  [ "${out}" = "${VERSION}" ]
}

t_cli_doctor_no_path_leak() {
  local out rc=0
  # doctor returns rc=2 when warnings are present (e.g. snapshot
  # STALE, context pin freshness); both 0 and 2 are acceptable
  # here — the assertion is on content, not status.
  out="$(_with_sandbox_env "${FAKE_HOME}/.local/bin/agent-continuity" doctor --human 2>&1)" || rc=$?
  echo "${out}" | head -15
  echo "(rc=${rc})"
  if [ "${rc}" -ne 0 ] && [ "${rc}" -ne 2 ]; then
    echo "doctor exited unexpectedly: rc=${rc}"
    return 1
  fi
  # Substrate version line must be present.
  if ! echo "${out}" | grep -q "substrate v${VERSION}"; then
    echo "doctor output missing 'substrate v${VERSION}' marker"
    return 1
  fi
  # No leak of the BUILDER's repo path into the installed CLI's
  # output. If this leaks, it means install-dir resolution broke
  # and the CLI is reading from cwd or some hardcoded path.
  if echo "${out}" | grep -qF "${REPO_ROOT}"; then
    echo "FAIL: builder repo path leaked into installed CLI output:"
    echo "${out}" | grep -F "${REPO_ROOT}" | head -5
    return 1
  fi
  return 0
}

t_cli_migrate_dryrun() {
  local out
  if ! out="$(_with_sandbox_env "${FAKE_HOME}/.local/bin/agent-continuity" migrate --dry-run)"; then
    return 1
  fi
  echo "${out}"
  echo "${out}" | grep -q "no migrations needed"
}

t_quickstart_init() {
  if ! _with_sandbox_env "${FAKE_HOME}/.local/bin/agent-continuity" \
       quickstart init >/dev/null; then
    return 1
  fi
  [ -d "${FAKE_HOME}/.config/agent-continuity-quickstart" ]
}

t_quickstart_doctor() {
  local rc=0
  _with_sandbox_env "${FAKE_HOME}/.local/bin/agent-continuity" \
    quickstart doctor >/dev/null 2>&1 || rc=$?
  # 0 or 2 acceptable (doctor warn aggregation).
  [ "${rc}" -eq 0 ] || [ "${rc}" -eq 2 ]
}

t_quickstart_enqueue() {
  _with_sandbox_env "${FAKE_HOME}/.local/bin/agent-continuity" \
    quickstart enqueue >/dev/null
}

t_quickstart_run_worker() {
  _with_sandbox_env "${FAKE_HOME}/.local/bin/agent-continuity" \
    quickstart run-fake-worker >/dev/null
}

t_quickstart_decision_present() {
  local out
  if ! out="$(_with_sandbox_env "${FAKE_HOME}/.local/bin/agent-continuity" \
              quickstart decisions list)"; then
    return 1
  fi
  echo "${out}"
  # The fixture worker writes a decision with repo=quickstart-project
  # and adapter [codex]. Both should appear in the formatted output.
  echo "${out}" | grep -q "repo=quickstart-project" \
    && echo "${out}" | grep -q "\[codex\]"
}

t_quickstart_reset_removes_sandbox() {
  _with_sandbox_env "${FAKE_HOME}/.local/bin/agent-continuity" \
    quickstart reset >/dev/null
  ! [ -d "${FAKE_HOME}/.config/agent-continuity-quickstart" ] \
    && ! [ -d "${FAKE_HOME}/.local/state/agent-continuity-quickstart" ] \
    && ! [ -d "${FAKE_HOME}/.cache/agent-continuity-quickstart" ]
}

t_no_writes_outside_temp_home() {
  local finger_after
  # Re-fingerprint with HOME pointed at the REAL HOME, not the fake.
  HOME="${REAL_HOME}" finger_after="$(_fingerprint)"
  if [ "${FINGER_BEFORE}" = "${finger_after}" ]; then
    echo "real HOME continuity namespace: BYTE-IDENTICAL before/after"
    return 0
  fi
  echo "FAIL: real HOME continuity namespace changed during smoke:"
  diff <(echo "${FINGER_BEFORE}") <(echo "${finger_after}") | head -40
  return 1
}

# ────────────────────────────────────────────────────────────────
# Run the tests.

_run_test "artifact present or buildable" t_artifact_present_or_buildable
_run_test "reproducible build (sha256 stable across rebuilds at same SHA)" t_reproducible_build
_run_test "local build at tag matches published release sha256" t_local_matches_published_release
_run_test "CycloneDX 1.5 SBOM present and bound to tarball hash" t_sbom_present_and_valid
_run_test "install from tarball" t_install_from_tarball
_run_test "agent-continuity --version" t_cli_version
_run_test "doctor --human (substrate version present, no path leak)" t_cli_doctor_no_path_leak
_run_test "migrate --dry-run no-op" t_cli_migrate_dryrun
_run_test "quickstart init" t_quickstart_init
_run_test "quickstart doctor" t_quickstart_doctor
_run_test "quickstart enqueue" t_quickstart_enqueue
_run_test "quickstart run-fake-worker" t_quickstart_run_worker
_run_test "quickstart decisions list contains durable decision" t_quickstart_decision_present
_run_test "quickstart reset removes sandbox dirs" t_quickstart_reset_removes_sandbox
_run_test "no writes outside temp HOME (real namespace fingerprint unchanged)" t_no_writes_outside_temp_home

# ────────────────────────────────────────────────────────────────
# Summary.

TOTAL=$((PASS + FAIL))
echo
echo "════════════════════════════════════════════════════"
echo "smoke summary: ${PASS}/${TOTAL} passed, ${FAIL} failed"
for r in "${RESULTS[@]}"; do
  echo "  ${r}"
done
echo "log:      ${ARTIFACT_LOG}"
echo "sandbox:  ${SB}"

if [ "${FAIL}" -eq 0 ]; then
  echo "(sandbox cleaned on PASS)"
  return 0
else
  echo "(sandbox preserved at ${SB} for debugging)"
  return 1
fi
}

# Tee the full run into a temp log first. Publishing to docs/artifacts only
# after all tests finish keeps release.sh's clean-tree precondition meaningful:
# the smoke harness must not dirty the repo before it asks release.sh to build.
set +e
_run_smoke 2>&1 | tee -a "${LOG}"
smoke_rc=${PIPESTATUS[0]}
set -e

_publish_log
echo "published smoke log: ${ARTIFACT_LOG}"

if [ "${smoke_rc}" -eq 0 ]; then
  rm -rf "${SB}"
fi
exit "${smoke_rc}"
