import { shareSettingsValue } from "../view-settings/shareSettingsValue.js";

/** Keep Preview's rendering preference out of the studio's lighting/material identity. */
export function previewStudioConfiguration(configuration, previous = null) {
  if (!configuration) return null;
  const { performance: ignored, ...studio } = configuration;
  return shareSettingsValue(previous, studio);
}

/** Stage only scene changes; quality preferences use the live Preview command lane. */
export function previewStudioScene(scene, previous = null) {
  const { quality: ignored, ...view } = scene.view;
  return shareSettingsValue(previous, { ...scene, view,
    render: { ...scene.render, configuration: previewStudioConfiguration(scene.render.configuration) } });
}
