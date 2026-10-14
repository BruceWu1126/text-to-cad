import { useCallback, useMemo, useRef } from 'react';
import { buildPositionSection } from './MotionControlsSection.js';
import { useStepReferenceChips } from './StepReferenceSection.js';
import ModelingTree from './ModelingTree.jsx';
import ToolPanel from '../../../kit/tools/ToolPanel.jsx';
import { KeptPositionPanel } from '../../../kit/inspector/kinematicsControls.jsx';
import { stepGeometryMeasurements } from '../../workbench/stepGeometryMeasurements.js';
const EMPTY = [];
// What a part file's Select card asks for, by the Select mode in hand.

/**
 * `fn` behind one identity for the life of the panels, calling whatever `fn` is now: the tree's
 * rows are memoized, so a host callback rebuilt on every render must not re-render them. Absent
 * stays absent (a row reads whether an action is offered from whether its callback exists).
 */
function useLatestCallback(fn) {
  const latest = useRef(fn);
  latest.current = fn;
  const stable = useCallback((...args) => latest.current?.(...args), []);
  return typeof fn === 'function' ? stable : fn;
}

/** An object of callbacks (`partMenuActions`) the same way: one object while its names stay the same. */
function useLatestActions(actions) {
  const latest = useRef(actions);
  latest.current = actions;
  const names = actions ? Object.keys(actions).filter(name => typeof actions[name] === 'function').sort().join('\n') : null;
  return useMemo(() => names === null ? actions : Object.fromEntries(names.split('\n').filter(Boolean)
    .map(name => [name, (...args) => latest.current?.[name]?.(...args)])), [names]);
}

/**
 * What is selected, as Quick Edit's chips (`useStepReferenceChips`): each face, edge or run of them
 * named after its part as the tree names that part, with its key measurements for its card — a
 * part's or subassembly's size from the boxes of the parts it is made of. `onRemove(ids)` and
 * `onHover(target)` are the host's: what a chip's X drops and what resting on it lights.
 */
export function useStepSelectionChips({ stepTreeRoot, selectedMeshData = null, geometryInspection = null, references = EMPTY,
  onRemove = null, onHover = null, copyText = null }) {
  const modelParts = geometryInspection?.parts || EMPTY;
  const partsSize = useCallback(ids => stepGeometryMeasurements({ partIds: ids }, EMPTY, modelParts).size, [modelParts]);
  const partNames = useMemo(() => {
    const names = new Map();
    const visit = node => { if (!node) return; if (node.id) names.set(String(node.id), String(node.displayName || node.name || '').trim()); (node.children || EMPTY).forEach(visit); };
    visit(stepTreeRoot);
    return names;
  }, [stepTreeRoot]);
  const partName = useCallback(id => partNames.get(String(id || '')) || '', [partNames]);
  const remove = useLatestCallback(onRemove), hover = useLatestCallback(onHover);
  return useStepReferenceChips({ references, meshData: selectedMeshData, partsSize, partName, onRemove: remove, onHover: hover, copyText });
}

/**
 * A STEP's panels in the tool stack, top to bottom: while Select is the tool, an assembly's
 * **Features** (the filter and the model tree; a part file, whose tree would be its one row, has
 * none, and Select's modes are the chevron after its button on the strip); what is picked is Quick
 * Edit's to show, as chips (`useStepSelectionChips`); while Position is the tool, **Position**. Each stays
 * mounted while its tool is not up, so the tree keeps its expansion, filter and scroll. The kept
 * effects' panels follow them (`ModelTools.jsx`); the stack itself is the shell's
 * (`kit/shell/RendererShell.jsx`).
 *
 * `selectActive`: Features is on screen, which is when a pick is scrolled to. `selectMode` is the
 * Select tool's mode, which the tree's disclosure follows.
 */
