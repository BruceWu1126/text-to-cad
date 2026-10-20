import { Slider } from "@text-to-cad/ui/primitives/slider";
import { xbfDrivenPairs, xbfPoseControls, xbfUnposable, xbfOtherWarnings } from "@text-to-cad/core/lib/xbf/xbfKinematics.js";
import ToolPanel from "../kit/tools/ToolPanel.jsx";
import {
  FILE_SHEET_PRECISION_SLIDER_CLASSES, FileSheetSliderField, FileSheetStatusText, parseFileSheetNumberInput
} from "../kit/inspector/FileSheet.js";
import { DEFAULT_POSE_VALUE, KinematicsPoseRow, NO_PRESET_VALUE } from "../kit/inspector/kinematicsControls.jsx";

// An engine document's Position: the file sidebar's section exactly as a STEP's is (the STEP
// renderer's `PoseControlsSection.js` and `MotionControlsSection.js`, copied: one again at the
// renderers' migration) -- headed "Position", Reset in its heading, the Pose row (Default, the
// document as built, then its configurations), then a slider per value of every pair: an angle
// for a revolute pair, a distance for a prismatic one, both for a cylindrical one, three angles
// for a spherical one, and a pair a gear drives too, written through its gear as a STEP's coupled
// row is. Writes pose the scene at once (the page's forward kinematics, `xbfKinematics.js`).

// A value as its compact field shows it, as a STEP's row does.
function formatControlNumber(value) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) return "0";
  if (Math.abs(numericValue) >= 100) return numericValue.toFixed(0);
  if (Math.abs(numericValue) >= 10) return numericValue.toFixed(1);
  return numericValue.toFixed(2);
}

// A value's unit as its compact field shows it: degrees as the sign ("90.0°"), others after a space.
function unitSuffix(unit) {
  const text = String(unit || "").trim();
  return !text ? "" : /^(deg|degrees?|°)$/i.test(text) ? "°" : ` ${text}`;
}

// A slider's step from its range: about a thousand steps across it, never coarser than a
// hundredth (a STEP's `resolveParameterNumberControlStep`).
function controlStep(control) {
  const range = Math.abs(Number(control.max) - Number(control.min));
  if (!(range > 0)) return 0.01;
  return Number(Math.max(10 ** Math.floor(Math.log10(range / 1000)), 0.000001).toPrecision(12));
}

/**
 * Every row of the section, in the pairs' order: each posable pair's values, and each pair a gear
 * drives (`driver`: what its row writes through, and the ratio).
 */
export function xbfPositionRows(kinematics) {
  const own = xbfPoseControls(kinematics);
  const driven = xbfDrivenPairs(kinematics);
  const unposable = xbfUnposable(kinematics);
  const rows = [];
  for (const pair of kinematics?.pairs || []) {
    if (pair.type === "gear" || unposable.has(pair.label)) continue;
    if (driven.has(pair.label)) {
      const { driver, ratio, gear } = driven.get(pair.label);
      const source = own.find(control => control.pair === driver && control.index === 0);
      if (!source || !ratio) continue;
      const ends = [source.min * ratio, source.max * ratio];
      rows.push({ id: pair.label, pair: pair.label, index: 0, label: pair.label, unit: pair.units?.[0] || "deg",
        min: Math.min(...ends), max: Math.max(...ends), driver: { control: driver, ratio, gear } });
      continue;
    }
    rows.push(...own.filter(control => control.pair === pair.label));
  }
  return rows;
}

/**
 * Every pair's values once `control` is set to `next` (within its limits), or null for no number:
 * a row a gear drives writes its driver, any other its own value. The section's sliders and
 * fields and Position's knobs all write through here.
 */
export function writeXbfPosition(values, control, next) {
  const value = Math.min(Math.max(Number(next), control.min), control.max);
  if (!Number.isFinite(value)) return null;
  const pair = control.driver ? control.driver.control : control.pair;
  const index = control.driver ? 0 : control.index;
  const current = [...(values[pair] || [])];
  current[index] = control.driver ? value / control.driver.ratio : value;
  return { ...values, [pair]: current };
}

/**
 * @param {{ kinematics: object, values: Record<string, number[]>, preset: string, changed: boolean,
 *   onValues: (values: Record<string, number[]>) => void, onPreset: (name: string) => void, onReset: () => void }} props
 *   `values`: every pair's values now. `preset`: the configuration chosen, `DEFAULT_POSE_VALUE` as built,
 *   `NO_PRESET_VALUE` set by hand.
 */
export default function XbfPositionSection({ kinematics, values, preset, changed, onValues, onPreset, onReset }) {
  const controls = xbfPositionRows(kinematics);
  const poseNames = Object.keys(kinematics?.configurations || {}).filter(name => String(name || "").trim());
  const unposable = xbfUnposable(kinematics);
  const others = xbfOtherWarnings(kinematics);
  const change = (control, next) => {
    const written = writeXbfPosition(values, control, next);
    if (written) onValues(written);
  };
  return <ToolPanel id="position" title="Position" label="Position controls" fit="details" changed={changed}>
    <div className="space-y-1.5 pb-1.5 pt-0.5">
      {poseNames.length || controls.length ? <KinematicsPoseRow poses={poseNames.map(name => ({ value: name, label: name }))}
        activeValue={preset} onSelect={onPreset} onReset={onReset} /> : null}
      {controls.map((control) => {
        const currentValue = values[control.pair]?.[control.index] ?? 0;
        return <FileSheetSliderField key={control.id} label={control.label}
          labelTitle={control.driver ? `${control.label} · driven by ${control.driver.control}` : control.label}
          value={`${formatControlNumber(currentValue)}${unitSuffix(control.unit)}`}
          onValueCommit={text => change(control, parseFileSheetNumberInput(text, { fallback: currentValue, min: control.min, max: control.max }))}
          valueInputProps={{ ariaLabel: `${control.label} slider value` }}>
          <Slider className={FILE_SHEET_PRECISION_SLIDER_CLASSES} value={[Number(currentValue) || 0]} min={control.min} max={control.max}
            step={controlStep(control)} onValueChange={next => change(control, next?.[0] ?? currentValue)} thumbProps={{ "aria-label": control.label }} />
        </FileSheetSliderField>;
      })}
      {!controls.length && !poseNames.length ? <FileSheetStatusText>No pose controls.</FileSheetStatusText> : null}
      {/* What the document's mechanism cannot pose, where a STEP's sidecar would have refused the joint. */}
      {[...unposable].map(([label, why]) => <FileSheetStatusText key={label}>{`${label} cannot be posed: ${why}`}</FileSheetStatusText>)}
      {others.map(text => <FileSheetStatusText key={text}>{text}</FileSheetStatusText>)}
    </div>
  </ToolPanel>;
}

export { DEFAULT_POSE_VALUE, NO_PRESET_VALUE };
