import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Check, Settings2 } from "lucide-react";
import { RadioGroup } from "radix-ui";
import { Button } from "@text-to-cad/ui/primitives/button";
import { Popover, PopoverAnchor, PopoverContent, PopoverTrigger } from "@text-to-cad/ui/primitives/popover";
import { ScrollArea } from "@text-to-cad/ui/primitives/scroll-area";
import { ToolbarButton } from "@text-to-cad/ui/primitives/toolbar-button";
import { cn } from "@text-to-cad/ui/utils";
import { FLOATING_SURFACE_CLASS } from "../../../lib/floatingSurface.js";
import { OrthographicProjectionIcon, PerspectiveProjectionIcon } from "../camera/ProjectionModeIcons.js";
import { useStripButtonClass } from "../tools/FloatingToolBar.js";

const ROW_CLASS = "relative flex min-h-7 w-full cursor-pointer items-center gap-2 rounded-sm py-1.5 pr-8 pl-2 text-left text-tiny leading-4 outline-none hover:bg-accent focus-visible:bg-accent focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/45 disabled:pointer-events-none disabled:opacity-50";

function ModeChoice({ value, Icon, children, ...props }) {
  return <RadioGroup.Item value={value} className={ROW_CLASS} {...props}>
    <Icon className="size-3.5 shrink-0" aria-hidden="true" />{children}
    <span className="pointer-events-none absolute right-2 flex size-3.5 items-center justify-center" aria-hidden="true">
      <RadioGroup.Indicator><Check className="size-3.5" /></RadioGroup.Indicator>
    </span>
  </RadioGroup.Item>;
}

/**
 * One surface: choose a mode, or edit the selected mode. Its own tile opens it; on a phone's strip
 * with no room for that tile it opens from the strip's More button instead (`anchor`, its ref, with
 * `open` held by the strip).
 */
export default function DisplayPopover({ disabled = false, boundary = null, tooltipSide = "top", onOpenChange, mode, projection, anchor = null, open: held, triggerClassName = null, align = "center", children }) {
  const [ownOpen, setOpen] = useState(false);
  const open = held ?? ownOpen;
  const stripClass = useStripButtonClass();
  // Its tile is the strip's, or another row's where it stands elsewhere (the navbar's).
  const tileClass = triggerClassName ?? stripClass;
  const [editing, setEditing] = useState(false);
  const back = useRef(null);
  const settingsButton = useRef(null);
  const returningToSettings = useRef(false);
  const current = mode.options.find(option => option.value === mode.value) || mode.options[0];
  const detail = editing ? current : null;
  const Icon = current?.Icon;
  const label = current?.label || "Solid";
  const changeOpen = next => {
    setOpen(next);
    if (!next) {
      returningToSettings.current = false;
      setEditing(false);
    }
    onOpenChange?.(next);
  };
  useEffect(() => {
    if (editing) back.current?.focus();
    else if (returningToSettings.current) {
      settingsButton.current?.focus();
      returningToSettings.current = false;
    }
  }, [editing]);
  const goBack = () => {
    returningToSettings.current = true;
    setEditing(false);
  };
  return <Popover open={open} onOpenChange={changeOpen}>
    {anchor ? <PopoverAnchor virtualRef={anchor} /> : <PopoverTrigger asChild>
      <ToolbarButton label={`Display mode: ${label}`} tooltip="Display" tooltipSide={tooltipSide} disabled={disabled} className={tileClass}
        data-display-mode={current?.value}>
        {Icon ? <Icon /> : null}
      </ToolbarButton>
    </PopoverTrigger>}
    <PopoverContent align={align} sideOffset={6} collisionPadding={8} collisionBoundary={boundary || undefined}
      aria-label={detail ? `${detail.label} settings` : "Display mode"} data-display-settings={detail?.value}
      onEscapeKeyDown={event => {
        // Committed value inputs cancel their draft on Escape before this surface closes.
        if (event.target instanceof HTMLInputElement && event.target.type === "text") event.preventDefault();
      }}
      className={cn(FLOATING_SURFACE_CLASS, "flex max-h-[var(--radix-popover-content-available-height)] w-52 max-w-[var(--radix-popover-content-available-width)] flex-col overflow-hidden p-0 text-tiny data-[state=closed]:animate-none!")}>
      {detail ? <>
        <div className="flex h-8 shrink-0 items-center gap-1 border-b border-border px-1">
          <Button ref={back} type="button" variant="ghost" size="icon-xs" aria-label="Back to Display"
            onClick={goBack} className="size-6 shrink-0"><ArrowLeft className="size-3.5" aria-hidden="true" /></Button>
          <span className="text-tiny">Settings</span>
        </div>
        <div key={detail.value} className="flex min-h-0 flex-1 flex-col">{children(detail.value)}</div>
      </> : <ScrollArea className="min-h-0 flex-1">
        <div className="p-1">
          <div className="flex min-h-7 items-center justify-between py-0.5 pr-1 pl-2 text-muted-foreground">
            <span>Display Mode</span>
            <Button ref={settingsButton} type="button" variant="ghost" size="icon-xs"
              aria-label={`${label} settings`} onClick={() => setEditing(true)} disabled={!current}
              className="size-5 shrink-0 hover:text-foreground">
              <Settings2 className="size-3" aria-hidden="true" />
            </Button>
          </div>
          <RadioGroup.Root aria-label="Display Mode" value={current?.value} onValueChange={value => { mode.onChange(value); changeOpen(false); }}>
            {mode.options.map(({ value, label: name, Icon: ModeIcon }) =>
              <ModeChoice key={value} value={value} Icon={ModeIcon} data-display-mode-option={value}
                onClick={() => { if (value === current?.value) changeOpen(false); }}>{name}</ModeChoice>)}
          </RadioGroup.Root>
          {projection && <>
            <div className="-mx-1 my-1 h-px bg-border" />
            <div className="px-2 py-1.5 text-muted-foreground">Projection</div>
            <RadioGroup.Root aria-label="Projection" value={projection.value} onValueChange={projection.onChange}>
              <ModeChoice value="orthographic" Icon={OrthographicProjectionIcon}>Orthographic</ModeChoice>
              <ModeChoice value="perspective" Icon={PerspectiveProjectionIcon}>Perspective</ModeChoice>
            </RadioGroup.Root>
          </>}
        </div>
      </ScrollArea>}
    </PopoverContent>
  </Popover>;
}

