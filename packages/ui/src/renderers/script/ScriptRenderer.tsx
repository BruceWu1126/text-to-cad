import { useEffect } from 'react';
import type { CadEntry } from '@text-to-cad/core/client';
import type { FileRendererProps } from '../../file-viewer/types.js';

export default function ScriptRenderer({ data, onReady, onOpenFile }: FileRendererProps<CadEntry>) {
  useEffect(() => { onReady(true); return () => onReady(false); }, [onReady]);
  return <main className="mx-auto w-full max-w-2xl p-8">
    <h1 className="mb-2 text-lg">Models in this script</h1>
    <p className="mb-6 text-sm text-muted-foreground">Choose a model to build and open it.</p>
    {data.models?.length ? <ul className="space-y-1">{data.models.map(model => <li key={model.address}>
      <button type="button" className="w-full rounded-md p-3 text-left hover:bg-accent" disabled={!onOpenFile}
        onClick={() => onOpenFile?.(model.address)}>
        <span className="block text-sm">{model.name}</span>
        <span className="block truncate text-xs text-muted-foreground">{model.address}</span>
      </button>
    </li>)}</ul> : <p className="text-sm text-muted-foreground">No model declarations found.</p>}
  </main>;
}
