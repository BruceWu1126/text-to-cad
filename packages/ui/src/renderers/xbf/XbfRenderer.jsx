import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { viewFeaturesForKind } from "@text-to-cad/core/common/viewSettings.js";
import { sceneFramingBounds } from "@text-to-cad/core/lib/viewer/sceneContract.js";
import { boundsInUnits } from "@text-to-cad/core/lib/viewer/sceneUnits.js";
import { fetchXbfPartGroups } from "@text-to-cad/core/lib/xbf/xbfDocument.js";
import { parseXbfElementId } from "@text-to-cad/core/lib/xbf/xbfScene.js";
import { useViewerHost } from "../../host/context.js";
import RendererShell from "../kit/shell/RendererShell.jsx";
import { usePreviewState, useRendererShell } from "../kit/shell/useRendererShell.js";
import { usePreviewScene } from "../kit/shell/usePreviewScene.js";
import { useViewportLoadStatus } from "../kit/shell/useViewportLoadStatus.js";
import { noGeometryAlert } from "../kit/status/loadAlerts.js";
import { useModelTools } from "../kit/tools/model-effects/ModelTools.jsx";
import { useMotionExclusion } from "../kit/tools/model-effects/motionExclusion.js";
import { useMeasureSession } from "../kit/tools/measure/useMeasureSession.js";
import { useQuickEditMeasurements } from "../kit/tools/measure/measurementChips.jsx";
import { measureSelectHandlers } from "../kit/tools/measure/measureSelection.js";
import JointHandleOverlay from "../kit/tools/pose/JointHandleOverlay.jsx";
import { selects } from "../kit/tools/select/selectFilter.js";
import { usePointerPick } from "../kit/tools/select/usePointerPick.js";
import { useDeclinedSelectReference, useWorkspaceDocument, workspaceLoadAlert } from "../workspace/useWorkspaceDocument.js";
import XbfMeasureOverlay from "./XbfMeasure.jsx";
import XbfModelTree from "./XbfModelTree.jsx";
import XbfPositionSection, { useXbfPose, writeXbfPosition, xbfPositionRows } from "./XbfPositionSection.jsx";
import { MeasureIcon, SelectIcon, SelectModeMenu, XBF_SELECT_DEFAULT } from "./XbfSelectionModes.jsx";
import { useXbfElementFacts, useXbfSelectionChips, xbfCopyText } from "./xbfChips.jsx";
import { xbfJointHandles } from "./xbfJointHandles.js";
import { viewportMenuEntries } from "./xbfMenuItems.js";
import { xbfModelMenu, xbfPartMenu, xbfTopologyMenu } from "./xbfMenus.js";
import { NO_CONNECTED_SELECTION, nextSelectionIds, selectGeometryMode, treeShape } from "./xbfSelectionFilter.js";
import { isXbfAssemblyId, presentXbfTree, xbfAssemblyIds, xbfPickFrontier } from "./xbfTree.js";
import { XBF_DECLINED_LIVE_COMMANDS, XBF_TOOL, XBF_TOOL_MODES } from "./tools.js";
import { useXbfScene } from "./useXbfScene.js";

// An engine document in the viewer, exactly as a STEP is: the same tools in the same order
// (Select with its filter, Annotate, Measure, Explode, Clip), the same selection (Parts, Faces and
// Edges as the filter ticks them, a collapsed assembly picked as a unit, a part's faces and edges
// once it is open, Group faces and Group edges), the same file sidebar (Model tree, Position), the
// same menus, the same Quick Edit chips. What differs is underneath: the document is read from
// the engine that serves it, and every face and edge is to hand from the start, so nothing waits
// on topology.

const NO_REFERENCES = Object.freeze([]);
const EMPTY = Object.freeze([]);
const NO_HOVER = Object.freeze({ parts: EMPTY, element: null });
const EXPLODE_OFF = Object.freeze({ exploded: { enabled: false, amount: 0 } });
const isElementId = id => String(id).includes("/");
// How long after a pick its double-click still puts the selection back first.
const DOUBLE_CLICK_MS = 600;

/** The selection after a pick of a part or subassembly, as a STEP's: a plain pick drops the faces and edges. */
function pickNode(current, id, multiSelect) {
  const nodes = current.filter(item => !isElementId(item)), elements = current.filter(isElementId);
  if (!multiSelect && elements.length) return [id];
  const next = nextSelectionIds(nodes, [id], multiSelect);
  return multiSelect ? [...next, ...elements] : next;
}

/** The selection after a pick of faces or edges (one, or a grown group): a plain pick drops the parts. */
function pickElements(current, ids, multiSelect) {
  const nodes = current.filter(item => !isElementId(item)), elements = current.filter(isElementId);
  if (!multiSelect && nodes.length) return [...ids];
  const next = nextSelectionIds(elements, ids, multiSelect);
  return multiSelect ? [...nodes, ...next] : next;
}

