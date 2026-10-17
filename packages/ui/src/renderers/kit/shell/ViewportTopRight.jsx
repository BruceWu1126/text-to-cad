import { ScrollArea } from "@text-to-cad/ui/primitives/scroll-area";
import { VIEWPORT_INSET_PX } from "./viewportLayout.js";

const INSET = `${VIEWPORT_INSET_PX}px`;

/**
 * The viewport's top-right corner, one column: the host's notice first (`notice`, a question it
 * asks once, such as whether to share anonymous usage statistics), then what the view keeps there,
 * Quick Edit, which steps down below the notice while it is up rather than hiding under it. Where it
 * would reach under the tool strip it starts below it (`--cad-top-right-top`, the shell's measure).
 * @param {{ notice?: import("react").ReactNode, children?: import("react").ReactNode }} props
 */
export function ViewportTopRight({ notice = null, children = null }) {
  if (!notice && !children) return null;
  return <div className="pointer-events-none absolute z-30 flex flex-col items-end gap-2"
    style={{ top: `var(--cad-top-right-top, ${INSET})`, left: INSET, right: INSET }}
    data-viewport-top-right="">
    <ScrollArea className="w-full [&_[data-slot=scroll-area-scrollbar]]:pointer-events-auto">
    <div className="flex flex-col items-end gap-2" data-viewport-top-right-content="">
    {notice ? <div className="pointer-events-auto w-full max-w-xs" data-viewport-notice="">{notice}</div> : null}
    {children}
    </div>
    </ScrollArea>
  </div>;
}
