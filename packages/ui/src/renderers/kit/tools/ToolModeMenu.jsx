import { ChevronDown, SlidersHorizontal } from "lucide-react";
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from "@text-to-cad/ui/primitives/dropdown-menu";
import { cn } from "@text-to-cad/ui/utils";
import { FLOATING_SURFACE_CLASS } from "../../../lib/floatingSurface.js";
import { TOOL_PANEL_BUTTON_CLASS } from "./ToolPanel.jsx";

// The strip's chevron: a small button of its own after its tool's, lit by its own hover and while
// its menu is open, never with the tool. As tall as the strip's 24px tile and touching it, so the
// strip's own gap is the only space before the next tool, as between any two.
const CHEVRON_CLASS = "grid h-6 w-3 shrink-0 place-items-center rounded-sm text-sidebar-foreground/70 outline-none transition hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-ring/45 disabled:pointer-events-none disabled:opacity-50 aria-expanded:bg-sidebar-accent aria-expanded:text-sidebar-accent-foreground";

/**
 * A tool's settings: a button and the ordinary dropdown it opens with whatever the tool offers
 * (`children`: its items, submenus and checkboxes). The button is a small sliders button in a
 * panel's heading, beside the fold chevron or the X (`trigger` "settings"), or a chevron on the
 * strip right after the tool's own button ("chevron"), its menu dropping from it.
 *
 * @param {{ label: string, triggerLabel?: string, disabled?: boolean, trigger?: "chevron" | "settings",
 *   triggerProps?: object, children: import("react").ReactNode }} props
 */
export function ToolSettingsMenu({ label, triggerLabel = label, disabled = false, trigger = "settings", triggerProps = null, children }) {
  const chevron = trigger === "chevron";
  return <DropdownMenu modal={false}>
    <DropdownMenuTrigger asChild>
      <button type="button" aria-label={triggerLabel} disabled={disabled} {...triggerProps}
        className={chevron ? CHEVRON_CLASS
          : cn(TOOL_PANEL_BUTTON_CLASS, "disabled:pointer-events-none disabled:opacity-50 aria-expanded:bg-accent aria-expanded:text-foreground")}>
        {chevron ? <ChevronDown className="size-2 shrink-0" strokeWidth={3} aria-hidden="true" /> : <SlidersHorizontal className="size-3" aria-hidden="true" />}
      </button>
    </DropdownMenuTrigger>
    {/* Closes with no exit animation, so a quick second press always reaches the button. */}
    <DropdownMenuContent align={chevron ? "start" : "end"} sideOffset={chevron ? 6 : 4} collisionPadding={8} aria-label={label}
      className={cn(FLOATING_SURFACE_CLASS, "w-40 data-[state=closed]:animate-none!")}>
      {children}
    </DropdownMenuContent>
  </DropdownMenu>;
}
