'use strict';

/**
 * End-to-end verification: spawns bin/repolens-mcp.js as a real child process
 * and drives it over real stdio with the MCP SDK's own Client +
 * StdioClientTransport, the same mechanism Claude Desktop and Claude Code
 * use. Every tool call hits the live production API (or REPOLENS_API_URL if set).
 *
 *   node test/verify.js [owner/repo-to-ingest]
 *
 * The ingest repo must be a small real repo that is NOT yet indexed on the
 * target API. It will be indexed as a side effect.
 */

const path = require('node:path');
const assert = require('node:assert');
const { Client } = require('@modelcontextprotocol/sdk/client/index.js');
const { StdioClientTransport } = require('@modelcontextprotocol/sdk/client/stdio.js');

const SERVER = path.join(__dirname, '..', 'bin', 'repolens-mcp.js');
const INDEXED = 'sindresorhus/is-plain-obj';
const NEVER = 'repolens-test/definitely-never-indexed';
const INGEST = process.argv[2] || 'pallets/markupsafe';

function log(...a) {
  console.log(...a);
}

async function main() {
  const transport = new StdioClientTransport({ command: process.execPath, args: [SERVER], stderr: 'pipe' });
  const client = new Client({ name: 'repolens-mcp-verify', version: '0.0.0' });
  await client.connect(transport);
  log(`Connected to ${client.getServerVersion().name} ${client.getServerVersion().version} over stdio (pid ${transport.pid}).\n`);

  // ---------------------------------------------------------------- discovery
  log('--- tools/list: names, and descriptions an agent can plan from ---');
  const { tools } = await client.listTools();
  const byName = Object.fromEntries(tools.map((t) => [t.name, t]));
  assert.deepStrictEqual(Object.keys(byName).sort(), ['ask_repo', 'check_repo_indexed', 'ingest_repo']);
  for (const t of tools) {
    assert.match(t.description, /live RepoLens service/, `${t.name}: must say it's a live service`);
    assert.match(t.description, /not a static or offline knowledge source/, `${t.name}: must say it isn't static`);
    assert.ok(t.outputSchema, `${t.name}: has an output schema`);
  }
  // The sequence check -> ingest if needed -> ask, stated where an agent looks for it.
  assert.match(byName.check_repo_indexed.description, /Call this first/);
  assert.match(byName.check_repo_indexed.description, /indexed=false, call ingest_repo before ask_repo/);
  assert.match(byName.ask_repo.description, /how does the retry logic work in this repo/);
  assert.match(byName.ask_repo.description, /must be indexed first.*call ingest_repo/s);
  assert.match(byName.ingest_repo.description, /INDEXING IS SLOW: about 1-3 minutes .* up to ~15 minutes/);
  assert.match(byName.ingest_repo.description, /call ingest_repo again with the same repo_url: it resumes the same job/);
  log('  tools:', Object.keys(byName).join(', '));
  log('  every description: live service, not static; sequence and wait times stated\nPASS\n');

  // ------------------------------------------------------ check_repo_indexed
  log(`--- check_repo_indexed: ${INDEXED} (indexed) and ${NEVER} (never indexed) ---`);
  const yes = await client.callTool({ name: 'check_repo_indexed', arguments: { owner_repo: INDEXED } });
  assert.ok(!yes.isError, JSON.stringify(yes));
  assert.strictEqual(yes.structuredContent.indexed, true);
  assert.strictEqual(yes.structuredContent.next_step, 'ask_repo');
  log('  indexed repo ->', yes.content[0].text);
  const no = await client.callTool({ name: 'check_repo_indexed', arguments: { owner_repo: `https://github.com/${NEVER}` } });
  assert.ok(!no.isError, JSON.stringify(no));
  assert.strictEqual(no.structuredContent.indexed, false);
  assert.strictEqual(no.structuredContent.next_step, 'ingest_repo');
  log('  never-indexed ->', no.content[0].text);
  log('PASS\n');

  // ---------------------------------------------------------------- ask_repo
  log(`--- ask_repo: a real question about ${INDEXED} ---`);
  const t0 = Date.now();
  const ans = await client.callTool(
    { name: 'ask_repo', arguments: { owner_repo: INDEXED, question: 'How does it decide if something is a plain object?' } },
    undefined,
    { timeout: 300000 }
  );
  assert.ok(!ans.isError, JSON.stringify(ans));
  const a = ans.structuredContent;
  assert.strictEqual(a.answer_status, 'answered');
  assert.ok(a.citations.length > 0, 'has citations');
  assert.ok(a.citations.some((c) => c.file === 'index.js'), 'cites index.js, where isPlainObject lives');
  assert.ok(a.citations.every((c) => c.url.startsWith(`https://github.com/${INDEXED}/blob/${a.commit}/`)), 'links pinned to the commit');
  assert.match(a.answer, /prototype/i);
  log(`  ${((Date.now() - t0) / 1000).toFixed(1)}s, status ${a.answer_status}, ${a.citations.length} citations (${a.citations.filter((c) => c.verified).length} verified)`);
  log('  text content starts:', ans.content[0].text.split('\n').slice(0, 3).join(' | ').slice(0, 220));
  log('  e.g.', a.citations.find((c) => c.file === 'index.js').url);
  log('PASS\n');

  log(`--- ask_repo on a never-indexed repo: an actionable error an agent can act on ---`);
  const na = await client.callTool({ name: 'ask_repo', arguments: { owner_repo: NEVER, question: 'What does it do?' } });
  assert.strictEqual(na.isError, true);
  assert.match(na.content[0].text, /NOT indexed/);
  assert.match(na.content[0].text, new RegExp(`call ingest_repo with repo_url "https://github.com/${NEVER}"`));
  log('  ', na.content[0].text);
  log('PASS\n');

  // ------------------------------------------------------------- ingest_repo
  log(`--- ingest_repo: ${INGEST} (real, small, not yet indexed on this API) ---`);
  const pre = await client.callTool({ name: 'check_repo_indexed', arguments: { owner_repo: INGEST } });
  assert.strictEqual(pre.structuredContent.indexed, false, `${INGEST} must not be indexed before this test`);

  log('  call 1: wait_seconds=3, expecting a prompt "in_progress" with a job_id');
  let t = Date.now();
  const first = await client.callTool({ name: 'ingest_repo', arguments: { repo_url: `https://github.com/${INGEST}`, wait_seconds: 3 } });
  assert.ok(!first.isError, JSON.stringify(first));
  assert.strictEqual(first.structuredContent.status, 'in_progress');
  assert.match(first.structuredContent.job_id, /^[0-9a-f]{32}$/);
  assert.ok(Date.now() - t < 20000, 'returned within the bounded wait');
  log(`    returned after ${((Date.now() - t) / 1000).toFixed(1)}s:`, first.content[0].text);
  const jobId = first.structuredContent.job_id;

  log('  call 2+: default wait, with progress notifications, until complete');
  let result;
  const progress = [];
  for (let call = 2; call <= 12; call++) {
    t = Date.now();
    result = await client.callTool(
      { name: 'ingest_repo', arguments: { repo_url: INGEST } },
      undefined,
      { timeout: 120000, resetTimeoutOnProgress: true, onprogress: (p) => progress.push(p) }
    );
    assert.ok(!result.isError, JSON.stringify(result));
    const s = result.structuredContent;
    log(`    call ${call}: ${s.status} after ${((Date.now() - t) / 1000).toFixed(1)}s (job ${s.job_id}, resumed_existing_job=${s.resumed_existing_job})`);
    assert.ok(Date.now() - t < 70000, 'each call stays within the documented bounded wait');
    assert.strictEqual(s.job_id, jobId, 'resumed the same job instead of starting a new one');
    assert.strictEqual(s.resumed_existing_job, true);
    if (s.status === 'complete') break;
  }
  assert.strictEqual(result.structuredContent.status, 'complete');
  assert.ok(progress.length > 0, 'progress notifications arrived over the protocol');
  log(`    progress notifications received: ${progress.length}, e.g. ${progress.slice(-3).map((p) => `"${p.message}"`).join(', ')}`);
  const r = result.structuredContent;
  log(`    result: ${r.repo_id} @ ${r.commit.slice(0, 7)}: ${r.files_indexed} files, ${r.chunks} chunks, ${r.internal_imports} imports`);

  const post = await client.callTool({ name: 'check_repo_indexed', arguments: { owner_repo: INGEST } });
  assert.strictEqual(post.structuredContent.indexed, true);
  assert.strictEqual(post.structuredContent.chunks, r.chunks);
  log('  check_repo_indexed now ->', post.content[0].text);

  const again = await client.callTool({ name: 'ingest_repo', arguments: { repo_url: INGEST } });
  assert.strictEqual(again.structuredContent.status, 'already_indexed');
  log('  ingest_repo again ->', again.content[0].text);
  log('PASS\n');

  await client.close();
  log('ALL CHECKS PASSED');
}

main().catch((err) => {
  console.error('\nFAILED:', err);
  process.exit(1);
});
