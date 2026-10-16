import { useRef, useState } from "react";
import { RotateCcw } from "lucide-react";
import { resolveViewModeSettings, viewModeChanged, VIEW_MODES } from "@text-to-cad/core/common/viewSettings.js";
import {
  DropdownMenuCheckboxItem, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup,
  DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuSub, DropdownMenuSubContent, DropdownMenuSubTrigger
} from "@text-to-cad/ui/primitives/dropdown-menu";
import { ScrollArea } from "@text-to-cad/ui/primitives/scroll-area";
import { Slider } from "@text-to-cad/ui/primitives/slider";
import { FLOATING_SURFACE_CLASS } from "../../../lib/floatingSurface.js";

const keepOpen = event => event.preventDefault();
const keepKeys = event => { if (event.key !== "Escape") event.stopPropagation(); };
const PALETTE = ["#253443", "#ffffff", "#94a3b8", "#ef4444", "#f97316", "#eab308", "#22c55e", "#3b82f6", "#a855f7"];
const WHEEL = { background: "conic-gradient(red, yellow, lime, cyan, blue, magenta, red)" };
const swatch = color => ({ backgroundColor: color, boxShadow: "inset 0 0 0 1px color-mix(in oklab, currentColor 35%, transparent)" });

function Submenu({ label, children, glyph = null }) {
  const [open, setOpen] = useState(false);
  const trigger = useRef(null);
  return <DropdownMenuSub open={open} onOpenChange={setOpen}>
    <DropdownMenuSubTrigger ref={trigger}>{glyph}{label}</DropdownMenuSubTrigger>
    <DropdownMenuSubContent aria-label={label} onEscapeKeyDown={event => { event.preventDefault(); setOpen(false); trigger.current?.focus(); }} sideOffset={4} collisionPadding={8} className={`${FLOATING_SURFACE_CLASS} flex max-h-[var(--radix-dropdown-menu-content-available-height)] flex-col overflow-hidden`}><ScrollArea className="min-h-0 flex-1">{children}</ScrollArea></DropdownMenuSubContent>
  </DropdownMenuSub>;
}
function Choice({ label, value, options, onChange }) {
  return <Submenu label={label}><DropdownMenuRadioGroup value={value} onValueChange={onChange}>
    {options.map(([id, name]) => <DropdownMenuRadioItem key={id} value={id} onSelect={keepOpen}>{name}</DropdownMenuRadioItem>)}
  </DropdownMenuRadioGroup></Submenu>;
}
function Toggle({ label, value, onChange }) {
  return <DropdownMenuCheckboxItem checked={value} onCheckedChange={onChange} onSelect={keepOpen}>{label}</DropdownMenuCheckboxItem>;
}
function Range({ label, value, onChange, min = 0, max = 1, step = 0.01, format = v => `${Math.round(v * 100)}%` }) {
  return <div className="flex min-w-60 items-center gap-2 px-2 py-2 text-tiny" onKeyDown={keepKeys}>
    <span className="w-20 shrink-0">{label}</span>
    <Slider className="min-w-12 flex-1" value={[value]} min={min} max={max} step={step}
      thumbProps={{ "aria-label": label }} onValueChange={([next]) => onChange(next)} />
    <span className="w-10 shrink-0 text-right tabular-nums text-muted-foreground">{format(value)}</span>
  </div>;
}
// The same swatches and custom colour wheel pattern as Annotate. It stays in its submenu so a
// colour change is an edit, not a mode selection; its native picker belongs to this control.
function Color({ label, value, onChange }) {
  const picker = useRef(null);
  return <Submenu label={label} glyph={<span className="size-3.5 rounded-full" style={swatch(value)} aria-hidden="true" />}>
    <div className="grid grid-cols-3 gap-1 p-1">
      {PALETTE.map(color => <DropdownMenuItem key={color} aria-label={`${label}: ${color}`} onSelect={event => { event.preventDefault(); onChange(color); }}
        className="size-7 justify-center rounded-md p-0" data-checked={value === color ? "" : undefined}>
        <span className="size-4 rounded-full" style={swatch(color)} aria-hidden="true" />
      </DropdownMenuItem>)}
      <DropdownMenuItem aria-label={`Custom ${label.toLowerCase()}`} className="size-7 justify-center rounded-md p-0"
        onSelect={event => { event.preventDefault(); try { picker.current?.showPicker(); } catch { picker.current?.click(); } }}>
        <span className="size-4 rounded-full" style={WHEEL} aria-hidden="true" />
      </DropdownMenuItem>
    </div>
    <input ref={picker} type="color" value={value} onChange={event => onChange(event.target.value)} tabIndex={-1}
      aria-hidden="true" className="pointer-events-none absolute size-0 opacity-0" onKeyDown={keepKeys} />
  </Submenu>;
}

