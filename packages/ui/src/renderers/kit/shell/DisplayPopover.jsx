import {
  DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuLabel, DropdownMenuRadioGroup,
  DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuTrigger
} from "@text-to-cad/ui/primitives/dropdown-menu";
import { ToolbarButton } from "@text-to-cad/ui/primitives/toolbar-button";
import { cn } from "@text-to-cad/ui/utils";
import { FLOATING_SURFACE_CLASS } from "../../../lib/floatingSurface.js";
import { useStripButtonClass } from "../tools/FloatingToolBar.js";
import { GridIcon } from "../view-settings/DisplayModeOptions.js";

/**
 * Display's choices, one list wherever it is shown (the Display tile's menu, a phone's More
 * submenu): the file's modes, then, after a rule, the Grid switch, which every mode draws.
 *
 * @param {{ mode: { value: string, options: { value: string, label: string, Icon?: Function }[], onChange(value: string): void },
 *   grid: { value: boolean, onChange(value: boolean): void } }} props
 */
export function DisplayMenuItems({ mode, grid }) {
  return <>
    <DropdownMenuRadioGroup aria-label="Display mode" value={mode.value} onValueChange={mode.onChange}>
      {mode.options.map(({ value, label, Icon }) => <DropdownMenuRadioItem key={value} value={value} data-display-mode-option={value}>
        {Icon ? <Icon className="size-4" /> : null}{label}
      </DropdownMenuRadioItem>)}
    </DropdownMenuRadioGroup>
    <DropdownMenuSeparator />
    {/* Ticking it leaves the menu open, as every checkbox row in a menu does: the grid is seen coming and going. */}
    <DropdownMenuCheckboxItem checked={grid.value} onCheckedChange={value => grid.onChange(value === true)}
      onSelect={event => event.preventDefault()} data-display-grid="">
      <GridIcon className="size-4" />Grid
    </DropdownMenuCheckboxItem>
  </>;
}

/**
 * The Display tile: the mode on screen's sphere, opening Display's list (`DisplayMenuItems`). A
 * mode chosen closes it; the Grid switch leaves it open. Projection is a menu of its own
 * (`ProjectionPopover.jsx`); on a phone Display is More's submenu instead (`RendererShell.jsx`).
 */
export default function DisplayPopover({ disabled = false, boundary = null, tooltipSide = "top", onOpenChange, mode, grid }) {
  const tileClass = useStripButtonClass();
  const current = mode.options.find(option => option.value === mode.value) || mode.options[0];
  const Icon = current?.Icon;
  return <DropdownMenu modal={false} onOpenChange={onOpenChange}>
    <DropdownMenuTrigger asChild>
      <ToolbarButton label={`Display mode: ${current?.label || "Solid"}`} tooltip="Display" tooltipSide={tooltipSide} disabled={disabled}
        className={tileClass} data-display-mode={current?.value}>
        {Icon ? <Icon /> : null}
      </ToolbarButton>
    </DropdownMenuTrigger>
    {/* Closes with no exit animation, so a quick second press always reaches the tile. */}
    <DropdownMenuContent align="center" sideOffset={6} collisionPadding={8} collisionBoundary={boundary || undefined} aria-label="Display"
      className={cn(FLOATING_SURFACE_CLASS, "w-44 data-[state=closed]:animate-none!")}>
      <DropdownMenuLabel className="text-muted-foreground">Display</DropdownMenuLabel>
      <DisplayMenuItems mode={mode} grid={grid} />
    </DropdownMenuContent>
  </DropdownMenu>;
}
