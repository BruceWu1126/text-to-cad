import {
  DropdownMenuCheckboxItem, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuSeparator
} from "@text-to-cad/ui/primitives/dropdown-menu";
import { SpeedSubmenu } from "../PlaybackMenu.jsx";
import { ToolSettingsMenu } from "../ToolModeMenu.jsx";
import ToolPanel from "../ToolPanel.jsx";
import { PLAYBACK_SPEEDS, formatSeconds } from "./ViewportAnimationBar.js";

/**
 * The Animation tool's menu, its chevron on the strip: the file's routines when it has more than one
 * (choosing one plays it next and takes the tool up), then its settings — Speed, Loop and Autoplay,
 * the file's (Autoplay is preview's Playback settings' too, `PlaybackMenu.jsx`; their Speed and
 * Loop are preview's own). Ticking a checkbox leaves the menu open. The tool's button plays and
 * pauses; its playbar is the viewport's, at the bottom centre while the tool is up.
 *
 * @param {{ runtime: object, autoplay: boolean, onAutoplayChange(value: boolean): void,
 *   onRoutine(id: string): void, disabled?: boolean }} props  `runtime` is the playbar runtime, its
 *   Speed and Loop already writing the file's choice.
 */
export function AnimationToolMenu({ runtime, autoplay, onAutoplayChange, onRoutine, disabled = false }) {
  const clips = runtime.clips;
  const keepOpen = event => event.preventDefault();
  return <ToolSettingsMenu label="Animation settings" trigger="chevron" disabled={disabled}>
    {clips.length > 1 ? <>
      <DropdownMenuLabel>Routine</DropdownMenuLabel>
      <DropdownMenuRadioGroup value={runtime.activeClipId} onValueChange={onRoutine}>
        {clips.map(clip => <DropdownMenuRadioItem key={clip.id} value={clip.id}>
          <span className="min-w-0 truncate">{clip.label}</span>
        </DropdownMenuRadioItem>)}
      </DropdownMenuRadioGroup>
      <DropdownMenuSeparator />
    </> : null}
    <SpeedSubmenu label="Speed" name="Animation speed" value={Number(runtime.speed) || 1} values={PLAYBACK_SPEEDS} onChange={runtime.onSpeedChange} />
    <DropdownMenuCheckboxItem checked={runtime.loopEnabled !== false} onSelect={keepOpen}
      onCheckedChange={checked => runtime.onLoopToggle(checked === true)}>Loop</DropdownMenuCheckboxItem>
    <DropdownMenuCheckboxItem checked={autoplay === true} onSelect={keepOpen}
      onCheckedChange={checked => onAutoplayChange(checked === true)}>Autoplay</DropdownMenuCheckboxItem>
  </ToolSettingsMenu>;
}

/**
 * A routine's frame kept while the Animation tool is not up: one line in the stack, as a kept pose
 * is, headed "Animation" with the time it paused at, and an X ("Reset animation") that puts the
 * model back at rest. A press on the Animation tool takes it up again as it was.
 *
 * @param {{ runtime: object }} props  `runtime` is the playbar runtime, paused past its start.
 */
export function KeptAnimationPanel({ runtime }) {
  return <ToolPanel id="animation-kept" title="Animation" summary={formatSeconds(runtime.elapsedSec)} label="Animation kept"
    collapsible={false} closeLabel="Reset animation" onClose={() => runtime.onRelease()} />;
}
