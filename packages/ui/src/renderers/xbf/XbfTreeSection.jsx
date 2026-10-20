import { useEffect, useMemo, useRef, useState } from "react";
import { Box, Eye, EyeOff, Focus } from "lucide-react";
import { ROW_NAME_UNDER_ACTIONS, TREE_GLYPH_STROKE, TREE_INDENT_PX, TREE_ROW_DENSE_ICON_CLASS, TreeRowSurface, TreeRowChevron, TreeRowGuides, TreeRowLabel, treeRowActionsClass } from "@text-to-cad/ui/primitives/tree-row";
import { TreeFilterHighlight, TreeFilterInput } from "@text-to-cad/ui/primitives/tree-filter";
import { Button } from "@text-to-cad/ui/primitives/button";
import { TooltipHint } from "@text-to-cad/ui/primitives/tooltip";
import { cn } from "@text-to-cad/ui/utils";
import ToolPanel from "../kit/tools/ToolPanel.jsx";
import { useTreeSearch } from "../kit/inspector/modelTreeSearch.js";
import { revealRow } from "../kit/inspector/VirtualRows.jsx";
import { allHidden, buildXbfTree, xbfTreeAncestors } from "./xbfTree.js";

// The document's tree (FreeCAD's tree view): its assemblies and, under them, each placed part a
// row. A part row selects its instance, which the view lights; resting on a row lights it there
// too. Each row's eye hides what it holds and shows it again; its target isolates it (everything
// else hidden), and pressed again shows everything.

const ROW_ACTIONS_WIDTH = "2.75rem";
const partGlyph = <span className="grid h-6 w-4 shrink-0 place-items-center" aria-hidden="true"><Box className={TREE_ROW_DENSE_ICON_CLASS} strokeWidth={TREE_GLYPH_STROKE}/></span>;

function RowActions({ node, hidden, isolated, onHide, onIsolate }) {
  if (!node.partIds.length) return null;
  const off = allHidden(node.partIds, hidden);
  const action = off ? "Show" : "Hide";
  const isolating = isolated === node.id;
  return <div data-row-actions="" className={cn(treeRowActionsClass(off || isolating), "gap-0.5")}>
    <TooltipHint content={isolating ? "Show all" : "Isolate"}>
      <Button type="button" variant="ghost" size="icon-xs" aria-label={`${isolating ? "Show all, from" : "Isolate"} ${node.label}`} aria-pressed={isolating}
        className={cn("h-5 w-4 text-muted-foreground", isolating && "text-foreground")}
        onClick={event => { event.stopPropagation(); onIsolate(node); }}>
        <Focus className="size-3" aria-hidden="true"/>
      </Button>
    </TooltipHint>
    <TooltipHint content={action}>
      <Button type="button" variant="ghost" size="icon-xs" aria-label={`${action} ${node.label}`} className="h-5 w-4 text-muted-foreground"
        onClick={event => { event.stopPropagation(); onHide(node.partIds, !off); }}>
        {off ? <EyeOff className="size-3" aria-hidden="true"/> : <Eye className="size-3" aria-hidden="true"/>}
      </Button>
    </TooltipHint>
  </div>;
}

function rowName(node, hidden) {
  return node.partIds.length && allHidden(node.partIds, hidden) ? ROW_NAME_UNDER_ACTIONS.shown : ROW_NAME_UNDER_ACTIONS.hover;
}

function XbfRow({ node, depth, expanded, toggle, selection, rowRefs, hidden, isolated, onHide, onIsolate }) {
  const open = expanded.has(node.id), branch = node.children.length > 0;
  const active = Boolean(node.selectionId) && selection.selected.includes(node.selectionId);
  const enter = () => node.selectionId && selection.hover(node.selectionId);
  const leave = () => selection.hover("");
  return <li className="min-w-0">
    <TreeRowSurface ref={element => { if (element) rowRefs.current.set(node.id, element); else rowRefs.current.delete(node.id); }}
      dense active={active} className="group/row gap-0 pr-0" style={{ paddingLeft: depth * TREE_INDENT_PX, "--row-actions": ROW_ACTIONS_WIDTH }}
      onMouseEnter={enter} onMouseLeave={leave}>
      <TreeRowGuides depth={depth} column={16}/>
      {branch ? <button type="button" aria-label={`${open ? "Collapse" : "Expand"} ${node.label}`} aria-expanded={open}
        className="grid h-6 w-4 shrink-0 place-items-center rounded focus-visible:ring-2 focus-visible:ring-ring"
        onClick={() => toggle(node)}><TreeRowChevron expanded={open}/></button> : partGlyph}
      <button type="button" aria-label={`Select ${node.label}`} aria-pressed={active} disabled={!node.selectionId && !branch}
        onClick={event => (node.selectionId ? selection.select(node.selectionId, { multiSelect: event.ctrlKey || event.metaKey || event.shiftKey }) : toggle(node))}
        className={cn("flex h-full min-w-0 flex-1 items-center gap-1.5 rounded pr-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", rowName(node, hidden))}>
        <TreeRowLabel className="max-w-full shrink-0">{node.label}</TreeRowLabel>
        {node.detail && <TreeRowLabel className="flex-1 text-micro text-muted-foreground">{node.detail}</TreeRowLabel>}
      </button>
      <RowActions {...{ node, hidden, isolated, onHide, onIsolate }}/>
    </TreeRowSurface>
    {branch && open && <ul>{node.children.map(child => <XbfRow key={child.id} {...{ node: child, depth: depth + 1, expanded, toggle, selection, rowRefs, hidden, isolated, onHide, onIsolate }}/>)}</ul>}
  </li>;
}

