import { ScrollArea } from "@text-to-cad/ui/primitives/scroll-area";
import { cn } from "@text-to-cad/ui/utils";
import { VIEWPORT_INSET_PX, VIEWPORT_STACK_BOTTOM, VIEWPORT_TOP_BAR_PX } from "./viewportLayout.js";

const INSET = `${VIEWPORT_INSET_PX}px`;
// Under the tool strip's row: where a notice starts on a narrow view, so the strip stays in reach.
const BELOW_STRIP = `${VIEWPORT_INSET_PX * 2 + VIEWPORT_TOP_BAR_PX}px`;

/**
 * The viewport's top-right corner, one column: the host's notice first (`notice`, a question it
 * asks once, such as whether to share anonymous usage statistics), then what the view keeps there,
 * Quick Edit, which steps down below the notice while it is up rather than hiding under it. On a
 * narrow view, with a strip (`belowStrip`), this column starts below all its controls and
 * takes at most half the viewer, clear of the sidebar. It scrolls above the view cube.
 * @param {{ notice?: import("react").ReactNode, belowStrip?: boolean, children?: import("react").ReactNode }} props
 */
export function ViewportTopRight({ notice = null, belowStrip = false, children = null }) {
  if (!notice && !children) return null;
  return <div className={cn("pointer-events-none absolute z-30 flex flex-col items-end gap-2 top-[var(--cad-top-right-top)]",
    belowStrip && "@max-5xl/cad-viewport:top-[var(--cad-tools-bottom,var(--cad-top-right-narrow))] @max-5xl/cad-viewport:left-auto! @max-5xl/cad-viewport:w-[calc(50%-0.75rem)] @max-5xl/cad-viewport:bottom-[var(--cad-right-bottom)]")}
    style={{ left: INSET, right: INSET, "--cad-top-right-top": INSET, "--cad-top-right-narrow": BELOW_STRIP, "--cad-right-bottom": VIEWPORT_STACK_BOTTOM }}
    data-viewport-top-right="">
    <ScrollArea className="w-full [&_[data-slot=scroll-area-scrollbar]]:pointer-events-auto">
    <div className="flex flex-col items-end gap-2" data-viewport-top-right-content="">
    {notice ? <div className="pointer-events-auto w-full max-w-xs" data-viewport-notice="">{notice}</div> : null}
    {children}
    </div>
    </ScrollArea>
  </div>;
}
