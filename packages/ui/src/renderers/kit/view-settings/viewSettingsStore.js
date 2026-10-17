import { normalizeViewSettings, resetViewMode } from "@text-to-cad/core/common/viewSettings.js";
import { resolveViewSceneSettings } from "@text-to-cad/core/common/sceneSettings.js";
import { mergeViewerDisplaySettings } from "./viewerDisplaySettings.js";
import { shareSettingsValue } from "./shareSettingsValue.js";

// One per mounted file. UI actions, agent commands and session restoration all
// write here. Resolved defaults are outputs, never written back as new edits.
// Every mode keeps its own settings: choosing a mode shows them, editing one mode's
// (the one on screen or another) leaves the rest alone, and Reset takes one mode's away. A mode
// configured as `presentMode` is drawn instead of the file's, which stays its own.
export function createViewSettingsStore(initial = {}, defaults = {}) {
  let context = defaults;
  let snapshot;
  const listeners = new Set();
  function commit(settings) {
    const display = normalizeViewSettings(settings);
    // `presentMode`, configured: the mode the scene is drawn in whatever the file's is (preview's
    // Render), the file's own mode and settings left as they are.
    const { presentMode = null, ...defaults } = context;
    const next = shareSettingsValue(snapshot, {
      display,
      scene: resolveViewSceneSettings({ ...defaults, display: presentMode ? { ...display, mode: presentMode } : display })
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
    resetMode: mode => commit(resetViewMode(snapshot.display, mode)),
    /** The scene the store would draw in `mode`, the file's settings as they stand: nothing is committed. */
    sceneFor: mode => {
      const { presentMode: _present, ...defaults } = context;
      return resolveViewSceneSettings({ ...defaults, display: { ...snapshot.display, mode } });
    }
  };
}
