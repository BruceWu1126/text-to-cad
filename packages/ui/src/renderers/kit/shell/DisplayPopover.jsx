import { useRef, useState } from "react";
import { Settings2 } from "lucide-react";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem,
  DropdownMenuSeparator, DropdownMenuSub, DropdownMenuSubContent, DropdownMenuSubTrigger, DropdownMenuTrigger
} from "@text-to-cad/ui/primitives/dropdown-menu";
import { ScrollArea } from "@text-to-cad/ui/primitives/scroll-area";
import { ToolbarButton } from "@text-to-cad/ui/primitives/toolbar-button";
import { cn } from "@text-to-cad/ui/utils";
import { FLOATING_SURFACE_CLASS } from "../../../lib/floatingSurface.js";
import { OrthographicProjectionIcon, PerspectiveProjectionIcon } from "../camera/ProjectionModeIcons.js";
import { STRIP_BUTTON_CLASS } from "../tools/FloatingToolBar.js";

// Inside the viewer, 8px clear of its edges.
const COLLISION_PADDING = 8;

function ModeRow({ value, name, Icon, onChange, boundary, children }) {
  const [open, setOpen] = useState(false);
  const trigger = useRef(null);
  return <div className="relative">
    <DropdownMenuRadioItem value={value} className="pr-16" data-display-mode-option={value} onSelect={() => onChange(value)}>
      <Icon className="size-3.5" aria-hidden="true" />{name}
    </DropdownMenuRadioItem>
    <DropdownMenuSub open={open} onOpenChange={setOpen}>
      <DropdownMenuSubTrigger ref={trigger} aria-label={`${name} settings`} className="absolute top-0 right-7 size-7 justify-center p-0 [&>svg:last-child]:hidden">
        <Settings2 className="size-3.5" aria-hidden="true" />
      </DropdownMenuSubTrigger>
      <DropdownMenuSubContent sideOffset={4} collisionPadding={COLLISION_PADDING} collisionBoundary={boundary || undefined}
        onEscapeKeyDown={event => { event.preventDefault(); setOpen(false); trigger.current?.focus(); }}
        aria-label={`${name} settings`} data-display-settings={value}
        className={cn(FLOATING_SURFACE_CLASS, "max-h-[var(--radix-dropdown-menu-content-available-height)] min-w-60 flex flex-col overflow-hidden data-[state=closed]:animate-none!")}>
        <ScrollArea className="min-h-0 flex-1">{children(value)}</ScrollArea>
      </DropdownMenuSubContent>
    </DropdownMenuSub>
  </div>;
}

/** The selected mode's sphere opens the modes; each row's settings trigger edits that mode independently. */
export default function DisplayPopover({ disabled = false, boundary = null, tooltipSide = "top", onOpenChange, mode, projection, children }) {
  const current = mode.options.find(option => option.value === mode.value) || mode.options[0];
  const Icon = current?.Icon;
  const label = current?.label || "Solid";
  return <DropdownMenu modal={false} onOpenChange={onOpenChange}>
    <DropdownMenuTrigger asChild>
      <ToolbarButton label={`Display mode: ${label}`} tooltipSide={tooltipSide} disabled={disabled} className={STRIP_BUTTON_CLASS}
        data-display-mode={current?.value}>
        {Icon ? <Icon /> : null}
      </ToolbarButton>
    </DropdownMenuTrigger>
    <DropdownMenuContent align="center" sideOffset={6} collisionPadding={COLLISION_PADDING} aria-label="Display mode"
      className={cn(FLOATING_SURFACE_CLASS, "min-w-44 data-[state=closed]:animate-none!")}>
      <DropdownMenuLabel className="text-tiny font-normal text-muted-foreground">Display Mode</DropdownMenuLabel>
      <DropdownMenuRadioGroup value={current?.value}>
        {mode.options.map(({ value, label: name, Icon }) => <ModeRow key={value} value={value} name={name} Icon={Icon}
          onChange={mode.onChange} boundary={boundary}>{children}</ModeRow>)}
      </DropdownMenuRadioGroup>
      {projection && <>
        <DropdownMenuSeparator />
        <DropdownMenuLabel className="text-tiny font-normal text-muted-foreground">Projection</DropdownMenuLabel>
        <DropdownMenuRadioGroup aria-label="Projection" value={projection.value} onValueChange={projection.onChange}>
          <DropdownMenuRadioItem value="orthographic" disabled={projection.disabled} onSelect={event => event.preventDefault()}>
            <OrthographicProjectionIcon className="size-3.5" />Orthographic
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="perspective" disabled={projection.disabled} onSelect={event => event.preventDefault()}>
            <PerspectiveProjectionIcon className="size-3.5" />Perspective
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </>}
    </DropdownMenuContent>
  </DropdownMenu>;
}