function XbfSearchRow({ match, cursor, selection, hidden, isolated, onHide, onIsolate }) {
  const { entry, indices } = match, { node } = entry;
  const active = Boolean(node.selectionId) && selection.selected.includes(node.selectionId);
  const owners = entry.prefix.slice(0, -1);
  return <li className="min-w-0" data-search-row={node.id}>
    <TreeRowSurface dense active={active} cursor={cursor} className="group/row gap-0 pr-0" style={{ "--row-actions": ROW_ACTIONS_WIDTH }}
      onMouseEnter={() => node.selectionId && selection.hover(node.selectionId)} onMouseLeave={() => selection.hover("")}>
      <button type="button" aria-label={`Select ${node.label}`} aria-pressed={active} disabled={!node.selectionId}
        onClick={event => selection.select(node.selectionId, { multiSelect: event.ctrlKey || event.metaKey || event.shiftKey })}
        className={cn("flex h-full min-w-0 flex-1 items-center gap-1.5 rounded pl-2 pr-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", rowName(node, hidden))}>
        <TreeRowLabel className="max-w-full shrink-0"><TreeFilterHighlight indices={indices} text={entry.label}/></TreeRowLabel>
        {owners && <TreeRowLabel className="flex-1 text-micro text-muted-foreground">{owners}</TreeRowLabel>}
      </button>
      <RowActions {...{ node, hidden, isolated, onHide, onIsolate }}/>
    </TreeRowSurface>
  </li>;
}

/**
 * @param {{ structure: object | null, selection: ReturnType<typeof import("../kit/tools/select/useSceneSelection.js").useSceneSelection>,
 *   hidden: string[], onHiddenChange: (hidden: string[]) => void, active?: boolean, status?: import("react").ReactNode }} props
 *   `hidden`: the instance ids hidden now. `status`: a line under the tree (the last update's account).
 */
export default function XbfTreeSection({ structure, selection, hidden: hiddenIds, onHiddenChange, active = true, status = null }) {
  const tree = useMemo(() => buildXbfTree(structure), [structure]);
  const hidden = useMemo(() => new Set(hiddenIds), [hiddenIds]);
  const [userExpanded, setUserExpanded] = useState(null);
  // The root opens: a tree that opens on one row answers nothing.
  const expanded = userExpanded || new Set(tree.roots.map(root => root.id));
  const toggle = node => {
    const next = new Set(expanded);
    if (!next.delete(node.id)) next.add(node.id);
    setUserExpanded(next);
  };
  const [isolated, setIsolated] = useState(null);
  const everyPart = useMemo(() => tree.roots.flatMap(root => root.partIds), [tree]);
  const onHide = (ids, hide) => {
    setIsolated(null);
    const next = new Set(hidden);
    for (const id of ids) { if (hide) next.add(id); else next.delete(id); }
    onHiddenChange([...next]);
  };
  const onIsolate = (node) => {
    if (isolated === node.id) { setIsolated(null); onHiddenChange([]); return; }
    setIsolated(node.id);
    const keep = new Set(node.partIds);
    onHiddenChange(everyPart.filter(id => !keep.has(id)));
  };

  const { query, searching, deferredQuery, found, cursorId, listRef, changeQuery, onKeyDown } = useTreeSearch(tree.roots);
  const rowRefs = useRef(new Map());
  // A part picked in the view opens its owners and comes into sight.
  const target = selection.selected.at(-1) || "";
  useEffect(() => {
    const row = tree.byInstance.get(target);
    if (!active || !row || searching) return;
    const missing = xbfTreeAncestors(tree, row).filter(id => !expanded.has(id));
    if (missing.length) { setUserExpanded(new Set([...expanded, ...missing])); return; }
    const element = rowRefs.current.get(row);
    if (element) revealRow(element);
  }, [active, target, tree, expanded, searching]);

  return <ToolPanel id="xbf-tree" label="Document" fit="tree" resizable collapsible={false} hidden={!active}
    header={<TreeFilterInput label="Filter parts" placeholder="Filter…" value={query} onChange={changeQuery} onKeyDown={onKeyDown} dense className="px-0 shadow-none"/>}>
    <div className="flex flex-col text-tiny" aria-label="Document parts">
      <div ref={listRef} className="px-1 py-1" onClick={event => { if (!event.target.closest("li,button,input")) selection.clear(); }}>
        {searching ? found.matches.length
          ? <ul aria-label="Part search results">{found.matches.map(match => <XbfSearchRow key={match.entry.node.id} {...{ match, cursor: match.entry.node.id === cursorId, selection, hidden, isolated, onHide, onIsolate }}/>)}</ul>
          : deferredQuery.trim() && <p className="px-3 py-6 text-center text-tiny text-muted-foreground">{`No part matches “${deferredQuery.trim()}”`}</p>
          : tree.roots.length
            ? <ul aria-label="Document tree">{tree.roots.map(node => <XbfRow key={node.id} {...{ node, depth: 0, expanded, toggle, selection, rowRefs, hidden, isolated, onHide, onIsolate }}/>)}</ul>
            : <p role="status" className="p-2 text-tiny text-muted-foreground">{structure ? "This document places no parts." : "Loading document…"}</p>}
      </div>
      {status ? <p role="status" className="border-t border-sidebar-border/60 px-2 py-1 text-micro text-muted-foreground">{status}</p> : null}
    </div>
  </ToolPanel>;
}