export function useStepPanels({
  selectActive, positionActive, selectedEntry, viewerLoading,
  geometryInspection = null, stepTreeRoot, isAssemblyView = false,
  selectedPartIds = EMPTY, selectedReferences = EMPTY,
  hiddenPartIds = EMPTY, focusedNodeIds = EMPTY, selectableNodeIds = null,
  expandedTreeNodeIds = EMPTY, onToggleTreeNode,
  activeTreeNodeScrollKey = '', onSelectTreeNode, onClearSelection,
  onFocusTreeNode, onUnfocusTreeNode, onExitAllIsolate, onTogglePartVisibility,
  onHoverTreeNode, showAllHiddenParts,
  // The menu a tree row carries: a node's part menu descriptor, and the actions behind it.
  menuForNode = null, partMenuActions = null,
  treeSelectionDisabled = false, selectMode = 'all', loadingGeometry = false,
  positionRuntime = null,
  // The viewport's hover (`workbench/hoverStore.js`): a large tree under Faces or Edges asks for the
  // topology of the part the pointer rests on.
  hoverStore = null,
}) {
  // What every tree row is handed, the same object until something in it changes: the host's
  // actions behind stable identities, its menus and state as they are.
  const toggleTreeNode = useLatestCallback(onToggleTreeNode), selectTreeNode = useLatestCallback(onSelectTreeNode);
  const focusTreeNode = useLatestCallback(onFocusTreeNode), unfocusTreeNode = useLatestCallback(onUnfocusTreeNode);
  const exitAllIsolate = useLatestCallback(onExitAllIsolate), togglePartVisibility = useLatestCallback(onTogglePartVisibility);
  const showAll = useLatestCallback(showAllHiddenParts);
  const hoverTreeNode = useLatestCallback(onHoverTreeNode);
  // A row's menu is built when it opens, from the host's descriptor as it is then.
  const nodeMenu = useLatestCallback(menuForNode);
  const menuActions = useLatestActions(partMenuActions);
  const partControls = useMemo(() => ({ isAssemblyView, hiddenPartIds, focusedNodeIds, selectableNodeIds, expandedTreeNodeIds,
    onToggleTreeNode: toggleTreeNode, onSelectTreeNode: selectTreeNode, onFocusTreeNode: focusTreeNode, onUnfocusTreeNode: unfocusTreeNode,
    onExitAllIsolate: exitAllIsolate, onTogglePartVisibility: togglePartVisibility, showAllHiddenParts: showAll,
    onHoverTreeNode: hoverTreeNode, menuForNode: nodeMenu, partMenuActions: menuActions }), [isAssemblyView,
    hiddenPartIds, focusedNodeIds, selectableNodeIds, expandedTreeNodeIds, toggleTreeNode, selectTreeNode, focusTreeNode, unfocusTreeNode,
    exitAllIsolate, togglePartVisibility, showAll, hoverTreeNode, nodeMenu, menuActions]);
  const loadTopology = useLatestCallback(geometryInspection?.onLoadTopology);
  const clearSelection = useLatestCallback(onClearSelection);
  if (!selectedEntry) return null;
  const position = buildPositionSection({ poseRuntime: positionRuntime });
  const disabled = treeSelectionDisabled || viewerLoading;
  return <>
    {isAssemblyView ? <ModelingTree key={`${selectedEntry.file}:${geometryInspection?.revision}`}
      stepRoot={stepTreeRoot} active={selectActive} mode={selectMode} loading={loadingGeometry} disabled={disabled}
      selectedReferences={selectedReferences} selectedPartIds={selectedPartIds} activeTreeNodeScrollKey={activeTreeNodeScrollKey}
      onLoadTopology={loadTopology} onClearSelection={clearSelection}
      partControls={partControls} hoverStore={hoverStore}
    /> : null}
    {/* Headed "Position", and its X while the pose is off its default (the X resets it); sized like
        every resizable panel: its content's height, up to the stack's. Another tool puts Position
        down, and the values stay. */}
    {position ? <ToolPanel id="position" title={position.title} label="Position controls" fit="details" resizable collapsible={false}
      hidden={!positionActive} lead={positionActive} closeLabel="Reset position"
      onClose={position.custom && position.onReset ? position.onReset : null}>{position.content}</ToolPanel> : null}
    {/* A pose that is not the default, kept while Position is not the tool: one line, its X the Reset. */}
    {position?.custom && !positionActive && position.onReset ? <KeptPositionPanel summary={position.summary} onReset={position.onReset}
      poseRow={position.poseRow} /> : null}
  </>;
}
