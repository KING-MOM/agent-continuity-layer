#!/usr/bin/env bash
# quickstart.sh — M11 OSS quickstart driver.
#
# Always sandboxed. Sets up agent-continuity state in a parallel
# namespace so a stranger can experience the full delegated-task loop
# without it touching their real config/state/cache.
#
# Sandbox namespace (all under "agent-continuity-quickstart" rather
# than "agent-continuity"):
#   XDG_CONFIG_HOME=~/.config/agent-continuity-quickstart
#   XDG_STATE_HOME=~/.local/state/agent-continuity-quickstart
#   XDG_CACHE_HOME=~/.cache/agent-continuity-quickstart
#
# Subcommands (M11.0):
#   init      copy fixture project + write trust policy + print env block
#   env       print the env block (so you can `eval $(quickstart.sh env)`)
#   status    show whether quickstart is initialized + where state lives
#   doctor    run scripts/doctor.sh under the sandbox env
#
# M11.1 adds:
#   enqueue           generate the fixture task
#   run-fake-worker   claim + submit it as the fixture worker
#
# Hard guarantee: this script NEVER touches the real ~/.config/agent-
# continuity, ~/.local/state/agent-continuity, ~/.cache/agent-continuity,
# or any real project repo. Acceptance test verifies this.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
FIXTURE_SRC="${REPO_ROOT}/fixtures/quickstart-project"

# Sandbox namespace — parallel to the real one, distinct dir name.
QS_CONFIG="${HOME}/.config/agent-continuity-quickstart"
QS_STATE="${HOME}/.local/state/agent-continuity-quickstart"
QS_CACHE="${HOME}/.cache/agent-continuity-quickstart"
QS_WORKSPACE="${QS_STATE}/workspace/quickstart-project"
QS_POLICY="${QS_CONFIG}/agent-continuity/trust-policy.json"
QS_STATE_FILE="${QS_STATE}/quickstart-state.json"

# Fixed identifiers used throughout the quickstart so re-runs are
# deterministic and idempotent.
QS_PROJECT_UUID="proj-quickstart-fixture"
QS_REPO_NAME="quickstart-project"
QS_FAKE_WORKER="${SCRIPT_DIR}/_quickstart_fake_worker.sh"

