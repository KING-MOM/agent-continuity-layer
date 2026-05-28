#!/usr/bin/env node
// M5.4 MCP smoke test harness.
//
// Imports the real agent-worker MCP extension and invokes each registered
// tool's execute() function with realistic params. Captures the JSON output
// in the exact shape Mika would consume.
//
// Read-only-ish: enqueue creates a real task in the bridged queue. We tag the
// goal as M5.4 so post-test cleanup is obvious.

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import url from 'node:url';

const EXTENSION_PATH = path.join(
  os.homedir(), '.openclaw/workspace/.openclaw/extensions/agent-worker/index.js'
);
const OUT_DIR = '/tmp/m5-4-out';
fs.mkdirSync(OUT_DIR, { recursive: true });

const captured = { tools: [] };
const fakeApi = {
  registerTool: (def) => { captured.tools.push(def); },
  logger: { info: () => {}, error: (m) => process.stderr.write(`[ext logger] ${m}\n`) },
};

const mod = await import(url.pathToFileURL(EXTENSION_PATH).href);
mod.default(fakeApi);

console.log(`captured ${captured.tools.length} tool(s):`,
  captured.tools.map(t => t.name).join(', '));

async function callTool(name, args) {
  const tool = captured.tools.find(t => t.name === name);
  if (!tool) throw new Error(`tool not found: ${name}`);
  const t0 = Date.now();
  const result = await tool.execute(`m5-4-${name}-${Date.now()}`, args || {});
  return { result, elapsed_ms: Date.now() - t0 };
}

function save(name, payload) {
  fs.writeFileSync(path.join(OUT_DIR, `${name}.json`), JSON.stringify(payload, null, 2) + '\n');
}

function summarize(name, callResult) {
  const r = callResult.result;
  const details = r?.details ?? null;
  console.log(`\n--- ${name} (${callResult.elapsed_ms}ms) ---`);
  console.log(`  content[0].type:   ${r?.content?.[0]?.type}`);
  console.log(`  details top keys:  ${details ? Object.keys(details).sort().join(', ') : '(none)'}`);
  if (details?.ok !== undefined) console.log(`  details.ok:        ${details.ok}`);
  return details;
}

// === 1. worker_enqueue ===
const r1 = await callTool('worker_enqueue', {
  mode: 'research',
  worker: 'codex',
  repo: '/Users/<operator>/.openclaw/workspace/agent-continuity-layer',
  goal: 'M5.4 MCP smoke — count lines in CHARTER.md (read-only, do not execute beyond a dry-run inspection)',
  filesystem: 'read_only',
});
const d1 = summarize('worker_enqueue', r1);
save('worker_enqueue', r1);
const enqueuedMikaId = d1?.task?.task_id;
console.log(`  task_id:           ${enqueuedMikaId}`);
console.log(`  task_id regex OK:  ${/^task_[A-Za-z0-9_\-]+$/.test(enqueuedMikaId ?? '')}`);
console.log(`  task keys:         ${d1?.task ? Object.keys(d1.task).sort().join(',') : ''}`);
console.log(`  policy.queued_only:${d1?.policy?.queued_only}`);
console.log(`  policy.execution:  ${d1?.policy?.execution}`);

// === 2. worker_list (no filter) ===
const r2 = await callTool('worker_list', {});
const d2 = summarize('worker_list', r2);
save('worker_list', r2);
console.log(`  tasks (parsed):    ${d2?.tasks?.length ?? 'n/a'}`);
console.log(`  raw is JSON text:  ${(d2?.raw ?? '').trimStart().startsWith('[')}`);
console.log(`  queue_root:        ${d2?.queue_root}`);

// === 3. worker_list (state filter) ===
const r3 = await callTool('worker_list', { state: 'pending' });
const d3 = summarize('worker_list(pending)', r3);
save('worker_list_pending', r3);
console.log(`  raw shows task:    ${(d3?.raw ?? '').includes(enqueuedMikaId)}`);

// FINDING: MCP wrapper's enqueueTask() expects task_id at top level of .mjs output,
// but .mjs always wraps as {queued, task: {task_id, ...}}. The wrapper has been
// reading the wrong level since day 1. Pre-existing bug; bridge didn't introduce it.
// Recover the real task_id by parsing the raw field the wrapper exposed.
let realEnqueuedId = enqueuedMikaId;
if (!realEnqueuedId && d1?.raw) {
  try {
    const parsed = JSON.parse(d1.raw);
    realEnqueuedId = parsed?.task?.task_id;
    console.log(`  ⚠  wrapper failed to extract task_id; recovered from raw: ${realEnqueuedId}`);
  } catch {}
}
if (!realEnqueuedId) throw new Error('cannot recover task_id even from raw');

// === 4. worker_show on the enqueued task ===
const r4 = await callTool('worker_show', { task_id: realEnqueuedId });
const d4 = summarize('worker_show(new)', r4);
save('worker_show_new', r4);
console.log(`  task.task_id:      ${d4?.task?.task_id}`);
console.log(`  task.worker:       ${d4?.task?.worker}`);
console.log(`  task.mode:         ${d4?.task?.mode}`);
console.log(`  task.repo:         ${d4?.task?.repo}`);
console.log(`  task.state:        ${d4?.task?.state}`);
console.log(`  task keys:         ${Object.keys(d4?.task ?? {}).sort().join(',')}`);

// === 5. worker_show on the completed M5.3 task (proves bridge round-trip on a completed task) ===
const COMPLETED_TASK = 'task_e49c5caffbea';
const r5 = await callTool('worker_show', { task_id: COMPLETED_TASK });
const d5 = summarize('worker_show(completed)', r5);
save('worker_show_completed', r5);
console.log(`  state:             ${d5?.task?.state}`);
console.log(`  task.status:       ${d5?.task?.status}`);

// === 6. worker_dry_run_next ===
const r6 = await callTool('worker_dry_run_next', {});
const d6 = summarize('worker_dry_run_next', r6);
save('worker_dry_run_next', r6);
const dryParsed = (() => { try { return JSON.parse(d6?.output ?? ''); } catch { return null; } })();
console.log(`  dry-run task_id:   ${dryParsed?.task_id}`);
console.log(`  bridged marker:    ${dryParsed?.bridged}`);
console.log(`  no_state_mutated:  ${dryParsed?.no_state_mutated}`);
console.log(`  policy.no_execution:${d6?.policy?.no_execution}`);

// === 7. worker_trust_list ===
const r7 = await callTool('worker_trust_list', {});
const d7 = summarize('worker_trust_list', r7);
save('worker_trust_list', r7);
console.log(`  policy.version:    ${d7?.policy?.version}`);
console.log(`  grants count:      ${d7?.policy?.grants?.length}`);

// === 8. worker_trust_check on the enqueued task ===
const r8 = await callTool('worker_trust_check', { task_id: realEnqueuedId });
const d8 = summarize('worker_trust_check', r8);
save('worker_trust_check', r8);
console.log(`  result.trusted:    ${d8?.result?.trusted}`);
console.log(`  result.grant_id:   ${d8?.result?.grant_id}`);
console.log(`  result.state:      ${d8?.result?.state}`);
console.log(`  checked keys:      ${Object.keys(d8?.result?.checked ?? {}).sort().join(',')}`);

console.log(`\n=== ALL 8 MCP TOOL CALLS COMPLETED ===`);
console.log(`Evidence captured at: ${OUT_DIR}`);
console.log(`Created test task for cleanup: ${realEnqueuedId}`);
