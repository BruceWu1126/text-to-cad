import type { CadWorkspaceService } from '@text-to-cad/core/client';
import { defineFileRenderer } from '../../file-viewer/registry.js';

export function createScriptRenderer(client: CadWorkspaceService) {
  return defineFileRenderer({
    id: 'script', priority: 120, matches: file => /\.py$/i.test(file.path),
    async prepare({ file, signal }) { return { data: await client.resolveEntry(file.path, { signal }) }; },
    load: () => import('./ScriptRenderer.js'),
  });
}
