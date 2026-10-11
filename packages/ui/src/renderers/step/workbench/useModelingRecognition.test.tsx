import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { createHash } from 'node:crypto';
import { createCadClient } from '@text-to-cad/core/client';
import { useModelingRecognition as useRecognition } from './useModelingRecognition.js';
import { useStepModeling as useStepModelingHook } from './useStepModeling.js';
import { completedPackages } from '../render/completedPackageCache.js';
import { completedModelingRecognition, modelingRecognitionKey } from './modelingRecognitionCache.js';
import { installRuntimePackageDescriptor } from '../components/workbench/hooks/packageDescriptorCache.js';

let fixtureClient;
let fixtureOccurrenceIds: string[] = [];
function useModelingRecognition(url, enabled, options = {}) {
  return useRecognition(url, enabled, { client: fixtureClient, requestedOccurrenceIds: fixtureOccurrenceIds, ...options });
}
function useStepModeling(entry, enabled, options = {}) {
  return useStepModelingHook(entry, enabled, { requestedOccurrenceIds: fixtureOccurrenceIds, ...options });
}
class RecognitionWorker {
  static instances: RecognitionWorker[] = [];
  // Every component handed to a worker, in order: one worker takes one component after another.
  static jobs: { worker: RecognitionWorker, message: any }[] = [];
  terminate = vi.fn();
  postMessage = vi.fn((message: any) => { RecognitionWorker.jobs.push({ worker: this, message }); });
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor() { RecognitionWorker.instances.push(this); }
}
const tree = [{ id: 'feature', label: 'Base extrude', faces: [1], edges: [] }];
// A part's key, named for `text`.
const partOf = (text: string) => `${createHash('sha256').update(text).digest('hex')}-v1`;
let fixture = 0;
function setup() {
  fixtureOccurrenceIds = ['o1', 'o2', 'o3'];
  const version = ++fixture;
  vi.stubGlobal('Worker', RecognitionWorker);
  // A view of one component: its part, and the SURF cadgen extracts for it.
  const document = (name: string, shape = 'exact', cid = 'c') => ({
    kind: 'assembly-package', components: {
      [cid]: { part: partOf(`${shape}-${version}`), surf: 'component.surf' },
    }, occurrences: [{ id: name, component: cid }],
  });
  const urls = { a: `https://recognition.test/__cad/store?file=/${version}/a`, b: `https://recognition.test/__cad/store?file=/${version}/b`, c: `https://recognition.test/__cad/store?file=/${version}/c` };
  const fetch = vi.fn(async (url: string) => {
    const file = new URL(url).searchParams.get('file') || '';
    return new Response(JSON.stringify(file.includes('/b/') ? document('o2', 'exact', 'other') : file.includes('/c/') ? document('o3', 'changed') : document('o1')),
      { headers: { 'content-type': 'application/json' } });
  });
  vi.stubGlobal('fetch', fetch);
  fixtureClient = createCadClient();
  return { urls, fetch };
}
// Results reach the hook's state at most once a frame.
const frame = () => act(() => new Promise<void>(resolve => { requestAnimationFrame(() => resolve()); }));
async function answer(worker: RecognitionWorker, data: unknown) {
  await act(async () => { worker.onmessage?.({ data }); });
  await frame();
}
/** Answer the `job`th component handed to a worker (counting from 0 across workers). */
async function respond(data: unknown, job: number) {
  await waitFor(() => expect(RecognitionWorker.jobs.length).toBeGreaterThan(job));
  await answer(RecognitionWorker.jobs[job].worker, data);
}
afterEach(() => { cleanup(); completedPackages.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); RecognitionWorker.instances = []; RecognitionWorker.jobs = []; });

