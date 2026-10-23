import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createExportService, ExportRequestError } from '../../dist/client/exports.js';

test('export requests stay in the viewer session and stream download bytes', async () => {
  const calls = [];
  const service = createExportService({ origin: 'https://example.test/s/session', fetch: async (url, init) => {
    calls.push({ url, ...init });
    if (String(url).endsWith('/file')) return new Response('artifact');
    return Response.json({ id: 'job', sequence: 7, state: 'ready' });
  } });
  const request = { requestId: 'stable-request', epoch: 'epoch', source: { doc: 'doc', revision: 'rev', part: null, pose: null }, format: 'step' };
  const abort = new AbortController();
  await service.create(request, abort.signal);
  await service.status('job', 7, abort.signal);
  await service.cancel('job');
  const response = await service.download('job', abort.signal);
  assert.equal(response.bodyUsed, false);
  assert.equal(await response.text(), 'artifact');
  assert.equal(calls[0].url, 'https://example.test/s/session/__cad/exports');
  assert.equal(calls[0].headers['x-cadgen-viewer'], '1');
  assert.deepEqual(JSON.parse(calls[0].body), request);
  assert.match(calls[1].url, /\/job\?after=7&wait=25$/);
  assert.match(calls[2].url, /\/job\/cancel$/);
  assert.equal(calls[3].signal, abort.signal);
});

test('an expired artifact reports its status for the toast, not a second generation', async () => {
  const service = createExportService({ fetch: async () => Response.json({ error: 'Expired' }, { status: 410 }) });
  await assert.rejects(service.download('old', new AbortController().signal), error => error instanceof ExportRequestError && error.status === 410 && error.message === 'Expired');
});
