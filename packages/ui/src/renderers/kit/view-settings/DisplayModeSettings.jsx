import { Minus, Plus, RotateCcw } from "lucide-react";
import { resolveViewModeSettings, viewModeChanged } from "@text-to-cad/core/common/viewSettings.js";
import { Button } from "@text-to-cad/ui/primitives/button";
import { ScrollArea } from "@text-to-cad/ui/primitives/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@text-to-cad/ui/primitives/select";
import { Slider } from "@text-to-cad/ui/primitives/slider";
import { TooltipHint } from "@text-to-cad/ui/primitives/tooltip";
import { cn } from "@text-to-cad/ui/utils";
import {
  FILE_SHEET_SELECT_TRIGGER_CLASSES, FileSheetCheckboxRow, FileSheetColorPicker, FileSheetSettingRow,
  FileSheetSettingsSection, FileSheetValueInput
} from "../inspector/FileSheet.js";
import { SUB_TOOLBAR_SLIDER_CLASS } from "../tools/SubToolbar.jsx";

// Every control fills the row's control column at the row's 24px height.
const CONTROL_CLASS = "!h-6 w-full min-w-0";
// A dropdown or colour is as wide as its value, at the row's right end, where every control ends.
const CHOICE_WIDTH_CLASS = "ml-auto w-fit max-w-full";

// `label` is what the row shows, short under its section's heading ("Opacity" under Grid); `name`
// is the control's accessible name, whole ("Grid opacity").
function Choice({ label, name = label, value, options, onChange, disabled = false }) {
  return <FileSheetSettingRow label={label} disabled={disabled}>
    <Select value={value} onValueChange={onChange} disabled={disabled}>
      <SelectTrigger aria-label={name} size="sm" className={cn(FILE_SHEET_SELECT_TRIGGER_CLASSES, CONTROL_CLASS, CHOICE_WIDTH_CLASS)}><SelectValue /></SelectTrigger>
      <SelectContent>{options.map(([id, title]) => <SelectItem key={id} value={id}>{title}</SelectItem>)}</SelectContent>
    </Select>
  </FileSheetSettingRow>;
}
function Range({ label, name = label, value, onChange, min = 0, max = 1, step = 0.01, disabled = false,
  scale = 100, format = v => `${Math.round(v * 100)}%` }) {
  const commit = draft => {
    // FileSheetValueInput also commits an untouched blur. Its formatted readout may be
    // rounded, so only a changed draft is an edit to this mode's sparse settings.
    if (draft === format(value)) return;
    const parsed = Number.parseFloat(draft) / scale;
    if (!Number.isFinite(parsed)) return;
    const clamped = Math.min(max, Math.max(min, parsed));
    const precision = String(step).split(".")[1]?.length || 0;
    const next = Number(clamped.toFixed(precision));
    if (next !== value) onChange(next);
  };
  return <FileSheetSettingRow label={label} disabled={disabled}>
    <Slider className={cn(SUB_TOOLBAR_SLIDER_CLASS, "px-1")} value={[value]} min={min} max={max} step={step} disabled={disabled}
      thumbProps={{ "aria-label": name }} onValueChange={([next]) => onChange(next)} />
    <FileSheetValueInput ariaLabel={`${name} value`} value={format(value)} onValueCommit={commit} disabled={disabled}
      className="h-6 w-13 px-1.5" />
  </FileSheetSettingRow>;
}
function Color({ label, name = label, value, onChange, disabled = false }) {
  return <FileSheetSettingRow label={label} disabled={disabled}>
    <FileSheetColorPicker aria-label={name} value={value} onChange={onChange} disabled={disabled} className={cn("!h-6 min-w-0", CHOICE_WIDTH_CLASS)} />
  </FileSheetSettingRow>;
}
const Toggle = ({ label, value, onChange }) => <FileSheetCheckboxRow label={label} checked={value} onCheckedChange={onChange} />;

/** By part's palette: a swatch per colour, then add and remove, wrapping in the control column. */
function Palette({ colors, onChange }) {
  const iconButton = (label, Icon, disabled, onClick) => <TooltipHint content={label}>
    <Button type="button" variant="ghost" size="icon-xs" aria-label={label} disabled={disabled} onClick={onClick}
      className="size-5 text-muted-foreground hover:text-foreground"><Icon className="size-3.5" aria-hidden="true" /></Button>
  </TooltipHint>;
  return <FileSheetSettingRow label="Palette" className="h-auto min-h-6 items-start [&>span]:pt-1">
    <div className="flex flex-wrap gap-1 py-0.5">
      {colors.map((value, index) => <FileSheetColorPicker key={index} aria-label={`Part colour ${index + 1}`} value={value} showValue={false}
        className="!size-5 justify-center p-0" swatchClassName="size-3"
        onChange={color => onChange(colors.map((entry, i) => i === index ? color : entry))} />)}
      {iconButton("Add colour", Plus, colors.length >= 50, () => onChange([...colors, "#94a3b8"]))}
      {iconButton("Remove last colour", Minus, colors.length <= 1, () => onChange(colors.slice(0, -1)))}
    </div>
  </FileSheetSettingRow>;
}

