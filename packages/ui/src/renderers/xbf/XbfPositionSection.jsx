import { useCallback, useMemo, useState } from "react";
import { Slider } from "@text-to-cad/ui/primitives/slider";
import {
  xbfCouplingControls, xbfCouplingValues, xbfCouplingWrite, xbfCouplings, xbfDrivenPairs, xbfLockedPairs, xbfPoseControls,
  xbfPoseValues, xbfSamePose, xbfUnposable, xbfOtherWarnings
} from "@text-to-cad/core/lib/xbf/xbfKinematics.js";
import ToolPanel from "../kit/tools/ToolPanel.jsx";
import {
  FILE_SHEET_PRECISION_SLIDER_CLASSES, FileSheetSliderField, FileSheetStatusText, parseFileSheetNumberInput
} from "../kit/inspector/FileSheet.js";
import { DEFAULT_POSE_VALUE, KinematicsPoseRow, NO_PRESET_VALUE } from "../kit/inspector/kinematicsControls.jsx";

// An engine document's Position: the file sidebar's section exactly as a STEP's is (the STEP
// renderer's `PoseControlsSection.js` and `MotionControlsSection.js`, copied: one again at the
// renderers' migration) -- headed "Position", Reset in its heading, the Pose row (Default, the
// document as built, then its configurations), then a slider per coupling (one control driving
// several joints by ratio, 0 as built), then a slider per value of every pair: an angle for a
// revolute pair, a distance for a prismatic one, both for a cylindrical one, three angles for a
// spherical one, and a pair a gear drives too, written through its gear as a STEP's coupled row
// is. A locked coupling's joints have no slider: it is their only control. Writes pose the scene
// at once (the page's forward kinematics, `xbfKinematics.js`).

const NO_COUPLINGS = Object.freeze({});

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
 * Every pair row of the section, in the pairs' order: each posable pair's values, and each pair a
 * gear drives (`driver`: what its row writes through, and the ratio). A locked coupling's joints
 * have none.
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
 * Position's pose of a document's mechanism: the configuration chosen (`preset`), or values set by
 * hand; every pair's values at it (`values`), and whether that is not as built (`applied`); and
 * each coupling's slider (`couplings`), where the pose stands on it until a slider moves, then
 * where it was last set: moving a joint after leaves a coupling's slider where it was, as the Pose
 * row keeps what it chose. `setValues(values, couplings?)` takes every pair's values (a gear's and
 * a locked coupling's are their drivers'), `choosePreset(name)` a configuration, `reset()` as built.
 */
export function useXbfPose(kinematics) {
  const key = kinematics ? JSON.stringify([kinematics.pairs, kinematics.configurations, kinematics.asBuilt, kinematics.couplings || null]) : "";
  const [pose, setPose] = useState({ key: "", preset: DEFAULT_POSE_VALUE, given: null, couplings: null });
  const current = pose.key === key ? pose : { key, preset: DEFAULT_POSE_VALUE, given: null, couplings: null };
  const values = useMemo(() => {
    if (!kinematics) return null;
    // A locked coupling's joints are its value's: the rest is what was set by hand.
    const locked = Object.fromEntries(Object.entries(xbfCouplings(kinematics)).filter(([, coupling]) => coupling.lock)
      .map(([name]) => [name, Number(current.couplings?.[name]) || 0]));
    const preset = current.preset !== DEFAULT_POSE_VALUE && current.preset !== NO_PRESET_VALUE ? current.preset : null;
    try {
      return xbfPoseValues(kinematics, current.given ? { ...current.given, ...locked } : preset);
    } catch {
      return xbfPoseValues(kinematics);
    }
  }, [kinematics, current.given, current.preset, current.couplings]);
  const asBuilt = useMemo(() => (kinematics ? xbfPoseValues(kinematics) : null), [kinematics]);
  const applied = Boolean(values && asBuilt && !xbfSamePose(values, asBuilt));
  const preset = !applied ? DEFAULT_POSE_VALUE
    : current.preset !== NO_PRESET_VALUE && current.preset !== DEFAULT_POSE_VALUE && !current.given ? current.preset : NO_PRESET_VALUE;
  const couplings = useMemo(() => current.couplings || (kinematics && values ? xbfCouplingValues(kinematics, values) : NO_COUPLINGS),
    [kinematics, values, current.couplings]);
  const setValues = useCallback((next, nextCouplings = null) => {
    const driven = xbfDrivenPairs(kinematics);
    const locked = xbfLockedPairs(kinematics);
    setPose({ key, preset: NO_PRESET_VALUE, couplings: nextCouplings || couplings,
      given: Object.fromEntries(Object.entries(next).filter(([label]) => !driven.has(label) && !locked.has(label))) });
  }, [kinematics, key, couplings]);
  const choosePreset = useCallback(name => setPose({ key, preset: name, given: null, couplings: null }), [key]);
  const reset = useCallback(() => setPose({ key, preset: DEFAULT_POSE_VALUE, given: null, couplings: null }), [key]);
  return { values, asBuilt, applied, preset, couplings, setValues, choosePreset, reset };
}

