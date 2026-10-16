import { TooltipHint } from "@text-to-cad/ui/primitives/tooltip";
import { useMemo, useRef } from "react";
import { Blend, Expand, Plus, RotateCcw, RotateCw, Sun, SunDim, X } from "lucide-react";
import { ALL_VIEW_FEATURES, normalizeViewFeatures, normalizeViewSettings, resolveViewSettings } from "@text-to-cad/core/common/viewSettings.js";
import { MAX_THEME_FILL_COLORS } from "@text-to-cad/core/lib/themeSettings.js";
import { Button } from "@text-to-cad/ui/primitives/button";
import {
  FileSheetColorPicker, FileSheetColorProperty,
  FileSheetControlRow, FileSheetSelectRow, FileSheetSettingsSection,
  FileSheetFieldGrid, FileSheetNumberProperty, parseFileSheetNumberInput
} from "../inspector/FileSheet.js";

const PART_COLOR_OPTIONS = [
  { value: "original", label: "Original" }, { value: "single", label: "Single color" }, { value: "by-part", label: "Color by part" }
];
// Surface styles mirror the CLI; presets are shortcuts over these same controls.
const SURFACE_STYLE_OPTIONS = [
  { value: "shaded", label: "Shaded" }, { value: "flat", label: "Flat" },
  { value: "hidden", label: "Hidden" }, { value: "off", label: "Off" }
];
const FLOOR_PLACEMENT_OPTIONS = [{ value: "origin", label: "Model origin" }, { value: "lowest", label: "Lowest point" }];
// Glossy reflects the model too, at the cost of a second, smaller draw of the scene.
const FLOOR_FINISH_OPTIONS = [{ value: "matte", label: "Matte" }, { value: "glossy", label: "Glossy" }];

function ColorPalette({ colors, onChange }) {
  const palette = Array.isArray(colors) && colors.length ? colors : ["#ffffff"];
  const commit = (next) => onChange(next.filter(Boolean).slice(0, MAX_THEME_FILL_COLORS));
  return (
    <FileSheetControlRow label="Colors" value={`${palette.length}/${MAX_THEME_FILL_COLORS}`}>
      <div className="flex flex-wrap gap-1.5">
        {palette.map((color, index) => (
          <div key={`${index}:${color}`} className="group relative">
            <FileSheetColorPicker
              value={color}
              swatchClassName="size-5"
              onChange={(nextColor) => commit(palette.map((entry, colorIndex) => (
                colorIndex === index ? nextColor : entry
              )))}
              aria-label={`Part color ${index + 1}`}
            />
            {palette.length > 1 ? (
              <Button
                type="button"
                variant="outline"
                size="icon-xs"
                className="absolute -right-1.5 -top-1.5 size-4 rounded-full bg-background p-0 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100"
                onClick={() => commit(palette.filter((_, colorIndex) => colorIndex !== index))}
                aria-label={`Remove part color ${index + 1}`}
              >
                <X className="size-2.5" aria-hidden="true" />
              </Button>
            ) : null}
          </div>
        ))}
        {palette.length < MAX_THEME_FILL_COLORS ? (
          <Button
            type="button"
            variant="outline"
            size="icon-sm"
            className="size-7"
            onClick={() => commit([...palette, palette[palette.length - 1] || "#ffffff"])}
            aria-label="Add part color"
          >
            <Plus className="size-3.5" aria-hidden="true" />
          </Button>
        ) : null}
      </div>
    </FileSheetControlRow>
  );
}

function NumberProperty({ label, Icon, value, min, max, unit = "", digits = 2, onChange }) {
  return <FileSheetNumberProperty label={label} Icon={Icon} value={`${Number(value).toFixed(digits)}${unit}`}
    onValueCommit={draft => onChange(parseFileSheetNumberInput(draft, { fallback: value, min, max }))} />;
}

