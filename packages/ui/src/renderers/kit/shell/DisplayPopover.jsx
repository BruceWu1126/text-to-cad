import { Check, SlidersHorizontal } from "lucide-react";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem,
  DropdownMenuSeparator, DropdownMenuSub, DropdownMenuSubContent, DropdownMenuSubTrigger, DropdownMenuTrigger
} from "@text-to-cad/ui/primitives/dropdown-menu";
import { ScrollArea } from "@text-to-cad/ui/primitives/scroll-area";
import { ToolbarButton } from "@text-to-cad/ui/primitives/toolbar-button";
import { cn } from "@text-to-cad/ui/utils";
import { FLOATING_SURFACE_CLASS } from "../../../lib/floatingSurface.js";
import { STRIP_BUTTON_CLASS } from "../tools/FloatingToolBar.js";

// Inside the viewer, 8px clear of its edges.
const COLLISION_PADDING = 8;

/**
 * Display: the display mode, a tile at the tool strip's end before Projection (and of preview's row
 * in the strip's place), its icon the mode in hand — Custom's sliders while the settings are the
 * person's own. A press opens the modes: Solid, Render, X-ray, Hidden line, Wireframe, Grid, each
 * its icon and name, the one in hand checked; choosing one applies it at once (the one in hand
 * too: back as it comes). Last, **Custom**, checked while the settings are the person's, opens
 * a submenu that is the settings themselves (`children`: `useRendererShell`'s `frame.display`),
 * starting from Solid's. It is not a tool: opening it leaves the tool in hand as it is.
 *
 * The settings are a menu's content but not its items: keys typed in them stay theirs (a menu
 * would take them for its typeahead), and they scroll inside the submenu, never taller than the
 * viewer (`boundary`).
 * @param {{ disabled?: boolean, boundary?: Element | null, tooltipSide?: "top" | "bottom", onOpenChange?(open: boolean): void,
 *   mode: { value: string, custom: boolean, options: { value: string, label: string, Icon: import("react").ComponentType<any> }[],
 *     onChange(value: string): void },
 *   children: import("react").ReactNode }} props
 */
export default function DisplayPopover({ disabled = false, boundary = null, tooltipSide = "top", onOpenChange, mode, children }) {
  const current = mode.options.find(option => option.value === mode.value) || mode.options[0];
  const Icon = mode.custom ? SlidersHorizontal : current?.Icon;
  const label = mode.custom ? "Custom" : current?.label || "Solid";
  return <DropdownMenu modal={false} onOpenChange={onOpenChange}>
    <DropdownMenuTrigger asChild>
      <ToolbarButton label={`Display mode: ${label}`} tooltipSide={tooltipSide} disabled={disabled} className={STRIP_BUTTON_CLASS}
        data-display-mode={mode.custom ? "custom" : current?.value}>
        {Icon ? <Icon /> : null}
      </ToolbarButton>
    </DropdownMenuTrigger>
    <DropdownMenuContent align="center" sideOffset={6} collisionPadding={COLLISION_PADDING} aria-label="Display mode"
      className={cn(FLOATING_SURFACE_CLASS, "min-w-44 data-[state=closed]:animate-none!")}>
      <DropdownMenuLabel className="text-tiny font-normal text-muted-foreground">Display mode</DropdownMenuLabel>
      <DropdownMenuRadioGroup value={mode.custom ? "" : current?.value}>
        {mode.options.map(({ value, label: name, Icon: OptionIcon }) => <DropdownMenuRadioItem key={value} value={value}
          data-display-mode-option={value} onSelect={() => mode.onChange(value)}>
          <OptionIcon className="size-3.5" aria-hidden="true" />{name}
        </DropdownMenuRadioItem>)}
      </DropdownMenuRadioGroup>
      <DropdownMenuSeparator />
      <DropdownMenuSub>
        <DropdownMenuSubTrigger data-display-mode-option="custom" aria-checked={mode.custom} className="[&>svg:last-child]:size-3">
          <SlidersHorizontal className="size-3.5" aria-hidden="true" />Custom
          {mode.custom ? <Check className="ml-auto size-3.5" aria-label="Current" /> : null}
        </DropdownMenuSubTrigger>
        <DropdownMenuSubContent sideOffset={4} collisionPadding={COLLISION_PADDING} collisionBoundary={boundary || undefined}
          aria-label="Display settings" data-display-popover=""
          className={cn(FLOATING_SURFACE_CLASS, "flex w-76 max-h-[var(--radix-dropdown-menu-content-available-height)] flex-col overflow-hidden p-0 text-tiny data-[state=closed]:animate-none!")}>
          {/* The settings keep their keys (a menu's typeahead and arrows would take them). */}
          <ScrollArea className="min-h-0 flex-1" onKeyDown={event => { if (event.key !== "Escape") event.stopPropagation(); }}>{children}</ScrollArea>
        </DropdownMenuSubContent>
      </DropdownMenuSub>
    </DropdownMenuContent>
  </DropdownMenu>;
}
