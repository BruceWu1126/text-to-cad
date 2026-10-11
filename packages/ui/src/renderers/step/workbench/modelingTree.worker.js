import { readCadWorkerTicket } from "@text-to-cad/core/client";
import { parseSurf } from '@text-to-cad/core/lib/surf/container.js';
import { buildModelingTree } from './modelingTree.js';

// One requested component, bounded input, and a disposable worker. Never generate CAD.
self.onmessage = async ({ data: { resource } }) => {
  try {
    const bytes = await readCadWorkerTicket(resource);
    // The SURF is here (cadgen extracts it when asked, behind its build pool): the analysis starts.
    self.postMessage({ analyzing: true });
    const { index, floats } = parseSurf(bytes);
    const tree = buildModelingTree(index, floats);
    self.postMessage({ tree, hasFaces: index.faces.length > 0, edgeFaces: Object.fromEntries(index.edges.map(edge => [edge.ord, edge.faceOrds || []])) });
  } catch (error) {
    self.postMessage({ error: error instanceof Error ? error.message : 'Could not infer a modeling tree.' });
  }
};
