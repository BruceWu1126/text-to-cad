import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";
import { PanelLeft } from "lucide-react";
import { ScrollArea } from "@text-to-cad/ui/primitives/scroll-area";
import { cn } from "@text-to-cad/ui/utils";
import { FLOATING_SURFACE_CLASS } from "../../../lib/floatingSurface.js";
import { capturePointer, releasePointer } from "../../../lib/pointerCapture.js";
import ResizeGrip from "./ResizeGrip.jsx";
import { TOOL_PANEL_BUTTON_CLASS } from "./ToolPanel.jsx";
import { ToolStackContext } from "./ToolStack.jsx";
import { VIEWPORT_INSET_PX } from "../shell/viewportLayout.js";
import { FILE_SIDEBAR_ID, FILE_SIDEBAR_MIN_WIDTH, TOOL_PANEL_WIDTH, clampToolPanelHeight, clampToolPanelWidth, normalizeToolStack } from "./toolStackLayout.js";

const KEY_NUDGE_PX = 16;
// How far the bar's toggle sits in from the bar's top edge on its first heading: the border, the
// head's 4px inset, and the 20px button centred in the 24px row. Put away, the toggle that brings
// it back is that far down from the corner the bar hung from, and centred under the navbar's logo
// above it: its 24px button 8px in from the viewer's left (`NAVBAR_ROW_CLASS`'s padding), so 20px
// to its centre, where the bar's room starts `VIEWPORT_INSET_PX` in.
const TOGGLE_INSET_PX = 7, TOGGLE_PX = 20;
const NAVBAR_LOGO_CENTRE_PX = 8 + 24 / 2;
const SHOW_TOGGLE_LEFT_PX = NAVBAR_LOGO_CENTRE_PX - VIEWPORT_INSET_PX - TOGGLE_PX / 2;
/**
 * The file's sidebar: one solid floating bar at the viewer's top-left, holding what there is to
 * read of the file, its hierarchy, properties and controls, whatever tool is in hand. The toolbar is for using tools; this is for the file. Each
 * of its sections is a `ToolPanel` drawn as a section (`ToolStackContext`'s `sidebar`): a heading
 * row that folds it at a press anywhere on it (a tree row in shape), then its body. It is read for as long as a file is
 * open, so it has the opaque surface (`FLOATING_SURFACE_CLASS`).
 *
 * Each section is its content's height (a folded one its heading alone), and none scrolls on its
 * own: the bar is as tall as its sections, up to its cap — the viewer's full height within its
 * inset, unless the person has set one — and past that the whole bar scrolls, as one. A tree's rows are mounted
 * as the bar's scroll brings them into view (`VirtualRows.jsx`). The heading of a section below the
 * view docks at the bar's bottom, over those of the sections after it (`dock`), so every section
 * is a press away: a press on a docked heading opens its section, if it is folded, and brings it
 * to the top of the view at once (`reveal`). The head of the section the view is in — its heading
 * and, for a tree, its filter — pins to the top until the next one pushes it off. The pinned and
 * docked heads are the scroller's padding, so a row revealed in a section (`revealRow`) is never
 * left under them. A section folds
 * only where there are others: one alone is its heading and its body.
 *
 * The whole bar slides out to the left and back (`layout.collapsed.sidebar`): its toggle ends the
 * first section's heading, and while it is away the same toggle, bare, is in the top-left corner.
 *
 * Its size is the person's, from its bottom-right corner (Quick Edit's grip, as a resizable panel
 * has): width and cap together, by pointer, or by keyboard (arrows by 16px; Home and End to the
 * bounds), written back once a gesture lets go (`layout.panels.sidebar`). It opens a tool panel's
 * width, and may be made narrower than that (`FILE_SIDEBAR_MIN_WIDTH`). Which sections are folded is the person's too, by section id, across files
 * (`layout.collapsed`).
 *
 * @param {{ layout: { panels: object, collapsed: object },
 *   onLayoutChange(patch: object | ((layout: object) => object)): void, hidden?: boolean,
 *   style?: import("react").CSSProperties, children?: import("react").ReactNode }} props
 *   `style` places the room the bar may take; the bar hangs from its top-left corner.
 */
