import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";
import { ToolbarTooltipScope } from "@text-to-cad/ui/primitives/toolbar-button";
import { cn } from "@text-to-cad/ui/utils";
import { VIEWPORT_INSET_PX } from "../shell/viewportLayout.js";
import { SIDEBAR_SLIDE_CLASS } from "./FileSidebar.jsx";

/** How long Preview's chrome stays up after the pointer last moved, as a video player's controls do. */
export const PREVIEW_IDLE_MS = 2000;
// How what fades goes and comes back.
export const PREVIEW_FADE_CLASS = "transition-opacity duration-300 motion-reduce:transition-none";

/**
 * Preview's chrome, its bar at the top and its playbar at the bottom, shown and gone together: up as
 * Preview opens, gone after `PREVIEW_IDLE_MS` with no pointer movement over the view (its root), back
 * on the next movement and on a press (a tap, on a touch screen). It stays while held (its settings
 * open) and while the pointer is on either piece or something in one has the focus — a menu of theirs
 * open among them, its portal being theirs in React's tree. `hold(piece)` is the handlers each piece
 * spreads on its own element. Gone, the pieces are transparent and let presses through, but keep their
 * place in the tab order, so the keyboard brings them back.
 *
 * A store outside React (one per frame): the pieces read it (`usePreviewShown`) and nothing else
 * re-renders as it changes, so a hover in Preview redraws two rows, never the frame.
 */
export function createPreviewIdle() {
  let shown = true, active = false, held = false, root = null, timer;
  const holders = new Set();
  const listeners = new Set();
  const set = next => { if (next !== shown) { shown = next; for (const listener of [...listeners]) listener(); } };
  const holding = () => held || holders.size > 0;
  const wake = () => {
    set(true);
    clearTimeout(timer);
    if (active && !holding()) timer = setTimeout(() => set(false), PREVIEW_IDLE_MS);
  };
  const listen = (element, on) => {
    if (!element) return;
    const method = on ? "addEventListener" : "removeEventListener";
    element[method]("pointermove", wake);
    element[method]("pointerdown", wake);
  };
  const hold = (key, on) => {
    if (holders.has(key) === on) return;
    if (on) holders.add(key); else holders.delete(key);
    wake();
  };
  const handlers = new Map();
  return {
    subscribe(listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    shown: () => shown,
    /** Preview opened or closed, over the view's root: each entry starts up. */
    setActive(next, element) {
      listen(root, false);
      active = next; root = next ? element : null; holders.clear();
      listen(root, true);
      if (active) wake(); else { clearTimeout(timer); set(true); }
    },
    /** Its settings open: held for as long as they are. */
    setHeld(next) { if (next !== held) { held = next; wake(); } },
    hold(piece) {
      if (!handlers.has(piece)) handlers.set(piece, {
        onPointerOver: () => hold(`${piece}:over`, true),
        onPointerOut: event => { if (!event.currentTarget.contains(event.relatedTarget)) hold(`${piece}:over`, false); },
        onFocus: () => hold(`${piece}:focus`, true),
        onBlur: event => { if (!event.currentTarget.contains(event.relatedTarget)) hold(`${piece}:focus`, false); },
      });
      return handlers.get(piece);
    },
  };
}

/** Whether Preview's chrome is up, from its store (`createPreviewIdle`); up where there is none. */
export function usePreviewShown(idle) {
  const subscribe = useCallback(listener => (idle ? idle.subscribe(listener) : () => {}), [idle]);
  return useSyncExternalStore(subscribe, () => (idle ? idle.shown() : true), () => true);
}

/** The frame's store for Preview's chrome, following `active` over `root` and held while `held`. */
export function usePreviewIdle(root, active, held) {
  const idle = useMemo(createPreviewIdle, []);
  useEffect(() => { idle.setActive(active, root); }, [idle, active, root]);
  useEffect(() => () => idle.setActive(false, null), [idle]);
  useEffect(() => { idle.setHeld(held); }, [idle, held]);
  return idle;
}

/**
 * The viewer's chrome around Preview. `children` — the tool strip and its stack, Quick Edit, the
 * host's notice, everything a person edits with — is hidden and inert while `active`. The host's
 * navbar is gone too (FileViewer hides it while the renderer says Preview is up); in its place, over
 * the view's top edge, Preview's own bar: the file's name at the left, as words, and `controls` at
 * the right (its settings and the way out), nothing else, on no surface: the model shows through.
 * It comes and goes with the playbar (`idle`, `usePreviewIdle`). Under its right end `panel`,
 * Preview's settings, docked at the view's right edge, sliding in from it while `panelOpen` and out
 * again as it closes, as the file sidebar's panel does from its edge (`SIDEBAR_SLIDE_CLASS`).
 *
 * @param {{ active: boolean, name?: string, controls?: import("react").ReactNode, panel?: import("react").ReactNode,
 *   panelOpen?: boolean, idle?: ReturnType<typeof createPreviewIdle> | null, children?: import("react").ReactNode }} props
 */
export default function PreviewChrome({ active, name = "", controls = null, panel = null, panelOpen = false, idle = null, children }) {
  return <>
    <div data-preview-chrome="" hidden={active} inert={active}
      className="pointer-events-none absolute inset-0 z-20">
      <ToolbarTooltipScope enabled={!active}>{children}</ToolbarTooltipScope>
    </div>
    {active ? <ToolbarTooltipScope enabled>
      <PreviewBar name={name} controls={controls} idle={idle} />
      {/* `panel`, under the bar (`top-9`), as tall as its content up to the room under it. Closed, it
          is off the view's right edge, out of reach and out of the accessibility tree. */}
      {panel ? <div data-preview-panel="" data-open={panelOpen ? "" : undefined} inert={!panelOpen} aria-hidden={panelOpen ? undefined : true}
        className={cn("pointer-events-none absolute top-9 z-30 flex min-h-0 max-w-[calc(100%-1rem)] flex-col items-end", SIDEBAR_SLIDE_CLASS,
          panelOpen ? null : "translate-x-[calc(100%+1rem)] opacity-0")}
        style={{ right: VIEWPORT_INSET_PX, bottom: VIEWPORT_INSET_PX }}>{panel}</div> : null}
    </ToolbarTooltipScope> : null}
  </>;
}

// The navbar's row (`h-9`, a phone's 48px in `globals.css`) with no surface under it: the only part of
// the frame that redraws as it comes and goes.
function PreviewBar({ name, controls, idle }) {
  const shown = usePreviewShown(idle);
  const reach = shown ? "pointer-events-auto" : "pointer-events-none";
  return <div data-preview-bar="" data-hidden={shown ? undefined : ""} {...idle?.hold("bar")}
    className={cn("pointer-events-none absolute inset-x-0 top-0 z-30 flex h-9 items-center justify-between gap-2 px-2", PREVIEW_FADE_CLASS,
      shown ? "opacity-100" : "opacity-0")}>
    <span data-preview-name="" className={cn("min-w-0 truncate px-1 text-sm text-foreground [text-shadow:0_0_6px_var(--background)]", reach)}>{name}</span>
    {controls ? <div data-preview-controls="" className={cn("flex shrink-0 items-center gap-1", reach)}>{controls}</div> : null}
  </div>;
}
