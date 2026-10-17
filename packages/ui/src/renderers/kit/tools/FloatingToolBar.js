import { Fragment, useLayoutEffect, useRef, useState } from "react";
import { Ellipsis } from "lucide-react";
import {
  DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger
} from "@text-to-cad/ui/primitives/dropdown-menu";
import { ToolbarButton } from "@text-to-cad/ui/primitives/toolbar-button";
import { cn } from "@text-to-cad/ui/utils";
import { FLOATING_CHROME_SURFACE_CLASS, FLOATING_SURFACE_CLASS } from "../../../lib/floatingSurface.js";
import { STRIP_BUTTON_CLASS, StripTouchProvider, TOUCH_STRIP_BUTTON_CLASS } from "./stripTouch.js";

export { STRIP_BUTTON_CLASS, StripTouchProvider, TOUCH_STRIP_BUTTON_CLASS, useStripButtonClass, useStripTouch } from "./stripTouch.js";

/**
 * One tool of the strip. The strip draws it; whoever hands it over decides what
 * it is, when it exists and what pressing it does. A tool with settings (Select's filter,
 * Animation's routines) has a small chevron right after its button that opens them (`menu`, a
 * `ToolSettingsMenu` with its "chevron" trigger), as a design tool's toolbar has it.
 *
 * @typedef {object} ViewportTool
 * @property {string} id
 * @property {string} label  The button's accessible name and tooltip.
 * @property {import("react").ReactNode} icon
 * @property {boolean} [active]
 * @property {boolean} [disabled]
 * @property {() => void} onSelect  Every press of the button.
 * @property {string} [description]  `aria-description`.
 * @property {import("react").ReactNode} [menu]  Its modes: the chevron after its button, and the menu it opens.
 */

/**
 * One of the view's controls after the strip's rule (Display, the Studio): its tile as drawn on the
 * strip (`tile`), and what it is as a row of the touch strip's More menu, when there is no room for
 * it: `onSelect` for a press, or `popover` for a control whose tile opens a popover, which then
 * opens from More itself (`anchor`, the More button's ref).
 *
 * @typedef {object} ViewportViewControl
 * @property {string} id
 * @property {string} label
 * @property {import("react").ReactNode} icon
 * @property {boolean} [disabled]
 * @property {boolean} [active]  A control that stays on (a phone's view control): a ticked row in More.
 * @property {import("react").ReactNode} tile
 * @property {() => void} [onSelect]
 * @property {(props: { anchor: import("react").RefObject<HTMLElement>, open: boolean, onOpenChange(open: boolean): void }) => import("react").ReactNode} [popover]
 */

// A plain function, not a component: the strip's own output is the buttons. A tool with a menu is
// its button as every tool's, then the menu's small chevron, a button of its own with its own
// hover, as a design tool's toolbar has it (`ToolSettingsMenu`'s "chevron" trigger).
function toolButton(tool, className = STRIP_BUTTON_CLASS) {
  const active = tool.active === true;
  const button = <ToolbarButton tooltipSide="top" label={tool.label} active={active} disabled={tool.disabled}
    className={className} aria-pressed={active} aria-description={tool.description} onClick={() => tool.onSelect()}>
    {tool.icon}
  </ToolbarButton>;
  if (!tool.menu) return button;
  return <span className="inline-flex items-center" data-tool-split="">{button}{tool.menu}</span>;
}

const GROUP_CLASS = `pointer-events-auto inline-flex min-h-8 max-w-full items-center gap-1 rounded-md p-1 ${FLOATING_CHROME_SURFACE_CLASS}`;
const rule = <span role="separator" aria-orientation="vertical" className="mx-0.5 h-4 w-px shrink-0 bg-border" data-strip-rule="" />;


/**
 * The interaction tools: a dumb strip positioned by the viewport shell. It renders the
 * tools it is handed, left to right, then, after a thin rule, the view's own controls (`views`:
 * a 3D view's Display and the Studio), which are no tools: pressing one leaves the tool in hand as it
 * is. It holds no state and knows no tool by name. On a phone (`touch`) it is one row of
 * touch-sized tiles, and what does not fit goes, from its end, into a More menu (`TouchStrip`).
 *
 * @param {{ tools: ViewportTool[], views?: ViewportViewControl[], touch?: boolean }} props
 */
export default function FloatingToolBar({ tools = [], views = [], touch = false }) {
  // Nothing to hold, no strip: an empty bar is never drawn.
  if (!tools.length && !views.length) return null;
  if (touch) return <TouchStrip tools={tools} views={views} />;
  return (<div className="relative z-20 flex max-w-full shrink-0 flex-col items-end gap-1" data-cad-toolbar="tools">
      <div role="group" aria-label="Interaction tools" className={cn(GROUP_CLASS, "flex-wrap")}>
        {tools.map(tool => <Fragment key={tool.id}>{toolButton(tool)}</Fragment>)}
        {tools.length && views.length ? rule : null}
        {views.length ? <span className="inline-flex items-center gap-1" data-view-controls="">
          {views.map(view => <Fragment key={view.id}>{view.tile}</Fragment>)}
        </span> : null}
      </div>
  </div>);
}

// The touch strip's geometry, as its classes draw it: 36px tiles 4px apart (`gap-1`), a tool's
// chevron 20px and touching its button, the rule 1px with 2px each side, and 4px of padding inside
// a 1px border.
const TILE_PX = 36, CHEVRON_PX = 20, GAP_PX = 4, RULE_PX = 5, FRAME_PX = 10;

