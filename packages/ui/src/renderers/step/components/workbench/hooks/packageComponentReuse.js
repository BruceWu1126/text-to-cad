export function matchingDisplayedPackageContext(displayed, meshState, file) {
  const expectedFile = String(file || "");
  if (!displayed?.complete || !meshState?.assemblyInteractionReady) return null;
  if (String(displayed.file || "") !== expectedFile || String(meshState.file || "") !== expectedFile) {
    return null;
  }
  return String(displayed.meshHash || "") === String(meshState.meshHash || "")
    ? displayed
    : null;
}

// Keep decoded geometry that is still visible alive across an atomic revision
// swap: a component whose part is the one already drawn (its key binds the exact
// geometry and surface producer) keeps its mesh, and the row it was drawn from.
export function retainedComponentMeshesForRevision({
  previous,
  descriptor,
  meshUrl,
} = {}) {
  if (!previous?.descriptor || !previous?.meshUrl || !descriptor || !meshUrl) {
    return {};
  }
  const retained = {};
  for (const [cid, component] of Object.entries(descriptor.components || {})) {
    const row = previous.componentIdentityByCid?.[cid];
    const meshData = previous.componentMeshDataByCid?.[cid];
    if (row?.part && row.part === component?.part && meshData) retained[cid] = meshData;
  }
  return retained;
}
