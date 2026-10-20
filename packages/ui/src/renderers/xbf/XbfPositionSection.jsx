import { memo } from "react";
import { TriangleAlert } from "lucide-react";
import { cn } from "@text-to-cad/ui/utils";
import { Slider } from "@text-to-cad/ui/primitives/slider";
import { TooltipHint } from "@text-to-cad/ui/primitives/tooltip";
import { xbfOtherWarnings, xbfPoseControls, xbfUnposable } from "@text-to-cad/core/lib/xbf/xbfKinematics.js";
import ToolPanel from "../kit/tools/ToolPanel.jsx";
import {
  FILE_SHEET_PRECISION_SLIDER_CLASSES, FileSheetSliderField, FileSheetStatusText, parseFileSheetNumberInput
} from "../kit/inspector/FileSheet.js";
import { DEFAULT_POSE_VALUE, KinematicsPoseRow, NO_PRESET_VALUE } from "../kit/inspector/kinematicsControls.jsx";

// An engine document's Position (the file sidebar's section, as a STEP's and a robot's): its pose
// row -- Default, the document as built, then its configurations -- and a slider per value of every
// pair a gear does not drive, within its limits: an angle for a revolute pair, a distance for a
// prismatic one, both for a cylindrical one, three angles for a spherical one. Writes pose the
// scene at once (the page's forward kinematics, `xbfKinematics.js`); nothing is fetched.

const isAngle = control => control.unit === "deg";
const format = (value, control) => {
  const rounded = Math.round(Number(value) * (isAngle(control) ? 10 : 1000)) / (isAngle(control) ? 10 : 1000);
  return isAngle(control) ? `${rounded}°` : `${rounded} mm`;
};

const ValueRow = memo(function ValueRow({ control, value, onChange }) {
  const write = next => onChange(control, Math.min(Math.max(Number(next), control.min), control.max));
  return <FileSheetSliderField label={control.label} value={format(value, control)}
    onValueCommit={text => write(parseFileSheetNumberInput(text, { fallback: value, min: control.min, max: control.max }))}
    valueInputProps={{ ariaLabel: `${control.label} value in ${control.unit}` }}>
    <TooltipHint content={`${format(control.min, control)} to ${format(control.max, control)}`}>
      <Slider className={cn(FILE_SHEET_PRECISION_SLIDER_CLASSES, "min-w-0")} min={control.min} max={control.max}
        step={isAngle(control) ? 1 : 0.1} value={[value]} onValueChange={next => write(next?.[0])}
        thumbProps={{ "aria-label": control.label }} />
    </TooltipHint>
  </FileSheetSliderField>;
});

/**
 * @param {{ kinematics: object, values: Record<string, number[]>, preset: string, changed: boolean,
 *   onValues: (values: Record<string, number[]>) => void, onPreset: (name: string) => void, onReset: () => void,
 *   active?: boolean }} props
 *   `values`: every pair's values now. `preset`: the configuration chosen, `DEFAULT_POSE_VALUE` as built,
 *   `NO_PRESET_VALUE` set by hand.
 */
export default function XbfPositionSection({ kinematics, values, preset, changed, onValues, onPreset, onReset, active = true }) {
  const controls = xbfPoseControls(kinematics);
  const unposable = xbfUnposable(kinematics);
  const others = xbfOtherWarnings(kinematics);
  const onChange = (control, next) => {
    const current = [...(values[control.pair] || [])];
    current[control.index] = next;
    onValues({ ...values, [control.pair]: current });
  };
  // In the pairs' order: a pair's controls, or, where the engine found it cannot be posed, why.
  const rows = (kinematics?.pairs || []).flatMap((pair) => {
    if (unposable.has(pair.label)) {
      return [<div key={`warning:${pair.label}`} className="px-2" data-xbf-unposable={pair.label} role="note"
        aria-label={`${pair.label} cannot be posed`}>
        <span className="block min-w-0 truncate text-tiny leading-4 text-muted-foreground">{pair.label}</span>
        <TooltipHint content={unposable.get(pair.label)}>
          <span className="flex min-w-0 items-start gap-1 text-micro leading-4 text-amber-600 dark:text-amber-400">
            <TriangleAlert className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
            <span className="line-clamp-3">{`Cannot be posed: ${unposable.get(pair.label)}`}</span>
          </span>
        </TooltipHint>
      </div>];
    }
    return controls.filter(control => control.pair === pair.label).map(control => <ValueRow key={control.id} control={control}
      value={values[control.pair]?.[control.index] ?? 0} onChange={onChange} />);
  });
  return <ToolPanel id="position" title="Position" label="Position controls" fit="details" changed={changed} hidden={!active}>
    <div className="space-y-1.5 pb-1.5 pt-0.5" data-xbf-position="">
      {controls.length ? <KinematicsPoseRow poses={Object.keys(kinematics.configurations || {}).map(name => ({ value: name, label: name }))}
        activeValue={preset} onSelect={onPreset} onReset={onReset} /> : null}
      {rows}
      {others.map(text => <FileSheetStatusText key={text} className="text-amber-600 dark:text-amber-400">{text}</FileSheetStatusText>)}
    </div>
  </ToolPanel>;
}

export { DEFAULT_POSE_VALUE, NO_PRESET_VALUE };
