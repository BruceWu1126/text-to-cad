import test from 'node:test';
import assert from 'node:assert/strict';
import { createHttpPromptFiles } from './promptFiles.js';

test("a prompt's files are asked for once and asked after until rendered; a file is read by its name", async () => {
  const calls = [];
  const replies = [
    Response.json({ ok: true, id: 'p1', retryMs: 10 }, { status: 202 }),
    Response.json({ ok: true, id: 'p1', retryMs: 10 }, { status: 202 }),
    Response.json({ ok: true, job: '/tmp/p/a-1.job.json', sketch: null, images: [{ label: 'current', path: '/tmp/p/a-1-current.png' }] }),
    new Response(new Blob([new Uint8Array([0x89, 0x50])], { type: 'image/png' })),
  ];
  const waits = [];
  const files = createHttpPromptFiles({ origin: 'http://cad.invalid', wait: async ms => { waits.push(ms); },
    fetch: async (url, options = {}) => { calls.push({ url, method: options.method || 'GET', headers: options.headers }); return replies.shift(); } });
  const request = { name: 'a.step', input: '/m/a.step', views: [{ label: 'current' }], sketch: null };
  assert.deepEqual(await files.create(request),
    { job: '/tmp/p/a-1.job.json', sketch: null, images: [{ label: 'current', path: '/tmp/p/a-1-current.png' }] });
  assert.deepEqual(calls.map(call => `${call.method} ${call.url}`), [
    'POST http://cad.invalid/__cad/prompt-files',
    'GET http://cad.invalid/__cad/prompt-files?id=p1',
    'GET http://cad.invalid/__cad/prompt-files?id=p1',
  ]);
  assert.ok(calls.every(call => call.headers['x-cadgen-viewer'] === '1'));
  assert.deepEqual(waits, [50, 50]);
  const png = await files.read('/tmp/p/a-1-current.png');
  assert.equal(png.type, 'image/png');
  assert.equal(calls.at(-1).url, 'http://cad.invalid/__cad/prompt-files/a-1-current.png');
});

test("a refused or failed render says why", async () => {
  const files = createHttpPromptFiles({ fetch: async () => Response.json({ ok: false, error: 'sketch: marks must be an array' }, { status: 400 }) });
  await assert.rejects(files.create({ name: 'a.step', input: '/m/a.step', views: [], sketch: {} }), /marks must be an array/);
});