/**
 * Display's sections, stacked in its dropdown's one scroller (`DisplayPopover.jsx`). A
 * section with `onEnabledChange` is a feature gate (expanded IS enabled); the others are always
 * open. A press on an open gate's title scrolls it into view within the scroller's natural range,
 * never adding space to force it to the top, and never disables it.
 */
function DisplaySections({ sections }) {
  const list = useRef(null);
  const reveal = id => {
    const element = list.current?.closest('[data-slot="scroll-area-viewport"]') || list.current;
    const target = list.current?.querySelector(`[data-settings-section="${CSS.escape(id)}"] [data-settings-section-body]`);
    if (!target) return;
    const top = element.scrollTop + target.getBoundingClientRect().top - element.getBoundingClientRect().top
      - (target.previousElementSibling?.getBoundingClientRect().height || 0);
    element.scrollTop = Math.max(0, Math.min(top, element.scrollHeight - element.clientHeight));
  };
  return <div ref={list} data-settings-sections="">
    {sections.filter(Boolean).map(section => {
      const gated = typeof section.onEnabledChange === "function";
      return <FileSheetSettingsSection key={section.id} sectionId={section.id} title={section.title} headingAction={section.headingAction}
        open={gated ? section.enabled === true : true} onOpenChange={gated ? section.onEnabledChange : undefined}
        gated={gated} onReveal={() => reveal(section.id)}>{section.content}</FileSheetSettingsSection>;
    })}
  </div>;
}

