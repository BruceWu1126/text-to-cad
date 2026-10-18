import { useMemo, useState } from "react";
import { Box, Eye, EyeOff, Layers } from "lucide-react";
import { Button } from "@text-to-cad/ui/primitives/button";
import { TooltipHint } from "@text-to-cad/ui/primitives/tooltip";
import {
  ROW_NAME_UNDER_ACTIONS, TREE_GLYPH_STROKE, TREE_INDENT_PX, TREE_ROW_DENSE_ICON_CLASS, TreeRowChevron, TreeRowGuides, TreeRowLabel,
  TreeRowSurface, treeRowActionsClass
} from "@text-to-cad/ui/primitives/tree-row";
import { cn } from "@text-to-cad/ui/utils";
import { LAYER_GLYPH_COLUMN_CLASS, LAYER_ROW_INSET_PX, LayerSwatch } from "../kit/inspector/layerRows.jsx";
import { openUntilChoice } from "../kit/inspector/treeExpansion.js";
import ToolPanel from "../kit/tools/ToolPanel.jsx";
import { drawingReferenceId, layerRows } from "./drawingReferences.js";

// The room the eye takes at a row's right end: the row's `--row-actions`.
const ROW_ACTIONS_WIDTH = "1.5rem";
// A block reference hangs under its layer one level in, as a tree's child does.
const INSERT_INSET_PX = LAYER_ROW_INSET_PX + TREE_INDENT_PX;

/** A layer's Hide/Reveal eye, as a tree row's is: over the row's right end, on hover, kept while hidden. */
function LayerEye({ name, hidden, onToggle }) {
  const action = hidden ? "Reveal" : "Hide";
  return <div data-row-actions="" className={treeRowActionsClass(hidden)}>
    <TooltipHint content={action}>
      <Button type="button" variant="ghost" size="icon-xs" aria-label={`${action} layer ${name}`} aria-pressed={hidden} className="h-5 w-4 text-muted-foreground"
        data-layer-visibility={name} onClick={event => { event.stopPropagation(); onToggle(name); }}>
        {hidden ? <EyeOff className="size-3" aria-hidden="true" /> : <Eye className="size-3" aria-hidden="true" />}
      </Button>
    </TooltipHint>
  </div>;
}

/**
 * A drawing's **Layers**, a section of the file sidebar for a drawing with the structure to read
 * (`layersSectionShown`): each layer drawn on, in the drawing's order — its pen, its name, how many
 * entities drew on it, and its eye, which hides or shows it in the view (a hidden layer is not drawn
 * and nothing on it is picked or snapped to) — and under a layer, the block references placed on
 * it, each a row of its block's name that selects it as a click on the drawing does (Shift adds).
 * A row has one glyph, on the section title's line (`kit/inspector/layerRows.jsx`): a layer's pen,
 * or, for a layer with block references under it, its disclosure chevron.
 *
 * @param {{ drawing: import("@text-to-cad/core/lib/drawing2d/drawing.js").PreparedDrawing,
 *   hiddenLayers: ReadonlySet<string>, onToggleLayer(name: string): void,
 *   selection: ReturnType<typeof import("../kit/tools/select/useSceneSelection.js").useSceneSelection> }} props
 */
export default function LayersSection({ drawing, hiddenLayers, onToggleLayer, selection }) {
  const rows = useMemo(() => layerRows(drawing), [drawing]);
  // The layers are the drawing's, so they open as every tree's nodes do (`kit/inspector/treeExpansion.js`):
  // a layer with one block reference on it opens on it, one with several stays folded behind its
  // chevron. From the first layer the person opens or folds, the shape is theirs.
  const opened = useMemo(() => {
    const open = openUntilChoice([{ name: "", inserts: rows }], row => row.inserts, row => row.name);
    open.delete("");
    return open;
  }, [rows]);
  const [touched, setTouched] = useState(null);
  const shown = touched || opened;
  const chosen = useMemo(() => new Set(selection.selected), [selection.selected]);
  const toggle = name => setTouched(current => {
    const next = new Set(current || opened);
    if (!next.delete(name)) next.add(name);
    return next;
  });
  return <ToolPanel id="layers" title="Layers" label="Layers" fit="tree" resizable collapsible={false} icon={Layers}>
    <ul className="px-1 py-1 text-tiny" aria-label="Drawing layers" data-drawing-layers="">
      {rows.map(row => {
        const hidden = hiddenLayers.has(row.name), open = row.inserts.length > 0 && shown.has(row.name);
        return <li key={row.name} className="min-w-0">
          <TreeRowSurface dense className="group/row gap-0 pr-0" style={{ paddingLeft: LAYER_ROW_INSET_PX, "--row-actions": ROW_ACTIONS_WIDTH }} data-layer-row={row.name}>
            {row.inserts.length ? <button type="button" aria-label={`${open ? "Collapse" : "Expand"} ${row.name}`} aria-expanded={open}
              className={cn(LAYER_GLYPH_COLUMN_CLASS, "rounded focus-visible:ring-2 focus-visible:ring-ring")} onClick={() => toggle(row.name)} data-layer-glyph="chevron">
              <TreeRowChevron expanded={open} />
            </button> : <LayerSwatch color={row.color} />}
            <span className={cn("flex h-full min-w-0 flex-1 items-center gap-1.5 pl-1 pr-2", hidden && "text-muted-foreground",
              hidden ? ROW_NAME_UNDER_ACTIONS.shown : ROW_NAME_UNDER_ACTIONS.hover)}>
              <TreeRowLabel className="max-w-full shrink-0">{row.name}</TreeRowLabel>
              <TreeRowLabel className="flex-1 text-micro text-muted-foreground">{row.entities.toLocaleString("en-US")}</TreeRowLabel>
            </span>
            <LayerEye name={row.name} hidden={hidden} onToggle={onToggleLayer} />
          </TreeRowSurface>
          {open ? <ul>{row.inserts.map(insert => {
            const id = drawingReferenceId(insert.handle);
            return <li key={insert.handle} className="min-w-0">
              <TreeRowSurface dense active={chosen.has(id)} className="gap-0" style={{ paddingLeft: INSERT_INSET_PX }}
                onMouseEnter={() => selection.hover(id)} onMouseLeave={() => selection.hover("")}>
                <TreeRowGuides depth={1} inset={LAYER_ROW_INSET_PX} />
                <span className={LAYER_GLYPH_COLUMN_CLASS} aria-hidden="true">
                  <Box className={TREE_ROW_DENSE_ICON_CLASS} strokeWidth={TREE_GLYPH_STROKE} />
                </span>
                <button type="button" aria-label={`Select block ${insert.block || insert.handle}`} aria-pressed={chosen.has(id)} data-insert-row={insert.handle}
                  onClick={event => selection.select(id, { multiSelect: event.shiftKey || event.metaKey || event.ctrlKey })}
                  className="flex h-full min-w-0 flex-1 items-center gap-1.5 rounded pl-1 pr-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  <TreeRowLabel className="max-w-full shrink-0">{insert.block || "Block"}</TreeRowLabel>
                  <TreeRowLabel className="flex-1 text-micro text-muted-foreground">{insert.handle}</TreeRowLabel>
                </button>
              </TreeRowSurface>
            </li>;
          })}</ul> : null}
        </li>;
      })}
    </ul>
  </ToolPanel>;
}
