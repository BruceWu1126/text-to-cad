import { cn } from "@text-to-cad/ui/utils";
import { FLOATING_CHROME_SURFACE_CLASS } from "../../../lib/floatingSurface.js";

/**
 * A tool's own controls while it is in hand: one row hung under the strip, on the strip's surface,
 * its buttons the strip's 24px tiles (`STRIP_BUTTON_CLASS`), its groups — what the tool does, how
 * it looks, what it can undo — parted by a thin rule (`SubToolbarSeparator`), as a design tool's
 * options bar is. It stays up for as long as its tool does.
 *
 * @param {{ label: string, className?: string, children: import("react").ReactNode }} props
 *   `label` names the row for assistive technology ("Drawing controls").
 */
export default function SubToolbar({ label, className, children, ...props }) {
  return <div role="toolbar" aria-label={label} data-cad-sub-toolbar="" {...props}
    className={cn("pointer-events-auto inline-flex min-h-8 max-w-full flex-wrap items-center gap-1 rounded-md p-1", FLOATING_CHROME_SURFACE_CLASS, className)}>
    {children}
  </div>;
}

/**
 * The width of a slider tool's sub-toolbar: one width for all of
 * them, their slider taking whatever their buttons leave (`min-w-0 flex-1`), so a conditional
 * button (Animation's Routines) shortens the slider rather than the row.
 */
export const SLIDER_SUB_TOOLBAR_CLASS = "w-56 flex-nowrap";

/**
 * A slider tool's row is one shape: what leads it (Clip's axis; Animation's routines and Play),
 * the slider, its value right beside it, then its actions, Reset last. Its slider is the row's: a
 * visible track, the part set filled, and a solid round thumb (a hollow one reads as a "0" at the
 * track's start).
 */
export const SUB_TOOLBAR_SLIDER_CLASS = [
  "h-6 min-w-0 flex-1 px-2",
  "[&_[data-slot=slider-track]]:h-0.5",
  "[&_[data-slot=slider-track]]:bg-foreground/15",
  "[&_[data-slot=slider-range]]:bg-foreground/70",
  "[&_[data-slot=slider-thumb]]:size-2.5",
  "[&_[data-slot=slider-thumb]]:border-0",
  "[&_[data-slot=slider-thumb]]:bg-foreground",
  "[&_[data-slot=slider-thumb]]:shadow-none",
  "[&_[data-slot=slider-thumb]]:ring-0",
  "[&_[data-slot=slider-thumb]]:hover:ring-2",
  "[&_[data-slot=slider-thumb]]:hover:ring-ring/30",
  "[&_[data-slot=slider-thumb]]:focus-visible:ring-2",
  "[&_[data-slot=slider-thumb]]:focus-visible:ring-ring/40"
].join(" ");

/** A slider's value, right beside it and only as wide as it reads: the slider takes the rest. */
export function SubToolbarValue({ children, ...props }) {
  return <span className="shrink-0 pr-1 text-tiny tabular-nums text-muted-foreground" {...props}>{children}</span>;
}

/** The thin rule between a sub-toolbar's groups. */
export function SubToolbarSeparator() {
  return <span role="separator" aria-orientation="vertical" className="mx-0.5 h-4 w-px shrink-0 bg-border" data-sub-toolbar-rule="" />;
}
