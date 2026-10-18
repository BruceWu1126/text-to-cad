import { normalizeAnnotation } from "./annotation.js";
import { normalizeMeasurementMarks } from "../lib/viewer/measureDimension.js";
import { normalizeViewSettings } from "./viewSettings.js";

export function validateSnapshotRenderJob(job = {}) {
  const mode = String(job.mode || "view").trim().toLowerCase();
  if (!["view", "list"].includes(mode)) {
    throw new Error(`Unsupported render mode: ${mode}. Modes: list, view`);
  }
  for (const field of ["theme", "section"]) {
    if (Object.hasOwn(job, field)) throw new Error(`Unsupported snapshot field: ${field}`);
  }
  // Render is its own request beside the display, never a display mode: `render: true`.
  if (Object.hasOwn(job, "render") && typeof job.render !== "boolean") throw new Error("render must be a boolean");
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
  if (job.render === true && String(job.mode || "view").trim().toLowerCase() !== "view") {
    throw new Error("Render supports only view mode");
  }
}