usage() {
  cat <<EOF
quickstart.sh — M11 OSS quickstart for agent-continuity-layer

Usage:
  quickstart.sh init                  initialize the sandbox (idempotent)
  quickstart.sh env                   print the env block (for: eval \$(quickstart.sh env))
  quickstart.sh status                show what's initialized
  quickstart.sh doctor                run doctor.sh under the sandbox env
  quickstart.sh enqueue [--new]       create the fixture delegated task
  quickstart.sh run-fake-worker       claim + submit the fixture task as a fake worker
  quickstart.sh decisions <subcmd>    wrapper for decisions.sh under the sandbox env
  quickstart.sh reset [--dry-run]     remove the three sandbox dirs (idempotent)
  quickstart.sh reset --reinit        reset then re-init
  quickstart.sh -h | --help

Recommended order:
  init  ->  doctor  ->  enqueue  ->  run-fake-worker  ->  decisions list

To start fresh:
  reset  (or reset --reinit for clear-and-start-over)

Sandbox lives at:
  ${QS_CONFIG}/
  ${QS_STATE}/
  ${QS_CACHE}/

Run \`quickstart.sh init\` once; everything else either uses the saved
sandbox or reads from it. The real ~/.config, ~/.local/state, ~/.cache
namespaces are never touched.
EOF
}

cmd_env() {
  cat <<EOF
export XDG_CONFIG_HOME="${QS_CONFIG}"
export XDG_STATE_HOME="${QS_STATE}"
export XDG_CACHE_HOME="${QS_CACHE}"
EOF
}

cmd_status() {
  echo "quickstart sandbox layout:"
  echo "  config:    ${QS_CONFIG}  $([ -d "${QS_CONFIG}" ] && echo present || echo MISSING)"
  echo "  state:     ${QS_STATE}  $([ -d "${QS_STATE}" ] && echo present || echo MISSING)"
  echo "  cache:     ${QS_CACHE}  $([ -d "${QS_CACHE}" ] && echo present || echo MISSING)"
  echo "  workspace: ${QS_WORKSPACE}  $([ -d "${QS_WORKSPACE}" ] && echo present || echo MISSING)"
  echo "  policy:    ${QS_POLICY}  $([ -f "${QS_POLICY}" ] && echo present || echo MISSING)"
  if [ -d "${QS_STATE}" ] && [ -f "${QS_POLICY}" ]; then
    echo
    echo "  initialized. Next step:"
    echo "    quickstart.sh doctor"
  else
    echo
    echo "  NOT initialized. Run:"
    echo "    quickstart.sh init"
  fi
}

# Compute an ISO-8601 UTC timestamp N days from now (cross-platform).
_expires_at_iso() {
  local days="$1"
  python3 -c "
import datetime as dt
print((dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=${days})).strftime('%Y-%m-%dT%H:%M:%SZ'))
"
}

cmd_init() {
  # Idempotency: if everything's already there, just report + exit 0.
  if [ -d "${QS_WORKSPACE}" ] && [ -f "${QS_POLICY}" ]; then
    echo "quickstart already initialized at ${QS_STATE}"
    echo "to inspect:    quickstart.sh status"
    echo "to verify:     quickstart.sh doctor"
    echo
    cmd_env
    return 0
  fi

  # Sanity check the fixture source.
  if [ ! -d "${FIXTURE_SRC}" ]; then
    echo "error: fixture source missing at ${FIXTURE_SRC}" >&2
    echo "       (was scripts/quickstart.sh moved out of the repo?)" >&2
    return 1
  fi

  echo "initializing quickstart sandbox..."

  # 1. Create the three XDG dirs.
  mkdir -p "${QS_CONFIG}/agent-continuity"
  mkdir -p "${QS_STATE}/agent-continuity"
  mkdir -p "${QS_CACHE}/agent-continuity"

  # 2. Copy the fixture project into the workspace.
  mkdir -p "$(dirname "${QS_WORKSPACE}")"
  if [ ! -d "${QS_WORKSPACE}" ]; then
    cp -R "${FIXTURE_SRC}" "${QS_WORKSPACE}"
    echo "  copied fixture project -> ${QS_WORKSPACE}"
  fi

  # 3. Initialize a git repo in the workspace if not already one. Lets
  # trust grants scope by the file:// origin and lets future M11.1
  # worker tasks reference a real git repo.
  if [ ! -d "${QS_WORKSPACE}/.git" ]; then
    (
      cd "${QS_WORKSPACE}"
      git init -q
      git add -A
      # Use a fixed identity so the quickstart commit looks the same
      # regardless of the user's global git config.
      git -c user.email="quickstart@agent-continuity.local" \
          -c user.name="Quickstart Fixture" \
          commit -q -m "Quickstart fixture project (M11.0 initial commit)"
    )
    echo "  git init + initial commit"
  fi

  # 4. Write a starter trust policy. Time-limited (30 days), repo-
  # scoped, research kind only, read-only trust level. Safe defaults —
  # even a buggy fake worker can't do anything dangerous under this
  # policy.
  #
  # M11.1: the repo origin is the FRIENDLY NAME "quickstart-project"
  # rather than the file:// URI. Reason: the worker-result writeback
  # path (M8.3) derives decision.repo from task.input.repo verbatim,
  # so a file:// origin would put a full local path in every
  # quickstart-generated decision entry. Using the basename keeps
  # the decision log readable and matches the M11.1 acceptance
  # criterion (decision.repo == "quickstart-project"). Trust-policy
  # matching is plain string equality on the origin field, so the
  # friendly-name origin still gates the grant correctly.
  local origin="quickstart-project"
  local expires
  expires="$(_expires_at_iso 30)"
  cat > "${QS_POLICY}" <<EOF
{
  "schema_version": "1.0",
  "default": {
    "allow_kinds": [],
    "max_trust_level": "read-only",
    "require_human_approval_for": [],
    "allowed_workers": []
  },
  "repos": [
    {
      "origin": "${origin}",
      "policy": {
        "allow_kinds": ["research"],
        "max_trust_level": "read-only",
        "allowed_workers": ["codex"],
        "expires_at": "${expires}"
      }
    }
  ],
  "grants": []
}
EOF
  echo "  wrote trust policy -> ${QS_POLICY}"
  echo "    grant: research only, read-only, repo=${origin}, expires=${expires}"

  echo
  echo "quickstart initialized."
  echo
  echo "next step:"
  echo "  scripts/quickstart.sh doctor"
  echo
  echo "to use other scripts (worker.sh, decisions.sh, context.sh) under"
  echo "the sandbox env, either prefix each invocation OR eval the env block:"
  echo
  cmd_env
}

cmd_doctor() {
  if [ ! -d "${QS_STATE}" ]; then
    echo "error: quickstart not initialized; run quickstart.sh init first" >&2
    return 1
  fi
  XDG_CONFIG_HOME="${QS_CONFIG}" \
  XDG_STATE_HOME="${QS_STATE}" \
  XDG_CACHE_HOME="${QS_CACHE}" \
    "${SCRIPT_DIR}/doctor.sh" "$@"
}

# --- M11.1 helpers ---

_require_initialized() {
  if [ ! -d "${QS_STATE}" ] || [ ! -f "${QS_POLICY}" ]; then
    echo "error: quickstart not initialized; run quickstart.sh init first" >&2
    return 1
  fi
}

# Read fixture_task_id from quickstart-state.json (empty string if absent
# or unset). Pure stdout; no side effects.
_state_get_fixture_task_id() {
  if [ ! -f "${QS_STATE_FILE}" ]; then
    echo ""
    return 0
  fi
  python3 -c "
import json, sys
try:
    d = json.load(open('${QS_STATE_FILE}'))
    print(d.get('fixture_task_id') or '')
except Exception:
    print('')
"
}

_state_set_fixture_task_id() {
  local tid="$1"
  python3 -c "
import json, datetime as dt, os
path = '${QS_STATE_FILE}'
data = {}
if os.path.exists(path):
    try:
        data = json.load(open(path))
    except Exception:
        data = {}
data.update({
    'schema_version': '1.0',
    'fixture_task_id': '${tid}',
    'fixture_set_at': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
})
os.makedirs(os.path.dirname(path), exist_ok=True)
tmp = path + '.tmp'
json.dump(data, open(tmp, 'w'), indent=2)
os.replace(tmp, path)
"
}

# Lookup the fixture task on disk. Returns "queued|claimed|running|completed|..." on
# stdout if found, "" if absent. (Reads under sandbox env so it sees the sandbox queue.)
_lookup_fixture_state() {
  local tid="$1"
  if [ -z "${tid}" ]; then
    echo ""
    return 0
  fi
  XDG_CONFIG_HOME="${QS_CONFIG}" \
  XDG_STATE_HOME="${QS_STATE}" \
  XDG_CACHE_HOME="${QS_CACHE}" \
    python3 -c "
import json, subprocess, sys
r = subprocess.run(['${SCRIPT_DIR}/worker.sh', '--json', 'show', '${tid}'],
                   capture_output=True, text=True)
if r.returncode != 0:
    print('')
    sys.exit(0)
try:
    payload = json.loads(r.stdout)
    task = payload.get('task') or payload
    print(task.get('status') or '')
except Exception:
    print('')
"
}

cmd_enqueue() {
  _require_initialized || return 1

  local force_new=0
  for arg in "$@"; do
    case "${arg}" in
      --new) force_new=1 ;;
      *) echo "error: unknown arg to enqueue: ${arg}" >&2; return 64 ;;
    esac
  done

  # Idempotency: if a prior fixture task exists and is still discoverable,
  # surface it instead of creating a duplicate. --new forces a fresh task.
  local existing_id
  existing_id="$(_state_get_fixture_task_id)"
  if [ -n "${existing_id}" ] && [ "${force_new}" -eq 0 ]; then
    local existing_state
    existing_state="$(_lookup_fixture_state "${existing_id}")"
    if [ -n "${existing_state}" ]; then
      echo "fixture task already exists: ${existing_id} (state: ${existing_state})"
      echo "  to force a new one:  quickstart.sh enqueue --new"
      return 0
    fi
  fi

  # Fixed instruction. Self-referential by design (matches the fake
  # worker's decision text). Explicit cleanup at end of function rather
  # than a RETURN trap because `set -u` and the trap context disagree
  # on local-variable scope at return time.
  local instruction_tmp
  instruction_tmp="$(mktemp -t quickstart-instruction.XXXXXX)"
  cat > "${instruction_tmp}" <<'INSTRUCTION_EOF'
Review the quickstart fixture project at ${repo} and confirm the
continuity layer's delegated-handoff loop is operational. The
acceptable outcome is a single decision entry describing the
review and its rationale; no code changes, no tests, no
artifacts. This task exists to prove the substrate works
end-to-end on a fresh OSS install.
INSTRUCTION_EOF

  # Enqueue under the sandbox env. --kind research and --trust-level
  # read-only match the trust grant scope. --target codex and --source-
  # adapter human reflect the fixture flow: operator (human) is the
  # source; the assigned worker is codex.
  local enqueue_out
  enqueue_out="$(
    XDG_CONFIG_HOME="${QS_CONFIG}" \
    XDG_STATE_HOME="${QS_STATE}" \
    XDG_CACHE_HOME="${QS_CACHE}" \
    "${SCRIPT_DIR}/worker.sh" --json enqueue \
      --project "${QS_PROJECT_UUID}" \
      --kind research \
      --target codex \
      --trust-level read-only \
      --repo "${QS_REPO_NAME}" \
      --instruction "${instruction_tmp}" \
      --source-adapter human \
      --source-actor quickstart-operator
  )"

  # Extract the new task id from the enqueue payload.
  local new_id
  new_id="$(python3 -c "
import json, sys
try:
    p = json.loads('''${enqueue_out}''')
    t = p.get('task') if isinstance(p, dict) else None
    if isinstance(t, dict):
        print(t.get('id') or t.get('task_id') or '')
    else:
        print(p.get('id') or p.get('task_id') or '')
except Exception:
    print('')
")"
  if [ -z "${new_id}" ]; then
    echo "error: could not parse new task id from enqueue output" >&2
    echo "${enqueue_out}" >&2
    rm -f "${instruction_tmp}"
    return 1
  fi

  _state_set_fixture_task_id "${new_id}"
  rm -f "${instruction_tmp}"

  echo "enqueued fixture task: ${new_id}"
  echo "  project:      ${QS_PROJECT_UUID}"
  echo "  repo:         ${QS_REPO_NAME}"
  echo "  kind:         research"
  echo "  trust_level:  read-only"
  echo "  state:        queued"
  echo
  echo "next step:"
  echo "  scripts/quickstart.sh run-fake-worker"
}

cmd_run_fake_worker() {
  _require_initialized || return 1

  local tid
  tid="$(_state_get_fixture_task_id)"
  if [ -z "${tid}" ]; then
    echo "error: no fixture task recorded; run quickstart.sh enqueue first" >&2
    return 1
  fi

  local state
  state="$(_lookup_fixture_state "${tid}")"
  case "${state}" in
    "")
      echo "error: fixture task ${tid} not found in queue (was it cleaned up?)" >&2
      echo "       run: quickstart.sh enqueue --new" >&2
      return 1
      ;;
    completed|rejected|failed|cancelled)
      echo "fixture task ${tid} already in terminal state: ${state}"
      echo "  no work to do."
      echo "  to see the decision it produced:  quickstart.sh decisions list"
      echo "  to start fresh:                    quickstart.sh enqueue --new"
      return 0
      ;;
    queued)
      ;;  # proceed
    claimed|running)
      echo "error: fixture task ${tid} is in state '${state}'; cannot re-run" >&2
      echo "       (something claimed it but didn't finish. inspect with:" >&2
      echo "        XDG_*_HOME=quickstart  scripts/worker.sh show ${tid})" >&2
      return 1
      ;;
    *)
      echo "error: fixture task ${tid} in unexpected state '${state}'" >&2
      return 1
      ;;
  esac

  echo "running fake worker against fixture task: ${tid}"
  XDG_CONFIG_HOME="${QS_CONFIG}" \
  XDG_STATE_HOME="${QS_STATE}" \
  XDG_CACHE_HOME="${QS_CACHE}" \
    "${QS_FAKE_WORKER}" "${tid}" | python3 -c "
import json, sys
try:
    r = json.load(sys.stdin)
    print(f\"  submit status:           {r.get('status')}\")
    ids = r.get('appended_decision_ids') or []
    if ids:
        print(f\"  appended_decision_ids:   {ids}\")
    print(f\"  final task path:         {r.get('path')}\")
except Exception:
    pass
"
  echo
  echo "the thing that survived — read it:"
  echo "  scripts/quickstart.sh decisions list"
}

cmd_decisions() {
  _require_initialized || return 1
  XDG_CONFIG_HOME="${QS_CONFIG}" \
  XDG_STATE_HOME="${QS_STATE}" \
  XDG_CACHE_HOME="${QS_CACHE}" \
    "${SCRIPT_DIR}/decisions.sh" "$@"
}

# M11.4 safety validator. Refuses any path that doesn't smell like a
# sandbox path. The three targets reset removes are pre-computed
# constants (QS_CONFIG / QS_STATE / QS_CACHE), so the only way this
# fires is if HOME is corrupted at script-init time or someone
# modifies the constants — but the check exists so 'rm -rf' under
# any circumstance only ever touches a quickstart-namespaced path.
#
# Rules:
#   1. target must be non-empty
#   2. HOME must be set and not '/'
#   3. target must start with '$HOME/'
#   4. target must end with '/agent-continuity-quickstart'
#   5. target must not contain '/agent-continuity/' as a parent (the
#      real namespace — distinct from the quickstart variant)
_validate_qs_target() {
  local target="$1"
  if [ -z "${target}" ]; then
    return 1
  fi
  if [ -z "${HOME:-}" ] || [ "${HOME}" = "/" ]; then
    return 1
  fi
  case "${target}" in
    "${HOME}"/*) ;;
    *) return 1 ;;
  esac
  case "${target}" in
    */agent-continuity-quickstart) ;;
    *) return 1 ;;
  esac
  case "${target}" in
    *agent-continuity/*) return 1 ;;
  esac
  return 0
}

cmd_reset() {
  local dry_run=0
  local reinit=0
  for arg in "$@"; do
    case "${arg}" in
      --dry-run) dry_run=1 ;;
      --reinit)  reinit=1 ;;
      *)
        echo "error: unknown arg to reset: ${arg}" >&2
        echo "       valid args: --dry-run, --reinit" >&2
        return 64
        ;;
    esac
  done

  local targets=("${QS_CONFIG}" "${QS_STATE}" "${QS_CACHE}")

  # Validate every target BEFORE printing or removing. If any fails
  # safety, refuse the entire operation; do not partially remove.
  local t
  for t in "${targets[@]}"; do
    if ! _validate_qs_target "${t}"; then
      echo "error: refusing to remove unsafe path: ${t}" >&2
      echo "       (this is a defensive guard; if you see this, file a bug)" >&2
      return 1
    fi
  done

  echo "quickstart reset — targets:"
  local removed_any=0
  local would_remove=0
  for t in "${targets[@]}"; do
    if [ -e "${t}" ]; then
      echo "  ${t}    (will remove)"
      would_remove=1
    else
      echo "  ${t}    (does not exist; skip)"
    fi
  done

  if [ "${dry_run}" -eq 1 ]; then
    echo
    echo "--dry-run: nothing removed."
    return 0
  fi

  if [ "${would_remove}" -eq 0 ]; then
    echo
    echo "reset: nothing to remove (already clean)."
  else
    echo
    for t in "${targets[@]}"; do
      if [ -e "${t}" ]; then
        rm -rf -- "${t}"
        removed_any=1
      fi
    done
    echo "reset complete."
  fi

  if [ "${reinit}" -eq 1 ]; then
    echo
    echo "--reinit: running init..."
    echo
    cmd_init
  fi
}

main() {
  local cmd="${1:-}"
  case "${cmd}" in
    init)             shift; cmd_init "$@" ;;
    env)              shift; cmd_env "$@" ;;
    status)           shift; cmd_status "$@" ;;
    doctor)           shift; cmd_doctor "$@" ;;
    enqueue)          shift; cmd_enqueue "$@" ;;
    run-fake-worker)  shift; cmd_run_fake_worker "$@" ;;
    decisions)        shift; cmd_decisions "$@" ;;
    reset)            shift; cmd_reset "$@" ;;
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
