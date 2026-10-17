import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Clapperboard, FileText, ListTree, Spline } from "lucide-react";
import { ScrollArea } from "@text-to-cad/ui/primitives/scroll-area";
import { cn } from "@text-to-cad/ui/utils";
import { FILE_SIDEBAR_SURFACE_CLASS, FLOATING_CHROME_SURFACE_CLASS } from "../../../lib/floatingSurface.js";
import { ToolbarButton } from "@text-to-cad/ui/primitives/toolbar-button";
import { STRIP_BUTTON_CLASS } from "./stripTouch.js";
import { capturePointer, releasePointer } from "../../../lib/pointerCapture.js";
import ResizeGrip from "./ResizeGrip.jsx";
import { ToolStackContext } from "./ToolStack.jsx";
import { FILE_SIDEBAR_MIN_WIDTH, TOOL_PANEL_WIDTH, fileSidebarSizeId, clampToolPanelHeight, clampToolPanelWidth, normalizeToolStack } from "./toolStackLayout.js";

const KEY_NUDGE_PX = 16;
// A phone's panel: a joint's name and its slider side by side, within the view's inset.
const PHONE_PANEL_WIDTH = 256;

/**
 * The file's sidebar: what there is to read of the file, its hierarchy, properties and controls,
 * whatever tool is in hand. The toolbar is for using tools; this is for the file. It is a toolbar
 * of its own at the viewer's top-left, one tile per section (`ToolPanel`s drawn as sections,
 * `ToolStackContext`'s `sidebar`): a tree by its name (**Features**, **Links**: a tree icon, its
 * search box heading the panel), **Position**
 * for a model's joints, **Animation** for its routines, and any other section by its name. None is open as a file opens; a tile
 * opens its section in a panel under the toolbar, a second press on it closes it, and so does the
 * section's own collapse button; another tile opens its section instead. One section at a time.
 * The panel is read for as long as it is open, so it has its own surface (`FILE_SIDEBAR_SURFACE_CLASS`):
 * lighter than a menu's, heavier than the strip's. Every section stays mounted, closed or not, so
 * a tree keeps its expansion, filter and scroll.
 *
 * The open section is its content's height, up to the panel's cap — the room under the toolbar
 * within the viewer's inset, unless the person has set one — and past that it scrolls. A tree's
 * rows are mounted as the panel's scroll brings them into view (`VirtualRows.jsx`); the section's
 * head — its heading and, for a tree, its filter — pins to the top.
 *
 * Its size is the person's, from its bottom-right corner (Quick Edit's grip, as a resizable panel
 * has): width and cap together, by pointer, or by keyboard (arrows by 16px; Home and End to the
 * bounds), written back once a gesture lets go — each section's own (`layout.panels["sidebar:<id>"]`). It opens a tool panel's
 * width, and may be made narrower than that (`FILE_SIDEBAR_MIN_WIDTH`).
 *
 * On a phone the tiles are the tool strip's first group, there being no room beside it for a
 * toolbar of their own: `onTiles` hands them to the frame, which draws them there (each `{ id,
 * label, Icon, active, onSelect }`), and this draws the panel alone, under the strip, at a phone's
 * own width — wide enough for a joint's name beside its slider, not a desktop's size — with no grip.
 *
 * `sections` limits the sections on offer to those ids (the Studio's: Position and Animation; the
 * rest stay mounted, out of sight), and `bare` draws its toolbar's tiles with no surface under
 * them, the model showing through, as the Studio's corners are.
 *
 * @param {{ layout: { panels: object, collapsed: object },
 *   onLayoutChange(patch: object | ((layout: object) => object)): void, hidden?: boolean,
 *   sections?: string[] | null, bare?: boolean, onTiles?: ((tiles: object[]) => void) | null,
 *   style?: import("react").CSSProperties, children?: import("react").ReactNode }} props
 *   `style` places the room the toolbar and its panel may take; they hang from its top-left corner.
 */
