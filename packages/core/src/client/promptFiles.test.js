import test from 'node:test';
import assert from 'node:assert/strict';
import { createHttpPromptFiles } from './promptFiles.js';

test("a prompt's files are asked for once and asked after until rendered; a file is read by its name", async () => {
  const calls = [];
  const replies = [
    Response.json({ ok: true, id: 'p1', retryMs: 10 }, { status: 202 }),
    Response.json({ ok: true, id: 'p1', retryMs: 10 }, { status: 202 }),
    Response.json({ ok: true, job: '/tmp/p/a-1.job.json', annotation: null, images: [{ label: 'current', path: '/tmp/p/a-1-current.png' }] }),
    new Response(new Blob([new Uint8Array([0x89, 0x50])], { type: 'image/png' })),
  ];
  const waits = [];
  const files = createHttpPromptFiles({ origin: 'http://cad.invalid', wait: async ms => { waits.push(ms); },
    fetch: async (url, options = {}) => { calls.push({ url, method: options.method || 'GET', headers: options.headers }); return replies.shift(); } });
  const request = { name: 'a.step', input: '/m/a.step', views: [{ label: 'current' }], annotation: null };
  assert.deepEqual(await files.create(request),
    { job: '/tmp/p/a-1.job.json', annotation: null, images: [{ label: 'current', path: '/tmp/p/a-1-current.png' }] });
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

test("a chip's copy is saved as a file at once, and answered with its path", async () => {
  const calls = [];
  const files = createHttpPromptFiles({ origin: 'http://cad.invalid', fetch: async (url, options) => {
    calls.push({ url, method: options.method, body: JSON.parse(options.body), guard: options.headers['x-cadgen-viewer'] });
    return Response.json({ ok: true, path: '/tmp/p/a-3.measurement.json' });
  } });
  const data = { start: [0, 0, 0], end: [1, 0, 0], label: '1.00 mm', color: '#d59090' };
  assert.equal(await files.save({ name: 'a.step', kind: 'measurement', data }), '/tmp/p/a-3.measurement.json');
  assert.deepEqual(calls, [{ url: 'http://cad.invalid/__cad/prompt-files/save', method: 'POST', body: { name: 'a.step', kind: 'measurement', data }, guard: '1' }]);
});

test("a refused or failed render says why", async () => {
  const files = createHttpPromptFiles({ fetch: async () => Response.json({ ok: false, error: 'annotation: marks must be an array' }, { status: 400 }) });
  await assert.rejects(files.create({ name: 'a.step', input: '/m/a.step', views: [], annotation: {} }), /marks must be an array/);
});