export default function FileSidebar({ layout: stored, onLayoutChange, hidden = false, style, children }) {
  const layout = useMemo(() => normalizeToolStack(stored), [stored]);
  const room = useRef(null), bar = useRef(null), scroller = useRef(null), column = useRef(null);
  // The room's height, the most the bar may take, and the viewer's width, which bounds the bar's.
  const [measured, setMeasured] = useState({ room: 0, viewer: 0 });
  useLayoutEffect(() => {
    const element = room.current;
    if (!element) return undefined;
    const viewer = element.closest("[data-cad-scene-backdrop]");
    const measure = () => setMeasured(current => {
      const next = { room: element.clientHeight, viewer: Math.round(viewer?.getBoundingClientRect().width || 0) };
      return current.room === next.room && current.viewer === next.viewer ? current : next;
    });
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    if (viewer) observer.observe(viewer);
    return () => observer.disconnect();
  }, []);
  const clampWidth = value => clampToolPanelWidth(value, measured.viewer || window.innerWidth, FILE_SIDEBAR_MIN_WIDTH);
  const clampHeight = value => clampToolPanelHeight(value, measured.room || Infinity);
  const [draft, setDraft] = useState(null);
  const size = { ...layout.panels[FILE_SIDEBAR_ID], ...draft };
  const width = clampWidth(size.width ?? TOOL_PANEL_WIDTH);
  const cap = size.height !== undefined ? clampHeight(size.height) : measured.room || null;

  const open = !layout.collapsed[FILE_SIDEBAR_ID];
  const settle = useCallback((id, change) => onLayoutChange(current => {
    const patch = {};
    if (change.width !== undefined || change.height !== undefined) {
      const next = { ...current.panels[id] };
      if (change.width !== undefined) next.width = change.width;
      if (change.height !== undefined) next.height = change.height;
      patch.panels = { ...current.panels, [id]: next };
    }
    if (change.collapsed !== undefined) {
      const next = { ...current.collapsed };
      if (change.collapsed === Boolean(change.fallback)) delete next[id]; else next[id] = change.collapsed;
      patch.collapsed = next;
    }
    return patch;
  }), [onLayoutChange]);
  // Each section's head (`ToolPanel.jsx`'s, its heading and header) at its anchor, just before it.
  // Below the view, a head docks at the bottom over the heads after it: each sticks that far up
  // (`data-stuck`, drawn above its anchor). The head of the section the view is in pins to the
  // top (`data-pinned`, its anchor above the view), until the next head arrives and pushes it off.
  // The pinned and docked heads pad the scroller, so what is scrolled into view clears them.
  const dock = useCallback(() => {
    const element = scroller.current;
    const heads = [...(column.current?.querySelectorAll(":scope > section:not([hidden]) > [data-sidebar-head]") ?? [])];
    if (!element || !heads.length) return;
    const viewTop = element.getBoundingClientRect().top;
    const anchorTop = head => head.previousElementSibling?.getBoundingClientRect().top ?? 0;
    // The section the view is in: the last whose anchor has reached the top.
    const current = heads.findLastIndex(head => anchorTop(head) <= viewTop + 0.5);
    let below = 0, docked = 0, pinned = 0;
    for (let index = heads.length - 1; index >= 0; index -= 1) {
      const head = heads[index];
      head.style.bottom = `${below}px`;
      below += head.offsetHeight;
      head.style.top = index === current ? "0px" : "auto";
      const push = index === current && heads[index + 1] ? Math.min(0, anchorTop(heads[index + 1]) - viewTop - head.offsetHeight) : 0;
      head.style.translate = push ? `0 ${push}px` : "";
      head.toggleAttribute("data-pinned", index === current && anchorTop(head) < viewTop - 0.5);
      if (index === current) pinned = Math.max(0, head.offsetHeight + push);
      const stuck = anchorTop(head) > head.getBoundingClientRect().top + 0.5;
      head.toggleAttribute("data-stuck", stuck);
      if (stuck) docked += head.offsetHeight;
    }
    element.style.scrollPaddingTop = `${pinned}px`;
    element.style.scrollPaddingBottom = `${docked}px`;
  }, []);
  useLayoutEffect(dock);
  // Which sections are on screen, in order: the first carries the bar's own button, and one alone
  // does not fold.
  const [order, setOrder] = useState("");
  useLayoutEffect(() => {
    const ids = [...(column.current?.querySelectorAll(":scope > section:not([hidden])") ?? [])]
      .map(section => section.getAttribute("data-tool-panel-id") || "").join(" ");
    if (ids !== order) setOrder(ids);
  });
  useLayoutEffect(() => {
    const element = scroller.current;
    if (!element) return undefined;
    element.addEventListener("scroll", dock, { passive: true });
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(dock);
    observer?.observe(element);
    if (column.current) observer?.observe(column.current);
    return () => { element.removeEventListener("scroll", dock); observer?.disconnect(); };
  }, [dock]);
  // A section brought into view, its heading at the top, at once: now, or once the unfolding that
  // goes with it has been drawn (`unfolding`).
  const pending = useRef(null);
  const snap = anchor => {
    const element = scroller.current;
    if (!element || !anchor?.isConnected) return;
    element.scrollTop += anchor.getBoundingClientRect().top - element.getBoundingClientRect().top;
  };
  const reveal = useCallback((anchor, { unfolding = false } = {}) => { if (unfolding) pending.current = anchor; else snap(anchor); }, []);
  useLayoutEffect(() => {
    if (!pending.current) return;
    snap(pending.current);
    pending.current = null;
  });
  const toggleOpen = useCallback(() => settle(FILE_SIDEBAR_ID, { collapsed: open, fallback: false }), [settle, open]);
  const ids = order ? order.split(" ") : [];
  const sections = useMemo(() => ({
    sidebar: true,
    reveal,
    // Folding needs another section to give the room to; the bar's own button rides the first.
    sectionsFold: ids.length > 1,
    firstSection: ids[0] ?? null,
    closeSidebar: toggleOpen,
    viewerWidth: measured.viewer,
    size: () => null,
    defaultHeight: () => null,
    collapsed: (id, fallback) => layout.collapsed[id] ?? fallback,
    settle,
    room: () => room.current?.clientHeight || 0,
  }), [reveal, order, toggleOpen, measured.viewer, layout, settle]);

  // The corner moves both: the width and the cap, from the size on screen.
  const drag = useRef(null);
  const drawn = () => {
    const box = bar.current?.getBoundingClientRect();
    return { width: box?.width || width, height: box?.height || cap || 0 };
  };
  const startDrag = event => {
    if (event.button !== 0) return;
    event.preventDefault();
    drag.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, from: drawn(), next: null };
    capturePointer(event.currentTarget, event.pointerId);
  };
  const moveDrag = event => {
    const current = drag.current;
    if (current?.pointerId !== event.pointerId) return;
    current.next = { width: clampWidth(current.from.width + event.clientX - current.x), height: clampHeight(current.from.height + event.clientY - current.y) };
    setDraft(current.next);
  };
  const stopDrag = event => {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    drag.current = null;
    releasePointer(event.currentTarget, event.pointerId);
    setDraft(null);
    if (current.next) settle(FILE_SIDEBAR_ID, current.next);
  };
  const keyDrag = event => {
    const from = drawn(), change = {};
    const nextWidth = { ArrowLeft: from.width - KEY_NUDGE_PX, ArrowRight: from.width + KEY_NUDGE_PX, Home: 0, End: Infinity }[event.key];
    const nextHeight = { ArrowUp: from.height - KEY_NUDGE_PX, ArrowDown: from.height + KEY_NUDGE_PX, Home: 0, End: Infinity }[event.key];
    if (nextWidth !== undefined) change.width = clampWidth(nextWidth);
    if (nextHeight !== undefined) change.height = clampHeight(nextHeight);
    if (!Object.keys(change).length) return;
    event.preventDefault();
    settle(FILE_SIDEBAR_ID, change);
  };

  return <div ref={room} hidden={hidden} className="pointer-events-none absolute z-20 @max-5xl/cad-viewport:top-[var(--cad-tools-bottom,3.125rem)]! @max-5xl/cad-viewport:w-[calc(50%-0.75rem)]" style={style} data-file-sidebar-room="">
    {/* Put away, the bar leaves its toggle in the corner it hung from, under the navbar's logo, bare
        and the same size; it fades in as the bar slides out. */}
    <button type="button" aria-label="Show file sidebar" inert={open} data-file-sidebar-open=""
      className={cn(TOOL_PANEL_BUTTON_CLASS, "absolute transition-opacity duration-200 motion-reduce:transition-none",
        open ? "pointer-events-none opacity-0" : "pointer-events-auto opacity-100")}
      style={{ top: TOGGLE_INSET_PX, left: SHOW_TOGGLE_LEFT_PX }}
      onClick={toggleOpen}><PanelLeft className="size-3" aria-hidden="true" /></button>
    {/* Away, it slides out past the viewer's left edge, and nothing in it can be reached. */}
    <aside ref={bar} aria-label="File" data-cad-file-sidebar="" data-open={open ? "" : undefined} inert={!open}
      className={cn("relative flex max-w-full flex-col overflow-hidden rounded-md text-tiny transition-[translate,opacity] duration-200 ease-out motion-reduce:transition-none",
        FLOATING_SURFACE_CLASS, open ? "pointer-events-auto" : "pointer-events-none -translate-x-[calc(100%+1rem)] opacity-0")}
      style={{ width, maxHeight: cap ?? undefined }}>
      <ScrollArea className="min-h-0 flex-1" viewportRef={scroller} viewportProps={{ "data-file-sidebar-scroller": "" }}>
        {/* The first section's head needs no rule above it: the bar's edge is there. */}
        <div ref={column} className="flex flex-col [&>section:first-child>[data-sidebar-head]]:border-t-0">
          <ToolStackContext.Provider value={sections}>{children}</ToolStackContext.Provider>
        </div>
      </ScrollArea>
      <ResizeGrip corner="bottom-right" role="separator" tabIndex={0} aria-label="Resize file sidebar"
        data-file-sidebar-corner="" data-dragging={draft !== null ? "" : undefined}
        onPointerDown={startDrag} onPointerMove={moveDrag} onPointerUp={stopDrag} onPointerCancel={stopDrag} onKeyDown={keyDrag} />
    </aside>
  </div>;
}
