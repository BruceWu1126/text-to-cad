import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MousePointer2 } from "lucide-react";
import { scenePartCount, viewFeaturesForKind } from "@text-to-cad/core/common/viewSettings.js";
import { parseCadRefSelector } from "@text-to-cad/core/lib/cadRefs.js";
import { parseXbfElementId } from "@text-to-cad/core/lib/xbf/xbfScene.js";
import { formatPromptReference } from "@text-to-cad/core/prompt";
import RendererShell from "../kit/shell/RendererShell.jsx";
import { usePreviewState, useRendererShell } from "../kit/shell/useRendererShell.js";
import { usePreviewScene } from "../kit/shell/usePreviewScene.js";
import { noGeometryAlert } from "../kit/status/loadAlerts.js";
import { useSceneToolState, useSceneTools } from "../kit/tools/sceneTools.jsx";
import { usePointerPick } from "../kit/tools/select/usePointerPick.js";
import { useSceneSelection } from "../kit/tools/select/useSceneSelection.js";
import { InfoRow, MonoValue } from "../kit/inspector/referenceRows.jsx";
import { useDeclinedSelectReference, useWorkspaceDocument, workspaceLoadAlert } from "../workspace/useWorkspaceDocument.js";
import { xbfDrivenPairs, xbfPoseValues, xbfSamePose } from "@text-to-cad/core/lib/xbf/xbfKinematics.js";
import XbfElementSection from "./XbfElementSection.jsx";
import XbfPositionSection, { DEFAULT_POSE_VALUE, NO_PRESET_VALUE } from "./XbfPositionSection.jsx";
import XbfTreeSection from "./XbfTreeSection.jsx";
import { XBF_DECLINED_LIVE_COMMANDS, XBF_TOOL, XBF_TOOL_MODES } from "./tools.js";
import { useXbfScene } from "./useXbfScene.js";

const SELECT_ICON = <MousePointer2 className="size-3" strokeWidth={2} aria-hidden="true" />;
const NO_REFERENCES = Object.freeze([]);
const EMPTY = Object.freeze([]);
const KIND_LABEL = Object.freeze({ face: "face", edge: "edge", vertex: "vertex" });
// Two picks are the same thing when they are the same element (or both nothing).
const sameElement = (left, right) => (left?.id || "") === (right?.id || "");

// Toggle for a modified click, replace for a plain one, and a second plain click on the one
// selected element clears it: the rule every selecting view in the viewer follows.
function nextSelection(current, id, multiSelect) {
  if (!id) return EMPTY;
  if (multiSelect) return current.includes(id) ? current.filter(candidate => candidate !== id) : [...current, id];
  return current.length === 1 && current[0] === id ? EMPTY : [id];
}

/**
 * Preselection and selection of a document's faces, edges and vertices (FreeCAD's): the element
 * under a resting pointer is lit and named beside it; a tap picks it. The scene picks by the screen
 * (`pickElement`: a vertex or an edge within a few pixels, else the face), so it is given the camera
 * and the viewport's size with each ray.
 */
function XbfElementPick({ viewport, scene, enabled, onPick, onHover }) {
  const picker = useMemo(() => scene ? {
    pick(ray) {
      const camera = viewport.runtimeRef.current?.camera;
      const rect = viewport.hostRef.current?.getBoundingClientRect?.();
      if (!camera || !rect?.width) return null;
      return scene.pickElement(ray, { camera, width: rect.width, height: rect.height });
    }
  } : null, [scene, viewport.runtimeRef, viewport.hostRef]);
  usePointerPick({ viewport, scene: picker, enabled, onPick, onHover, sameHit: sameElement });
  return null;
}

/** The element the pointer rests on, named beside it: its kind and ordinal, and its element name. */
function PreselectionLabel({ hover }) {
  if (!hover?.hit || !hover.pointer) return null;
  const { hit, pointer } = hover;
  return <div className="pointer-events-none absolute z-20 whitespace-nowrap rounded-md bg-background/95 px-1.5 py-0.5 text-micro text-foreground shadow-sm"
    style={{ left: Math.min(pointer.x + 14, Math.max(pointer.width - 320, 0)), top: pointer.y + 16 }} data-xbf-preselection="">
    <span className="tabular-nums">{`${hit.partName || hit.path} · ${KIND_LABEL[hit.kind]} ${hit.ordinal}`}</span>
    {hit.name ? <span className="ml-1.5 text-muted-foreground">{hit.name}</span> : null}
  </div>;
}

