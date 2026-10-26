import { useCallback, useEffect, useRef, useState } from "react";
import { ToolbarTooltipScope } from "@text-to-cad/ui/primitives/toolbar-button";
import { cn } from "@text-to-cad/ui/utils";
import { VIEWPORT_INSET_PX } from "../shell/viewportLayout.js";

/** How long Preview's bar stays up after the pointer last moved, as a video player's controls do. */
export const PREVIEW_BAR_IDLE_MS = 2000;
// The bar is the navbar's row with no surface under it: the same height (`h-9`, a phone's 48px in
// `globals.css`) and side padding; Preview's settings dock under it (`top-9`, the same).

/**
 * The viewer's chrome around Preview. `children` — the tool strip and its stack, Quick Edit, the
 * host's notice, everything a person edits with — is hidden and inert while `active`. The host's
 * navbar is gone too (FileViewer hides it while the renderer says Preview is up); in its place, over
 * the view's top edge, Preview's own bar (`PreviewBar`): the file's name at the left, as words,
 * and `controls` at the right (its settings and the way out), nothing else, on no surface: the model
 * shows through. Under its right end `panel`, Preview's settings, docked at the view's right edge
 * while they are open. The top-left under the bar is the file sidebar's, the Preview's sections
 * (Position, Animation), drawn by the frame (`RendererShell.jsx`).
 *
 * @param {{ active: boolean, name?: string, controls?: import("react").ReactNode, panel?: import("react").ReactNode,
 *   children?: import("react").ReactNode }} props
 */
export default function PreviewChrome({ active, name = "", controls = null, panel = null, children }) {
  return <>
    <div data-preview-chrome="" hidden={active} inert={active}
      className="pointer-events-none absolute inset-0 z-20">
      <ToolbarTooltipScope enabled={!active}>{children}</ToolbarTooltipScope>
    </div>
    {active ? <ToolbarTooltipScope enabled><PreviewBar name={name} controls={controls} panel={panel} /></ToolbarTooltipScope> : null}
  </>;
}

/**
 * Preview's bar: up as Preview opens, gone after `PREVIEW_BAR_IDLE_MS` with no pointer movement over
 * the view, back on the next movement, on a press (a tap, on a touch screen) and on keyboard focus of
 * anything in it. It stays while the pointer is on it, while something in it has the focus and while
 * the settings panel is open. Gone, it is transparent and lets presses through to the model, but its
 * controls keep their place in the tab order, so the keyboard brings it back.
 */
function PreviewBar({ name, controls, panel }) {
  const bar = useRef(null);
  const [shown, setShown] = useState(true);
  const [over, setOver] = useState(false);
  const [focused, setFocused] = useState(false);
  const held = over || focused || Boolean(panel);
  const heldRef = useRef(held);
  heldRef.current = held;
  const timer = useRef(undefined);
  const wake = useCallback(() => {
    setShown(true);
    clearTimeout(timer.current);
    if (!heldRef.current) timer.current = setTimeout(() => setShown(false), PREVIEW_BAR_IDLE_MS);
  }, []);
  // Held, it stays; let go, it counts down again.
  useEffect(() => { wake(); return () => clearTimeout(timer.current); }, [held, wake]);
  // Movement anywhere over the view (the frame's root, `data-slot="cad-file-view"`) brings it back.
  useEffect(() => {
    const view = bar.current?.closest("[data-slot=cad-file-view]") ?? bar.current?.parentElement;
    if (!view) return undefined;
    view.addEventListener("pointermove", wake);
    view.addEventListener("pointerdown", wake);
    return () => { view.removeEventListener("pointermove", wake); view.removeEventListener("pointerdown", wake); };
  }, [wake]);
  const reach = shown ? "pointer-events-auto" : "pointer-events-none";
  return <>
    <div ref={bar} data-preview-bar="" data-hidden={shown ? undefined : ""}
      className={cn("pointer-events-none absolute inset-x-0 top-0 z-30 flex h-9 items-center justify-between gap-2 px-2 transition-opacity duration-300 motion-reduce:transition-none",
        shown ? "opacity-100" : "opacity-0")}
      onPointerOver={() => setOver(true)}
      onPointerOut={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOver(false); }}
      onFocus={() => setFocused(true)}
      onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false); }}>
      <span data-preview-name="" className={cn("min-w-0 truncate px-1 text-sm text-foreground [text-shadow:0_0_6px_var(--background)]", reach)}>{name}</span>
      {controls ? <div data-preview-controls="" className={cn("flex shrink-0 items-center gap-1", reach)}>{controls}</div> : null}
    </div>
    {/* `panel`, as tall as its content up to the room under the bar. */}
    {panel ? <div data-preview-panel="" className="pointer-events-none absolute top-9 z-30 flex min-h-0 max-w-[calc(100%-1rem)] flex-col items-end"
      style={{ right: VIEWPORT_INSET_PX, bottom: VIEWPORT_INSET_PX }}>{panel}</div> : null}
  </>;
}
