import { useEffect, useState } from 'react';
import type { FileCollection, FileSource } from '../file-viewer/types.js';

export function MemoryModels({ source, onOpen }: { source: FileSource; onOpen(path: string): void }) {
  const [groups, setGroups] = useState<readonly FileCollection[]>([]);
  useEffect(() => {
    if (!source.collections) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const read = async () => {
      try {
        const next = await source.collections!({ signal: controller.signal });
        if (!controller.signal.aborted) setGroups(next);
      } catch { if (!controller.signal.aborted) setGroups([]); }
      finally { if (!controller.signal.aborted) timer = setTimeout(read, 2000); }
    };
    void read();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [source]);
  return <>{groups.filter(group => group.entries.length).map(group => <section key={group.id} className="mb-6" aria-label={group.label}>
    <h2 className="cad-library-heading mb-2">{group.label}</h2>
    <ul>{group.entries.map(entry => <li key={entry.path}>
      <button type="button" className="w-full rounded-md px-3 py-2 text-left hover:bg-accent" onClick={() => onOpen(entry.path)}>
        <span className="block text-sm">{entry.name}</span>
        <span className="block truncate text-xs text-muted-foreground">{entry.detail || entry.path}</span>
      </button>
    </li>)}</ul>
  </section>)}</>;
}