function ElementCard({ element }) {
  return <>
    <InfoRow label="Element"><MonoValue>{`${KIND_LABEL[element.kind]} ${element.ordinal}`}</MonoValue></InfoRow>
    <InfoRow label="Part"><MonoValue>{element.path}</MonoValue></InfoRow>
    {element.edgeClass ? <InfoRow label="Class"><MonoValue>{element.edgeClass}</MonoValue></InfoRow> : null}
  </>;
}

function XbfSurface({ view, data }) {
  const document = useWorkspaceDocument({ view, data });
  const loaded = useXbfScene({ client: document.client, file: view.file.path });
  const scene = loaded.scene;
  const loadAlert = useMemo(() => workspaceLoadAlert({
    catalogError: document.catalogError, error: loaded.error, modelKey: document.modelKey, hasScene: Boolean(scene)
  }) || (loaded.empty ? noGeometryAlert(document.modelKey) : null),
  [document.catalogError, loaded.error, loaded.empty, document.modelKey, scene]);
  // Every mode (the document has its B-rep's edges), Parts where it places several parts, and Clip.
  const features = useMemo(() => viewFeaturesForKind("xbf", { parts: scenePartCount(scene) }), [scene]);

  const shellRef = useRef(null);
  const preview = usePreviewState();
  const previewing = preview.previewing;
  const tools = useSceneToolState({ toolModes: XBF_TOOL_MODES, previewing, fileKey: document.modelKey, revision: loaded.revision,
    measurable: false, resource: document.resource });
  const requestHighlightFrame = useCallback(() => shellRef.current?.requestFrame?.(), []);
  // Parts, as the tree selects and lights them (`i3`).
  const parts = useSceneSelection({ scene, requestRender: requestHighlightFrame });
  // Elements, as the view picks them (`i3/face/12`), and the one the pointer rests on.
  const [chosen, setChosen] = useState(EMPTY);
  const elements = useMemo(() => (scene ? chosen.filter(id => scene.describeElement(id)) : EMPTY), [scene, chosen]);
  const [hover, setHover] = useState(null);
  useEffect(() => {
    scene?.setElementHighlight({ hovered: hover?.hit?.id || null, selected: elements });
    requestHighlightFrame();
  }, [scene, hover, elements, requestHighlightFrame]);
  const clearElements = useCallback(() => setChosen(current => (current.length ? EMPTY : current)), []);
  // Hidden parts: the tree's eyes and isolation.
  const [hidden, setHidden] = useState(EMPTY);
  useEffect(() => { scene?.setHiddenParts(hidden); shellRef.current?.requestRender?.(); }, [scene, hidden]);

  // Position: the mechanism's pose, a configuration or values set by hand, the page's own forward
  // kinematics moving the instances. A new version keeps the pose while its pairs and configurations
  // are what they were; one that changed them starts as built.
  const kinematics = scene?.kinematics || null;
  const kinematicsKey = kinematics ? JSON.stringify([kinematics.pairs, kinematics.configurations, kinematics.asBuilt]) : "";
  const [pose, setPose] = useState({ key: "", preset: DEFAULT_POSE_VALUE, given: null });
  const current = pose.key === kinematicsKey ? pose : { key: kinematicsKey, preset: DEFAULT_POSE_VALUE, given: null };
  const poseValues = useMemo(() => {
    if (!kinematics || kinematics.loop?.length) return null;
    try {
      return xbfPoseValues(kinematics, current.given || (current.preset !== DEFAULT_POSE_VALUE && current.preset !== NO_PRESET_VALUE ? current.preset : null));
    } catch {
      return xbfPoseValues(kinematics);
    }
  }, [kinematics, current.given, current.preset]);
  const asBuilt = useMemo(() => (kinematics && !kinematics.loop?.length ? xbfPoseValues(kinematics) : null), [kinematics]);
  useEffect(() => {
    if (!scene || !poseValues) return;
    scene.setPose(xbfSamePose(poseValues, asBuilt) ? null : poseValues);
    shellRef.current?.requestRender?.();
  }, [scene, poseValues, asBuilt]);
  const posePreset = !poseValues ? DEFAULT_POSE_VALUE : xbfSamePose(poseValues, asBuilt) ? DEFAULT_POSE_VALUE
    : current.preset !== NO_PRESET_VALUE && current.preset !== DEFAULT_POSE_VALUE && !current.given ? current.preset : NO_PRESET_VALUE;
  const setPoseValues = useCallback((values) => {
    const driven = xbfDrivenPairs(kinematics);
    setPose({ key: kinematicsKey, preset: NO_PRESET_VALUE, given: Object.fromEntries(Object.entries(values).filter(([label]) => !driven.has(label))) });
  }, [kinematics, kinematicsKey]);
  const choosePreset = useCallback(name => setPose({ key: kinematicsKey, preset: name, given: null }), [kinematicsKey]);
  const resetPose = useCallback(() => setPose({ key: kinematicsKey, preset: DEFAULT_POSE_VALUE, given: null }), [kinematicsKey]);

  const live = useMemo(() => ({
    declined: XBF_DECLINED_LIVE_COMMANDS,
    commands: { clearSelection: () => { parts.clear(); clearElements(); } },
    state: () => ({ selectedElements: [...chosen], selectedParts: [...parts.selected], hiddenParts: [...hidden] })
  }), [parts, clearElements, chosen, hidden]);
  const escape = useMemo(() => ({
    active: parts.active || elements.length > 0,
    handle: () => {
      if (elements.length) { clearElements(); return true; }
      if (parts.active) { parts.clear(); return true; }
      return false;
    }
  }), [parts, elements.length, clearElements]);

  const shell = useRendererShell({
    view, services: document.services, resource: document.resource, modelKey: document.modelKey, revisionKey: loaded.revision,
    features, toolModes: XBF_TOOL_MODES, tool: tools.tool, previewable: true, preview, scene, sceneEffects: true,
    load: { busy: loaded.busy && !scene, updating: loaded.busy && Boolean(scene), progress: loaded.progress, alert: loadAlert },
    live, escape, measurements: null,
    // A prompt's annotation is in the scene's coordinates: the document's millimetres.
    annotationUnits: scene?.units?.unit || "mm"
  });
  shellRef.current = shell;
  useDeclinedSelectReference(document);

  // The edges the display mode draws (Solid's visible ones, X-ray's all, none in Shaded), as the view
  // resolved them: the viewport hands a scene its surface look, and the edges are this family's own.
  const edges = shell.frame.resolvedScene?.view?.edges || null;
  const edgesKey = JSON.stringify(edges);
  useEffect(() => {
    if (!scene || !edges) return;
    scene.setEdgeLook(edges);
    shell.requestRender();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scene, edgesKey]);
  // Preview's own scene: the editor's sibling, over its geometry; Render draws no edges.
  const previewScene = usePreviewScene(scene, previewing);
  useEffect(() => { previewScene?.setEdgeLook({ enabled: false }); }, [previewScene]);
  const previewView = useMemo(() => ({ scene: previewScene }), [previewScene]);

  const sceneTools = useSceneTools({ shell, scene, features, state: tools, measurable: false,
    clearParts: () => { parts.clear(); clearElements(); } });
  const selectActive = !previewing && Boolean(scene) && shell.toolMode === XBF_TOOL.SELECT;
  const strip = [shell.tools.own({ id: XBF_TOOL.SELECT, label: "Select", icon: SELECT_ICON }), ...sceneTools.tools];
  const handlePick = useCallback((hit, modifiers) => {
    if (!hit) { if (!modifiers?.multiSelect) { clearElements(); parts.clear(); } return; }
    setChosen(current => nextSelection(current, hit.id, Boolean(modifiers?.multiSelect)));
  }, [clearElements, parts]);
  const handleHover = useCallback((hit, pointer) => setHover(hit ? { hit, pointer } : null), []);
  useEffect(() => { if (!selectActive) setHover(null); }, [selectActive]);

  // What is picked, for Quick Edit: each part and element as a chip, and -- where the document's
  // names spell a ref in the shared grammar (`cadRefs.js`: a part's label, `label/f:…`) -- as a
  // reference into the document, by the name it gives it (an element name outlives the ordinals an
  // edit renumbers). A name the grammar does not admit is a chip that copies its description.
  const resource = shell.frame.resource;
  const { references, chips } = useMemo(() => {
    if (!scene || resource?.kind !== "workspace-file") return { references: EMPTY, chips: EMPTY };
    const reference = selector => ({ resource: { ...resource }, target: { kind: "cad-selector", selectors: [selector] } });
    const refOf = (selector) => {
      const parsed = selector ? parseCadRefSelector(selector) : null;
      return parsed && parsed.selectorType !== "opaque" ? reference(parsed.canonical) : null;
    };
    const labelOf = id => String(scene.structure.instances[parseXbfElementId(id).instance]?.path || "").split("/").pop();
    const rows = [];
    for (const id of parts.selected) {
      const part = scene.describePart(id);
      if (!part) continue;
      const value = refOf(labelOf(id));
      rows.push({ value, chip: { key: `part:${id}`, label: part.name, glyph: "solid", id,
        copy: value ? formatPromptReference(value) : scene.structure.instances[parseXbfElementId(id).instance].path, card: null,
        onRemove: () => parts.select(id, { multiSelect: true }), onHover: on => parts.hover(on ? id : "") } });
    }
    for (const id of elements) {
      const element = scene.describeElement(id);
      if (!element) continue;
      const value = element.name ? refOf(`${labelOf(id)}/${element.name}`) : null;
      rows.push({ value, chip: { key: `element:${id}`, label: element.name || `${KIND_LABEL[element.kind]} ${element.ordinal}`,
        glyph: element.kind, id, copy: value ? formatPromptReference(value)
          : `${element.path} ${KIND_LABEL[element.kind]} ${element.ordinal}${element.name ? ` (${element.name})` : ""}`,
        card: <ElementCard element={element} />, onRemove: () => setChosen(current => current.filter(item => item !== id)), onHover: () => {} } });
    }
    return { references: rows.map(row => row.value).filter(Boolean), chips: rows.map(row => row.chip) };
  }, [scene, resource, parts, elements]);

  // The panel reads the last element picked; the facts are the engine's.
  const lastElement = elements.length ? scene?.describeElement(elements.at(-1)) || null : null;
  const sidebarShown = !shell.previewing;
  const update = loaded.update;
  const unmeshed = scene?.unmeshed?.reduce((sum, row) => sum + row.faces.length, 0) || 0;
  const status = scene ? [
    `${scene.structure.prototypes.length} parts, ${scene.partCount} placed, ${scene.triangleCount.toLocaleString("en-US")} triangles`,
    unmeshed ? `${unmeshed} face${unmeshed === 1 ? "" : "s"} not meshed` : "",
    update ? `updated in ${update.ms} ms: ${update.fetched} fetched, ${update.kept} kept` : ""
  ].filter(Boolean).join(" · ") : null;
  const sidebar = <>
    <XbfTreeSection key={document.modelKey} active={sidebarShown} structure={scene?.structure || null} selection={parts}
      hidden={hidden} onHiddenChange={setHidden} status={status} />
    {kinematics ? <XbfPositionSection active={sidebarShown} kinematics={kinematics} values={poseValues || {}} preset={posePreset}
      changed={posePreset !== DEFAULT_POSE_VALUE} onValues={setPoseValues} onPreset={choosePreset} onReset={resetPose} /> : null}
    <XbfElementSection element={lastElement} source={loaded.document?.source?.url || ""} version={loaded.revision} active={sidebarShown} />
  </>;

  return <RendererShell shell={shell} tools={strip} subToolbar={sceneTools.subToolbar} sidebar={sidebar} previewView={previewView}
    references={previewing ? NO_REFERENCES : references} referenceChips={previewing ? NO_REFERENCES : chips}
    onClearReferences={() => { parts.clear(); clearElements(); }}
    onSelectPress={sceneTools.onSelectPress} onSelectHover={sceneTools.onSelectHover}
    viewportOverlay={viewport => <>
      {sceneTools.overlay(viewport)}
      <XbfElementPick viewport={viewport} scene={scene} enabled={selectActive} onPick={handlePick} onHover={handleHover} />
      {selectActive ? <PreselectionLabel hover={hover} /> : null}
    </>} />;
}

export default function XbfRenderer(props) {
  const { data, ...view } = props;
  return <XbfSurface key={JSON.stringify([view.source.id, view.file.path])} view={view} data={data} />;
}
