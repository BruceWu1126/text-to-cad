import assert from 'node:assert/strict';
import { test, after } from 'node:test';
import { build } from 'esbuild';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
const directory = await mkdtemp(join(tmpdir(), 'cad-downloads-test-'));
after(() => rm(directory, { recursive: true, force: true }));
const output = join(directory, 'downloads.mjs');
await build({ entryPoints: [fileURLToPath(new URL('./downloads.ts', import.meta.url))], bundle: true, platform: 'node', format: 'esm', outfile: output });
const { createBrowserDownloads } = await import(pathToFileURL(output));
const storage = { getItem: () => null, setItem() {}, removeItem() {} };
const options = { secure: true, storage, key: 'test' };

test('picker runs synchronously; bytes stream and close before success', async () => {
  const events = [];
  const port = createBrowserDownloads({ ...options, picker: async () => {
    events.push('picker');
    return { name: 'local.step', createWritable: async () => new WritableStream({ write(chunk) { events.push([...chunk]); }, close() { events.push('closed'); } }) };
  } });
  const pending = port.choose('server.step', '.step');
  assert.deepEqual(events, ['picker']);
  const destination = await pending;
  assert.equal(destination.name, 'local.step');
  await destination.write(new Response(new Uint8Array([1, 2, 3]), { headers: { 'content-length': '3' } }), { signal: new AbortController().signal, progress: bytes => events.push(bytes) });
  assert.deepEqual(events, ['picker', 3, [1, 2, 3], 'closed']);
});

test('cancelled picker makes no destination; insecure origin has no silent fallback', async () => {
  let calls = 0;
  const picker = async () => { calls++; throw new DOMException('Cancelled', 'AbortError'); };
  const port = createBrowserDownloads({ ...options, picker });
  assert.equal(await port.choose('part.stl', '.stl'), null);
  const insecure = createBrowserDownloads({ ...options, picker, secure: false });
  await assert.rejects(insecure.choose('part.stl', '.stl'), /HTTPS/);
  assert.equal(calls, 1);
});

test('truncated transfer aborts the writable instead of committing a partial file', async () => {
  let closed = false, aborted = false;
  const port = createBrowserDownloads({ ...options, picker: async () => ({ name: 'x.stl', createWritable: async () => new WritableStream({ close() { closed = true; }, abort() { aborted = true; } }) }) });
  const destination = await port.choose('x.stl', '.stl');
  await assert.rejects(destination.write(new Response('short', { headers: { 'content-length': '100' } }), { signal: new AbortController().signal, progress() {} }), /incomplete/);
  assert.equal(closed, false);
  assert.equal(aborted, true);
});

test('restoration stores job requests only, never filesystem handles', () => {
  let held;
  const port = createBrowserDownloads({ ...options, picker: async () => {}, storage: { ...storage, getItem: () => held, setItem: (_key, value) => held = value } });
  const value = [{ filename: 'x.stl', request: { requestId: 'a', epoch: 'e', source: { doc: 'd' }, format: 'stl' } }];
  port.remember(value);
  assert.deepEqual(port.restore(), value);
});