// A view whose every part was recognized before, in this resource scope, as a reopen finds it.
function recognizedPackage(count = 1) {
  const { urls, fetch } = setup();
  const digest = (value: number) => (fixture * 10000 + value).toString(16).padStart(64, '0');
  const components = {}, occurrences = [];
  for (let i = 0; i < count; i++) {
    const cid = `c${i}`;
    components[cid] = { part: `${digest(i)}-v1`, surf: `components/${cid}.surf` };
    occurrences.push({ id: `o${i}`, component: cid });
    expect(completedModelingRecognition.set(modelingRecognitionKey(components[cid], { resources: fixtureClient.resources }), { tree })).toBe(true);
  }
  const descriptor = { kind: 'assembly-package', tree: digest(2000), viewId: digest(2001),
    surfaceProducer: { build123d: '0.10.0', ocp: '7.9.3', cadqueryOcp: '7.9.3.1' }, components, occurrences };
  fixtureOccurrenceIds = occurrences.flatMap(row => [row.id, `b-${row.id}`]);
  fetch.mockImplementation(async () => new Response(JSON.stringify(descriptor)));
  const entry = { file: 'assembly.step', kind: 'assembly', sourceFormat: 'step', hash: `file-${fixture}`,
    documentHash: `document-${fixture}`, url: urls.a };
  // Recognition reads the SURF its view names: it never asks for a surface.
  const client = Object.assign(fixtureClient, { requestSurfaces: vi.fn() });
  return { client, entry, descriptor, urls, fetch, digest };
}

