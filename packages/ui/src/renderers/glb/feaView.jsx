/**
 * The FEA result viewer GlbRenderer loads only for a GLB that carries a result (`cadgen fea solve`): one module, so the
 * bundler makes it a chunk of its own and a plain GLB never fetches it. Everything GlbRenderer uses of the FEA viewer
 * is here, by name.
 */
import { useLayoutEffect } from "react";
import { clamp } from "@text-to-cad/core/common/numbers.js";
import { FEA_PARTS_PANEL_ID, FEA_STUDY_PANEL_ID } from "./FeaStudyPanel.jsx";
import { framingInset } from "./feaChrome.js";
import { ClipIcon } from "../kit/view-settings/ClipControls.jsx";

export { default as FeaStudyPanel } from "./FeaStudyPanel.jsx";
export { default as FeaColourBar } from "./FeaColourBar.jsx";
export { default as FeaLoadLabels } from "./FeaLoadLabels.jsx";
export { default as FeaFallClock } from "./FeaFallClock.jsx";
export { ClipControls, NEUTRAL_CLIP, clipApplied, clipSummary } from "../kit/view-settings/ClipControls.jsx";
export {
  applyDeformation, faceIndices, facePartDetail, faceTitle, facePromptSummary, faceRole, feaControls, feaDefaults, feaMarkerShow, feaMotion, feaPresets, feaSections, feaShownControls,
  feaShowsParts, feaVerdict, findingSelector, pickFace, readFeaResult, recolorByField, resultSourcePath, partRows, routineScale, studySections, symmetricRange,
  weakestPartIndex
} from "./feaResult.js";
export { PLANE_SIZE, createFeaMarkers, floorPlaneMaterial, markerGateText } from "./feaMarkers.js";
export { feaDrawnBounds } from "./feaBounds.js";
export { fallFrame, fallMatrix, feaFallBounds, pastFloor } from "./feaFall.js";
export { fallAt, fallWords, framesAtTime, shortTime } from "./fea/fall.js";
export { motionSpan } from "./fea/motion.js";
export { paceWords, readTracers } from "./fea/poses.js";
export { feaPoseBounds, poseScale } from "./feaPose.js";
export { createFeaTracers } from "./feaTracers.js";
export { createFeaClip } from "./feaClip.js";
export { feaAnalysis } from "./fea/analyses/index.js";
export { activeFrameIndex, deformationAt, fieldAtFrame, framesBetween, sigmaScale, snapFrame } from "./fea/series.js";

// Each routine an analysis plays (`routine`, fea/analyses), and how long one pass of it is: the Load ramp
// (the load going on, from none to all of it; buckling's Buckle), Vibrate (one swing a second) and
// Play (a series' frames, start to end); Ring (a sound mode's pressure swinging through its cycle, once a second).
export const ROUTINE_SECONDS = Object.freeze({ load_ramp: 2, vibrate: 1, play: 3, pulse: 1 });
// What the markers are drawn in. The theme has no colour of its own to spare: every saturated hue is
// the ramp's or the chosen faces' magenta, so a load is the ink and a fixture a muted grey.
export const MARKER_COLOURS = Object.freeze({
  light: Object.freeze({ load: "#18181b", fixture: "#71717a", halo: "rgba(255, 255, 255, 0.9)" }),
  dark: Object.freeze({ load: "#fafafa", fixture: "#a1a1aa", halo: "rgba(18, 19, 21, 0.9)" }),
});
export const CLIP_ICON = <ClipIcon className="size-3" strokeWidth={2} aria-hidden="true" />;
// Select's own panels, which a person can close: Study, and above it an assembly's Parts. A result
// opens with them up, except on a phone.
export const STUDY_PANEL = Object.freeze({ id: FEA_STUDY_PANEL_ID, label: "Study", startsClosed: false });
export const ASSEMBLY_PANELS = Object.freeze([Object.freeze({ id: FEA_PARTS_PANEL_ID, label: "Parts", startsClosed: false }), STUDY_PANEL]);

/** Study's row for a face, where it has one (a fixed face, a loaded face), so a pick of it marks that row. */
export function faceRow(rows, ref) {
  for (const row of rows) {
    const found = row.faces?.length === 1 && row.faces[0] === ref && !row.children ? row : row.children ? faceRow(row.children, ref) : null;
    if (found) return found;
  }
  return null;
}

/** A short, stable name for a view, for its slice's signature. */
export function viewKey(view) {
  const text = JSON.stringify(view);
  let hash = 5381;
  for (let i = 0; i < text.length; i += 1) hash = ((hash * 33) ^ text.charCodeAt(i)) >>> 0;
  return hash.toString(36);
}

/** A control's value: what was chosen, where it is still one of the control's; else its default. */
export function controlValue(control, chosen) {
  if (control.type === "enum") return control.options.some((option) => option.value === chosen) ? chosen : control.defaultValue;
  return Number.isFinite(chosen) ? clamp(chosen, control.min, control.max) : control.defaultValue;
}

// Study's setup rows by the marker kind they stand for: a row `<row kind>:<group>` chooses its entry's markers.
const ROW_MARKERS = Object.freeze({ load: "load", fixed: "fixture", roller: "roller", body: "body_load", shaken: "base_excitation", temperature: "temperature",
  heat: "heat", convection: "convection", drop: "drop", inlet: "inlet", outlet: "outlet", rigid: "rigid_plane", source: "speaker",
  electrode: "electrode", bearings: "bearing" });

/**
 * Whether a marker is of what Study chose: a setup row's markers (a load's arrows on its faces, a
 * fixed row's cones, a roller row's cones on their plates, a heat input's wavy arrows, ...; a body load, an opening or a floor by its
 * row alone, standing on no face), and every marker on a face picked on the result. A part or a joint has none.
 */
export function markerChosen(faces, site) {
  const [kind, group] = String(faces.id).split(":");
  const own = ROW_MARKERS[kind];
  const ofRow = own === site.kind && (group === undefined || Number(group) === site.group);
  if (!site.ref) return ofRow;
  if (!faces.faces.includes(site.ref)) return false;
  if (own) return ofRow;
  return kind !== "joint";
}

/**
 * A result's camera frames the model right of Select's panels (Study, an assembly's Parts) and above the
 * colour bar: the viewport reads what they cover whenever it fits the camera (`runtime.framingInset`, read at
 * the fit, so a panel closed or widened since counts as it is then), and Preview, which puts the panels away,
 * takes the whole width. Installed before the shell's first fit of the result: a child's layout effect runs first.
 */
export function FeaFraming({ runtimeRef, hostRef, sync, syncRef }) {
  useLayoutEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return undefined;
    runtime.framingInset = () => framingInset(hostRef.current);
    return () => { if (runtime.framingInset) delete runtime.framingInset; };
  }, [runtimeRef, hostRef]);
  // How the renderer tells the viewport its scene's bounds moved (a drop lifted off the floor), so the stage and the
  // camera's near and far planes follow it.
  useLayoutEffect(() => {
    if (!syncRef) return undefined;
    syncRef.current = sync || null;
    return () => { syncRef.current = null; };
  }, [sync, syncRef]);
  return null;
}