export default function FileSidebar({ layout: stored, onLayoutChange, hidden = false, sections: allowedSections = null, bare = false,
  onTiles = null, style, children }) {
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
    const heads = [...(column.current?.querySelectorAll(":scope > section:not([hidden]):not([data-sidebar-closed]) > [data-sidebar-head]") ?? [])];
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
  const ids = order ? order.split(" ") : [];
  // The open section: none as a file opens, and none once the one open is gone.
  const [openId, setOpenId] = useState(null);
  const open = openId !== null && ids.includes(openId) ? openId : null;
  // The section the panel shows: the open one, and while the panel slides away, the one it held.
  const lastOpen = useRef(null);
  if (open) lastOpen.current = open;
  const shown = open ?? (ids.includes(lastOpen.current) ? lastOpen.current : null);
  // Each section's panel has a size of its own, the person's, kept under its id.
  const sizeId = fileSidebarSizeId(shown);
  const size = { ...layout.panels[sizeId], ...draft };
  const phone = Boolean(onTiles);
  const width = phone ? Math.min(PHONE_PANEL_WIDTH, (measured.viewer || window.innerWidth) - 16) : clampWidth(size.width ?? TOOL_PANEL_WIDTH);
  const cap = size.height !== undefined ? clampHeight(size.height) : measured.room || null;
  const closeSection = useCallback(() => setOpenId(null), []);
  // What each section's tile says, as the section reports it (`register`, from `ToolPanel.jsx`).
  const meta = useRef(new Map());
  const [metaTick, setMetaTick] = useState(0);
  const register = useCallback((id, next) => {
    const current = meta.current.get(id);
    if (current && current.title === next.title && current.fit === next.fit) return;
    meta.current.set(id, next);
    setMetaTick(tick => tick + 1);
  }, []);
  const sections = useMemo(() => ({
    sidebar: true,
    reveal,
    // Which sections are on offer (the Studio's: Position and Animation); null, every one.
    allowed: allowedSections ? id => allowedSections.includes(id) : null,
    openSection: shown,
    closeSection,
    register,
    viewerWidth: measured.viewer,
    size: () => null,
    defaultHeight: () => null,
    collapsed: (id, fallback) => layout.collapsed[id] ?? fallback,
    settle,
    room: () => room.current?.clientHeight || 0,
  }), [reveal, shown, closeSection, register, measured.viewer, layout, settle, allowedSections]);
  const tiles = ids.map(id => {
    const section = meta.current.get(id) || {};
    const tile = section.fit === "tree" ? { label: section.title || "Tree", Icon: ListTree }
      : id === "position" ? { label: "Position", Icon: Spline }
        : id === "animation" ? { label: "Animation", Icon: Clapperboard }
        : { label: section.title || id, Icon: FileText };
    return { id, ...tile };
  });
  void metaTick;
  // A press of a tile: its section opens, or closes if it was the open one.
  const toggle = useCallback(id => setOpenId(current => (current === id ? null : id)), []);
  const tileKey = tiles.map(tile => `${tile.id}:${tile.label}`).join(" ");
  useEffect(() => {
    onTiles?.(tiles.map(({ id, label, Icon }) => ({ id, label, Icon, active: open === id, onSelect: () => toggle(id) })));
    // `tileKey` stands for `tiles`, rebuilt each render.
  }, [onTiles, tileKey, open, toggle]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => onTiles?.([]), [onTiles]);

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
    if (current.next) settle(sizeId, current.next);
  };
  const keyDrag = event => {
    const from = drawn(), change = {};
    const nextWidth = { ArrowLeft: from.width - KEY_NUDGE_PX, ArrowRight: from.width + KEY_NUDGE_PX, Home: 0, End: Infinity }[event.key];
    const nextHeight = { ArrowUp: from.height - KEY_NUDGE_PX, ArrowDown: from.height + KEY_NUDGE_PX, Home: 0, End: Infinity }[event.key];
    if (nextWidth !== undefined) change.width = clampWidth(nextWidth);
    if (nextHeight !== undefined) change.height = clampHeight(nextHeight);
    if (!Object.keys(change).length) return;
    event.preventDefault();
    settle(sizeId, change);
  };

  const aside = <aside ref={bar} aria-label="File" data-cad-file-sidebar="" data-open={open ? "" : undefined} inert={!open}
    className={cn("relative flex max-w-full flex-col overflow-hidden text-tiny transition-[translate,opacity] duration-200 ease-out motion-reduce:transition-none",
      "rounded-md", FILE_SIDEBAR_SURFACE_CLASS,
      open ? "pointer-events-auto" : "pointer-events-none -translate-x-[calc(100%+1rem)] opacity-0")}
    style={{ width, maxHeight: cap ?? undefined }}>
    <ScrollArea className="min-h-0 flex-1" viewportRef={scroller} viewportProps={{ "data-file-sidebar-scroller": "" }}>
      {/* The section's head needs no rule above it: the panel's edge is there. */}
      <div ref={column} className="flex flex-col [&>section>[data-sidebar-head]]:border-t-0">
        <ToolStackContext.Provider value={sections}>{children}</ToolStackContext.Provider>
      </div>
    </ScrollArea>
    {phone ? null : <ResizeGrip corner="bottom-right" role="separator" tabIndex={0} aria-label="Resize file sidebar"
      data-file-sidebar-corner="" data-dragging={draft !== null ? "" : undefined}
      onPointerDown={startDrag} onPointerMove={moveDrag} onPointerUp={stopDrag} onPointerCancel={stopDrag} onKeyDown={keyDrag} />}
  </aside>;
  return <>
    <div hidden={hidden} className="pointer-events-none absolute z-20 flex flex-col items-start gap-2" style={style} data-file-sidebar-room="">
      {/* The sidebar's toolbar: the tool strip's surface, padding and tiles, on the strip's line. */}
      {tiles.length && !onTiles ? <div role="group" aria-label="File sidebar" data-file-sidebar-tools=""
        className={cn("pointer-events-auto inline-flex shrink-0 items-center gap-1 rounded-md p-1", bare ? "border border-transparent" : FLOATING_CHROME_SURFACE_CLASS)}>
        {tiles.map(({ id, label, Icon }) => <ToolbarButton key={id} label={label} tooltipSide="bottom" className={STRIP_BUTTON_CLASS}
          active={open === id} aria-pressed={open === id} onClick={() => toggle(id)} data-file-sidebar-tool={id}>
          <Icon aria-hidden="true" />
        </ToolbarButton>)}
      </div> : null}
      {/* The panel under it, the open section's; closed, its sections stay mounted out of sight. It
          slides in from the left as a section opens with none open before, and back out as the last
          one closes; from one section to another it stays where it is. */}
      <div ref={room} className="relative min-h-0 w-full flex-1">{aside}</div>
    </div>
  </>;
}