/** Every selection id at and under `ids` in the tree: what an isolation keeps in reach. */
function subtreeIds(tree, ids) {
  const found = new Set();
  const visit = node => { if (node.selectionId) found.add(node.selectionId); (node.children || EMPTY).forEach(visit); };
  for (const id of ids) { const node = tree.nodes.get(id); if (node) visit(node); }
  return found;
}

function XbfSurface({ view, data }) {
  const host = useViewerHost();
  const document = useWorkspaceDocument({ view, data });
  const loaded = useXbfScene({ client: document.client, file: view.file.path });
  const scene = loaded.scene;
  const source = loaded.document?.source?.url || "";
  // What every later read of the document's engine is made with: the fetch it was read with.
  const engineFetch = loaded.document?.fetch;
  const loadAlert = useMemo(() => workspaceLoadAlert({
    catalogError: document.catalogError, error: loaded.error, modelKey: document.modelKey, hasScene: Boolean(scene)
  }) || (loaded.empty ? noGeometryAlert(document.modelKey) : null),
  [document.catalogError, loaded.error, loaded.empty, document.modelKey, scene]);
  const tree = useMemo(() => presentXbfTree(scene?.structure || null), [scene?.structure]);
  // The model's two readiness moments, as a STEP's are published (`window.__cadViewerLoad`): its
  // first scene, and the whole of it -- one moment here, every part is in the first scene.
  useViewportLoadStatus({ modelKey: `${document.modelKey}:${loaded.revision}`, hasGeometry: Boolean(scene),
    modelComplete: Boolean(scene) && !loaded.busy });
  // An assembly's view is a STEP assembly's: the Model tree, Parts in the filter, Explode.
  const assembly = (scene?.partCount || 0) > 1;
  const features = useMemo(() => viewFeaturesForKind("xbf", { parts: scene?.partCount || 0 }), [scene]);

  const shellRef = useRef(null);
  const preview = usePreviewState();
  const previewing = preview.previewing;
  const [toolMode, setToolMode] = useState(XBF_TOOL_MODES.defaultMode);
  const tool = useMemo(() => ({ mode: toolMode, set: setToolMode }), [toolMode]);
  const measuring = !previewing && toolMode === XBF_TOOL.MEASURE;
  const measure = useMeasureSession({ fileKey: document.modelKey, revision: loaded.revision, picking: measuring });
  const measurements = useQuickEditMeasurements(measure);
  const requestFrame = useCallback(() => shellRef.current?.requestFrame?.(), []);
  const requestRender = useCallback(() => shellRef.current?.requestRender?.(), []);

  // ---- Select's filter: every kind ticked as the file opens ---------------------------------
  const [selectFilter, setSelectFilter] = useState(XBF_SELECT_DEFAULT);
  const [connected, setConnected] = useState(NO_CONNECTED_SELECTION);
  const geometryFilter = useMemo(() => (assembly ? selectFilter : selectFilter.filter(id => id !== "parts")), [assembly, selectFilter]);
  const mode = selectGeometryMode(geometryFilter);
  const shape = treeShape(mode);

  // ---- the model tree's state, the host's: what is open, hidden, isolated, selected ------------
  const [expandedIds, setExpandedIds] = useState(EMPTY);
  const [hiddenIds, setHiddenIds] = useState(EMPTY);
  const [isolatedIds, setIsolatedIds] = useState(EMPTY);
  const [chosen, setChosen] = useState(EMPTY);
  const [hover, setHover] = useState(NO_HOVER);
  const selected = useMemo(() => (scene ? chosen.filter(id => (isElementId(id) ? scene.describeElement(id) : tree.nodes.has(id))) : EMPTY),
    [scene, tree, chosen]);
  const selectedNodeIds = useMemo(() => selected.filter(id => !isElementId(id)), [selected]);
  const selectedElementIds = useMemo(() => selected.filter(isElementId), [selected]);
  const leavesOf = useCallback(ids => [...new Set(ids.flatMap(id => tree.nodes.get(id)?.leafPartIds || []))], [tree]);
  // What is open: the person's own under "all"; every assembly under the filter's other shapes, and
  // under Faces and Edges every part's faces and edges with them.
  const assemblyIds = useMemo(() => xbfAssemblyIds(tree), [tree]);
  const openIds = useMemo(() => {
    if (shape === "all") return new Set(expandedIds);
    const open = new Set(assemblyIds);
    if (shape !== "parts") for (const id of tree.nodes.keys()) if (!isXbfAssemblyId(id)) open.add(id);
    return open;
  }, [shape, expandedIds, assemblyIds, tree]);
  const selectableIds = useMemo(() => (isolatedIds.length ? subtreeIds(tree, isolatedIds) : null), [tree, isolatedIds]);

  // The scene follows: hidden parts, the isolation, what is lit.
  useEffect(() => { scene?.setHiddenParts(hiddenIds); requestRender(); }, [scene, hiddenIds, requestRender]);
  useEffect(() => { scene?.setFocusedParts(leavesOf(isolatedIds)); requestRender(); }, [scene, isolatedIds, leavesOf, requestRender]);
  useEffect(() => {
    if (!scene) return;
    scene.setHighlight({ hovered: hover.parts, selected: leavesOf(selectedNodeIds) });
    scene.setElementHighlight({ hovered: hover.element, selected: selectedElementIds });
    requestFrame();
  }, [scene, hover, selectedNodeIds, selectedElementIds, leavesOf, requestFrame]);

  // ---- Position: the mechanism's pose ---------------------------------------------------------
  const kinematics = scene?.kinematics || null;
  const positionRows = useMemo(() => (kinematics ? xbfPositionRows(kinematics) : EMPTY), [kinematics]);
  const posable = positionRows.length > 0;
  const pose = useXbfPose(kinematics);
  const { values: poseValues, applied: poseApplied, preset: posePreset, couplings: poseCouplings,
    setValues: setPoseValues, choosePreset, reset: resetPose } = pose;
  useEffect(() => {
    if (!scene || !poseValues) return;
    scene.setPose(poseApplied ? poseValues : null);
    shellRef.current?.syncSceneBounds?.();
    requestRender();
  }, [scene, poseValues, poseApplied, requestRender]);

  // ---- the shell ------------------------------------------------------------------------------
  const clearSelection = useCallback(() => setChosen(now => (now.length ? EMPTY : now)), []);
  const measureRef = useRef(measure);
  measureRef.current = measure;
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const live = useMemo(() => ({
    declined: XBF_DECLINED_LIVE_COMMANDS,
    commands: { clearSelection: () => setChosen(EMPTY) },
    state: () => ({ selected: [...selectedRef.current] })
  }), []);
  // Escape, as a STEP's: a measurement in progress, then the tool in hand (Select taken up), then
  // the measurements picked, then the parts, faces and edges, then the isolation -- one layer a press.
  const escapeRef = useRef(() => false);
  const escape = useMemo(() => ({
    active: selected.length > 0 || isolatedIds.length > 0 || toolMode !== XBF_TOOL.SELECT || measure.selected.length > 0,
    handle: () => escapeRef.current()
  }), [selected.length, isolatedIds.length, toolMode, measure.selected.length]);
  // An xbf's CAD edges are hairlines (`xbfScene.js`): while the view draws them the viewport keeps
  // its pixel ratio through a camera gesture, as a STEP's does for its edges, so a press or a
  // drag never softens and shifts the model (the edges the view draws are only known after the
  // shell resolves them; until then a view draws them, as every display mode but Shaded does).
  const [edgesDrawn, setEdgesDrawn] = useState(true);
  const shell = useRendererShell({
    view, services: document.services, resource: document.resource, modelKey: document.modelKey, revisionKey: loaded.revision,
    features, toolModes: XBF_TOOL_MODES, tool, previewable: true, preview, scene, sceneEffects: true,
    load: { busy: loaded.busy && !scene, updating: loaded.busy && Boolean(scene), progress: loaded.progress, alert: loadAlert },
    live, escape, measurements, deleteSelection: measure.removeSelected, preserveInteractionPixelRatio: edgesDrawn,
    // A prompt's annotation is in the scene's coordinates: the document's millimetres.
    annotationUnits: scene?.units?.unit || "mm"
  });
  shellRef.current = shell;
  useDeclinedSelectReference(document);
  const ensureSelect = useCallback(() => { if (shellRef.current?.toolMode !== XBF_TOOL.SELECT) shellRef.current?.selectTool(XBF_TOOL.SELECT); }, []);

  // ---- one motion at a time: Explode and the pose ------------------------------------------
  const { view: viewSettings, store } = shell.viewSettings;
  const explodeApplied = Boolean(viewSettings.exploded?.enabled && viewSettings.exploded.amount > 0);
  useMotionExclusion(document.modelKey, {
    explode: { applied: explodeApplied, reset: () => store.patch(EXPLODE_OFF) },
    position: { applied: poseApplied, reset: resetPose }
  });

  // The edges the display mode draws, as the view resolved them; Preview's scene draws none.
  const edges = shell.frame.resolvedScene?.view?.edges || null;
  const edgesKey = JSON.stringify(edges);
  useEffect(() => {
    if (!edges) return;
    setEdgesDrawn(edges.enabled !== false);
    if (!scene) return;
    scene.setEdgeLook(edges);
    shell.requestRender();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scene, edgesKey]);
  // The scene's screen-space lines (its highlights) are registered with the viewport showing it,
  // as a STEP's are: the viewport keeps their resolution at its drawing buffer's and counts them.
  // The viewport's runtime is made after the first render and again after a lost context, so
  // every render hands the scene the one there is (the scene ignores the same one twice).
  useEffect(() => { scene?.setLineRegistry(viewportRef.current?.runtimeRef.current || null); });
  const previewScene = usePreviewScene(scene, previewing);
  useEffect(() => { previewScene?.setEdgeLook({ enabled: false }); }, [previewScene]);
  const previewView = useMemo(() => ({ scene: previewScene }), [previewScene]);

  // ---- the tree's actions --------------------------------------------------------------------
  const toggleExpanded = useCallback(id => setExpandedIds(now => (now.includes(id) ? now.filter(item => item !== id) : [...now, id])), []);
  const collapseSubtree = useCallback(id => {
    const under = subtreeIds(tree, [id]);
    setExpandedIds(now => (now.some(item => under.has(item)) ? now.filter(item => !under.has(item)) : now));
  }, [tree]);
  const selectTreeNode = useCallback((id, { multiSelect = false } = {}) => {
    ensureSelect();
    setChosen(now => pickNode(now, id, multiSelect));
  }, [ensureSelect]);
  // Isolate: what it names alone in reach, everything else dimmed; its parts shown, opened.
  const isolate = useCallback((ids) => {
    if (!assembly) return;
    const targets = [ids].flat().filter(id => tree.nodes.has(id));
    if (!targets.length) { setIsolatedIds(EMPTY); return; }
    const leaves = new Set(leavesOf(targets));
    setChosen(EMPTY);
    setIsolatedIds(targets);
    setExpandedIds(now => [...new Set([...now, ...targets])]);
    setHiddenIds(now => (now.some(id => leaves.has(id)) ? now.filter(id => !leaves.has(id)) : now));
  }, [assembly, tree, leavesOf]);
  const exitIsolate = useCallback(() => {
    for (const id of isolatedIds) collapseSubtree(id);
    setIsolatedIds(EMPTY);
  }, [isolatedIds, collapseSubtree]);
  escapeRef.current = () => {
    const now = measureRef.current;
    if (!previewing && toolMode === XBF_TOOL.MEASURE && now.drafting) { now.cancelDraft(); return true; }
    if (!previewing && toolMode !== XBF_TOOL.SELECT) { shellRef.current?.selectTool(XBF_TOOL.SELECT); return true; }
    if (now.selected.length) { now.select([]); return true; }
    if (selectedRef.current.length) { setChosen(EMPTY); return true; }
    if (isolatedIds.length) { exitIsolate(); return true; }
    return false;
  };
  const exitSingleIsolate = useCallback((id) => {
    collapseSubtree(id);
    setIsolatedIds(now => now.filter(item => item !== id));
  }, [collapseSubtree]);
  const hideNodes = useCallback((ids) => {
    const leaves = new Set(leavesOf(ids));
    for (const id of ids) collapseSubtree(id);
    // What is hidden is let go of: a face or edge of a hidden part, a node all of whose parts are.
    const gone = item => (isElementId(item) ? leaves.has(item.split("/")[0])
      : (tree.nodes.get(item)?.leafPartIds || EMPTY).every(leaf => leaves.has(leaf)));
    setChosen(now => now.filter(item => !gone(item)));
    setIsolatedIds(now => now.filter(item => !ids.some(id => subtreeIds(tree, [id]).has(item))));
    setHiddenIds(now => [...new Set([...now, ...leaves])]);
  }, [leavesOf, collapseSubtree, tree]);
  const revealNodes = useCallback((ids) => {
    const leaves = new Set(leavesOf(ids));
    setHiddenIds(now => now.filter(id => !leaves.has(id)));
  }, [leavesOf]);
  const toggleVisibility = useCallback((id) => {
    const leaves = tree.nodes.get(id)?.leafPartIds || EMPTY;
    if (leaves.length && leaves.every(leaf => hiddenIds.includes(leaf))) revealNodes([id]); else hideNodes([id]);
  }, [tree, hiddenIds, hideNodes, revealNodes]);
  const showAll = useCallback(() => setHiddenIds(EMPTY), []);
  const hideOthers = useCallback((ids) => {
    const keep = new Set(leavesOf(ids));
    setIsolatedIds(EMPTY);
    setHiddenIds([...tree.nodes.values()].filter(node => node.kind === "part" && !isXbfAssemblyId(node.selectionId))
      .map(node => node.selectionId).filter(id => !keep.has(id)));
    setChosen(now => now.filter(item => (isElementId(item) ? keep.has(item.split("/")[0])
      : (tree.nodes.get(item)?.leafPartIds || EMPTY).some(leaf => keep.has(leaf)))));
  }, [leavesOf, tree]);

  // ---- picking --------------------------------------------------------------------------------
  // The instances whose faces and edges a press can take under "all": each part the host has open
  // whose owners are open too (a press elsewhere takes the part, or the collapsed assembly it is in).
  const frontier = useCallback(id => xbfPickFrontier(tree, id, openIds), [tree, openIds]);
  const openElements = useMemo(() => {
    if (shape !== "all") return null;
    const open = new Set();
    for (const id of openIds) if (!isXbfAssemblyId(id) && frontier(id) === id) open.add(parseXbfElementId(id).instance);
    return open;
  }, [shape, openIds, frontier]);
  const pickOptions = useMemo(() => ({
    faces: geometryFilter.includes("faces"), edges: geometryFilter.includes("edges"),
    parts: assembly && geometryFilter.includes("parts"), elements: openElements
  }), [geometryFilter, assembly, openElements]);
  const viewportRef = useRef(null);
  // What the last press was made with (`XbfPick`): a finger's press reaches an edge from further off, as a STEP's.
  const pointerTypeRef = useRef("");
  // A press's pick and a resting pointer's (an edge is taken from further away on a press), as one hit.
  const picker = useMemo(() => (scene ? {
    pick(ray) {
      const camera = viewportRef.current?.runtimeRef.current?.camera;
      const rect = viewportRef.current?.hostRef.current?.getBoundingClientRect?.();
      if (!camera || !rect?.width || mode === "none") return null;
      const view = { camera, width: rect.width, height: rect.height };
      const press = scene.pickElement(ray, view, { ...pickOptions, touch: pointerTypeRef.current === "touch" });
      const rest = scene.pickElement(ray, view, { ...pickOptions, hover: true });
      return press || rest ? { id: rest?.id || "", press, rest } : null;
    }
  } : null), [scene, pickOptions, mode]);
  const pickAtClient = useCallback((clientX, clientY) => {
    const viewport = viewportRef.current;
    const runtime = viewport?.runtimeRef.current;
    const rect = viewport?.hostRef.current?.getBoundingClientRect?.();
    if (!runtime?.camera || !rect?.width || !picker) return null;
    const raycaster = new runtime.THREE.Raycaster();
    raycaster.setFromCamera(new runtime.THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, 1 - ((clientY - rect.top) / rect.height) * 2), runtime.camera);
    return picker.pick(raycaster.ray)?.press || null;
  }, [picker]);
  // What a hit reaches: a face or an edge, or the node a press on its part takes.
  const nodeOf = useCallback(hit => (hit && !hit.kind ? frontier(hit.id) : ""), [frontier]);

  // Group faces and Group edges: the engine's groups of a part, asked for once per part.
  const groupsRef = useRef(new Map());
  const groupsOf = useCallback((key) => {
    if (!source) return Promise.resolve(null);
    const known = groupsRef.current.get(key);
    if (known) return known;
    const asked = fetchXbfPartGroups(source, key, { fetch: engineFetch }).catch(() => null);
    groupsRef.current.set(key, asked);
    return asked;
  }, [source, engineFetch]);
  const grownIds = useCallback(async (hit) => {
    const option = hit.kind === "face" ? "tangentFaces" : "edgeChain";
    if (!connected[option]) return [hit.id];
    const groups = await groupsOf(hit.key);
    const table = groups?.[hit.kind === "face" ? "faces" : "edges"];
    const group = table?.[String(hit.ordinal)];
    if (group == null) return [hit.id];
    return Object.entries(table).filter(([, value]) => value === group).map(([ordinal]) => `i${hit.instance}/${hit.kind}/${ordinal}`);
  }, [connected, groupsOf]);

  const lastPick = useRef(null);
  const handlePick = useCallback(async (found, modifiers) => {
    const hit = found?.press || null;
    const multiSelect = Boolean(modifiers?.multiSelect);
    lastPick.current = { at: performance.now(), before: selectedRef.current };
    if (shellRef.current?.toolMode !== XBF_TOOL.SELECT) ensureSelect();
    if (!hit) { if (!multiSelect) { setChosen(EMPTY); measureRef.current.select([]); } return; }
    if (!multiSelect) measureRef.current.select([]);
    if (hit.kind) {
      const ids = await grownIds(hit);
      setChosen(now => pickElements(now, ids, multiSelect));
      return;
    }
    const node = nodeOf(hit);
    if (node && (!selectableIds || selectableIds.has(node))) setChosen(now => pickNode(now, node, multiSelect));
  }, [ensureSelect, grownIds, nodeOf, selectableIds]);
  const handleHover = useCallback((found) => {
    const hit = found?.rest || null;
    if (!hit) { setHover(now => (now === NO_HOVER ? now : NO_HOVER)); return; }
    if (hit.kind) { setHover({ parts: EMPTY, element: hit.id }); return; }
    const node = nodeOf(hit);
    setHover({ parts: node ? tree.nodes.get(node)?.leafPartIds || EMPTY : EMPTY, element: null });
  }, [nodeOf, tree]);
  const selectActive = !previewing && Boolean(scene) && toolMode === XBF_TOOL.SELECT;
  // A pick under Explode or Clip takes Select up, as a STEP's does.
  const picking = !previewing && Boolean(scene) && [XBF_TOOL.SELECT, XBF_TOOL.EXPLODE, XBF_TOOL.CLIP].includes(toolMode);
  useEffect(() => { if (!picking) setHover(NO_HOVER); }, [picking]);

  // ---- references and copies -------------------------------------------------------------------
  const facts = useXbfElementFacts({ source, fetch: engineFetch, version: loaded.revision, scene, elementIds: selectedElementIds });
  const removeFromSelection = useCallback(ids => setChosen(now => now.filter(id => !ids.includes(id))), []);
  const hoverChip = useCallback(ids => {
    if (!ids) { setHover(NO_HOVER); return; }
    const elements = ids.filter(isElementId);
    setHover(elements.length ? { parts: EMPTY, element: elements[0] } : { parts: ids, element: null });
  }, []);
  const resource = shell.frame.resource;
  const { references, chips } = useXbfSelectionChips({ scene, tree, resource, selectedIds: selected, facts,
    onRemove: removeFromSelection, onHover: hoverChip });
  // What a node or an element copies: its reference, the file's path with it.
  const singleChip = useCallback(ids => xbfCopyText({ scene, tree, resource, ids }), [scene, tree, resource]);
  const copy = useCallback(text => { if (text) void host.clipboard.writeText(text).catch(() => {}); }, [host.clipboard]);
  const copySelection = selected.length && !previewing ? () => copy(chips.map(chip => chip.copy).filter(Boolean).join("\n")) : null;
  const nameReference = useCallback((reference) => {
    const selector = reference?.target?.kind === "cad-selector" ? reference.target.selectors?.[0] : "";
    const chip = selector ? chips.find(item => item.selectors?.includes(selector)) : null;
    if (!chip) return "";
    return chip.compact || { name: chip.label, kind: chip.glyph };
  }, [chips]);

  // ---- double-click: a face or edge copied, a part isolated, empty space leaves isolation --------
  useEffect(() => {
    const element = viewportRef.current?.hostRef.current;
    if (!element || !selectActive) return undefined;
    const onDoubleClick = (event) => {
      if (event.target !== viewportRef.current?.runtimeRef.current?.renderer?.domElement) return;
      // The first click of the gesture picked: what it found comes back first.
      const first = lastPick.current;
      if (first && performance.now() - first.at < DOUBLE_CLICK_MS) setChosen(first.before);
      const hit = pickAtClient(event.clientX, event.clientY);
      if (!hit) { exitIsolate(); return; }
      if (hit.kind) {
        if (!(first?.before || EMPTY).includes(hit.id)) setChosen(now => pickElements(now, [hit.id], false));
        copy(singleChip([hit.id]));
        return;
      }
      const node = nodeOf(hit);
      if (node && assembly) isolate([node]);
    };
    element.addEventListener("dblclick", onDoubleClick);
    return () => element.removeEventListener("dblclick", onDoubleClick);
  }, [selectActive, pickAtClient, exitIsolate, nodeOf, assembly, isolate, copy, singleChip]);

  // ---- the menus --------------------------------------------------------------------------------
  const zoomSelectionAvailable = selected.length > 0;
  const zoomToSelection = useCallback(() => {
    const ids = [...leavesOf(selectedRef.current.filter(id => !isElementId(id))), ...selectedRef.current.filter(isElementId)];
    const bounds = scene?.selectionBounds(ids);
    if (bounds) shellRef.current?.frame.viewerRef.current?.zoomToBounds?.(bounds);
  }, [scene, leavesOf]);
  const menuForNode = useCallback(id => xbfPartMenu({ tree, nodeId: id, assembly, hiddenIds, isolatedIds,
    selectedNodeIds, expandedIds, copyText: singleChip([...new Set([...selectedNodeIds, id])]), zoomSelectionAvailable, shape }),
  [tree, assembly, hiddenIds, isolatedIds, selectedNodeIds, expandedIds, singleChip, zoomSelectionAvailable, shape]);
  const partMenuActions = useMemo(() => Object.fromEntries(Object.entries({
    onCopyReference: menu => copy(menu.copyText),
    onSelect: (menu) => {
      if (menu.referenceId) {
        if (menu.selected && menu.referenceIds.length > 1) { setChosen(now => now.filter(id => !isElementId(id))); return; }
        setChosen(now => pickElements(now, [menu.referenceId], false));
        return;
      }
      if (menu.selected) {
        if (menu.actionNodeIds.length > 1) setChosen(EMPTY); else setChosen(now => now.filter(id => id !== menu.nodeId));
        return;
      }
      setChosen(now => pickNode(now, menu.nodeId, false));
    },
    onIsolate: menu => (menu.focused ? exitSingleIsolate(menu.nodeId) : isolate(menu.actionNodeIds)),
    onExitAllIsolate: exitIsolate,
    onHideOther: menu => hideOthers(menu.actionNodeIds),
    onHideAll: () => showAll(),
    onHide: menu => hideNodes(menu.selected && menu.actionNodeIds.length > 1 ? selectedNodeIds : menu.actionNodeIds),
    onReveal: menu => revealNodes(menu.actionNodeIds),
    onExpandSelected: menu => menu.collapsedActionNodeIds.forEach(toggleExpanded),
    onCollapseSelected: menu => menu.expandedActionNodeIds.forEach(toggleExpanded),
    onExpandAll: menu => setExpandedIds(now => [...new Set([...now, ...menu.collapsedExpandableTreeNodeIds])]),
    onCollapseAll: menu => setExpandedIds(now => now.filter(id => !menu.expandedExpandableTreeNodeIds.includes(id))),
    onZoomFit: () => shellRef.current?.frame.viewerRef.current?.resetZoom?.(),
    onZoomSelection: zoomToSelection
  }).map(([name, action]) => [name, (menu) => { ensureSelect(); return action(menu); }])),
  [copy, exitSingleIsolate, isolate, exitIsolate, hideOthers, showAll, hideNodes, selectedNodeIds, revealNodes, toggleExpanded,
    zoomToSelection, ensureSelect]);
  const contextMenuItems = selectActive ? (press) => {
    const hit = pickAtClient(press.clientX, press.clientY);
    let menu = null;
    if (hit?.kind) {
      menu = xbfTopologyMenu({ elementId: hit.id, label: hit.name || hit.id, selectedElementIds,
        copyText: singleChip([...new Set([...selectedElementIds, hit.id])]), zoomSelectionAvailable });
    } else if (hit && assembly) {
      menu = menuForNode(nodeOf(hit));
    }
    menu ||= xbfModelMenu({ tree, assembly, expandedIds, hiddenCount: hiddenIds.length, zoomSelectionAvailable, shape,
      copyText: assembly ? "" : singleChip([tree.roots[0]?.selectionId].filter(Boolean)) });
    return viewportMenuEntries(menu, { actions: partMenuActions });
  } : null;

  // ---- the strip --------------------------------------------------------------------------------
  const bounds = scene ? boundsInUnits(sceneFramingBounds(scene), scene.units) : null;
  const effects = useModelTools({ view: viewSettings, features, store, mesh: bounds ? { bounds } : null,
    disabled: shell.idle, selectedTool: shell.toolMode, onSelect: shell.selectTool });
  const selectModeMenu = useMemo(() => <SelectModeMenu value={selectFilter} assembly={assembly} posable={posable} disabled={shell.idle}
    onChange={value => { setSelectFilter(value); shellRef.current?.selectTool(XBF_TOOL.SELECT); }}
    connected={connected} onConnectedChange={(id, checked) => setConnected(now => ({ ...now, [id]: checked }))} />,
  [selectFilter, assembly, posable, shell.idle, connected]);
  const strip = [
    shell.tools.own({ id: XBF_TOOL.SELECT, label: "Select", icon: <SelectIcon className="size-3.5" aria-hidden="true" />, menu: selectModeMenu }),
    { ...shell.tools.annotate, disabled: shell.idle },
    shell.tools.own({ id: XBF_TOOL.MEASURE, label: "Measure", icon: <MeasureIcon className="size-3.5" aria-hidden="true" />,
      onSelect: () => { if (shell.toolMode !== XBF_TOOL.MEASURE) shell.selectTool(XBF_TOOL.MEASURE); } }),
    ...effects.tools
  ];
  // Under Select a press on a measurement picks it before any part under it, while Select picks measurements.
  const runtimeOf = () => viewportRef.current?.runtimeRef.current;
  const measureSelect = measureSelectHandlers(measure, runtimeOf, clearSelection, { picks: selects(selectFilter, "measurement") });

  // Position's knobs: Select's, while its filter ticks Position, with nothing exploded.
  const knobsShown = selectActive && posable && selects(selectFilter, "position") && !explodeApplied;
  const handles = useMemo(() => (knobsShown && poseValues ? xbfJointHandles({ kinematics, rows: positionRows, values: poseValues, scene,
    onChange: (row, value) => { const written = writeXbfPosition(poseValues, row, value); if (written) setPoseValues(written); } }) : null),
  [knobsShown, kinematics, positionRows, poseValues, scene, setPoseValues]);
  const handleLayoutRef = useRef(null);

  // ---- the file sidebar: the Model tree (an assembly's), then Position --------------------------
  const partControls = useMemo(() => ({
    isAssemblyView: assembly, hiddenPartIds: hiddenIds, focusedNodeIds: isolatedIds, selectableNodeIds: selectableIds ? [...selectableIds] : null,
    expandedTreeNodeIds: expandedIds, onToggleTreeNode: toggleExpanded, onSelectTreeNode: selectTreeNode,
    onFocusTreeNode: ids => { ensureSelect(); isolate(ids); }, onUnfocusTreeNode: exitSingleIsolate, onExitAllIsolate: exitIsolate,
    onTogglePartVisibility: toggleVisibility, showAllHiddenParts: showAll,
    onHoverTreeNode: id => setHover(id ? { parts: tree.nodes.get(id)?.leafPartIds || EMPTY, element: null } : NO_HOVER),
    menuForNode, partMenuActions
  }), [assembly, hiddenIds, isolatedIds, selectableIds, expandedIds, toggleExpanded, selectTreeNode, ensureSelect, isolate, exitSingleIsolate,
    exitIsolate, toggleVisibility, showAll, tree, menuForNode, partMenuActions]);
  const sidebar = scene && (assembly || kinematics) ? <>
    {assembly ? <XbfModelTree key={document.modelKey} tree={tree} active={!previewing} disabled={shell.idle} mode={shape}
      selectedIds={selected} onClearSelection={clearSelection} partControls={partControls} /> : null}
    {kinematics ? <XbfPositionSection kinematics={kinematics} values={poseValues || {}} couplings={poseCouplings} preset={posePreset}
      changed={poseApplied} onValues={setPoseValues} onPreset={choosePreset} onReset={resetPose} /> : null}
  </> : null;

  return <RendererShell shell={shell} tools={strip} subToolbar={measuring ? null : effects.subToolbar} sidebar={sidebar} previewView={previewView}
    references={previewing ? NO_REFERENCES : references} referenceChips={previewing ? NO_REFERENCES : chips} nameReference={nameReference}
    onClearReferences={() => { setChosen(EMPTY); measure.select([]); }} copySelection={copySelection}
    onSelectPress={measureSelect.press} onSelectHover={measureSelect.hover} selectsAnnotations={selects(selectFilter, "annotation")}
    contextMenuItems={contextMenuItems}
    viewportOverlay={viewport => {
      viewportRef.current = viewport;
      return <>
        <XbfMeasureOverlay viewport={viewport} scene={scene} measure={measure} picking={measuring} preview={shell.previewing} />
        {handles ? <JointHandleOverlay handles={handles} layoutSeamRef={handleLayoutRef} {...viewport} /> : null}
        <XbfPick viewport={viewport} scene={picker} enabled={picking} onPick={handlePick} onHover={handleHover} pointerTypeRef={pointerTypeRef} />
      </>;
    }} />;
}

/**
 * Select's pointer: taps and the resting pointer through the picker (`usePointerPick`), and what
 * each press is made with (`pointerTypeRef`), heard before the pick's own listener hears it.
 */
function XbfPick({ viewport, scene, enabled, onPick, onHover, pointerTypeRef }) {
  const { hostRef, viewerReadyTick } = viewport;
  useEffect(() => {
    const host = hostRef.current;
    if (!enabled || !host) return undefined;
    const note = (event) => { pointerTypeRef.current = event.pointerType || ""; };
    host.addEventListener("pointerdown", note, true);
    return () => host.removeEventListener("pointerdown", note, true);
  }, [enabled, hostRef, viewerReadyTick, pointerTypeRef]);
  usePointerPick({ viewport, scene, enabled, onPick, onHover, sameHit: (left, right) => (left?.rest?.id || "") === (right?.rest?.id || "") });
  return null;
}

export default function XbfRenderer(props) {
  const { data, ...view } = props;
  return <XbfSurface key={JSON.stringify([view.source.id, view.file.path])} view={view} data={data} />;
}
