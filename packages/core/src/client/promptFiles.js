import { cadApiUrl } from './client.js';

/**
 * The viewer server's prompt files (`/__cad/prompt-files`): a Quick Edit's views as a snapshot job
 * file, its annotation as an annotation file, and each view's picture, rendered by the server with the
 * snapshot's own code. `create(request)` asks for them and answers where they are once rendered —
 * the server answers at once and renders off the request, so this asks again until they are there.
 * `read(path)` fetches one of them. `save({ name, kind, data })` writes a Quick Edit chip's copy as a
 * file at once — an annotation's annotation (`kind` "annotation") or one measurement ("measurement") — and
 * answers its path, which the chip copies. It speaks to the server over `fetch` at `origin` as the CAD
 * client does, whatever carries that fetch.
 *
 * @typedef {{ name: string, input: string, views: object[], annotation: object | null }} PromptFilesRequest
 * @typedef {{ job: string, annotation: string | null, images: { label: string, path: string }[] }} PromptFiles
 * @param {{ origin?: string, fetch?: typeof globalThis.fetch, wait?: (ms: number) => Promise<void> }} [options]
 * @returns {{ create(request: PromptFilesRequest): Promise<PromptFiles>, read(path: string): Promise<Blob>,
 *   save(request: { name: string, kind: 'annotation' | 'measurement', data: object }): Promise<string> }}
 */
export function createHttpPromptFiles({ origin = '', fetch: fetchImpl = globalThis.fetch, wait = ms => new Promise(done => setTimeout(done, ms)) } = {}) {
  const headers = { 'x-cadgen-viewer': '1' };
  const answer = async response => {
    const body = await response.json().catch(() => null);
    if (!body?.ok) throw new Error(body?.error || `The prompt's files could not be made (${response.status}).`);
    return body;
  };
  return {
    async create(request) {
      let body = await answer(await fetchImpl(cadApiUrl('/__cad/prompt-files', { origin }), {
        method: 'POST', headers: { ...headers, 'content-type': 'application/json' }, body: JSON.stringify(request),
      }));
      // Rendering: ask again after the server's pause, as long as it says it is still at it.
      while (!Array.isArray(body.images)) {
        await wait(Math.min(2000, Math.max(50, Number(body.retryMs) || 200)));
        body = await answer(await fetchImpl(cadApiUrl('/__cad/prompt-files', { origin, params: { id: body.id } }), { headers }));
      }
      return { job: body.job, annotation: body.annotation ?? null, images: body.images.map(image => ({ label: String(image.label), path: String(image.path) })) };
    },
    async save(request) {
      const body = await answer(await fetchImpl(cadApiUrl('/__cad/prompt-files/save', { origin }), {
        method: 'POST', headers: { ...headers, 'content-type': 'application/json' }, body: JSON.stringify(request),
      }));
      return String(body.path);
    },
    async read(path) {
      const name = String(path).split(/[\\/]/).pop();
      const response = await fetchImpl(cadApiUrl(`/__cad/prompt-files/${encodeURIComponent(name)}`, { origin }), { headers });
      if (!response.ok) throw new Error(`The prompt file ${name} could not be read (${response.status}).`);
      return response.blob();
    },
  };
}
