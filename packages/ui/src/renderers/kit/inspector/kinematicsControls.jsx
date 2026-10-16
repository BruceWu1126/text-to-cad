import { ChevronDown } from "lucide-react";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger
} from "@text-to-cad/ui/primitives/dropdown-menu";
import { cn } from "@text-to-cad/ui/utils";
import { FLOATING_SURFACE_CLASS } from "../../../lib/floatingSurface.js";
import { ToolPanelHeadAction } from "../tools/ToolPanel.jsx";

export const NO_PRESET_VALUE = "__none__";
export const DEFAULT_POSE_VALUE = "__default__";

export function positionValuesAreDefault(values, defaults) {
  return Object.entries(defaults || {}).every(([key, value]) => {
    const current = values?.[key] ?? value;
    return typeof value === "number" && typeof current === "number"
      ? Math.abs(current - value) <= 1e-6 : current === value;
  });
}

/**
 * Position's Pose, in its heading at the right end (`ToolPanelHeadAction`), so it is on screen
 * whatever the section shows, folded too: a borderless button reading the pose in hand ("Custom"
 * while the joints are set by hand), whose compact list is Default first where the section can
 * reset (the way back to the default pose: there is no other), then the named poses. Nothing
 * where there is neither to choose.
 */
export function KinematicsPoseRow({ poses = [], activeValue, onSelect, onReset }) {
  if (!poses.length && !onReset) return null;
  const options = [...(onReset ? [{ value: DEFAULT_POSE_VALUE, label: "Default" }] : []), ...poses];
  const active = options.find(pose => pose.value === activeValue);
  const label = active?.label || "Custom";
  return <ToolPanelHeadAction>
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <button type="button" aria-label={`Pose: ${label}`} data-pose-menu=""
          className="flex h-5 min-w-0 max-w-28 shrink items-center gap-0.5 rounded-sm px-1 text-tiny font-normal text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/45 aria-expanded:bg-accent aria-expanded:text-foreground">
          <span className="min-w-0 truncate">{label}</span>
          <ChevronDown className="size-2.5 shrink-0 opacity-60" aria-hidden="true" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" sideOffset={4} collisionPadding={8} aria-label="Pose"
        className={cn(FLOATING_SURFACE_CLASS, "min-w-28 data-[state=closed]:animate-none!")}>
        <DropdownMenuRadioGroup value={active?.value || NO_PRESET_VALUE}
          onValueChange={value => value === DEFAULT_POSE_VALUE ? onReset?.() : onSelect?.(value)}>
          {options.map(option => <DropdownMenuRadioItem key={option.value} value={option.value}>
            <span className="min-w-0 truncate">{option.label}</span>
          </DropdownMenuRadioItem>)}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  </ToolPanelHeadAction>;
}


