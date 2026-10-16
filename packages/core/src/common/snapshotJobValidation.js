import { normalizeAnnotation } from "./annotation.js";
import { normalizeMeasurementMarks } from "../lib/viewer/measureDimension.js";
import { normalizeViewSettings, resolveViewSettings } from "./viewSettings.js";

export function validateSnapshotRenderJob(job = {}) {
  if (Object.hasOwn(job, "theme")) {
    throw new Error("Unsupported snapshot field: theme");
  }
  if (Object.hasOwn(job, "render")) {
    throw new Error("Unsupported snapshot field: render; use display.mode 'render'");
  }
  normalizeViewSettings(Object.hasOwn(job, "display") ? job.display : {});
  // The ink drawn over the model (`annotation.js`): refused before anything loads, by what is wrong.
  if (job.annotation != null) normalizeAnnotation(job.annotation);
  // Measure's results drawn over the picture (`measureDimension.js`), refused the same way.
  if (job.measurements != null) normalizeMeasurementMarks(job.measurements);
  for (const camera of [job.camera, ...(Array.isArray(job.outputs) ? job.outputs.map(output => output.camera) : [])]) {
    if (camera && typeof camera === "object") {
      const moved = ["projection", "focalLength"].filter(key => Object.hasOwn(camera, key));
      if (moved.length) throw new Error(`Camera ${moved.join(", ")} moved to display.camera`);
    }
  }
  if (resolveViewSettings(job.display ?? {}).lighting.enabled && String(job.mode || "view").trim().toLowerCase() !== "view") {
    throw new Error("Render display supports only view mode");
  }
}