/**
 * How many of the strip's entries (its tools, then its view controls) fit, in order, in `room`
 * pixels: all of them, or as many as leave room for the More button that holds the rest.
 * @param {{ width: number, view: boolean }[]} entries
 */
export function touchStripFit(entries, room) {
  const width = (count, more) => {
    const shown = entries.slice(0, count);
    const items = shown.length + (more ? 1 : 0);
    const ruled = shown.some(entry => !entry.view) && shown.some(entry => entry.view);
    return FRAME_PX + shown.reduce((sum, entry) => sum + entry.width, 0) + (more ? TILE_PX : 0)
      + Math.max(0, items - 1) * GAP_PX + (ruled ? RULE_PX + GAP_PX : 0);
  };
  if (width(entries.length, false) <= room) return entries.length;
  let count = entries.length - 1;
  while (count > 0 && width(count, true) > room) count -= 1;
  return count;
}

/**
 * A phone's strip: one row of 36px tiles across the view, and when they do not all fit, the last
 * of them — the view's controls first, then the tools from the end — are the rows of a More menu
 * (an ellipsis, last on the strip). A tool there is a checkbox row, ticked while it is in hand; a
 * view control is its press, or its popover, which then opens from More.
 */
function TouchStrip({ tools, views }) {
  const root = useRef(null);
  const more = useRef(null);
  const [room, setRoom] = useState(Infinity);
  const [openView, setOpenView] = useState(null);
  const pending = useRef(null);
  useLayoutEffect(() => {
    // The room is the column the strip is centred in, which spans the view within its inset.
    const column = root.current?.closest("[data-cad-tool-groups]") || root.current?.parentElement;
    if (!column) return undefined;
    const measure = () => setRoom(column.clientWidth || Infinity);
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(column);
    return () => observer.disconnect();
  }, []);
  const entries = [
    ...tools.map(tool => ({ tool, view: false, width: TILE_PX + (tool.menu ? CHEVRON_PX : 0) })),
    ...views.map(view => ({ control: view, view: true, width: TILE_PX }))
  ];
  const count = touchStripFit(entries, room);
  const shown = entries.slice(0, count), hidden = entries.slice(count);
  const shownTools = shown.filter(entry => !entry.view), shownViews = shown.filter(entry => entry.view);
  const overflowViews = hidden.filter(entry => entry.view).map(entry => entry.control);
  const popoverView = overflowViews.find(view => view.id === openView && view.popover);
  return <StripTouchProvider value={true}>
    <div ref={root} className="relative z-20 flex max-w-full shrink-0 flex-col items-end gap-1" data-cad-toolbar="tools" data-touch-strip="">
      <div role="group" aria-label="Interaction tools" className={cn(GROUP_CLASS, "flex-nowrap")}>
        {shownTools.map(({ tool }) => <Fragment key={tool.id}>{toolButton(tool, TOUCH_STRIP_BUTTON_CLASS)}</Fragment>)}
        {shownTools.length && shownViews.length ? rule : null}
        {shownViews.length ? <span className="inline-flex items-center gap-1" data-view-controls="">
          {shownViews.map(({ control }) => <Fragment key={control.id}>{control.tile}</Fragment>)}
        </span> : null}
        {hidden.length ? <DropdownMenu modal={false}>
          <DropdownMenuTrigger asChild>
            <ToolbarButton ref={more} tooltipSide="top" label="More tools" className={TOUCH_STRIP_BUTTON_CLASS} data-strip-more="">
              <Ellipsis aria-hidden="true" />
            </ToolbarButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" sideOffset={6} collisionPadding={8} aria-label="More tools"
            className={cn(FLOATING_SURFACE_CLASS, "w-44 data-[state=closed]:animate-none!")}
            onCloseAutoFocus={event => {
              // A popover chosen here opens from More once the menu is gone, keeping the focus.
              if (!pending.current) return;
              event.preventDefault();
              setOpenView(pending.current);
              pending.current = null;
            }}>
            {hidden.map(entry => entry.view
              ? entry.control.active !== undefined
                ? <DropdownMenuCheckboxItem key={entry.control.id} checked={entry.control.active === true} disabled={entry.control.disabled}
                  className="min-h-9 gap-2 text-ui" onSelect={() => entry.control.onSelect?.()}>
                  <span className="flex size-4.5 items-center justify-center [&>svg]:size-4.5" aria-hidden="true">{entry.control.icon}</span>{entry.control.label}
                </DropdownMenuCheckboxItem>
                : <DropdownMenuItem key={entry.control.id} disabled={entry.control.disabled} className="min-h-9 gap-2 text-ui"
                onSelect={() => { if (entry.control.popover) pending.current = entry.control.id; else entry.control.onSelect?.(); }}>
                <span className="flex size-4.5 items-center justify-center [&>svg]:size-4.5" aria-hidden="true">{entry.control.icon}</span>{entry.control.label}
              </DropdownMenuItem>
              : <DropdownMenuCheckboxItem key={entry.tool.id} checked={entry.tool.active === true} disabled={entry.tool.disabled}
                className="min-h-9 gap-2 text-ui" onSelect={() => entry.tool.onSelect()}>
                <span className="flex size-4.5 items-center justify-center [&>svg]:size-4.5" aria-hidden="true">{entry.tool.icon}</span>{entry.tool.label}
              </DropdownMenuCheckboxItem>)}
          </DropdownMenuContent>
        </DropdownMenu> : null}
        {popoverView ? popoverView.popover({ anchor: more, open: true, onOpenChange: open => { if (!open) setOpenView(null); } }) : null}
      </div>
    </div>
  </StripTouchProvider>;
}
