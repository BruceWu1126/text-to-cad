import { useContext, useId, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { ToolbarButton } from "@text-to-cad/ui/primitives/toolbar-button";
import { cn } from "@text-to-cad/ui/utils";
import { ViewerHostContext } from "../../../host/context.js";
import { CHROME_INSET_PX } from "../../../lib/chromeInset.js";
import FloatingToolBar from "./FloatingToolBar.js";

// The stack slides out to the left and fades as it goes, and back in: transforms and opacity
// alone, so nothing is laid out again while it moves. Going, it eases in and is the quicker;
// coming back, it eases out. Under reduced motion it does neither: it is simply gone, or back.
const HIDE_MOTION = "duration-150 ease-in";
const SHOW_MOTION = "duration-200 ease-out";

// The toggle is one of the strip's 24px buttons with no surface of its own. Shown, it floats 2px
// outside the top panel's right edge, centred on that panel's 28px first row; hidden, it comes to
// rest where the stack began, under the strip's first button (its 1px border and 4px padding in).
const TOGGLE_GAP_PX = 2;
const TOGGLE_TOP_PX = 2;
const TOGGLE_REST_PX = 5;

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";
const reducedMotionQuery = () => (typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(REDUCED_MOTION) : null);
function subscribeReducedMotion(notify) {
  const query = reducedMotionQuery();
  query?.addEventListener?.("change", notify);
  return () => query?.removeEventListener?.("change", notify);
}
const systemReducedMotion = () => reducedMotionQuery()?.matches === true;
/** The system's `prefers-reduced-motion`, or the app's own (`environment.reducedMotion`): either keeps the stack still. */
function useReducedMotion() {
  const app = useContext(ViewerHostContext)?.environment?.reducedMotion === true;
  const system = useSyncExternalStore(subscribeReducedMotion, systemReducedMotion, () => false);
  return app || system;
}

/**
 * The stack's top panel — the first one shown — as the toggle places itself by it: its width and
 * how far down the stack it starts; null while the stack shows no panel, when there is nothing to
 * put away. Panels come and go by their `hidden` (a tool put down, Links closed) and are sized by
 * a person, so this follows both. Read from layout, which the slide's transform does not change.
 */
function useTopPanel(stack) {
  const [top, setTop] = useState(null);
  useLayoutEffect(() => {
    const element = stack.current;
    if (!element) return undefined;
    let observed = null;
    const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => measure());
    function measure() {
      const panel = [...element.querySelectorAll("[data-tool-panel]")].find(candidate => !candidate.closest("[hidden]")) ?? null;
      if (panel !== observed) {
        if (observed) resize?.unobserve(observed);
        if (panel) resize?.observe(panel);
        observed = panel;
      }
      const next = panel ? { width: panel.offsetWidth, top: Math.round(panel.getBoundingClientRect().top - element.getBoundingClientRect().top) } : null;
      setTop(current => current?.width === next?.width && current?.top === next?.top ? current : next);
    }
    measure();
    const mutations = typeof MutationObserver === "undefined" ? null : new MutationObserver(measure);
    mutations?.observe(element, { subtree: true, childList: true, attributes: true, attributeFilter: ["hidden"] });
    return () => { mutations?.disconnect(); resize?.disconnect(); };
  }, [stack]);
  return top;
}

/**
 * The stack's own toggle: one of the strip's buttons, transparent, floating just outside the top
 * panel's top-right corner, "Hide panels". Hidden, the stack slides away and the toggle with it, to
 * rest where the stack began, under the strip's first button: the bare icon ("Show panels"), faint
 * until a pointer or the keyboard comes to it.
 */
function ToolStackToggle({ away, motion, controls, panel, onToggle }) {
  const Icon = away ? PanelLeftOpen : PanelLeftClose;
  const x = away ? TOGGLE_REST_PX : panel.width + TOGGLE_GAP_PX;
  return <div className={cn("absolute left-0 top-0 flex", motion && `transition-transform ${motion}`)}
    style={{ transform: `translate(${x}px, ${panel.top + TOGGLE_TOP_PX}px)` }} data-cad-tools-toggle="">
    <ToolbarButton label={away ? "Show panels" : "Hide panels"} tooltipSide="right" disclosure aria-expanded={!away} aria-controls={controls}
      className={cn("pointer-events-auto", away && "opacity-60 hover:opacity-100 focus-visible:opacity-100")} onClick={onToggle}>
      <Icon className="size-3" strokeWidth={2} aria-hidden="true" />
    </ToolbarButton>
  </div>;
}

/**
 * The strip and the tool stack under it, one column at the viewer's top-left (`style` places it),
 * and the person's way to put the stack away and bring it back: the toggle floating at the top
 * panel's top-right corner, there while the stack shows a panel. The strip always stays.
 *
 * Hidden (`hidden`), every panel slides out to the left and fades, and is inert — out of reach and
 * out of the accessibility tree — and invisible once out, but mounted: a tree keeps its expansion,
 * filter and scroll, as panels already do from one tool to the next. Nothing here shows or hides
 * the stack but its toggle, which reports the person's choice (`onHiddenChange`, once per press);
 * where it starts is the caller's (`toolStackHidden`).
 *
 * @param {{ tools: import("./FloatingToolBar.js").ViewportTool[], hidden?: boolean,
 *   onHiddenChange?: ((hidden: boolean) => void) | null, mobile?: boolean,
 *   style?: import("react").CSSProperties, children?: import("react").ReactNode }} props
 *   `children`: the tool stack (`ToolStack.jsx`), which takes the column's height under the strip.
 */
export default function ToolColumn({ tools, hidden = false, onHiddenChange = null, mobile = false, style, children }) {
  const still = useReducedMotion();
  const stackId = `${useId()}stack`;
  const stack = useRef(null);
  const panel = useTopPanel(stack);
  const togglable = Boolean(onHiddenChange);
  const away = togglable && hidden;
  const motion = still ? null : away ? HIDE_MOTION : SHOW_MOTION;
  // Gone: transparent, and invisible once the slide has ended. Visibility is transitioned only on
  // the way out, where it steps at the end; coming back it is visible at once, and while the stack
  // is shown nothing in it transitions visibility, so whatever hides the column hides it at once.
  const gone = away ? { opacity: 0, visibility: "hidden" } : null;
  const slideAndFade = away ? "transition-[transform,opacity,visibility]" : "transition-[transform,opacity]";
  return <div className="group/tool-stack pointer-events-none absolute z-20 flex flex-col items-start gap-2" style={style}
    data-mobile={mobile ? "" : undefined} data-cad-tool-groups="" data-tools-hidden={away ? "" : undefined}>
    {tools.length > 0 ? <div className="flex max-w-full shrink-0 items-start" data-cad-tool-row="">
      <FloatingToolBar tools={tools} />
    </div> : null}
    <div className="relative flex min-h-0 max-w-full flex-1 flex-col items-start">
      {/* The panels slide out past their own width and the inset they hang at, so nothing of them is left at the edge. */}
      <div ref={stack} id={stackId} className={cn("flex min-h-0 max-w-full flex-1 flex-col items-start", motion && `${slideAndFade} ${motion}`)}
        inert={away} style={away ? { transform: `translateX(calc(-100% - ${CHROME_INSET_PX}px))`, ...gone } : undefined}>
        {children}
      </div>
      {togglable && panel ? <ToolStackToggle away={away} motion={motion} controls={stackId} panel={panel}
        onToggle={() => onHiddenChange(!away)} /> : null}
    </div>
  </div>;
}
