import { normalizeViewSettings, resolveViewSettings } from "@text-to-cad/core/common/viewSettings.js";

import { buildStepClipPatch } from "@text-to-cad/core/lib/viewer/clipPlane.js";

const object = value => Boolean(value) && typeof value === "object" && !Array.isArray(value);

// A patch merges into what is there, in the same shape: `mode` picks the mode and keeps every
// mode's settings; the shared groups merge field by field; `modes.<mode>` merges each of its
// groups field by field and replaces a plain value (Solid's `flat`, X-ray's `opacity`).
export function mergeViewerDisplaySettings(current, patch) {
  const normalized = normalizeViewSettings(patch);
  const base = normalizeViewSettings(current);
  const next = { ...base };
  if (Object.hasOwn(patch, "mode")) next.mode = normalized.mode;
  if (normalized.appearance) next.appearance = normalized.appearance;
  if (normalized.camera) next.camera = { ...base.camera, ...normalized.camera };
  if (normalized.exploded) next.exploded = { ...base.exploded, ...normalized.exploded };
  if (normalized.clip) next.clip = buildStepClipPatch(base.clip, normalized.clip);
  if (normalized.modes) {
    const modes = { ...base.modes };
    for (const [mode, settings] of Object.entries(normalized.modes)) {
      const own = { ...modes[mode] };
      for (const [key, value] of Object.entries(settings)) own[key] = object(value) ? { ...own[key], ...value } : value;
      modes[mode] = own;
    }
    next.modes = modes;
  }
  return normalizeViewSettings(next);
}

// A camera command carries both pose and optional View controls. Explicit lens
// or projection edits change the shared camera; a pose-only command leaves it alone. The tools
// view, where a camera command edits them, is never in Render (Preview's alone).
export function viewerDisplaySettingsForCamera(current, camera) {
  const controls = {
    ...(camera?.projection ? { projection: camera.projection } : {}),
    ...(camera?.focalLength != null ? { focalLength: camera.focalLength } : {})
  };
  return Object.keys(controls).length
    ? mergeViewerDisplaySettings(current, { camera: controls })
    : normalizeViewSettings(current);
}

export function cameraForViewSettings(camera, display, options) {
  if (!camera) return null;
  const settings = resolveViewSettings(display, options).camera;
  return { ...camera, projection: settings.projection, focalLength: settings.focalLength };
}

/** Presentation suspends model effects without changing the per-file settings. */
export function previewDisplaySettings(display) {
  return { ...display, clip: { ...display.clip, enabled: false },
    exploded: { ...display.exploded, enabled: false, amount: 0 } };
}
