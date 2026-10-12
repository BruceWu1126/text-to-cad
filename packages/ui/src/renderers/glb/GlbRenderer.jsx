import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { MousePointer2 } from "lucide-react";
import { clamp, finiteOr } from "@text-to-cad/core/common/numbers.js";
import { buildCadRefToken } from "@text-to-cad/core/lib/cadRefs.js";
import { EDGELESS_VIEW_FEATURES } from "@text-to-cad/core/common/viewSettings.js";
import RendererShell from "../kit/shell/RendererShell.jsx";
import { readFileView } from "../kit/shell/fileView.js";
import { useRendererShell } from "../kit/shell/useRendererShell.js";
import ToolPanel from "../kit/tools/ToolPanel.jsx";
import { useViewSettings } from "../kit/view-settings/useViewSettings.js";
import { useDeclinedSelectReference, useWorkspaceDocument, workspaceLoadAlert } from "../workspace/useWorkspaceDocument.js";
import { useViewerHost } from "../../host/context.js";
import { PointerPick } from "../kit/tools/select/usePointerPick.js";
import { FileSheetStatusText } from "../kit/inspector/FileSheet.js";
import { DEFAULT_POSE_VALUE, NO_PRESET_VALUE, positionValuesAreDefault } from "../kit/inspector/kinematicsControls.jsx";
import { GLB_DECLINED_LIVE_COMMANDS, GLB_TOOL, GLB_TOOL_MODES } from "./tools.js";
import { useGlbAnimation } from "./useGlbAnimation.js";
import { useGlbScene } from "./useGlbScene.js";

const LIVE = Object.freeze({ declined: GLB_DECLINED_LIVE_COMMANDS });
const NO_CHOICE = Object.freeze({ field: null, scale: null, loadScale: null, threshold: null, preset: null, markers: null, mode: null, frame: null, sigma: null,
  motion: null });
// What a series or a motion adds to the file's view, written only once chosen, so a result with none keeps the slice it always had.
const SERIES_KEYS = Object.freeze(["mode", "frame", "sigma", "motion"]);
// Where each control's value is kept in the file's view: the field and the deformation scale as they always were.
const DRIVE_KEYS = Object.freeze({ field: "field", deformation: "scale", load_scale: "loadScale", threshold: "threshold", mode: "mode", frame: "frame", sigma: "sigma",
  motion: "motion" });
const NO_VALUES = Object.freeze({ field: null, scale: null, loadScale: null, threshold: null, mode: null, frame: null, sigma: null, motion: null });
const EMPTY = Object.freeze([]);
// `refs`: what Quick Edit gets when it is not the faces' own (a part's or a joint's parts); `parts`: parts to tint.
const NO_FACES = Object.freeze({ result: null, id: "", faces: EMPTY, refs: EMPTY, parts: EMPTY, softParts: EMPTY, summary: "" });
// Whether motion should be kept small: the app's own setting, or the system's.
const prefersReducedMotion = () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true;
const SELECT_ICON = <MousePointer2 className="size-3" strokeWidth={2} aria-hidden="true" />;
// A result offers Clip besides what any GLB view does: a cut through it shows what is inside (a channel's
// walls, a flow's surfaces past an opening) in the field's colours. It has no edges, so no X-ray.
const FEA_VIEW_FEATURES = Object.freeze({ ...EDGELESS_VIEW_FEATURES, sections: Object.freeze([...EDGELESS_VIEW_FEATURES.sections, "clip"]) });

/** Whether a loaded scene carries an FEA result: a mesh `cadgen fea solve` wrote, with its fields (`feaResult.js` reads it). */
function carriesFeaResult(root) {
  let found = false;
  root?.traverse?.((object) => {
    if (!found && object?.isMesh && object.geometry && object.userData?.generator === "cadgen fea" && Array.isArray(object.userData.fields)) found = true;
  });
  return found;
}

// The FEA result viewer (`feaView.jsx`), once a result has loaded it, for every result after it.
let feaView = null;
/** Loads the FEA result viewer: its own chunk, which a plain GLB never fetches. */
export const loadFeaView = () => import("./feaView.jsx").then((module) => { feaView = module; return module; });

/**
 * The FEA result viewer for a scene that carries a result (`kit`), loaded the first time one does; `pending` while it
 * loads. A plain GLB's `kit` is null, and so is a result's should the chunk fail to load: it is then drawn as a plain GLB.
 */
function useFeaView(root) {
  const wanted = useMemo(() => carriesFeaResult(root), [root]);
  const [kit, setKit] = useState(feaView);
  useEffect(() => {
    if (!wanted || kit) return undefined;
    let live = true;
    loadFeaView().then((module) => { if (live) setKit(module); }, () => { if (live) setKit(false); });
    return () => { live = false; };
  }, [wanted, kit]);
  return { kit: wanted && kit ? kit : null, pending: wanted && kit === null };
}