it('reuses the completed recognition of all 317 parts through A → B → A without surface requests, workers or geometry reads', async () => {
  const { client, entry, descriptor, urls, fetch } = recognizedPackage(317);
  const b = { ...entry, file: 'b.step', hash: 'file-b', url: urls.b };
  const bDescriptor = { ...descriptor, occurrences: descriptor.occurrences.map(row => ({ ...row, id: `b-${row.id}` })) };
  fetch.mockImplementation(async url => new Response(JSON.stringify(
    new URL(url).searchParams.get('file')?.includes('/b/') ? bDescriptor : descriptor)));
  const geometryReads = vi.spyOn(completedPackages, 'get');
  for (const model of [entry, b, entry]) {
    const view = renderHook(() => useStepModeling(model, true, { client }));
    await waitFor(() => expect(Object.keys(view.result.current.results)).toHaveLength(317));
    expect(view.result.current.results.c316.tree).toEqual(tree);
    expect(view.result.current.descriptor.occurrences[0].id).toBe(model === b ? 'b-o0' : 'o0');
    expect(view.result.current.error).toBe('');
    view.unmount();
  }
  expect(geometryReads).not.toHaveBeenCalled();
  expect(client.requestSurfaces).not.toHaveBeenCalled();
  expect(RecognitionWorker.instances).toHaveLength(0);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('recognizes by the parts its view names, through a same-URL revision and a replacement view', async () => {
  const { client, entry, descriptor, digest } = recognizedPackage();
  const view = renderHook(({ model }) => useStepModeling(model, true, { client }), { initialProps: { model: entry } });
  await waitFor(() => expect(view.result.current.results.c0?.tree).toEqual(tree));
  // A revision of the same file naming the same part: its recognition is the part's.
  view.rerender({ model: { ...entry, hash: 'changed-with-same-url' } });
  await waitFor(() => expect(view.result.current.results.c0?.tree).toEqual(tree));
  expect(RecognitionWorker.instances).toHaveLength(0);
  view.unmount();
  // A replacement view naming another part for the component: that part is recognized.
  const replacement = { ...descriptor, viewId: digest(2002),
    components: { c0: { ...descriptor.components.c0, part: `${digest(3000)}-v1` } } };
  installRuntimePackageDescriptor(entry.url, replacement, { resources: client.resources });
  const reopened = renderHook(() => useStepModeling(entry, true, { client }));
  await respond({ tree: [{ ...tree[0], label: 'Replacement part' }] }, 0);
  expect(reopened.result.current.results.c0.tree[0].label).toBe('Replacement part');
  expect(client.requestSurfaces).not.toHaveBeenCalled();
});

it('recognizes a part again when its result exceeded the metadata budget, from the SURF its view names', async () => {
  const { client, entry, descriptor } = recognizedPackage();
  const oversized = { tree: [{ ...tree[0], label: 'x'.repeat(4 * 1024 * 1024) }] };
  expect(completedModelingRecognition.set(modelingRecognitionKey(descriptor.components.c0, { resources: client.resources }), oversized)).toBe(false);
  const first = renderHook(() => useStepModeling(entry, true, { client }));
  await respond({ tree }, 0);
  expect(first.result.current.results.c0.tree).toEqual(tree);
  const surf = new URL(RecognitionWorker.instances[0].postMessage.mock.calls[0][0].resource.url);
  expect(surf.searchParams.get('file')).toMatch(/\/components\/c0\.surf$/);
  first.unmount();
  const reopen = renderHook(() => useStepModeling(entry, true, { client }));
  await waitFor(() => expect(reopen.result.current.results.c0?.tree).toEqual(tree));
  expect(client.requestSurfaces).not.toHaveBeenCalled();
  expect(RecognitionWorker.instances).toHaveLength(1);
});

it('reuses completed exact-component recognition through A → B → A and keeps occurrences file-owned', async () => {
  const { urls, fetch } = setup();
  const a = renderHook(() => useModelingRecognition(urls.a, true));
  await respond({ tree }, 0);
  expect(a.result.current.results.c.tree).toEqual(tree);
  a.unmount();
  const b = renderHook(() => useModelingRecognition(urls.b, true));
  await waitFor(() => expect(b.result.current.results.other?.tree).toEqual(tree));
  expect(b.result.current.descriptor.occurrences[0].id).toBe('o2');
  b.unmount();
  const reopen = renderHook(() => useModelingRecognition(urls.a, true));
  await waitFor(() => expect(reopen.result.current.results.c?.tree).toEqual(tree));
  expect(reopen.result.current.descriptor.occurrences[0].id).toBe('o1');
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(RecognitionWorker.instances).toHaveLength(1);
  expect(RecognitionWorker.instances[0].terminate).toHaveBeenCalledTimes(1);
  reopen.unmount();
  const changed = renderHook(() => useModelingRecognition(urls.c, true));
  await respond({ tree: [{ ...tree[0], label: 'Changed solid' }] }, 1);
  expect(changed.result.current.results.c.tree[0].label).toBe('Changed solid');
  expect(fetch).toHaveBeenCalledTimes(3);
  expect(RecognitionWorker.instances).toHaveLength(2);
});

it('does not reuse a failed recognition when the file reopens', async () => {
  const { urls, fetch } = setup();
  const first = renderHook(() => useModelingRecognition(urls.a, true));
  await respond({ error: 'Unavailable' }, 0);
  first.unmount();
  const reopen = renderHook(() => useModelingRecognition(urls.a, true));
  await respond({ tree }, 1);
  expect(reopen.result.current.results.c.tree).toEqual(tree);
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('terminates an aborted worker and rejects its late result instead of caching it', async () => {
  const { urls } = setup();
  const first = renderHook(() => useModelingRecognition(urls.a, true));
  await waitFor(() => expect(RecognitionWorker.instances).toHaveLength(1));
  first.unmount();
  expect(RecognitionWorker.instances[0].terminate).toHaveBeenCalledTimes(1);
  await answer(RecognitionWorker.instances[0], { tree: [{ ...tree[0], label: 'Late' }] });
  const reopen = renderHook(() => useModelingRecognition(urls.a, true));
  await waitFor(() => expect(RecognitionWorker.instances[1]?.postMessage).toHaveBeenCalled());
  await answer(RecognitionWorker.instances[1], { tree });
  expect(reopen.result.current.results.c.tree[0].label).toBe('Base extrude');
});

it('retires an issued recognition ticket immediately when its resource generation is cancelled', async () => {
  const {urls}=setup();
  const generation=new AbortController();
  Object.defineProperty(fixtureClient.resources,'signal',{value:generation.signal,configurable:true});
  const view=renderHook(()=>useModelingRecognition(urls.a,true));
  await waitFor(()=>expect(RecognitionWorker.instances[0]?.postMessage).toHaveBeenCalled());
  const worker=RecognitionWorker.instances[0];
  await act(async()=>{generation.abort();});
  expect(worker.terminate).toHaveBeenCalled();
  await act(async()=>{worker.onmessage?.({data:{tree}});});
  expect(view.result.current.results).toEqual({});
  view.unmount();
  Object.defineProperty(fixtureClient.resources,'signal',{value:new AbortController().signal,configurable:true});
  const reopen=renderHook(()=>useModelingRecognition(urls.a,true));
  await waitFor(()=>expect(RecognitionWorker.instances).toHaveLength(2));
  expect(reopen.result.current.results).toEqual({});
});

it('loads only expanded occurrences, cancels collapsed work and reuses completed repeated components', async () => {
  const { urls, fetch } = setup();
  const descriptor = {
    components: { a: { part: partOf('a'), surf: 'a.surf' }, b: { part: partOf('b'), surf: 'b.surf' } },
    occurrences: [{ id: 'first', component: 'a' }, { id: 'repeat', component: 'a' }, { id: 'other', component: 'b' }],
  };
  fetch.mockImplementation(async () => new Response(JSON.stringify(descriptor)));
  const view = renderHook(({ requestedOccurrenceIds }) => useRecognition(urls.a, true, {
    client: fixtureClient, requestedOccurrenceIds,
  }), { initialProps: { requestedOccurrenceIds: [] as string[] } });
  await waitFor(() => expect(view.result.current.descriptor?.occurrences).toHaveLength(3));
  expect(RecognitionWorker.instances).toHaveLength(0);
  view.rerender({ requestedOccurrenceIds: ['first'] });
  await waitFor(() => expect(RecognitionWorker.instances[0]?.postMessage).toHaveBeenCalled());
  view.rerender({ requestedOccurrenceIds: [] });
  expect(RecognitionWorker.instances[0].terminate).toHaveBeenCalledTimes(1);
  await respond({ tree: [{ ...tree[0], label: 'Cancelled result' }] }, 0);
  expect(view.result.current.results).toEqual({});
  view.rerender({ requestedOccurrenceIds: ['first'] });
  await respond({ tree }, 1);
  expect(Object.keys(view.result.current.results)).toEqual(['a']);
  view.rerender({ requestedOccurrenceIds: ['repeat'] });
  expect(RecognitionWorker.instances).toHaveLength(2);
  view.rerender({ requestedOccurrenceIds: ['other'] });
  // The idle worker takes the next component: no new worker.
  await waitFor(() => expect(RecognitionWorker.jobs).toHaveLength(3));
  expect(RecognitionWorker.jobs[2].worker).toBe(RecognitionWorker.instances[1]);
  expect(RecognitionWorker.jobs[2].message.resource.url).toContain('b.surf');
  await respond({ tree }, 2);
  view.rerender({ requestedOccurrenceIds: [] });
  view.rerender({ requestedOccurrenceIds: ['first', 'repeat', 'other'] });
  expect(Object.keys(view.result.current.results)).toEqual(['a', 'b']);
  expect(RecognitionWorker.instances).toHaveLength(2);
});

it('defaults to descriptor-only inspection until a consumer supplies an expansion frontier', async () => {
  const { urls } = setup();
  const view = renderHook(() => useRecognition(urls.a, true, { client: fixtureClient }));
  await waitFor(() => expect(view.result.current.descriptor?.occurrences).toHaveLength(1));
  expect(RecognitionWorker.instances).toHaveLength(0);
  expect(view.result.current.results).toEqual({});
});

function pendingAssembly() {
  const { urls, fetch } = setup();
  const descriptor = {
    components: { a: { part: partOf('a'), surf: 'a.surf' }, b: { part: partOf('b'), surf: 'b.surf' } },
    occurrences: [{ id: 'first', component: 'a' }, { id: 'repeat', component: 'a' }, { id: 'other', component: 'b' }],
  };
  fetch.mockImplementation(async () => new Response(JSON.stringify(descriptor)));
  const view = renderHook(({ requestedOccurrenceIds }) => useRecognition(urls.a, true, {
    client: fixtureClient, requestedOccurrenceIds,
  }), { initialProps: { requestedOccurrenceIds: ['first'] } });
  return view;
}

it('keeps the active component running when another part is expanded, then processes the new part', async () => {
  const view = pendingAssembly();
  await waitFor(() => expect(RecognitionWorker.instances[0]?.postMessage).toHaveBeenCalled());
  const first = RecognitionWorker.instances[0];
  view.rerender({ requestedOccurrenceIds: ['first', 'other'] });
  expect(first.terminate).not.toHaveBeenCalled();
  expect(RecognitionWorker.instances).toHaveLength(1);
  await respond({ tree }, 0);
  await waitFor(() => expect(RecognitionWorker.jobs).toHaveLength(2));
  expect(view.result.current.results.a.tree).toEqual(tree);
  expect(RecognitionWorker.jobs[1].message.resource.url).toContain('b.surf');
  await respond({ tree }, 1);
  expect(Object.keys(view.result.current.results).sort()).toEqual(['a', 'b']);
  // One worker, reused for the next component rather than started again.
  expect(RecognitionWorker.instances).toHaveLength(1);
  expect(first.postMessage).toHaveBeenCalledTimes(2);
  expect(first.terminate).not.toHaveBeenCalled();
  view.unmount();
  expect(first.terminate).toHaveBeenCalledTimes(1);
});

it('keeps pending recognition for a repeated instance and removes collapsed parts from the queue', async () => {
  const view = pendingAssembly();
  await waitFor(() => expect(RecognitionWorker.instances[0]?.postMessage).toHaveBeenCalled());
  const first = RecognitionWorker.instances[0];
  view.rerender({ requestedOccurrenceIds: ['first', 'other'] });
  view.rerender({ requestedOccurrenceIds: ['repeat'] });
  expect(first.terminate).not.toHaveBeenCalled();
  await respond({ tree }, 0);
  expect(view.result.current.results.a.tree).toEqual(tree);
  expect(view.result.current.results.b).toBeUndefined();
  expect(RecognitionWorker.instances).toHaveLength(1);
});

it('retries a failed component without restarting another component that is still running', async () => {
  const view = pendingAssembly();
  view.rerender({ requestedOccurrenceIds: ['first', 'other'] });
  await respond({ error: 'Unavailable' }, 0);
  await waitFor(() => expect(RecognitionWorker.jobs).toHaveLength(2));
  const other = RecognitionWorker.jobs[1].worker;
  act(() => view.result.current.retryFailed());
  expect(other.terminate).not.toHaveBeenCalled();
  expect(RecognitionWorker.instances).toHaveLength(1);
  await respond({ tree }, 1);
  await waitFor(() => expect(RecognitionWorker.jobs).toHaveLength(3));
  expect(RecognitionWorker.jobs[2].message.resource.url).toContain('a.surf');
  await respond({ tree }, 2);
  expect(view.result.current.results.a.tree).toEqual(tree);
  expect(view.result.current.results.b.tree).toEqual(tree);
});

it('cancels only the unwanted active part and ignores its late result while processing the remaining part', async () => {
  const view = pendingAssembly();
  await waitFor(() => expect(RecognitionWorker.instances[0]?.postMessage).toHaveBeenCalled());
  const first = RecognitionWorker.instances[0];
  view.rerender({ requestedOccurrenceIds: ['first', 'other'] });
  view.rerender({ requestedOccurrenceIds: ['other'] });
  expect(first.terminate).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(RecognitionWorker.instances[1]?.postMessage).toHaveBeenCalled());
  await answer(first, { tree: [{ ...tree[0], label: 'Late' }] });
  expect(view.result.current.results.a).toBeUndefined();
  await answer(RecognitionWorker.instances[1], { tree });
  expect(Object.keys(view.result.current.results)).toEqual(['b']);
  view.rerender({ requestedOccurrenceIds: ['first', 'other'] });
  await waitFor(() => expect(RecognitionWorker.jobs).toHaveLength(3));
  expect(RecognitionWorker.jobs[2].worker).toBe(RecognitionWorker.instances[1]);
  await respond({ tree }, 2);
  expect(view.result.current.results.a.tree).toEqual(tree);
});
