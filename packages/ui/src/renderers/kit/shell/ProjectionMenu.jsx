import {
  DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger
} from "@text-to-cad/ui/primitives/dropdown-menu";
import { ToolbarButton } from "@text-to-cad/ui/primitives/toolbar-button";
import { cn } from "@text-to-cad/ui/utils";
import { FLOATING_SURFACE_CLASS } from "../../../lib/floatingSurface.js";
import { OrthographicProjectionIcon, PerspectiveProjectionIcon } from "../camera/ProjectionModeIcons.js";
import { STRIP_BUTTON_CLASS } from "../tools/FloatingToolBar.js";

export const PROJECTIONS = Object.freeze([
  Object.freeze({ value: "orthographic", label: "Orthographic", Icon: OrthographicProjectionIcon }),
  Object.freeze({ value: "perspective", label: "Perspective", Icon: PerspectiveProjectionIcon })
]);

/**
 * Projection: a tile of the strip's view controls, after the display mode (and of preview's row), its icon
 * the projection in hand, whose dropdown is the two: Orthographic (the default) and Perspective. It is the
 * camera's, not the look's, so it is no part of Display's settings.
 * @param {{ value: string, onChange(value: string): void, disabled?: boolean, tooltipSide?: "top" | "bottom",
 *   onOpenChange?(open: boolean): void }} props
 */
export default function ProjectionMenu({ value, onChange, disabled = false, tooltipSide = "top", onOpenChange }) {
  const current = PROJECTIONS.find(option => option.value === value) || PROJECTIONS[0];
  const Icon = current.Icon;
  return <DropdownMenu modal={false} onOpenChange={onOpenChange}>
    <DropdownMenuTrigger asChild>
      <ToolbarButton label={`Projection: ${current.label}`} tooltipSide={tooltipSide} disabled={disabled} className={STRIP_BUTTON_CLASS} data-projection={current.value}>
        <Icon />
      </ToolbarButton>
    </DropdownMenuTrigger>
    <DropdownMenuContent align="center" sideOffset={6} collisionPadding={8} aria-label="Projection"
      className={cn(FLOATING_SURFACE_CLASS, "min-w-36 data-[state=closed]:animate-none!")}>
      <DropdownMenuRadioGroup value={current.value} onValueChange={onChange}>
        {PROJECTIONS.map(({ value: option, label, Icon: OptionIcon }) => <DropdownMenuRadioItem key={option} value={option}>
          <OptionIcon className="size-3.5" />{label}
        </DropdownMenuRadioItem>)}
      </DropdownMenuRadioGroup>
    </DropdownMenuContent>
  </DropdownMenu>;
}
