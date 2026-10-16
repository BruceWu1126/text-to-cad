import { Fragment } from "react";
import { ToolbarButton } from "@text-to-cad/ui/primitives/toolbar-button";
import { FLOATING_CHROME_SURFACE_CLASS } from "../../../lib/floatingSurface.js";

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

// The strip's tiles: 24px, their icons 14px (a little over half the tile), whatever size the icon
// was handed at. The view's controls after its tools (Display, Preview) are its tiles too.
export const STRIP_BUTTON_CLASS = "size-6 [&>svg]:size-3.5!";

// A plain function, not a component: the strip's own output is the buttons. A tool with a menu is
// its button as every tool's, then the menu's small chevron, a button of its own with its own
// hover, as a design tool's toolbar has it (`ToolSettingsMenu`'s "chevron" trigger).
function toolButton(tool) {
  const active = tool.active === true;
  const button = <ToolbarButton tooltipSide="top" label={tool.label} active={active} disabled={tool.disabled}
    className={STRIP_BUTTON_CLASS} aria-pressed={active} aria-description={tool.description} onClick={() => tool.onSelect()}>
    {tool.icon}
  </ToolbarButton>;
  if (!tool.menu) return button;
  return <span className="inline-flex items-center" data-tool-split="">{button}{tool.menu}</span>;
}

/**
 * The interaction tools: a dumb strip positioned by the viewport shell. It renders the
 * tools it is handed, left to right, then, after a thin rule, the view's own controls (`trailing`:
 * a 3D view's Display and Preview), which are no tools: pressing one leaves the tool in hand as it
 * is. It holds no state and knows no tool by name.
 *
 * @param {{ tools: ViewportTool[], trailing?: import("react").ReactNode }} props
 */
export default function FloatingToolBar({ tools = [], trailing = null }) {
  // Nothing to hold, no strip: an empty bar is never drawn.
  if (!tools.length && !trailing) return null;
  return (<div className="relative z-20 flex max-w-full shrink-0 flex-col items-end gap-1" data-cad-toolbar="tools">
      <div role="group" aria-label="Interaction tools" className={`pointer-events-auto inline-flex min-h-8 max-w-full flex-wrap items-center gap-1 rounded-md p-1 ${FLOATING_CHROME_SURFACE_CLASS}`}>
        {tools.map(tool => <Fragment key={tool.id}>{toolButton(tool)}</Fragment>)}
        {tools.length && trailing ? <span role="separator" aria-orientation="vertical" className="mx-0.5 h-4 w-px shrink-0 bg-border" data-strip-rule="" /> : null}
        {trailing ? <span className="inline-flex items-center gap-1" data-view-controls="">{trailing}</span> : null}
      </div>
  </div>);
}
