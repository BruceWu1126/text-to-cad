// Routine frames only move rigid placements. Settings and other scene changes
// must rebuild the traced scene even when a routine is playing.
export function createPreviewSceneRequests(getRuntime, interactionState, requestRender) {
  const invalidate = change => getRuntime()?.progressivePreview?.invalidate(change);
  return {
    requestRender() {
      invalidate({ sceneChanged: true });
      interactionState.shadowsDirty = true;
      interactionState.sceneChanged = true;
      requestRender();
    },
    requestMotionFrame() {
      invalidate({ transformsChanged: true });
      interactionState.shadowsDirty = true;
      interactionState.sceneChanged = true;
      requestRender();
    },
    requestFrame() {
      invalidate({ sceneChanged: true });
      interactionState.sceneChanged = true;
      requestRender();
    },
    invalidateShadows() {
      invalidate({ sceneChanged: true });
      interactionState.shadowsDirty = true;
    },
  };
}
