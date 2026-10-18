import { normalizeExplodedViewSettings } from "@text-to-cad/core/lib/displaySettings.js";
import { clipAmount, clipAtForAmount, clipAxisBounds, normalizeStepClipSettings } from "@text-to-cad/core/lib/viewer/clipPlane.js";
import { ArrowLeftRight, ChevronDown } from "lucide-react";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger
} from "@text-to-cad/ui/primitives/dropdown-menu";
import { Slider } from "@text-to-cad/ui/primitives/slider";
import { ToolbarButton } from "@text-to-cad/ui/primitives/toolbar-button";
import { cn } from "@text-to-cad/ui/utils";
import { FLOATING_SURFACE_CLASS } from "../../../../lib/floatingSurface.js";
import { FILE_SHEET_PRECISION_SLIDER_CLASSES } from "../../inspector/FileSheet.js";

const AXES = Object.freeze(["x", "y", "z"]);

function formatNumber(value, digits = 2) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "0";
}

// These controls read the resolved scene's Clip and Explode groups. Public display
// validation belongs to the settings store; resolved scene groups are not a saved display.
// Persistent model effects are separate from Display and its presets. Clip and Explode are drawn
// alike: the amount in the panel's heading, one row for a body — Explode's a slider, Clip's the
// axis it cuts along and then its slider.
//
// Clip's view holds where its plane stands (`at`, millimetres); the panel shows the share of the
// model it cuts away, measured on `bounds` — the model at rest, the box the plane is placed in.
function clipState(viewSettings, bounds) {
  const clip = normalizeStepClipSettings(viewSettings?.clip);
  const { min, max } = clipAxisBounds(bounds, clip.axis);
  return { clip, range: max - min, amount: clipAmount(clip, bounds) * 100 };
}

/** How far Clip cuts, for its panel's heading. */
export function clipSummary(viewSettings, bounds) {
  return `${formatNumber(clipState(viewSettings, bounds).amount, 0)}%`;
}

const AXIS_OPTIONS = Object.freeze(AXES.map(value => ({ value, label: value.toUpperCase() })));

/** Clip's axis, left of its slider: a borderless button reading the axis in hand, as the row's other
 *  buttons are, whose dropdown lists X, Y and Z. */
function ClipAxisSelect({ viewSettings, onViewSettingsPatch, bounds }) {
  const { clip, amount } = clipState(viewSettings, bounds);
  const choose = nextAxis => {
      if (!nextAxis || nextAxis === clip.axis) return;
      // An axis alone is not an effect. A cut keeps its share on the new axis; with no box to
      // measure it on, it goes through the middle.
      const { min, max } = clipAxisBounds(bounds, nextAxis);
      const at = clip.enabled && max > min ? clipAtForAmount({ ...clip, axis: nextAxis }, bounds, amount / 100) : undefined;
      onViewSettingsPatch({ clip: { axis: nextAxis, ...(at !== undefined ? { at } : {}) } });
  };
  return <DropdownMenu modal={false}>
    <DropdownMenuTrigger asChild>
      <ToolbarButton tooltip={false} label={`Clip axis: ${clip.axis.toUpperCase()}`} data-clip-axis={clip.axis}
        className="w-auto shrink-0 gap-0.5 px-1 text-tiny">
        {clip.axis.toUpperCase()}<ChevronDown className="size-2.5 opacity-60" aria-hidden="true" />
      </ToolbarButton>
    </DropdownMenuTrigger>
    <DropdownMenuContent align="start" sideOffset={4} collisionPadding={8} aria-label="Clip axis"
      className={cn(FLOATING_SURFACE_CLASS, "min-w-16 data-[state=closed]:animate-none!")}>
      <DropdownMenuRadioGroup value={clip.axis} onValueChange={choose}>
        {AXIS_OPTIONS.map(option => <DropdownMenuRadioItem key={option.value} value={option.value}>{option.label}</DropdownMenuRadioItem>)}
      </DropdownMenuRadioGroup>
    </DropdownMenuContent>
  </DropdownMenu>;
}

/** Flip: which side of the plane is cut away, the cut keeping its share; idle while nothing is cut. */
export function ClipFlipButton({ viewSettings, onViewSettingsPatch, bounds }) {
  const { clip, amount } = clipState(viewSettings, bounds);
  const flipped = { ...clip, invert: !clip.invert };
  return <ToolbarButton label="Flip clip" disabled={!clip.enabled} active={clip.enabled && clip.invert} aria-pressed={clip.invert}
    onClick={() => onViewSettingsPatch({ clip: { invert: flipped.invert, at: clipAtForAmount(flipped, bounds, amount / 100) } })}>
    <ArrowLeftRight className="size-3" strokeWidth={2} aria-hidden="true" />
  </ToolbarButton>;
}

// Clip's controls: its axis, then one slider, which applies the cut as it leaves zero and removes it
// at zero. `className` lays them out where they sit, and `sliderClassName` draws the slider.
export function CrossSectionControls({ viewSettings, onViewSettingsPatch, bounds, className = "flex min-w-0 items-center gap-2 px-2 py-1",
  sliderClassName = FILE_SHEET_PRECISION_SLIDER_CLASSES }) {
  const { clip, range, amount } = clipState(viewSettings, bounds);
  return <div className={className}>
    <ClipAxisSelect viewSettings={viewSettings} onViewSettingsPatch={onViewSettingsPatch} bounds={bounds} />
    <Slider thumbProps={{ "aria-label": "Clip amount" }} value={[amount]} min={0} max={100}
      step={0.1} disabled={!range}
      onValueChange={([value]) => onViewSettingsPatch({ clip: {
        enabled: value > 0, at: clipAtForAmount(clip, bounds, value / 100)
      } })}
      className={sliderClassName} />
  </div>;
}

// Explode's control: one slider, which applies the effect as it leaves zero and removes it at zero.
// `className` draws it where it sits (its sub-toolbar's slider).
export function ExplodeControls({ viewSettings, onViewSettingsPatch, className = FILE_SHEET_PRECISION_SLIDER_CLASSES }) {
  const exploded = normalizeExplodedViewSettings(viewSettings?.exploded);
  return <Slider thumbProps={{ "aria-label": "Explode amount" }}
    value={[exploded.enabled ? exploded.amount * 100 : 0]} min={0} max={100} step={1}
    onValueChange={([amount]) => onViewSettingsPatch({ exploded: { amount: amount / 100, enabled: amount > 0 } })}
    className={className} />;
}
