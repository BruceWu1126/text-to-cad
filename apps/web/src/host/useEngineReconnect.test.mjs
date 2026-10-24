import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';
import { useEngineReconnect } from './useEngineReconnect.js';

test('a page that lost its engine asks the same address, backing off, and reloads once a new engine answers', async () => {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://127.0.0.1:3245/6f1c2a4e-1b2c-5d3e-8f4a-000000000001' });
  const previous = new Map(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  for (const [key, value] of Object.entries({ window: dom.window, document: dom.window.document, IS_REACT_ACT_ENVIRONMENT: true })) Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  // The engine as the page's fetches meet it: nothing listening, then one that does not know the
  // page's token, then the installation's engine again, a new process.
  const answers = ['refused', 'refused', 'forbidden', 'back'];
  const asked = [];
  const fetch = async (url) => {
    asked.push(url);
    const answer = answers.shift() ?? 'back';
    if (answer === 'refused') throw new TypeError('Failed to fetch');
    if (answer === 'forbidden') return new Response(JSON.stringify({ error: 'engine token required' }), { status: 403 });
    return new Response(JSON.stringify({ pid: 2 }), { status: 200 });
  };
  const ask = () => fetch('/__cad/server').then(response => (response.ok ? response.json() : null));
  const queued = [];
  const reloads = [];
  const options = { pid: 1, ask, reload: () => reloads.push(asked.length),
    schedule: (run, delayMs) => { const timer = { run, delayMs }; queued.push(timer); return timer; },
    cancel: timer => queued.splice(queued.indexOf(timer), 1) };
  let gone = false;
  function Probe() { useEngineReconnect(gone, options); return null; }
  const root = createRoot(dom.window.document.getElementById('root'));
  const render = () => act(() => root.render(createElement(Probe)));
  const delays = [];
  const advance = () => act(async () => { const timer = queued.shift(); delays.push(timer.delayMs); await timer.run(); });
  try {
    await render();
    assert.equal(queued.length, 0, 'nothing is asked while the engine answers');
    gone = true;
    await render();
    for (let turn = 0; turn < 3; turn += 1) await advance();
    assert.deepEqual(reloads, [], 'neither nothing listening nor an engine without the token is the engine back');
    gone = false;  // a read answered meanwhile: the notice goes, the asking does not
    await render();
    await advance();
    assert.deepEqual(delays, [1000, 2000, 4000, 5000], 'a second first, backing off to five');
    assert.deepEqual(asked, Array(4).fill('/__cad/server'), 'the same address each time, its token the cookie');
    assert.deepEqual(reloads, [4], 'reloaded into the same document once a new engine answered');
    assert.equal(queued.length, 0, 'and it stops asking');
  } finally {
    await act(() => root.unmount());
    dom.window.close();
    for (const [key, descriptor] of previous) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; }
  }
});

test('the engine the page loaded from answering again was a blip: nothing is reloaded', async () => {
  const dom = new JSDOM('<div id="root"></div>');
  const previous = new Map(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  for (const [key, value] of Object.entries({ window: dom.window, document: dom.window.document, IS_REACT_ACT_ENVIRONMENT: true })) Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  const queued = [];
  const reloads = [];
  function Probe() {
    useEngineReconnect(true, { pid: 1, ask: async () => ({ pid: 1 }), reload: () => reloads.push(1),
      schedule: (run, delayMs) => { queued.push({ run, delayMs }); return queued.at(-1); }, cancel: () => {} });
    return null;
  }
  const root = createRoot(dom.window.document.getElementById('root'));
  try {
    await act(() => root.render(createElement(Probe)));
    await act(async () => { await queued.shift().run(); });
    assert.deepEqual([reloads, queued.length], [[], 0]);
  } finally {
    await act(() => root.unmount());
    dom.window.close();
    for (const [key, descriptor] of previous) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; }
  }
});
