import { Pause, Play, RotateCcw } from "lucide-react";
import { ToolbarButton } from "@text-to-cad/ui/primitives/toolbar-button";
import PlaybackMenu, { RoutineMenu } from "../PlaybackMenu.jsx";
import SubToolbar, { SLIDER_SUB_TOOLBAR_CLASS, SUB_TOOLBAR_SLIDER_CLASS } from "../SubToolbar.jsx";
import { AnimationTimeControl } from "./ViewportAnimationBar.js";

/**
 * The Animation tool's sub-toolbar (`kit/tools/SubToolbar.jsx`), one compact row under the strip
 * while it is up, as preview's playbar is: the routines (a playlist, for a file with several), Play
 * or Pause, the scrubber, the Playback settings (Speed, Loop, Autoplay) — the playbar's own
 * menus (`PlaybackMenu.jsx`), opening down from the row — and Reset, last as Explode's and Clip's
 * is: it stops the routine and puts the model back at rest, idle while it is there already. There is no playbar under the model in
 * the tools view; preview has its own.
 *
 * @param {{ runtime: object, autoplay: boolean, onAutoplayChange(value: boolean): void, disabled?: boolean }} props
 *   `runtime` is the playbar runtime, its Speed and Loop already writing the file's choice.
 */
export function AnimationToolbar({ runtime, autoplay, onAutoplayChange, disabled = false }) {
  const playing = runtime?.playing === true;
  const atRest = !playing && !(Number(runtime?.elapsedSec) > 0);
  return <SubToolbar label="Animation controls" className={SLIDER_SUB_TOOLBAR_CLASS} data-animation-toolbar="">
    <RoutineMenu animation={runtime} side="bottom" disabled={disabled} />
    <ToolbarButton tooltip={false} disabled={disabled} label={`${playing ? "Pause" : "Play"} animation`} onClick={() => runtime?.onPlayToggle?.()}>
      {playing ? <Pause className="size-3" strokeWidth={2} aria-hidden="true" /> : <Play className="size-3" strokeWidth={2} aria-hidden="true" />}
    </ToolbarButton>
    <AnimationTimeControl runtime={runtime} disabled={disabled} className={SUB_TOOLBAR_SLIDER_CLASS} />
    <PlaybackMenu animation={runtime} side="bottom" disabled={disabled} autoplay={autoplay} onAutoplayChange={onAutoplayChange} />
    <ToolbarButton label="Reset animation" disabled={disabled || atRest} onClick={() => runtime?.onRelease?.()}>
      <RotateCcw className="size-3" strokeWidth={2} aria-hidden="true" />
    </ToolbarButton>
  </SubToolbar>;
}