/**
 * @param {{ kinematics: object, values: Record<string, number[]>, couplings?: Record<string, number>, preset: string,
 *   changed: boolean, onValues: (values: Record<string, number[]>, couplings?: Record<string, number>) => void,
 *   onPreset: (name: string) => void, onReset: () => void }} props
 *   `values`: every pair's values now. `couplings`: each coupling's slider (where `values` stand on
 *   it when not given). `preset`: the configuration chosen, `DEFAULT_POSE_VALUE` as built,
 *   `NO_PRESET_VALUE` set by hand. `onValues` takes every pair's values, and every coupling's when a
 *   coupling's slider moved.
 */
export default function XbfPositionSection({ kinematics, values, couplings = null, preset, changed, onValues, onPreset, onReset }) {
  const controls = xbfPositionRows(kinematics);
  const coupled = xbfCouplingControls(kinematics);
  const shown = couplings || xbfCouplingValues(kinematics, values);
  const poseNames = Object.keys(kinematics?.configurations || {}).filter(name => String(name || "").trim());
  const unposable = xbfUnposable(kinematics);
  const others = xbfOtherWarnings(kinematics);
  const change = (control, next) => {
    const written = writeXbfPosition(values, control, next);
    if (written) onValues(written);
  };
  const couple = (control, next) => {
    const written = xbfCouplingWrite(kinematics, values, shown, control.coupling, next);
    if (written) onValues(written.values, written.couplings);
  };
  const slider = (control, currentValue, write, title) => <FileSheetSliderField key={control.id} label={control.label}
    labelTitle={title} value={`${formatControlNumber(currentValue)}${unitSuffix(control.unit)}`}
    onValueCommit={text => write(control, parseFileSheetNumberInput(text, { fallback: currentValue, min: control.min, max: control.max }))}
    valueInputProps={{ ariaLabel: `${control.label} slider value` }}>
    <Slider className={FILE_SHEET_PRECISION_SLIDER_CLASSES} value={[Number(currentValue) || 0]} min={control.min} max={control.max}
      step={controlStep(control)} onValueChange={next => write(control, next?.[0] ?? currentValue)} thumbProps={{ "aria-label": control.label }} />
  </FileSheetSliderField>;
  return <ToolPanel id="position" title="Position" label="Position controls" fit="details" changed={changed}>
    <div className="space-y-1.5 pb-1.5 pt-0.5">
      {poseNames.length || controls.length || coupled.length ? <KinematicsPoseRow poses={poseNames.map(name => ({ value: name, label: name }))}
        activeValue={preset} onSelect={onPreset} onReset={onReset} /> : null}
      {coupled.map(control => slider(control, Number(shown[control.coupling]) || 0, couple,
        `${control.label} · ${control.lock ? "drives" : "couples"} ${control.pairs.join(", ")}`))}
      {controls.map((control) => {
        const currentValue = values[control.pair]?.[control.index] ?? 0;
        return slider(control, currentValue, change, control.driver ? `${control.label} · driven by ${control.driver.control}` : control.label);
      })}
      {!controls.length && !coupled.length && !poseNames.length ? <FileSheetStatusText>No pose controls.</FileSheetStatusText> : null}
      {/* What the document's mechanism cannot pose, where a STEP's sidecar would have refused the joint. */}
      {[...unposable].map(([label, why]) => <FileSheetStatusText key={label}>{`${label} cannot be posed: ${why}`}</FileSheetStatusText>)}
      {others.map(text => <FileSheetStatusText key={text}>{text}</FileSheetStatusText>)}
    </div>
  </ToolPanel>;
}

export { DEFAULT_POSE_VALUE, NO_PRESET_VALUE };
