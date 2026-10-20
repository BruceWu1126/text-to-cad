import { memo } from "react";
import { cn } from "@text-to-cad/ui/utils";
import { Slider } from "@text-to-cad/ui/primitives/slider";
import { TooltipHint } from "@text-to-cad/ui/primitives/tooltip";
import { xbfPoseControls } from "@text-to-cad/core/lib/xbf/xbfKinematics.js";
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
  const controls = kinematics?.loop?.length ? [] : xbfPoseControls(kinematics);
  const onChange = (control, next) => {
    const current = [...(values[control.pair] || [])];
    current[control.index] = next;
    onValues({ ...values, [control.pair]: current });
  };
  return <ToolPanel id="position" title="Position" label="Position controls" fit="details" changed={changed} hidden={!active}>
    {kinematics?.loop?.length
      ? <FileSheetStatusText className="py-2">{`Not a tree: pairs ${kinematics.loop.join(", ")} close a loop, which has no pose without a solver.`}</FileSheetStatusText>
      : <div className="space-y-1.5 pb-1.5 pt-0.5" data-xbf-position="">
        <KinematicsPoseRow poses={Object.keys(kinematics.configurations || {}).map(name => ({ value: name, label: name }))}
          activeValue={preset} onSelect={onPreset} onReset={onReset} />
        {controls.map(control => <ValueRow key={control.id} control={control} value={values[control.pair]?.[control.index] ?? 0}
          onChange={onChange} />)}
      </div>}
  </ToolPanel>;
}

export { DEFAULT_POSE_VALUE, NO_PRESET_VALUE };