/** A mode's menu, whether or not that mode is on screen. Only explicit edits write its sparse settings. */
export function DisplayModeSettings({ mode, display, appearance = "light", lightingQuality = "preview", features, onPatch, onReset }) {
  const options = { appearance, lightingQuality, features };
  const settings = resolveViewModeSettings(display, mode, options);
  const label = VIEW_MODES.find(entry => entry.id === mode)?.label;
  const patch = (name, value) => onPatch({ [name]: value });
  const color = (name, title) => <Color label={title} value={settings[name].color} onChange={color => patch(name, { color })} />;
  const opacity = (name, title) => <Range label={title} value={settings[name].opacity} onChange={opacity => patch(name, { opacity })} />;
  return <>
    <DropdownMenuLabel className="text-muted-foreground">{label} settings</DropdownMenuLabel>
    {settings.parts && <>
      <Choice label="Part colours" value={settings.parts.colorMode} options={[["original", "Original"], ["single", "Single colour"], ["by-part", "By part"]]}
        onChange={colorMode => patch("parts", { colorMode })} />
      {settings.parts.colorMode === "single" && color("parts", "Part colour")}
      {settings.parts.colorMode === "by-part" && <Submenu label="Part palette">
        {settings.parts.colors.map((value, index) => <Color key={index} label={`Part colour ${index + 1}`} value={value}
          onChange={color => patch("parts", { colors: settings.parts.colors.map((entry, i) => i === index ? color : entry) })} />)}
        <DropdownMenuItem disabled={settings.parts.colors.length >= 50} onSelect={event => { event.preventDefault(); patch("parts", { colors: [...settings.parts.colors, "#94a3b8"] }); }}>Add colour</DropdownMenuItem>
        <DropdownMenuItem disabled={settings.parts.colors.length <= 1} onSelect={event => { event.preventDefault(); patch("parts", { colors: settings.parts.colors.slice(0, -1) }); }}>Remove last colour</DropdownMenuItem>
      </Submenu>}
    </>}
    {settings.flat !== undefined && <Toggle label="Flat shading" value={settings.flat} onChange={flat => patch("flat", flat)} />}
    {settings.opacity !== undefined && <Range label="Surface opacity" value={settings.opacity} onChange={opacity => patch("opacity", opacity)} />}
    {settings.edges && <>
      {settings.edges.enabled !== undefined && <Toggle label="Edges" value={settings.edges.enabled} onChange={enabled => patch("edges", { enabled })} />}
      {settings.edges.enabled !== false && color("edges", "Edge colour")}
    </>}
    {[['grid', 'Grid'], ['axes', 'Axes']].map(([name, title]) => settings[name] && <div key={name}>
      <Toggle label={title} value={settings[name].enabled} onChange={enabled => patch(name, { enabled })} />
      {settings[name].enabled && <Submenu label={`${title} appearance`}>
        {color(name, `${title} colour`)}{opacity(name, `${title} opacity`)}
        {name === "grid" && <Range label="Grid density" value={settings.grid.density} min={0.25} max={4} step={0.25}
          format={value => `${value}×`} onChange={density => patch("grid", { density })} />}
      </Submenu>}
    </div>)}
    {settings.lighting && <Submenu label="Lighting">
      <Choice label="Quality" value={settings.lighting.quality} options={[["preview", "Preview"], ["final", "Final"]]} onChange={quality => patch("lighting", { quality })} />
      <Range label="Exposure" value={settings.lighting.exposure} min={-5} max={5} step={0.1} format={v => `${v.toFixed(1)} EV`} onChange={exposure => patch("lighting", { exposure })} />
      <Range label="Rotation" value={settings.lighting.rotation} min={-180} max={180} step={1} format={v => `${v}°`} onChange={rotation => patch("lighting", { rotation })} />
      <Range label="Softbox size" value={settings.lighting.size} min={0.25} max={3} step={0.05} format={v => `${v.toFixed(2)}×`} onChange={size => patch("lighting", { size })} />
      <Range label="Fill" value={settings.lighting.fill} onChange={fill => patch("lighting", { fill })} />
    </Submenu>}
    {settings.background && <Submenu label="Background">{color("background", "Background colour")}{opacity("background", "Opacity")}</Submenu>}
    {settings.floor && <>
      <Toggle label="Floor" value={settings.floor.enabled} onChange={enabled => patch("floor", { enabled })} />
      {settings.floor.enabled && <Submenu label="Floor appearance">
        {color("floor", "Floor colour")}{opacity("floor", "Opacity")}
        <Choice label="Placement" value={settings.floor.placement} options={[["lowest", "Lowest point"], ["origin", "Model origin"]]} onChange={placement => patch("floor", { placement })} />
        <Choice label="Finish" value={settings.floor.finish} options={[["matte", "Matte"], ["glossy", "Glossy"]]} onChange={finish => patch("floor", { finish })} />
      </Submenu>}
    </>}
    <DropdownMenuSeparator />
    <DropdownMenuItem disabled={!viewModeChanged(display, mode, options)} onSelect={event => { event.preventDefault(); onReset(); }}>
      <RotateCcw className="size-3.5" />Reset {label}
    </DropdownMenuItem>
  </>;
}