/** All of a mode's real settings, visible without opening sections or selecting that mode. */
export function DisplayModeSettings({ mode, display, appearance = "light", lightingQuality = "preview", features, onPatch, onReset }) {
  const options = { appearance, lightingQuality, features };
  const settings = resolveViewModeSettings(display, mode, options);
  const patch = (name, value) => onPatch({ [name]: value });
  const color = (name, title, disabled = false) => <Color label="Colour" name={title} value={settings[name].color} disabled={disabled} onChange={color => patch(name, { color })} />;
  const opacity = (name, title, disabled = false) => <Range label="Opacity" name={title} value={settings[name].opacity} disabled={disabled} onChange={opacity => patch(name, { opacity })} />;
  // A feature that switches on and off is its section's heading checkbox; off, the section folds to it.
  const gate = name => ({ enabled: settings[name].enabled, onEnabledChange: enabled => patch(name, { enabled }) });
  return <div className="flex min-h-0 flex-1 flex-col">
    <ScrollArea className="min-h-0 flex-1">
      <div className="p-1">
        {settings.parts && <FileSheetSettingsSection title="Part colours">
          <Choice label="Source" name="Colour source" value={settings.parts.colorMode} options={[["original", "Original"], ["single", "Single colour"], ["by-part", "By part"]]}
            onChange={colorMode => patch("parts", { colorMode })} />
          {settings.parts.colorMode === "by-part"
            ? <Palette colors={settings.parts.colors} onChange={colors => patch("parts", { colors })} />
            : color("parts", "Part colour", settings.parts.colorMode !== "single")}
        </FileSheetSettingsSection>}
        {(settings.flat !== undefined || settings.opacity !== undefined) && <FileSheetSettingsSection title="Surfaces">
          {settings.flat !== undefined && <Toggle label="Flat shading" value={settings.flat} onChange={flat => patch("flat", flat)} />}
          {settings.opacity !== undefined && <Range label="Opacity" name="Surface opacity" value={settings.opacity} onChange={opacity => patch("opacity", opacity)} />}
        </FileSheetSettingsSection>}
        {settings.edges && <FileSheetSettingsSection title="Edges" {...(settings.edges.enabled !== undefined ? gate("edges") : {})}>
          {color("edges", "Edge colour")}
        </FileSheetSettingsSection>}
        {settings.lighting && <FileSheetSettingsSection title="Lighting">
          <Choice label="Quality" value={settings.lighting.quality} options={[["preview", "Preview"], ["final", "Final"]]} onChange={quality => patch("lighting", { quality })} />
          <Range label="Exposure" value={settings.lighting.exposure} min={-5} max={5} step={0.1} scale={1} format={v => `${v.toFixed(1)} EV`} onChange={exposure => patch("lighting", { exposure })} />
          <Range label="Rotation" value={settings.lighting.rotation} min={-180} max={180} step={1} scale={1} format={v => `${v}°`} onChange={rotation => patch("lighting", { rotation })} />
          <Range label="Softbox" name="Softbox size" value={settings.lighting.size} min={0.25} max={3} step={0.05} scale={1} format={v => `${v.toFixed(2)}×`} onChange={size => patch("lighting", { size })} />
          <Range label="Fill" value={settings.lighting.fill} onChange={fill => patch("lighting", { fill })} />
        </FileSheetSettingsSection>}
        {settings.background && <FileSheetSettingsSection title="Background">
          {color("background", "Background colour")}{opacity("background", "Background opacity")}
        </FileSheetSettingsSection>}
        {settings.floor && <FileSheetSettingsSection title="Floor" {...gate("floor")}>
          {color("floor", "Floor colour")}{opacity("floor", "Floor opacity")}
          <Choice label="Placement" value={settings.floor.placement} options={[["lowest", "Lowest point"], ["origin", "Model origin"]]} onChange={placement => patch("floor", { placement })} />
          <Choice label="Finish" value={settings.floor.finish} options={[["matte", "Matte"], ["glossy", "Glossy"]]} onChange={finish => patch("floor", { finish })} />
        </FileSheetSettingsSection>}
        {[["grid", "Grid"], ["axes", "Axes"]].map(([name, title]) => settings[name] && <FileSheetSettingsSection key={name} title={title} {...gate(name)}>
          {color(name, `${title} colour`)}{opacity(name, `${title} opacity`)}
          {name === "grid" && <Range label="Density" name="Grid density" value={settings.grid.density} min={0.25} max={4} step={0.25} scale={1}
            format={value => `${value}×`} onChange={density => patch("grid", { density })} />}
        </FileSheetSettingsSection>)}
      </div>
    </ScrollArea>
    <div className="shrink-0 border-t border-border p-1">
      <Button type="button" variant="ghost" size="xs" className="h-7 w-full justify-start gap-2 px-2 text-tiny font-normal text-muted-foreground hover:text-foreground"
        disabled={!viewModeChanged(display, mode, options)} onClick={onReset}>
        <RotateCcw className="size-3.5" aria-hidden="true" />Reset to default
      </Button>
    </div>
  </div>;
}
