import PoseControlsSection, { poseControlsHaveContent, posePresetSelection } from "./PoseControlsSection.js";
import { DEFAULT_POSE_VALUE, KinematicsPoseRow, MotionResetButton, NO_PRESET_VALUE, positionValuesAreDefault } from "../../../kit/inspector/kinematicsControls.jsx";
import { openingControlValues } from "@text-to-cad/core/common/articulation.js";

// The Position panel for STEP (the tool stack's, shown while the Position tool is up): its
// named poses, its joint values and the Reset that puts them back. Animation is a tool of its
// own (and preview's playbar), not a section, so a file with routines and no joints has no
// Position at all. One host command still resets all motion, including pending playback and pose frames.
export function buildPositionSection({ poseRuntime = null } = {}) {
  if (!poseControlsHaveContent(poseRuntime)) return null;
  const onReset = poseRuntime?.onResetMotion || poseRuntime?.onResetParameters;
  // Kept while the pose is not the default: the named pose it is at, or Custom.
  const { activePose, poseNames } = posePresetSelection(poseRuntime);
  const custom = activePose !== DEFAULT_POSE_VALUE && !positionValuesAreDefault(poseRuntime?.parameterValues,
    openingControlValues(poseRuntime?.definition?.articulation));
  return {
    id: "position",
    title: "Position",
    custom,
    summary: activePose && activePose !== NO_PRESET_VALUE && activePose !== DEFAULT_POSE_VALUE ? activePose : "Custom",
    onReset,
    // The Pose row the kept panel shows, where there is a named pose to choose: Default, the poses, Custom.
    poseRow: poseNames.length ? <KinematicsPoseRow poses={poseNames.map(name => ({ value: name, label: name }))} activeValue={activePose}
      onReset={onReset} onSelect={name => poseRuntime?.onApplyPose?.(name)} /> : null,
    // Reset is the panel heading's, beside its title.
    actions: onReset ? <MotionResetButton onReset={onReset} /> : null,
    content: (
      <div className="space-y-1.5 pb-1.5 pt-0.5">
        <PoseControlsSection runtime={poseRuntime} />
      </div>
    ),
  };
}
