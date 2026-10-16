import { normalizeViewSettings, resetViewMode } from "@text-to-cad/core/common/viewSettings.js";
import { resolveViewSceneSettings } from "@text-to-cad/core/common/sceneSettings.js";
import { mergeViewerDisplaySettings } from "./viewerDisplaySettings.js";
import { shareSettingsValue } from "./shareSettingsValue.js";

// One per mounted file. UI actions, agent commands and session restoration all
// write here. Resolved defaults are outputs, never written back as new edits.
// Every mode keeps its own settings: choosing a mode shows them, editing one mode's
// (the one on screen or another) leaves the rest alone, and Reset takes one mode's away.
export function createViewSettingsStore(initial = {}, defaults = {}) {
  let context = defaults;
  let snapshot;
  const listeners = new Set();
  function commit(settings) {
    const display = normalizeViewSettings(settings);
    const next = shareSettingsValue(snapshot, {
      display,
      scene: resolveViewSceneSettings({ ...context, display })
    });
    if (next === snapshot) return snapshot;
    snapshot = next;
    for (const listener of listeners) listener();
    return snapshot;
  }
  commit(initial);
  return {
    getSnapshot: () => snapshot,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    // Partial: the appearance and the file's capabilities are configured by different owners.
    configure(defaults) { context = { ...context, ...defaults }; return commit(snapshot.display); },
    restore: commit,
    /** Merges a patch in the display's own shape: the mode, what modes share, any mode's settings. */
    patch: patch => commit(mergeViewerDisplaySettings(snapshot.display, patch)),
    selectMode: mode => commit(mergeViewerDisplaySettings(snapshot.display, { mode })),
    patchMode: (mode, patch) => commit(mergeViewerDisplaySettings(snapshot.display, { modes: { [mode]: patch } })),
    resetMode: mode => commit(resetViewMode(snapshot.display, mode))
  };
}
