import type { DownloadPort } from '@text-to-cad/ui/host';

type SaveHandle = { name: string; createWritable(): Promise<WritableStream<Uint8Array>> };
type Picker = (options: { suggestedName: string; types: { description: string; accept: Record<string, string[]> }[]; excludeAcceptAllOption: boolean }) => Promise<SaveHandle>;

function tabStorage(): Storage | undefined {
  try { return window.sessionStorage; } catch { return undefined; }
}

/** The picker runs immediately in the click, before any server work. Handles stay in this tab. */
export function createBrowserDownloads({ picker = (window as unknown as { showSaveFilePicker?: Picker }).showSaveFilePicker?.bind(window),
  secure = window.isSecureContext, storage = tabStorage(), key = `cadgen:export:${window.location.pathname}`,
}: { picker?: Picker; secure?: boolean; storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>; key?: string } = {}): DownloadPort {
  const unavailable = !secure ? 'Choosing a local save location requires HTTPS or localhost.'
    : !picker ? 'This browser cannot choose a local save location. Open the viewer in a browser with a save-file picker, such as desktop Chrome or Edge.' : undefined;
  return {
    unavailable,
    async choose(name, extension) {
      if (unavailable || !picker) throw new Error(unavailable);
      let handle: SaveHandle;
      try {
        handle = await picker({ suggestedName: name, types: [{ description: extension.slice(1).toUpperCase(),
          accept: { 'application/octet-stream': extension === '.step' ? ['.step', '.stp'] : [extension] } }], excludeAcceptAllOption: true });
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') return null;
        throw error;
      }
      // Never guess a full local path: the browser only exposes the chosen name.
      return {
        name: handle.name,
        async write(response, { signal, progress }) {
          if (!response.ok || !response.body) throw new Error('The export download did not return a file.');
          const writable = await handle.createWritable();
          let bytes = 0;
          const total = Number(response.headers.get('content-length'));
          const counted = new TransformStream<Uint8Array, Uint8Array>({
            transform(chunk, controller) { bytes += chunk.byteLength; progress(bytes); controller.enqueue(chunk); },
            flush() { if (total > 0 && bytes !== total) throw new Error('The export download was incomplete.'); },
          });
          // pipeTo closes only on success; abort/error aborts the temporary local write.
          await response.body.pipeThrough(counted).pipeTo(writable, { signal });
        },
      };
    },
    restore() {
      try {
        const value = JSON.parse(storage?.getItem(key) || '[]');
        return Array.isArray(value) ? value.filter(row => typeof row.filename === 'string' && typeof row.request?.requestId === 'string'
          && typeof row.request?.epoch === 'string' && row.request?.source) : [];
      } catch { return []; }
    },
    remember(value) { try { if (value) storage?.setItem(key, JSON.stringify(value)); else storage?.removeItem(key); } catch { /* tab storage may be unavailable */ } },
  };
}
