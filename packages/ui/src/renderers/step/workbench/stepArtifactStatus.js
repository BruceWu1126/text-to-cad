import { entryHasMesh } from "@text-to-cad/core/lib/entryAssets.js";

// The entry's failed STEP artifact record, or null.
//
// These used to take the entry's format and check it was a STEP, because one renderer served
// every format and a DXF - artifact-managed too - would otherwise have been shown a card about
// a STEP artifact. A DXF is its own renderer now and never reaches this module: the STEP
// renderer is only handed STEPs (its `matches`), so there is no format left to ask about.
export function failedStepArtifact(entry) {
  return entry?.artifact?.ok === false ? entry.artifact : null;
}

// A STEP artifact whose metadata is incomplete can still carry a renderable mesh:
// the viewport shows it and the alert drops to a warning instead of an error.
export function stepArtifactHasRenderableGlb(entry) {
  return entryHasMesh(entry);
}

// The alert description for a failed STEP artifact: its error code in words.
export function stepArtifactStatusMessage(artifact) {
  const code = String(artifact?.error || "").trim();
  if (code === "missing_glb") {
    return "Generated GLB is missing.";
  }
  if (code === "missing_step_topology") {
    return "Generated GLB is missing STEP topology metadata.";
  }
  if (code === "missing_selector_topology") {
    return "Generated GLB is missing selector topology metadata.";
  }
  if (code === "missing_edge_topology") {
    return "Generated GLB is missing surface edge topology metadata.";
  }
  if (code === "missing_surface_edge_attributes") {
    return "Generated GLB is missing surface edge render attributes.";
  }
  if (code === "unsupported_step_topology") {
    return "Generated GLB topology metadata is unsupported.";
  }
  if (code === "missing_source_path") {
    return "Generated GLB metadata is missing its source path.";
  }
  if (code === "missing_step_hash") {
    return "Generated GLB is missing the hash of the STEP file.";
  }
  return "Generated STEP artifact is unavailable.";
}