export function DisplaySettingsSection({
  viewSettings = {}, resolvedView, hostAppearance = "light", lightingQuality = "final", onViewSettingsPatch, onGroupEnabledChange, onViewReset,
  features = ALL_VIEW_FEATURES
}) {
  const settings = useMemo(() => normalizeViewSettings(viewSettings), [viewSettings]);
  const view = resolvedView || resolveViewSettings(settings, { appearance: hostAppearance, lightingQuality, features });
  // The panel is built from what its caller opted into: sections it mounts, presets and surface styles it lists.
  const offered = useMemo(() => normalizeViewFeatures(features), [features]);
  const offers = id => offered.sections.includes(id);
  const surfaceStyleOptions = SURFACE_STYLE_OPTIONS.filter(option => offered.surfaceStyles.includes(option.value));
  // Sparse writes stay in the canonical settings store; the renderer never owns UI state.
  const setGroup = (group, patch) => onViewSettingsPatch({ [group]: {
    ...(group === "surfaces" || view[group]?.enabled === false ? { enabled: true } : {}), ...patch
  } });
  const section = (group, title, content) => !offers(group) ? null : ({
    id: group, title, content, enabled: view[group].enabled,
    onEnabledChange: enabled => onGroupEnabledChange(group, enabled),
  });
  const color = (group, label, className) => <FileSheetColorProperty className={className} label={label} value={view[group].color}
    onChange={value => setGroup(group, { color: value })} opacity={view[group].opacity}
    onOpacityChange={opacity => setGroup(group, { opacity })} />;
  const surfaces = <>
        <FileSheetFieldGrid>
          <FileSheetSelectRow hideLabel className="px-0" label="Surface style" value={view.surfaces.style} onValueChange={style => setGroup("surfaces", { style })} options={surfaceStyleOptions} />
          <FileSheetSelectRow hideLabel className="px-0" label="Parts" value={view.surfaces.colorMode} onValueChange={colorMode => setGroup("surfaces", { colorMode })} options={PART_COLOR_OPTIONS} />
        </FileSheetFieldGrid>
        {view.surfaces.colorMode === "single" ? color("surfaces", "Part color") : null}
        {view.surfaces.colorMode === "by-part" ? <ColorPalette colors={view.surfaces.colors} onChange={colors => setGroup("surfaces", { colors })} /> : null}
        {view.surfaces.colorMode !== "single" ? <FileSheetFieldGrid columns={1}><NumberProperty label="Surface opacity" Icon={Blend} value={view.surfaces.opacity * 100} min={0} max={100} unit="%" digits={0} onChange={value => setGroup("surfaces", { opacity: value / 100 })} /></FileSheetFieldGrid> : null}
  </>;
  const sections = [
      // Custom's heading: Reset, back to Solid, the default. Choosing a mode is the menu's (`DisplayPopover.jsx`).
      offers("mode") && { id: "display", title: "Custom display",
        headingAction: <TooltipHint content="Reset to Solid"><Button type="button" variant="ghost" size="icon-xs" aria-label="Reset"  className="size-5 text-muted-foreground" onClick={onViewReset}><RotateCcw className="size-3" aria-hidden="true" /></Button></TooltipHint>, content: null },
      offers("surfaces") && { id: "surfaces", title: "Surfaces", content: surfaces },
      section("edges", "Edges", <FileSheetFieldGrid>
        <FileSheetSelectRow hideLabel className="px-0" label="Edge visibility" value={view.edges.visibility} onValueChange={visibility => setGroup("edges", { visibility })}
          options={[{ value: "visible", label: "Visible" }, { value: "all", label: "All" }]} />
        <FileSheetColorProperty className="px-0" label="Edge color" value={view.edges.color} onChange={value => setGroup("edges", { color: value })} />
      </FileSheetFieldGrid>),
      (offers("grid") || offers("axes")) && { id: "grid-axes", title: "Grid / Axes",
        enabled: view.grid.enabled || view.axes.enabled,
        onEnabledChange: enabled => { if (offers("grid")) onGroupEnabledChange("grid", enabled); if (offers("axes")) onGroupEnabledChange("axes", enabled); },
        content: <FileSheetFieldGrid>{offers("grid") && color("grid", "Grid color", "px-0")}{offers("axes") && color("axes", "Axis color", "px-0")}</FileSheetFieldGrid> },
      section("lighting", "Lighting", <>
        <FileSheetSelectRow hideLabel label="Quality" value={view.lighting.quality} onValueChange={quality => setGroup("lighting", { quality })}
          options={[{ value: "preview", label: "Preview" }, { value: "final", label: "Final" }]} />
        <FileSheetFieldGrid className="gap-1">
          <NumberProperty label="Exposure" Icon={Sun} value={view.lighting.exposure} min={-5} max={5} unit=" EV" digits={1} onChange={exposure => setGroup("lighting", { exposure })} />
          <NumberProperty label="Rotation" Icon={RotateCw} value={view.lighting.rotation} min={-180} max={180} unit="°" digits={0} onChange={rotation => setGroup("lighting", { rotation })} />
          <NumberProperty label="Softbox size" Icon={Expand} value={view.lighting.size} min={0.25} max={3} unit="×" onChange={size => setGroup("lighting", { size })} />
          <NumberProperty label="Fill ratio" Icon={SunDim} value={view.lighting.fill * 100} min={0} max={100} unit="%" digits={0} onChange={value => setGroup("lighting", { fill: value / 100 })} />
        </FileSheetFieldGrid>
      </>),
      section("background", "Background", color("background", "Background color")),
      section("floor", "Floor", <>
        {color("floor", "Floor color")}
        <FileSheetFieldGrid>
          <FileSheetSelectRow hideLabel className="px-0" label="Floor position" value={view.floor.placement} onValueChange={placement => setGroup("floor", { placement })}
            options={FLOOR_PLACEMENT_OPTIONS} />
          <FileSheetSelectRow hideLabel className="px-0" label="Floor finish" value={view.floor.finish} onValueChange={finish => setGroup("floor", { finish })}
            options={FLOOR_FINISH_OPTIONS} />
        </FileSheetFieldGrid>
      </>),
  ];
  return <div className="[&_[data-settings-section-heading]_.text-xs]:text-tiny">
    <DisplaySections sections={sections} />
  </div>;
}
