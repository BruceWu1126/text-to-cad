import { entryHasMesh } from "@text-to-cad/core/lib/entryAssets.js";
import { fileKey } from "./entryPaths.js";
import { failureAlert, isViewerServiceFailure, noGeometryAlert } from "../../kit/status/loadAlerts.js";

export function buildViewerMeshAlert(entry, hasMeshData, loadError, artifact = null,
  { partial = false, failedParts = [], unmeshedParts = [], unverifiedParts = [] } = {}) {
  const fileRef = fileKey(entry);
  if (!fileRef) {
    return null;
  }


  if (artifact?.status === "failed") {
    const alert = failureAlert(fileRef, artifact.error, artifact.failure, true);
    return hasMeshData && !partial ? {
      ...alert,
      blocking: false,
      message: `${alert.message} The existing model remains visible.`
    } : alert;
  }

  if (loadError && hasMeshData && failedParts.length) {
    return failedPartsAlert(fileRef, failedParts, loadError?.message || loadError);
  }

  if (loadError) {
    const alert = failureAlert(fileRef, loadError?.message || loadError, loadError?.failure);
    return hasMeshData && !partial ? {
      ...alert,
      blocking: false,
      message: `${alert.message} The existing model remains visible.`
    } : alert;
  }

  if (!hasMeshData) {
    return noGeometryAlert(fileRef);
  }

  if (!partial && artifact?.settled === true && artifact.status === "compiled" && !entryHasMesh(entry)) {
    // The model on screen is the previous version, kept while the rewritten file was built, and the
    // build ended with the server calling the file compiled over a catalog row that names no
    // geometry (`artifactEndsLoad`): an update that failed, over a model that survives it.
    return {
      ...noGeometryAlert(fileRef),
      blocking: false,
      summary: "Update failed",
      title: "Couldn’t update the model",
      message: `The latest update of “${fileRef}” produced no visible geometry. You’re still viewing the previous version.`
    };
  }

  if (!partial && (unmeshedParts.length || unverifiedParts.length)) {
    return namedPartsAlert(fileRef, unmeshedParts, unverifiedParts);
  }

  return null;
}

/**
 * Turn a failed build of the file into the same actionable alert used by file loads. A quiet or
 * unreachable feed stays quiet, and so does a build the file has since moved past: neither means
 * the model or its saved file is invalid.
 */
export function buildViewerEditAlert(editingState, hasGeometry = false) {
  const state = String(editingState?.state || "").trim().toLowerCase();
  if (!editingState || state !== "failed" || editingState.superseded === true) {
    return null;
  }

  const usableModelVisible = Boolean(hasGeometry);
  const actualDetail = String(editingState.error || "").trim()
    || "The viewer returned no diagnostic for the failed update.";
  const details = [
    editingState.file || editingState.output
      ? `File: ${editingState.file || editingState.output}`
      : "",
    editingState.revision ? `Revision: ${editingState.revision}` : "",
    "Operation: updating the model",
    actualDetail
  ].filter(Boolean).join("\n");
  const common = {
    severity: "error",
    details,
    reload: true,
    ...(usableModelVisible ? { blocking: false } : {})
  };

  if (isViewerServiceFailure(editingState.failure, actualDetail)) {
    return {
      ...common,
      kind: "service",
      summary: "Viewer service failed",
      title: "Couldn’t prepare the model",
      message: usableModelVisible
        ? "The viewer couldn’t prepare the latest update. You’re still viewing the previous version."
        : "The viewer’s processing service failed.",
      recovery: "Try again. If this continues, check the viewer’s terminal output."
    };
  }

  return {
    ...common,
    summary: usableModelVisible ? "Update failed" : "Open failed",
    title: usableModelVisible ? "Couldn’t update the model" : "Couldn’t open the model",
    message: usableModelVisible
      ? "The latest update couldn’t be loaded. You’re still viewing the previous version."
      : "The model could not be prepared for display.",
    reason: actualDetail,
    recovery: "Check the diagnostic in Details, correct the model, then run it again."
  };
}

function namedList(parts) {
  const quoted = parts.slice(0, 3).map(name => `“${name}”`);
  return parts.length > 3 ? `${quoted.join(", ")} and ${parts.length - 3} more` : quoted.join(", ");
}

// Parts drawn without faces cadgen could not mesh, and parts drawn with faces it could not bring
// onto their exact surface: the model is whole but for those faces, so one warning that names the
// parts, which the person can put away -- the missing faces first where there are both.
function namedPartsAlert(fileRef, unmeshedParts, unverifiedParts) {
  const missing = unmeshedParts.length
    ? `${namedList(unmeshedParts)} ${unmeshedParts.length === 1 ? "has" : "have"} faces that couldn’t be meshed, so they’re missing from the view.`
    : "";
  const off = unverifiedParts.length
    ? `${namedList(unverifiedParts)} ${unverifiedParts.length === 1 ? "has" : "have"} faces that couldn’t be brought onto their exact surface, so they’re drawn slightly off it.`
    : "";
  const summary = unmeshedParts.length
    ? (unmeshedParts.length === 1 ? "A part is missing faces" : `${unmeshedParts.length} parts are missing faces`)
    : (unverifiedParts.length === 1 ? "A part is drawn off its surface" : `${unverifiedParts.length} parts are drawn off their surfaces`);
  const details = unmeshedParts.length && unverifiedParts.length
    ? `Parts missing faces: ${unmeshedParts.join(", ")}\nParts drawn off their surface: ${unverifiedParts.join(", ")}`
    : `Parts: ${[...unmeshedParts, ...unverifiedParts].join(", ")}`;
  return {
    severity: "warning",
    blocking: false,
    summary,
    title: summary,
    message: `${[missing, off].filter(Boolean).join(" ")} Everything else is shown.`,
    details: `File: ${fileRef}\n${details}`,
  };
}

// Parts cadgen could not mesh are missing from a model drawn without them: a warning that names
// them, which the person can put away, never the card of a model that did not load.
function failedPartsAlert(fileRef, parts, error) {
  const quoted = parts.slice(0, 3).map(name => `“${name}”`);
  const named = parts.length > 3 ? `${quoted.join(", ")} and ${parts.length - 3} more` : quoted.join(", ");
  const summary = parts.length === 1 ? "A part couldn’t be shown" : `${parts.length} parts couldn’t be shown`;
  return {
    severity: "warning",
    blocking: false,
    summary,
    title: summary,
    message: `${named} couldn’t be meshed, so ${parts.length === 1 ? "it’s" : "they’re"} missing from the view. Everything else is shown.`,
    details: `File: ${fileRef}\nParts: ${parts.join(", ")}\n${String(error || "").trim()}`,
  };
}