function GlbSurface({ view, data }) {
  const host = useViewerHost();
  const document = useWorkspaceDocument({ view, data });
  const loaded = useGlbScene({ entry: document.entry, resources: document.client.resources });
  // A result waits for its viewer as the file loading, so it is framed once, as the viewer draws it.
  const { kit: fv, pending } = useFeaView(loaded.scene?.document?.scene);
  const scene = pending ? null : loaded.scene;
  const loadAlert = useMemo(() => workspaceLoadAlert({
    catalogError: document.catalogError, error: loaded.error, modelKey: document.modelKey, hasScene: Boolean(scene)
  }), [document.catalogError, loaded.error, document.modelKey, scene]);

  // An FEA result (cadgen fea solve) carries its fields and true displacement in the file; what is
  // shown of it is chosen in Study's What you see, with the controls the study's view names (`feaControls`;
  // by default the field and the deformation), per tab. That choice is this renderer's one slice of
  // the file's view (`kit/shell/fileView.js`), written against the result's own fields, scale and
  // view: a re-solved result opens at its own defaults.
  const fea = useMemo(() => (fv ? fv.readFeaResult(scene?.document?.scene) : null), [fv, scene]);
  // The box the model is drawn in over its whole series and its routine (`feaBounds.js`), at the deformation and load
  // it opens on, grown on the scene before the shell frames it (Preview frames on it once) and fits the camera's near
  // and far planes to it: a strap stretched at its last load step is never sliced off, nor a piezo tip at its ramp's top.
  // A drop's motion (`fea/fall.js`) draws the part turned the way it falls, on the floor: that is the box the tools view
  // frames, and Preview frames the whole fall from its height (`previewBounds`), as a kinematics pose leaves the
  // camera on the model at rest while a routine moves it.
  useMemo(() => {
    if (!fea) return;
    const opening = fv.feaControls(fea);
    const scale = opening.find((control) => control.drives === "deformation")?.defaultValue ?? fea.deformationScale;
    const load = fv.feaAnalysis(fea).scalesWithLoad ? opening.find((control) => control.drives === "load_scale")?.defaultValue ?? 1 : 1;
    const posing = fv.feaMotion(fea);
    const landed = posing?.kind === "fall" ? fv.feaFallBounds(THREE, scene, fea, { scale, height: false }) : null;
    const grown = landed || fv.feaDrawnBounds(THREE, scene, fea, { scale, load });
    if (grown) Object.assign(scene, { bounds: grown, restBounds: grown });
    if (landed) scene.previewBounds = fv.feaFallBounds(THREE, scene, fea, { scale });
    // A pose motion (`fea/poses.js`: a shake, a load cycle, a bolt tightened) is drawn moving over its whole timeline.
    const posed = posing?.poseAt ? fv.feaPoseBounds(THREE, scene, fea, posing, { scale }) : null;
    if (posed) Object.assign(scene, { bounds: posed, restBounds: posed });
  }, [scene, fea]);
  const analysis = fea ? fv.feaAnalysis(fea) : null;
  const controls = useMemo(() => (fea ? fv.feaControls(fea) : EMPTY), [fea]);
  const presets = useMemo(() => (fea ? fv.feaPresets(fea, controls) : EMPTY), [fea, controls]);
  const markerShow = useMemo(() => (fea ? fv.feaMarkerShow(fea) : null), [fea]);
  const signature = fea ? `${fea.fields.map((entry) => entry.attribute).join(",")}:${fea.deformationScale}${fea.view ? `:${fv.viewKey(fea.view)}` : ""}` : "";
  const [stored] = useState(() => view.state);
  const restored = useMemo(() => (fea ? { ...NO_CHOICE, ...readFileView(stored, { fea: signature }).renderer.fea } : NO_CHOICE), [fea, stored, signature]);
  const [edited, setEdited] = useState({ signature: "", ...NO_CHOICE });
  const choice = edited.signature === signature ? { ...restored, ...edited } : restored;
  // Every control's value, held to its own range and options whatever was written.
  const values = Object.fromEntries(controls.map((control) => [control.id, fv.controlValue(control, choice[DRIVE_KEYS[control.drives]])]));
  // The controls shown `when` the checks say; a hidden one acts at its default and keeps its value for when it shows again.
  const { shown: shownControls, effective, loadScale } = fea ? fv.feaShownControls(fea, controls, values) : { shown: EMPTY, effective: values, loadScale: 1 };
  const activeField = fea ? fea.fields.find((entry) => entry.attribute === effective.field) || fea.fields[0] : null;
  const activeScale = fea ? finiteOr(effective.deformation, fea.deformationScale) : null;
  // The frame of a series shown: the mode picker's or the scrubber's, else where a chosen check
  // jumped to, else the series' own default; and the sigma level a random vibration's RMS fields show at.
  const seriesControl = controls.find((control) => control.drives === "mode" || control.drives === "frame") || null;
  const frameIndex = fea ? fv.activeFrameIndex(fea, seriesControl ? effective : { mode: choice.mode }) : 0;
  const sigma = fea && effective.sigma !== undefined && effective.sigma !== null ? Number(effective.sigma) : null;
  // The moment of a motion the Time row shows (`fea/motion.js`), where it has one.
  // While the Time row's own play button plays it in place (`inPlace`), the moment it has reached.
  const [inPlace, setInPlace] = useState(null);
  const inPlaceRef = useRef(null);
  inPlaceRef.current = inPlace;
  const hasMotionRow = controls.some((control) => control.drives === "motion");
  const motionAt = hasMotionRow ? (inPlace ? inPlace.at : Number(effective.motion)) : null;
  // The load shown moves the colours and the deformation only where the analysis follows the load.
  const fieldLoad = analysis?.scalesWithLoad ? loadScale : 1;
  // The load as a multiple of the solved one (a linear study scales exactly), and what is drawn grey under a threshold.
  const thresholdControl = shownControls.find((control) => control.drives === "threshold");
  const thresholdField = fea && thresholdControl ? fea.fields.find((entry) => entry.attribute === thresholdControl.field) : null;
  const thresholdValue = thresholdField ? effective.threshold : null;
  const markersOn = Boolean(markerShow) && (typeof choice.markers === "boolean" ? choice.markers : markerShow.on);
  const defaults = useMemo(() => (fv ? fv.feaDefaults(controls) : {}), [fv, controls]);
  const activePreset = presets.some((preset) => preset.value === choice.preset) ? choice.preset
    : positionValuesAreDefault(values, defaults) ? DEFAULT_POSE_VALUE : NO_PRESET_VALUE;
  const choiceRef = useRef(choice);
  choiceRef.current = choice;
  const rendererState = useMemo(() => (fea
    ? { signatures: { fea: signature }, read: () => ({ fea: Object.fromEntries(Object.keys(NO_CHOICE)
      .filter((key) => !SERIES_KEYS.includes(key) || (choiceRef.current[key] !== null && choiceRef.current[key] !== undefined)).map((key) => [key, choiceRef.current[key]])) }) }
    : null), [fea, signature]);

  // What the result's checks found stays in the file for the agent; the viewer raises no alert for it
  // (Study's verdict is its status), so only a failure to load has the alert card.
  // What Study chose (a fixed face, a load, a load's face) or a press on the result picked: its faces
  // and what a prompt calls them.
  const [chosenFaces, setChosenFaces] = useState(NO_FACES);
  const clearChoice = useCallback(() => setChosenFaces(NO_FACES), []);
  const faces = fea && chosenFaces.result === fea ? chosenFaces : NO_FACES;
  // Study's setup and Details, and the parts of Study the view lists, in its order.
  const sections = useMemo(() => (fea ? fv.studySections(fea) : { setup: EMPTY, details: EMPTY }), [fea]);
  const rows = useMemo(() => [...sections.setup, ...sections.details], [sections]);
  const order = useMemo(() => (fea ? fv.feaSections(fea) : EMPTY), [fea]);
  // An assembly's parts, each with its joints, are a panel of their own above Study, from six parts
  // up unless the view says (`feaShowsParts`); under that a picked face's Reference names its part.
  const parts = useMemo(() => (fea && fv.feaShowsParts(fea) ? fv.partRows(fea, loadScale) : EMPTY), [fea, loadScale]);
  // The answer at a glance, at the load shown: it follows the load control, and may change its word.
  const verdict = useMemo(() => (fea ? fv.feaVerdict(fea, loadScale) : null), [fea, loadScale]);
  // A check whose value occurs at a frame of the series shows that frame when chosen: the mode picker
  // or the scrubber moves there, and with neither the view still jumps to it.
  const jumpRef = useRef(null);
  const chooseFaces = useCallback((row) => {
    setChosenFaces({ result: fea, id: row.id, faces: row.faces || EMPTY, refs: row.refs || EMPTY, parts: row.parts || EMPTY, softParts: row.softParts || EMPTY, summary: row.summary });
    if (Number.isInteger(row.frame)) jumpRef.current?.(row.frame);
  }, [fea]);
  // A press on the result picks the face under it, called what Study calls it: never one Clip has cut away.
  const clipRef = useRef(null);
  const pickScene = useMemo(() => (fea ? { pick: (ray) => fv.pickFace(fea, ray, clipRef.current?.plane() || null) } : null), [fea]);
  const pick = useCallback((hit) => {
    if (!hit) { clearChoice(); return; }
    const row = fv.faceRow(rows, hit.ref);
    chooseFaces({ id: row?.id || hit.ref, faces: [hit.ref], summary: fv.facePromptSummary(fea, hit.ref) });
  }, [fea, rows, chooseFaces, clearChoice]);
  // The part the result was solved from, the file a choice's references name.
  const source = useMemo(() => (fea ? fv.resultSourcePath(view.file.path, fea.document) : ""), [fea, view.file.path]);
  const references = useMemo(() => {
    if (!fea) return null;
    if (!source) return [];
    const reference = (selectors, summary) => (selectors.length ? [{ resource: { kind: "workspace-file", path: source },
      target: { kind: "cad-selector", selectors }, ...(summary ? { summary } : {}) }] : []);
    if (faces.refs.length) return reference(faces.refs.map(fv.findingSelector), faces.summary);
    return reference(faces.faces.map(fv.findingSelector), faces.summary);
  }, [fea, source, faces]);

  const requestRenderRef = useMemo(() => ({ current: null }), []);
  // A result that moves as a whole (`fea/motion.js`; a drop's fall, `fea/fall.js`, drawn by `feaFall.js`): the Time
  // row's value poses it, and Preview's playbar plays the same timeline (with reduced motion, only its `play` span).
  const reducedMotion = host.environment?.reducedMotion === true || prefersReducedMotion();
  const motion = useMemo(() => (fea ? fv.feaMotion(fea) : null), [fea]);
  const fall = useMemo(() => {
    const frame = motion?.kind === "fall" ? fv.fallFrame(THREE, fea) : null;
    return frame ? { frame, plan: motion.plan } : null;
  }, [fea, motion]);
  const fallShownRef = useRef(null);
  const syncBoundsRef = useRef(null);
  // A flow's tracers (`fea/poses.js`, drawn by `feaTracers.js`): its routine streams them along the solved flow.
  const tracers = useMemo(() => (fea && fv.feaAnalysis(fea).routine(fea)?.kind === "tracers" ? fv.readTracers(fea) : null), [fea]);
  const tracersShownRef = useRef(null);
  const clockRef = useRef(null);
  // The routine playing, `{ kind, value }`, or null while none plays: a Load ramp's share of the load
  // put on (0 to 1), Vibrate's phase (radians), Play's share of the way through the frames (0 to 1);
  // and the one pass that draws the colours, the deformation and the markers for what is shown.
  const routineRef = useRef(null);
  const paintRef = useRef(null);
  // A result's own routine, its analysis's (`routine`): the static family's Load ramp (the load going
  // on, from none to the load chosen, colours, deformation and markers together; buckling's Buckle),
  // modal's and harmonic's Vibrate (a loop of the shape swinging, a harmonic frame through its phase),
  // a series' Play (its frames in turn, each blended into the next); none for one with nothing to play.
  const ownClips = useMemo(() => {
    const routine = fea ? fv.feaAnalysis(fea).routine(fea) : null;
    // A motion's routine plays its timeline (`motionSpan`): its value is the moment on it, as the Time row's is.
    const span = routine?.kind === "motion" && motion ? fv.motionSpan(motion, reducedMotion) : null;
    // A routine that says how long it is (a flow's tracers' longest trip) plays that long.
    const duration = span ? span.duration : routine ? routine.duration || fv.ROUTINE_SECONDS[routine.kind] || 0 : 0;
    if (!duration) return EMPTY;
    const swings = routine.kind === "vibrate" || routine.kind === "pulse";
    const valueAt = (elapsedSec) => (span ? span.from + clamp(elapsedSec, 0, duration) : routine.kind === "tracers" ? elapsedSec : swings ? (2 * Math.PI * elapsedSec) / duration : clamp(elapsedSec / duration, 0, 1));
    return [{
      id: routine.id, label: routine.label, duration,
      play: {
        apply(elapsedSec) { routineRef.current = { kind: routine.kind, value: valueAt(elapsedSec) }; paintRef.current?.(); },
        release() { routineRef.current = null; paintRef.current?.(); },
      },
    }];
  }, [fea, motion, reducedMotion]);
  const animation = useGlbAnimation(scene?.document || null, () => requestRenderRef.current?.(), ownClips);
  const shellRef = useRef(null);
  const choose = useCallback((patch) => {
    setEdited({ ...choiceRef.current, ...patch, signature });
    shellRef.current?.scheduleStateSave();
  }, [signature]);
  jumpRef.current = (index) => {
    if (!fea?.series || index < 0 || index >= fea.series.frames.length) return;
    // A motion's Time row scrubs its frames too: it moves to the frame's moment.
    const at = motion?.atFrame?.(index);
    if (Number.isFinite(at)) { choose({ motion: at, preset: null }); return; }
    choose(seriesControl?.drives === "frame" ? { frame: seriesControl.snaps[index], preset: null } : { mode: String(index), preset: null });
  };
  // The markers built for the result (below), whose kinds Display's gate names.
  const [markers, setMarkers] = useState(null);
  const chosenSomething = faces.faces.length > 0 || faces.refs.length > 0;
  // The file's Display settings, held here as well as handed to the shell: Clip's panel edits them.
  const displaySettings = useViewSettings(view.appearance?.colorScheme === "dark" ? "dark" : "light");
  const shell = useRendererShell({
    view, services: document.services, resource: document.resource, modelKey: document.modelKey, revisionKey: loaded.revision,
    features: fea ? FEA_VIEW_FEATURES : EDGELESS_VIEW_FEATURES, toolModes: GLB_TOOL_MODES, previewable: true, scene, viewSettings: displaySettings,
    load: { busy: (loaded.busy || pending) && !scene, updating: loaded.busy && Boolean(scene), progress: loaded.progress, alert: loadAlert },
    animation, live: LIVE, rendererState, displaySections: markerShow ? [{
      id: "fea-markers", title: analysis.displayTitle, enabled: markersOn, onEnabledChange: (on) => choose({ markers: on }),
      content: <FileSheetStatusText>{fv.markerGateText(markers?.kinds?.length ? markers.kinds : analysis.markers.filter((kind) => kind !== "body_load"))}</FileSheetStatusText>,
    }] : null,
    // Escape lets go of what is chosen, which a result with no source to edit has no Quick Edit to clear.
    escape: { active: chosenSomething, handle: () => { if (!chosenSomething) return false; clearChoice(); return true; } }
  });
  shellRef.current = shell;
  requestRenderRef.current = shell.requestRender;
  useDeclinedSelectReference(document);

  // Clip: the view's cut through the result (`feaClip.js`), as the viewport has applied it; none in
  // preview, which suspends the tool effects. It cuts the box the result is drawn in over its series.
  useEffect(() => {
    if (!fea || !scene) return undefined;
    const clip = fv.createFeaClip(THREE, fea.mesh, scene.object3D);
    clipRef.current = clip;
    return () => { clipRef.current = null; clip.dispose(); requestRenderRef.current?.(); };
  }, [fea, scene, requestRenderRef]);
  // A flow keeps its cut in preview, so the tracers stream inside the part it opens.
  const cut = shell.previewing && !tracers ? null : shell.frame.resolvedScene?.view?.clip || null;
  const clipBounds = scene?.restBounds || scene?.bounds || null;
  useEffect(() => { if (clipRef.current?.set(cut, clipBounds)) requestRenderRef.current?.(); }, [fea, cut, clipBounds, requestRenderRef]);
  // Its panel is a kept effect's, as a STEP's is: up from a press on Clip until its X, while the view cuts,
  // and through a drag that passes its neutral end, until the pointer lets go. Clip takes no pointer: Select
  // stays the tool, so Study (and a pick) stays reachable over its panel while a cut is applied.
  const shownView = displaySettings.scene.view;
  const [clipOpen, setClipOpen] = useState(false);
  const [clipHeld, setClipHeld] = useState(false);
  useEffect(() => {
    if (!clipHeld) return undefined;
    const release = () => setClipHeld(false);
    window.addEventListener("pointerup", release, true);
    window.addEventListener("pointercancel", release, true);
    return () => { window.removeEventListener("pointerup", release, true); window.removeEventListener("pointercancel", release, true); };
  }, [clipHeld]);
  const clipShown = Boolean(fea) && !shell.previewing && (clipOpen || clipHeld || fv.clipApplied(shownView));
  const removeClip = () => {
    displaySettings.store.patch({ clip: fv.NEUTRAL_CLIP });
    setClipOpen(false);
  };

  // The colours and the drawn displacement follow the choice, in place on the loaded geometry, with
  // the faces (or a part's triangles) chosen in Study, or picked, tinted over the field. At a load
  // other than the solved one, the values and the ramp's range are that load's and so is the
  // displacement drawn; while the Load ramp plays, the values and the displacement climb to it
  // under the range of the load chosen. The markers stand on the positions drawn.
  const tinted = useMemo(() => (fea ? fv.faceIndices(fea, faces.faces) : EMPTY), [fea, faces]);
  const markersRef = useRef(null);
  // A series shows the frame chosen: its fields' colours from that frame's attributes and the model
  // deformed by its displacement or mode shape; Play blends each frame into the next, and Vibrate
  // swings the shape (a harmonic frame turning through its phase, re·cos − im·sin).
  // A drop's motion, drawn at a moment of it (the Time row's, or the routine's as it plays): the colours and the
  // deformation of that moment (an impact's frames by their real time; an estimate's load ramping to its peak and back
  // while it stops), and the mesh turned the way it falls, lifted and carried as the fall says, never through the
  // floor, which is drawn beside it. Off the floor (falling, hopping) the markers and their labels are put away; while
  // the routine plays, the readout says the real time and the slow motion.
  const paintFall = (at, playing) => {
    const { frame, plan } = fall;
    const mesh = fea.mesh;
    const state = fv.fallAt(plan, frame.fall, at);
    let shown = fallShownRef.current;
    if (!shown) {
      mesh.updateMatrix();
      const floor = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), fv.floorPlaneMaterial(THREE));
      floor.name = "fea-drop-floor";
      floor.renderOrder = 9;
      floor.raycast = () => {};
      floor.matrixAutoUpdate = false;
      mesh.parent?.add(floor);
      shown = { mesh, rest: mesh.matrix.clone(), floor, local: new THREE.Matrix4(), placed: false, off: false };
      fallShownRef.current = shown;
    }
    const frames = fea.series?.frames || [];
    const level = fv.sigmaScale(activeField, sigma);
    let index = 0;
    let blend = null;
    let centre = null;
    let share = 1;
    if (frames.length) {
      // Before it lands, the first frame (first contact, at rest); after, the last; between, the frames by their times.
      const between = state.phase === "impact" ? fv.framesAtTime(fea.series, state.sinceContactS)
        : state.phase === "hold" || state.phase === "fall" ? { from: 0, to: 0, weight: 0 } : { from: frames.length - 1, to: frames.length - 1, weight: 0 };
      index = between.from;
      blend = between.weight > 0 ? { to: between.to, weight: between.weight } : null;
      const centres = frame.centres;
      if (centres?.[between.from]) centre = centres[between.from].clone().lerp(centres[between.to] || centres[between.from], between.weight);
    } else {
      // An estimate's stop: the load goes on and comes off again as a half-sine over the impact.
      share = state.phase === "impact" ? Math.sin(Math.PI * state.share) : 0;
    }
    const field = fv.fieldAtFrame(fea, activeField, index);
    const mixed = blend ? { field: fv.fieldAtFrame(fea, activeField, blend.to), weight: blend.weight } : null;
    const threshold = thresholdField ? { field: fv.fieldAtFrame(fea, thresholdField, index), value: thresholdValue, scale: share * fieldLoad } : null;
    fv.recolorByField(mesh, field, fea.ramp, tinted, faces.parts, faces.softParts,
      { valueScale: share * fieldLoad * level, rangeScale: fieldLoad * level, threshold, ...(mixed ? { blend: mixed } : {}) });
    const { attribute } = fv.deformationAt(fea, index);
    const next = blend ? fv.deformationAt(fea, blend.to).attribute || attribute : attribute;
    const vector = blend && next !== attribute ? [[attribute, 1 - blend.weight], [next, blend.weight]] : attribute;
    const positions = mesh.geometry.getAttribute("position").array;
    if (vector && fv.applyDeformation(mesh, activeScale * share * (frames.length ? 1 : fieldLoad), fea.deformationScale, vector)) {
      markersRef.current?.update(positions);
    }
    // Never through the floor; on it once it has come to rest (and as it hops off it).
    const past = fv.pastFloor(positions, frame, centre);
    const settled = state.phase === "rest" || state.phase === "hop";
    const lift = state.lift / 1000 + (settled ? past : Math.max(0, past));
    mesh.matrixAutoUpdate = false;
    mesh.matrix.copy(shown.rest).multiply(fv.fallMatrix(THREE, frame, { lift, centre }, shown.local));
    mesh.matrixWorldNeedsUpdate = true;
    if (!shown.placed) {
      // The floor: level through the point it meets first, under the part, as wide as the markers' rigid floor.
      mesh.geometry.computeBoundingSphere();
      const sphere = mesh.geometry.boundingSphere;
      const size = fv.PLANE_SIZE * Math.max((sphere?.radius || 0) * 2, 1e-3);
      shown.floor.geometry.dispose();
      shown.floor.geometry = new THREE.PlaneGeometry(size, size).rotateX(-Math.PI / 2);
      const middle = (sphere?.center || new THREE.Vector3()).clone().applyMatrix4(fv.fallMatrix(THREE, frame, {}));
      shown.floor.matrix.copy(shown.rest).multiply(new THREE.Matrix4().makeTranslation(middle.x, frame.pivot.y, middle.z));
      shown.floor.matrixWorldNeedsUpdate = true;
      shown.placed = true;
    }
    // Off the floor the markers (where the study loads it, its own rigid floor) would ride up with it: put away.
    shown.off = state.lift > 0;
    // Off the floor, or playing, the scene's bounds are the whole fall's: the camera's near and far planes and the
    // stage follow `bounds`, and the part up at its height would be cut off by a near plane fitted to it on the floor.
    const wanted = (shown.off || playing) && scene.previewBounds ? scene.previewBounds : scene.restBounds;
    if (wanted && scene.bounds !== wanted) {
      shown.scene = scene;
      shown.landed = scene.restBounds;
      scene.bounds = wanted;
      syncBoundsRef.current?.();
    }
    const markers = markersRef.current;
    if (markers) markers.object3D.visible = !shown.off;
    // An impact's markers draw its rigid floor already, on the floor while it is.
    shown.floor.visible = shown.off || !(markers && markersOn && markerShow?.fixtures && markers.kinds.includes("rigid_plane"));
    shown.floor.material.color.set(colours.fixture);
    clockRef.current?.(playing ? fv.fallWords(state, frame.fall) : null);
    requestRenderRef.current?.();
  };
  // Puts back what the motion changed (a result whose Time row is gone): the mesh where it was, the floor gone, the markers, the readout.
  const endFall = () => {
    const shown = fallShownRef.current;
    if (!shown) return;
    fallShownRef.current = null;
    const mesh = shown.mesh;
    mesh.matrix.copy(shown.rest);
    mesh.matrixAutoUpdate = true;
    mesh.matrixWorldNeedsUpdate = true;
    shown.floor.removeFromParent();
    shown.floor.geometry.dispose();
    shown.floor.material.dispose();
    if (markersRef.current) markersRef.current.object3D.visible = true;
    if (shown.scene && shown.scene.bounds !== shown.landed) { shown.scene.bounds = shown.landed; syncBoundsRef.current?.(); }
    clockRef.current?.(null);
    requestRenderRef.current?.();
  };
  const endFallRef = useRef(endFall);
  endFallRef.current = endFall;
  useEffect(() => () => endFallRef.current(), [fea]);
  // A pose motion (`fea/poses.js`), drawn at a moment of it (the row's, or the routine's as it plays): its frame, its
  // colours (scaled with its load, or its own values at that moment: an AC phase, a temperature ramped), its
  // displacement and the markers on it; while it plays the readout says the real time, how much it is slowed and,
  // where it was drawn larger to be seen, by how much.
  const paintPose = (at, playing) => {
    const pose = motion.poseAt(Number.isFinite(at) ? at : motion.defaultAt, { field: activeField }) || {};
    const index = Number.isInteger(pose.frame) ? pose.frame : frameIndex;
    const field = fv.fieldAtFrame(fea, activeField, index);
    const blend = pose.blend?.weight > 0 ? { field: fv.fieldAtFrame(fea, activeField, pose.blend.to), weight: pose.blend.weight } : null;
    const level = fv.sigmaScale(activeField, sigma);
    const share = Number.isFinite(pose.valueScale) ? pose.valueScale : 1;
    const threshold = thresholdField ? { field: fv.fieldAtFrame(fea, thresholdField, index), value: thresholdValue, scale: fieldLoad * share } : null;
    let changed = fv.recolorByField(fea.mesh, field, fea.ramp, tinted, faces.parts, faces.softParts,
      { valueScale: fieldLoad * level * share, rangeScale: fieldLoad * level, threshold, ...(blend ? { blend } : {}),
        ...(pose.values ? { values: pose.values, stamp: pose.stamp || "" } : {}) });
    let vector = pose.vector || null;
    if (!vector) {
      const { attribute } = fv.deformationAt(fea, index);
      const next = blend ? fv.deformationAt(fea, pose.blend.to).attribute || attribute : attribute;
      vector = attribute && blend && next !== attribute ? [[attribute, 1 - blend.weight], [next, blend.weight]] : attribute;
    }
    const { scale, raised } = fv.poseScale(fea.mesh, pose, activeScale);
    if (vector && fv.applyDeformation(fea.mesh, scale * fieldLoad, fea.deformationScale, vector)) {
      markersRef.current?.update(fea.mesh.geometry.getAttribute("position").array);
      changed = true;
    }
    const larger = raised ? `drawn ${Math.round(raised * fieldLoad).toLocaleString("en-US")}× larger` : "";
    clockRef.current?.(playing && pose.words ? { time: pose.words.time, scale: [pose.words.scale, larger].filter(Boolean).join(" · ") } : null);
    if (changed) requestRenderRef.current?.();
  };
  // A flow's tracers while its routine plays: built on the first moment, moved every frame, put away when it stops.
  const paintTracers = (routine) => {
    let shown = tracersShownRef.current;
    if (routine?.kind !== "tracers" || !tracers) {
      if (shown) { tracersShownRef.current = null; shown.dispose(); clockRef.current?.(null); requestRenderRef.current?.(); }
      return;
    }
    if (!shown) {
      shown = fv.createFeaTracers(THREE, tracers, fea.mesh);
      tracersShownRef.current = shown;
    }
    shown.style({ colour: colours.load });
    shown.update(routine.value);
    const seconds = routine.value / tracers.slow;
    clockRef.current?.({ time: `${fv.shortTime(seconds)} of flow`, scale: fv.paceWords(tracers.slow) });
    requestRenderRef.current?.();
  };
  const paintTracersRef = useRef(paintTracers);
  paintTracersRef.current = paintTracers;
  useEffect(() => () => paintTracersRef.current(null), [fea]);
  paintRef.current = () => {
    if (!fea) return;
    const routine = routineRef.current;
    paintTracers(routine);
    if (motion?.poseAt) { paintPose(routine?.kind === "motion" ? routine.value : motionAt, routine?.kind === "motion" || Boolean(inPlace)); return; }
    // A motion is always drawn at a moment of it: the routine's as it plays, else the Time row's.
    if (fall) { paintFall(routine?.kind === "motion" ? routine.value : motionAt, routine?.kind === "motion" || Boolean(inPlace)); return; }
    endFall();
    const shownLoad = routine?.kind === "load_ramp" ? routine.value * fieldLoad : fieldLoad;
    const between = routine?.kind === "play" ? fv.framesBetween(fea, routine.value) : null;
    const index = between ? between.from : frameIndex;
    const field = fv.fieldAtFrame(fea, activeField, index);
    const blend = between?.weight > 0 ? { field: fv.fieldAtFrame(fea, activeField, between.to), weight: between.weight } : null;
    const level = fv.sigmaScale(activeField, sigma);
    const threshold = thresholdField ? { field: fv.fieldAtFrame(fea, thresholdField, index), value: thresholdValue, scale: shownLoad } : null;
    // Ring swings the shape's sign (p·cos 2πt) on a ramp symmetric about 0, so a mode's ends visibly trade colours.
    let changed = fv.recolorByField(fea.mesh, routine?.kind === "pulse" ? fv.symmetricRange(field) : field, fea.ramp, tinted, faces.parts, faces.softParts,
      { valueScale: shownLoad * level * (routine?.kind === "pulse" ? Math.cos(routine.value) : 1), rangeScale: fieldLoad * level, threshold,
        ...(blend ? { blend } : {}) });
    const { attribute, imaginary } = fv.deformationAt(fea, index);
    let vector = attribute;
    if (attribute && routine?.kind === "vibrate") {
      vector = imaginary ? [[attribute, Math.cos(routine.value)], [imaginary, -Math.sin(routine.value)]] : [[attribute, Math.sin(routine.value)]];
    } else if (attribute && blend) {
      // Two frames deformed by the same vector (a temperature series, whose displacement is the file's
      // one in every frame) are that vector: Play then steps the colours alone, never rewriting every
      // position and normal and re-placing every marker each tick for a model that does not move.
      const next = fv.deformationAt(fea, between.to).attribute || attribute;
      vector = next === attribute ? attribute : [[attribute, 1 - blend.weight], [next, blend.weight]];
    }
    // Vibrate, and a Load ramp outside the static family (a voltage, a buckle), move the model clearly: at the
    // Exaggerate value, raised where that moves it less than a tenth of its size. The static family's Load ramp is
    // drawn as it always was, and Play's frames as the scrubber shows them.
    const swings = routine?.kind === "vibrate" || (routine?.kind === "load_ramp" && analysis.family !== "static");
    const moving = swings && attribute
      ? fv.routineScale(fea.mesh, [attribute, ...(imaginary ? [imaginary] : []), ...(blend ? [fv.deformationAt(fea, between.to).attribute || attribute] : [])], activeScale)
      : activeScale;
    if (vector && fv.applyDeformation(fea.mesh, moving * shownLoad, fea.deformationScale, vector)) {
      markersRef.current?.update(fea.mesh.geometry.getAttribute("position").array);
      changed = true;
    }
    if (changed) requestRenderRef.current?.();
  };
  useEffect(() => { paintRef.current?.(); }, [fea, activeField, tinted, faces.parts, faces.softParts, activeScale, fieldLoad, thresholdField, thresholdValue, frameIndex, sigma, motionAt]);

  // Where the study loads and holds the part, on the model (`feaMarkers.js`): built once per result,
  // standing on the positions drawn, the chosen load's or fixture's in the chosen colour.
  useEffect(() => {
    if (!fea?.study) return undefined;
    const built = fv.createFeaMarkers(THREE, fea);
    if (!built.sites.length && !built.walls) { built.dispose(); return undefined; }
    fea.mesh.add(built.object3D);
    built.update(fea.mesh.geometry.getAttribute("position").array);
    markersRef.current = built;
    setMarkers(built);
    return () => {
      markersRef.current = null;
      setMarkers(null);
      built.dispose();
      requestRenderRef.current?.();
    };
  }, [fea, requestRenderRef]);
  const colours = fv?.MARKER_COLOURS[view.appearance?.colorScheme === "dark" ? "dark" : "light"] || null;
  useEffect(() => {
    if (!markers) return;
    markers.style({ colours, visible: { loads: markersOn && markerShow.loads, fixtures: markersOn && markerShow.fixtures },
      chosen: (site) => fv.markerChosen(faces, site) });
    requestRenderRef.current?.();
  }, [markers, colours, markersOn, markerShow, faces, requestRenderRef]);
  // A label goes with its marker when Clip cuts away where the load acts (read each frame, as the cut is).
  const loadLabels = useCallback(() => (markers && markersOn && !fallShownRef.current?.off
    ? markers.labels(fieldLoad, { loads: markerShow.loads, fixtures: markerShow.fixtures })
      .filter((label) => !clipRef.current || clipRef.current.keeps(label.face || label.at, fea.mesh)) : EMPTY), [markers, markersOn, markerShow, fieldLoad, fea]);

  // Select is an FEA result's one tool, with Study (and an assembly's Parts) its panels; a GLB that is not a result has none.
  const selectActive = Boolean(fea) && !shell.previewing && shell.toolMode === GLB_TOOL.SELECT;
  const tools = fea ? [shell.tools.own({ id: GLB_TOOL.SELECT, label: "Select", icon: SELECT_ICON, panel: parts.length ? fv.ASSEMBLY_PANELS : fv.STUDY_PANEL }),
    // A press opens its panel neutral (an edit cuts); a press while it is up, as its X, removes the cut.
    shell.tools.own({ id: GLB_TOOL.CLIP, label: "Clip", icon: fv.CLIP_ICON, active: clipShown,
      onSelect: () => {
        if (clipShown) { removeClip(); return; }
        setClipOpen(true);
        displaySettings.store.patch({ clip: fv.NEUTRAL_CLIP });
      } })] : [];
  // One face chosen is the Reference's: its ref, what the study does to it, and Copy (the copy key too).
  const single = faces.faces.length === 1 && !faces.refs.length ? faces.faces[0] : "";
  const copyFace = useCallback(async () => {
    if (!single) return false;
    const selectors = [fv.findingSelector(single)];
    try { await host.clipboard.writeText(source ? buildCadRefToken({ cadPath: source, selectors }) : single); return true; }
    catch (error) { shellRef.current?.reportActionError(error instanceof Error ? error.message : "Could not copy reference"); return false; }
  }, [single, source, host.clipboard]);
  const copySelection = selectActive && single ? copyFace : null;
  // The Time row's play button: the motion's timeline in place, in the tools view, its value following it, as the
  // playbar plays it in preview (with reduced motion only its `play` span); once through, then it stops there.
  const motionPlaying = Boolean(inPlace);
  useEffect(() => {
    if (!motionPlaying || !motion) return undefined;
    const span = fv.motionSpan(motion, reducedMotion);
    const end = span.from + span.duration;
    let previous = null;
    let frameId = 0;
    const tick = (now) => {
      const current = inPlaceRef.current;
      if (!current) return;
      const at = Math.min(current.at + (previous === null ? 0 : Math.max(now - previous, 0) / 1000), end);
      previous = now;
      if (at >= end) { setInPlace(null); choose({ motion: end, preset: null }); return; }
      setInPlace({ at });
      frameId = window.requestAnimationFrame(tick);
    };
    frameId = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frameId);
  }, [motionPlaying, motion, reducedMotion, choose]);
  // It stops where it is when the result, the tool or preview changes.
  useEffect(() => {
    const current = inPlaceRef.current;
    if (!current) return;
    setInPlace(null);
    if (Number.isFinite(current.at)) choose({ motion: current.at, preset: null });
  }, [fea, shell.toolMode, shell.previewing]); // eslint-disable-line react-hooks/exhaustive-deps
  const toggleMotion = () => {
    if (inPlace) { setInPlace(null); choose({ motion: inPlace.at, preset: null }); return; }
    if (!motion) return;
    const span = fv.motionSpan(motion, reducedMotion);
    const end = span.from + span.duration;
    const from = Number.isFinite(motionAt) && motionAt >= span.from && motionAt < end - 1e-6 * Math.max(end, 1) ? motionAt : span.from;
    setInPlace({ at: from });
  };
  // Study's What you see: each control writes its value, and a moved control leaves the preset (Custom).
  const resultControls = {
    controls: shownControls, values: inPlace ? { ...values, motion: inPlace.at } : values, presets, preset: activePreset,
    motionPlay: hasMotionRow && motion ? { playing: motionPlaying, onToggle: toggleMotion } : null,
    // A scrubber snaps to the frame nearest where it is let go; dragging the Time row pauses its play.
    onChange: (id, value) => {
      if (id === "motion" && inPlaceRef.current) setInPlace(null);
      const control = controls.find((entry) => entry.id === id);
      choose({ [DRIVE_KEYS[id]]: control?.snaps ? fv.snapFrame(control, value) : value, preset: null });
    },
    onPreset: (value) => {
      if (inPlaceRef.current) setInPlace(null);
      const preset = presets.find((entry) => entry.value === value);
      if (preset) choose({ ...Object.fromEntries(Object.entries(preset.values).map(([id, entry]) => [DRIVE_KEYS[id], entry])), preset: value });
    },
    onReset: () => choose({ ...NO_VALUES, preset: null }),
  };
  const toolPanels = fea ? <>
    <fv.FeaStudyPanel active={selectActive} parts={parts} openPart={fv.weakestPartIndex(fea)} setup={sections.setup} details={sections.details}
      sections={order} verdict={verdict} chosen={faces.id} onChoose={chooseFaces}
      result={resultControls}
      reference={single ? { title: fv.faceTitle(fea, single), ref: single, role: fv.faceRole(fea, single), part: fv.facePartDetail(fea, single, loadScale) } : null} onClearSelection={clearChoice}
      copy={single ? { label: "Copy", shortcut: shell.frame.copyShortcut, onCopy: copyFace } : null} />
    {clipShown ? <div className="contents" data-fea-clip-panel="" onPointerDownCapture={() => setClipHeld(true)}>
      <ToolPanel id="clip" title="Clip" label="Clip controls" summary={fv.clipSummary(shownView)} fit="fixed" collapsible={false} onClose={removeClip}>
        <div className="space-y-1 pb-1"><fv.ClipControls viewSettings={shownView} onViewSettingsPatch={displaySettings.store.patch} bounds={clipBounds} /></div>
      </ToolPanel>
    </div> : null}
  </> : null;

  const overlay = fea ? (viewport) => <>
    <fv.FeaFraming runtimeRef={viewport.runtimeRef} hostRef={viewport.hostRef} sync={viewport.syncSceneBounds} syncRef={syncBoundsRef} />
    {fall || motion?.poseAt || tracers ? <fv.FeaFallClock clockRef={clockRef} /> : null}
    <PointerPick viewport={viewport} scene={pickScene} enabled={selectActive} onPick={pick} />
    <fv.FeaColourBar result={fea} field={activeField} loadScale={loadScale} sigma={sigma} raised={Boolean(animation) && shell.previewing} />
    {markers ? <fv.FeaLoadLabels runtimeRef={viewport.runtimeRef} hostRef={viewport.hostRef} mesh={fea.mesh} labels={loadLabels}
      colours={{ ink: colours.load, halo: colours.halo }} /> : null}
  </> : null;
  return <RendererShell shell={shell} tools={tools} toolPanels={toolPanels} viewportOverlay={overlay} references={references}
    onClearReferences={clearChoice} copySelection={copySelection} />;
}

export default function GlbRenderer(props) {
  const { data, ...view } = props;
  return <GlbSurface key={JSON.stringify([view.source.id, view.file.path])} view={view} data={data} />;
}
