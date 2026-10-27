// A camera-only update must not discard a pose that was queued while the
// worker was preparing or applying a quality change.
export function mergePreviewInput(previous, next) {
  if (!previous) return next;
  return { ...next, frameChanged: Boolean(previous.frameChanged || next.frameChanged),
    transforms: next.transforms || previous.transforms,
    floorMatrix: next.floorMatrix || previous.floorMatrix };
}
