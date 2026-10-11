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
// swap. D already binds the exact geometry and surface producer. The prior
// runtime ticket supplies O when the new geometry descriptor intentionally
// omits display artifacts; an explicit new O must still agree.
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
    const oldComponent = previous.descriptor.components?.[cid];
    const oldIdentity = previous.componentIdentityByCid?.[cid] || oldComponent;
    const meshData = previous.componentMeshDataByCid?.[cid];
    if (!oldComponent?.surfaceInput || oldComponent.surfaceInput !== component?.surfaceInput
        || !oldIdentity?.surfaceObject || !meshData) continue;
    if (!component.surfaceObject || component.surfaceObject === oldIdentity.surfaceObject) retained[cid] = meshData;
  }
  return retained;
}
