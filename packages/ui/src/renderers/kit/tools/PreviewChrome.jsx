import { useCallback, useEffect, useRef, useState } from "react";
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
 * Preview opens, gone after `PREVIEW_IDLE_MS` with no pointer movement over the view (`root`), back
 * on the next movement and on a press (a tap, on a touch screen). It stays while `held` (its settings
 * open) and while the pointer is on either piece or something in one has the focus — a menu of theirs
 * open among them, its portal being theirs in React's tree. `hold(piece)` is the handlers each piece
 * spreads on its own element. Gone, the pieces are transparent and let presses through, but keep their
 * place in the tab order, so the keyboard brings them back.
 *
 * @param {HTMLElement | null} root
 * @param {boolean} active
 * @param {boolean} held
 */
export function usePreviewIdle(root, active, held) {
  const [shown, setShown] = useState(true);
  const [holders, setHolders] = useState(() => new Set());
  const holding = held || holders.size > 0;
  const holdingRef = useRef(holding);
  holdingRef.current = holding;
  const timer = useRef(undefined);
  const wake = useCallback(() => {
    setShown(true);
    clearTimeout(timer.current);
    if (!holdingRef.current) timer.current = setTimeout(() => setShown(false), PREVIEW_IDLE_MS);
  }, []);
  // Each entry to Preview starts up; held, it stays; let go, it counts down again.
  useEffect(() => {
    if (!active) { clearTimeout(timer.current); setShown(true); setHolders(new Set()); return undefined; }
    wake();
    return () => clearTimeout(timer.current);
  }, [active, holding, wake]);
  useEffect(() => {
    if (!active || !root) return undefined;
    root.addEventListener("pointermove", wake);
    root.addEventListener("pointerdown", wake);
    return () => { root.removeEventListener("pointermove", wake); root.removeEventListener("pointerdown", wake); };
  }, [active, root, wake]);
  const set = useCallback((key, on) => setHolders(previous => {
    if (previous.has(key) === on) return previous;
    const next = new Set(previous);
    if (on) next.add(key); else next.delete(key);
    return next;
  }), []);
  const hold = useCallback(piece => ({
    onPointerOver: () => set(`${piece}:over`, true),
    onPointerOut: event => { if (!event.currentTarget.contains(event.relatedTarget)) set(`${piece}:over`, false); },
    onFocus: () => set(`${piece}:focus`, true),
    onBlur: event => { if (!event.currentTarget.contains(event.relatedTarget)) set(`${piece}:focus`, false); },
  }), [set]);
  return { shown: !active || shown, hold };
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
 *   panelOpen?: boolean, idle?: ReturnType<typeof usePreviewIdle>, children?: import("react").ReactNode }} props
 */
export default function PreviewChrome({ active, name = "", controls = null, panel = null, panelOpen = false, idle = null, children }) {
  const shown = idle ? idle.shown : true;
  const reach = shown ? "pointer-events-auto" : "pointer-events-none";
  return <>
    <div data-preview-chrome="" hidden={active} inert={active}
      className="pointer-events-none absolute inset-0 z-20">
      <ToolbarTooltipScope enabled={!active}>{children}</ToolbarTooltipScope>
    </div>
    {active ? <ToolbarTooltipScope enabled>
      {/* The navbar's row (`h-9`, a phone's 48px in `globals.css`) with no surface under it. */}
      <div data-preview-bar="" data-hidden={shown ? undefined : ""} {...idle?.hold("bar")}
        className={cn("pointer-events-none absolute inset-x-0 top-0 z-30 flex h-9 items-center justify-between gap-2 px-2", PREVIEW_FADE_CLASS,
          shown ? "opacity-100" : "opacity-0")}>
        <span data-preview-name="" className={cn("min-w-0 truncate px-1 text-sm text-foreground [text-shadow:0_0_6px_var(--background)]", reach)}>{name}</span>
        {controls ? <div data-preview-controls="" className={cn("flex shrink-0 items-center gap-1", reach)}>{controls}</div> : null}
      </div>
      {/* `panel`, under the bar (`top-9`), as tall as its content up to the room under it. Closed, it
          is off the view's right edge, out of reach and out of the accessibility tree. */}
      {panel ? <div data-preview-panel="" data-open={panelOpen ? "" : undefined} inert={!panelOpen} aria-hidden={panelOpen ? undefined : true}
        className={cn("pointer-events-none absolute top-9 z-30 flex min-h-0 max-w-[calc(100%-1rem)] flex-col items-end", SIDEBAR_SLIDE_CLASS,
          panelOpen ? null : "translate-x-[calc(100%+1rem)] opacity-0")}
        style={{ right: VIEWPORT_INSET_PX, bottom: VIEWPORT_INSET_PX }}>{panel}</div> : null}
    </ToolbarTooltipScope> : null}
  </>;
}
