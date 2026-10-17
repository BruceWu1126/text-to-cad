import { SCENE_QUALITY, resolveSceneQuality } from "@text-to-cad/core/common/sceneSettings.js";
import { previewDisplaySettings } from "../view-settings/viewerDisplaySettings.js";

// How the Render Studio's own view (`kit/shell/StudioView.jsx`) draws the file's Render settings.
// The editor's view draws its scene exactly as the settings resolve it; the Studio is for looking
// at the model: no tool effects (`previewDisplaySettings`), drawn one scene-quality tier up (finer
// tessellation, a higher idle pixel ratio, and at the top tier larger shadow and environment
// maps), and it keeps its full pixel ratio while it moves: an orbit never rests, so the
// interaction drop would be all it ever showed.

const STUDIO_QUALITY_TIER = Object.freeze({
  [SCENE_QUALITY.INTERACTIVE]: SCENE_QUALITY.STANDARD,
  [SCENE_QUALITY.STANDARD]: SCENE_QUALITY.HIGH,
  [SCENE_QUALITY.HIGH]: SCENE_QUALITY.HIGH
});

/** The scene quality the Studio draws a scene resolved at `quality` with: the next tier up, capped at High. */
export function studioSceneQuality(quality) {
  const id = STUDIO_QUALITY_TIER[quality?.id] || SCENE_QUALITY.STANDARD;
  return id === quality?.id ? quality : resolveSceneQuality(id);
}

/** A resolved scene (`resolveViewSceneSettings`'s) as the Studio draws it: never a Display setting changed. */
export function studioViewScene(scene) {
  if (!scene) return scene;
  return { ...scene, display: previewDisplaySettings(scene.display), quality: studioSceneQuality(scene.quality) };
}
