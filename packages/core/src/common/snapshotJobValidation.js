import { normalizeViewSettings, resolveViewSettings } from "./viewSettings.js";

export function validateSnapshotRenderJob(job = {}) {
  normalizeViewSettings(Object.hasOwn(job, "display") ? job.display : {});
  if (resolveViewSettings(job.display ?? {}).lighting.enabled && String(job.mode || "view").trim().toLowerCase() !== "view") {
    throw new Error("Render display supports only view mode");
  }
}
