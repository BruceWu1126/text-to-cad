import { useState } from "react";
import { Clock, Eye, EyeOff, Pause, Play } from "lucide-react";
import { cn } from "@text-to-cad/ui/utils";
import { Button } from "@text-to-cad/ui/primitives/button";
import { TooltipHint } from "@text-to-cad/ui/primitives/tooltip";
import { ROW_NAME_UNDER_ACTIONS, TREE_INDENT_PX, TreeRowChevron, TreeRowLabel, TreeRowSurface, treeRowActionsClass } from "@text-to-cad/ui/primitives/tree-row";
import { KinematicsPoseRow } from "../kit/inspector/kinematicsControls.jsx";
import { parameterRow } from "../kit/inspector/parameterRow.jsx";
import FindingsList, { findingsLabel } from "../kit/status/findings.jsx";
import ToolPanel, { ToolPanelClose } from "../kit/tools/ToolPanel.jsx";
import { clockTime } from "./camResult.js";

// The panel's id: the tree's, as FEA's Study keeps it, so the stack and the strip treat it as Select's tree.
export const CAM_JOB_PANEL_ID = "tree";
// The room an operation row's eye takes at its right end (its `--row-actions`).
const ROW_ACTIONS_WIDTH = "1.5rem";
const NONE = Object.freeze([]);
// What the findings card heads what must be fixed first, and the rest.
const FINDING_HEADINGS = Object.freeze({ fix: "Fix before cutting", suggestions: "Suggestions" });

/** A caption that wraps between its parts alone ("4 operations · 1 tool change"), never inside one. */
const unbroken = (text) => String(text).split(" · ").map((part) => part.replace(/ /g, "\u00a0")).join(" · ");

/**
 * The job at a glance, at the top of the panel: how long it takes on the machine, then what it takes ("4 operations
 * · 1 tool change · linuxcnc"), and where cadgen assumed the rapids' speed, a muted note that says so.
 */
function JobSummary({ summary }) {
  return <section aria-label="Summary" data-cam-summary="" className="mx-1 mb-2 flex flex-col gap-1 rounded-md bg-muted/60 px-2.5 pb-2 pt-2.5">
    <div className="flex items-center gap-1.5 text-xs font-medium leading-4 text-foreground">
      <Clock className="size-3.5 shrink-0" strokeWidth={2} aria-hidden="true" />
      <span className="min-w-0 tabular-nums" data-cam-cycle-time="">{summary.title}</span>
    </div>
    <p className="text-tiny leading-4 text-foreground" data-cam-summary-caption="">{unbroken(summary.caption)}</p>
    {summary.note ? <p className="text-micro leading-3.5 text-muted-foreground [overflow-wrap:break-word]" data-cam-summary-note="">{summary.note}</p> : null}
  </section>;
}

/** A heading of the job's parts (Findings, Setups, Tools, Operations), muted, its rows under it; it folds by its chevron. */
function GroupRow({ id, label, detail = "", open, onToggle, children }) {
  return <li className="min-w-0 [&:not(:first-child)]:pt-1" data-cam-group={id}>
    <TreeRowSurface dense className="gap-0 pr-0 text-muted-foreground">
      <button type="button" aria-label={`${open ? "Collapse" : "Expand"} ${label}`} aria-expanded={open} onClick={onToggle}
        className="flex h-full min-w-0 flex-1 items-center gap-0 rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <span className="grid h-6 w-4 shrink-0 place-items-center"><TreeRowChevron expanded={open} /></span>
        <TreeRowLabel className={detail ? "shrink-0" : "flex-1"}>{label}</TreeRowLabel>
        {detail ? <span className="min-w-0 flex-1 truncate pl-1.5 text-micro" data-cam-group-detail="">{detail}</span> : null}
      </button>
    </TreeRowSurface>
    {open ? <ul>{children}</ul> : null}
  </li>;
}

/**
 * A row that only says something (a setup, a tool): its name and what it is on its line, and a second line where it
 * has one (a tool's speed and feed), muted; whole in its hint where the narrow panel cuts it.
 */
function FactRow({ row }) {
  return <li className="min-w-0">
    <TooltipHint content={row.hint} overflowOnly={false}>
      <div className="flex min-w-0 flex-col py-0.5 pr-2 text-tiny text-foreground/80" style={{ paddingLeft: TREE_INDENT_PX + 16 }} data-cam-row={row.id}>
        <span className="flex min-w-0 items-baseline gap-1.5 leading-5">
          <span className="shrink-0">{row.label}</span>
          {row.detail ? <span className="min-w-0 truncate text-micro text-muted-foreground">{row.detail}</span> : null}
        </span>
        {/* Its speed and its feed, each whole: the feed goes under the speed where the two do not fit one line. */}
        {row.speeds ? <span className="flex min-w-0 flex-wrap gap-x-1 text-micro leading-4 text-muted-foreground tabular-nums" data-cam-speeds="">
          {row.speeds.split(" · ").map((part, index, all) => <span key={part} className="whitespace-nowrap">{part}{index < all.length - 1 ? " ·" : ""}</span>)}
        </span> : null}
      </div>
    </TooltipHint>
  </li>;
}

/**
 * An operation: its place and word, then its time; a press isolates it (its path alone on the model), a second press
 * shows them all again. Its eye floats over its right end as an assembly row's does: on hover, and kept while its
 * path is hidden.
 */
function OperationRow({ row, hidden, isolated, onToggle, onIsolate }) {
  const action = hidden ? "Show" : "Hide";
  return <li className="min-w-0">
    <TreeRowSurface dense active={isolated} className="group/row gap-0 pr-0" style={{ paddingLeft: TREE_INDENT_PX, "--row-actions": ROW_ACTIONS_WIDTH }} data-cam-row={row.id}
      data-cam-hidden={hidden ? "" : undefined}>
      <span className="w-4 shrink-0" />
      <TooltipHint content={row.hint}>
        <button type="button" aria-label={`Isolate ${row.label}`} aria-pressed={isolated} onClick={() => onIsolate(row.index)}
          className={cn("flex h-full min-w-0 flex-1 items-center gap-1.5 rounded pr-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            hidden ? ROW_NAME_UNDER_ACTIONS.shown : ROW_NAME_UNDER_ACTIONS.hover, hidden && "text-muted-foreground")}>
          <TreeRowLabel className="max-w-full shrink-0">{row.label}</TreeRowLabel>
          <TreeRowLabel className="flex-1 text-micro text-muted-foreground tabular-nums">{row.detail}</TreeRowLabel>
        </button>
      </TooltipHint>
      <div data-row-actions="" className={treeRowActionsClass(hidden)}>
        <TooltipHint content={action}>
          <Button type="button" variant="ghost" size="icon-xs" aria-label={`${action} ${row.label}`} aria-pressed={!hidden} className="h-5 w-4 text-muted-foreground"
            onClick={(event) => { event.stopPropagation(); onToggle(row.index); }}>
            {hidden ? <EyeOff className="size-3" aria-hidden="true" /> : <Eye className="size-3" aria-hidden="true" />}
          </Button>
        </TooltipHint>
      </div>
    </TreeRowSurface>
  </li>;
}

/**
 * The job's Time row and Preset, at the panel's foot, which never scrolls (FEA's What you see is laid out the same
 * way): Preset (Default, the start of each operation, Custom once the row moves), then Time, a scrubber over the job's
 * clock whose value field says the job time, with a play button beside it that plays the job in place.
 */
function JobTime({ control, value, presets, preset, playing, onChange, onPreset, onReset, onToggle }) {
  const playButton = <TooltipHint content={playing ? "Pause" : "Play the job"}>
    <Button type="button" variant="ghost" size="icon-xs" className="size-6 text-muted-foreground" data-cam-play=""
      aria-label={playing ? "Pause" : "Play the job"} aria-pressed={playing} onClick={onToggle}>
      {playing ? <Pause className="size-3" aria-hidden="true" /> : <Play className="size-3" aria-hidden="true" />}
    </Button>
  </TooltipHint>;
  return <div className="flex flex-col gap-1.5 px-1 pb-1 [&_span:has(>[data-slot=select-trigger])]:min-w-0 [&_span:has(>[data-slot=select-trigger])]:shrink [&_[data-slot=select-trigger]]:min-w-0 [&_[data-slot=select-trigger]]:max-w-full"
    data-cam-time="">
    {presets.length ? <KinematicsPoseRow label="Preset" poses={presets} activeValue={preset} onSelect={onPreset} onReset={onReset} /> : null}
    {/* Its label and its value as a clock ("1:40") on one line, the slider the row's whole width under them. */}
    {parameterRow({ parameter: control, value, labelTitle: control.labelTitle, valueText: (at) => clockTime(at), action: playButton, wideLabel: true, onChange })}
  </div>;
}

/**
 * A CAM result's Select panel, **Job**, closable as the Features tree is (`panel: { id: "tree", label: "Job" }`): the
 * summary (the cycle time, what it takes), the findings in the findings card the viewer lists a board's in
 * (`kit/status/findings.jsx`), then the job's **Setups**, its **Tools** (with the speed and feed cadgen computed) and
 * its **Operations** (each with its time, its eye and a press that isolates it), and at its foot the job's Time row.
 *
 * @param {{ active: boolean, summary: { title: string, caption: string, note: string }, findings: object[],
 *   setups: object[], tools: object[], operations: object[], hidden: Set<number>, isolated: number | null,
 *   onToggle(index: number): void, onIsolate(index: number): void, onChooseFinding(finding: object): void,
 *   time: object }} props
 */
export default function CamJobPanel({ active, summary, findings = NONE, setups, tools, operations, hidden, isolated, onToggle, onIsolate, onChooseFinding, time }) {
  // Findings with nothing to fix start shut (the summary and the operations come first); one to fix opens them.
  const [shut, setShut] = useState(() => new Set(findings.some((finding) => finding.severity === "error") ? NONE : ["findings"]));
  const toggle = (id) => setShut((current) => {
    const next = new Set(current);
    if (!next.delete(id)) next.add(id);
    return next;
  });
  const group = (id, label, rows, draw) => (rows.length ? <GroupRow key={id} id={id} label={label} open={!shut.has(id)} onToggle={() => toggle(id)}>{rows.map(draw)}</GroupRow> : null);
  const footer = time ? <div className="shrink-0 border-t border-foreground/10 pt-1.5" data-cam-foot=""><JobTime {...time} /></div> : null;
  return <ToolPanel id={CAM_JOB_PANEL_ID} title="Job" label="Job" actions={<ToolPanelClose />} fit="tree" resizable fitContent closable collapsible={false} hidden={!active}
    footer={footer}>
    <JobSummary summary={summary} />
    <ul className="flex flex-col px-1 pb-1 text-tiny" aria-label="Job">
      {findings.length ? <GroupRow id="findings" label="Findings" detail={findingsLabel(findings)} open={!shut.has("findings")} onToggle={() => toggle("findings")}>
        {/* The findings card a board's are listed in, its type stepped down to the panel's. */}
        <li className="min-w-0 pb-1 pl-5 pr-2 [&_button]:py-0.5 [&_button]:text-tiny [&_button]:leading-4 [&_h3]:text-tiny [&_section]:min-w-0"><FindingsList findings={findings} onChoose={onChooseFinding} headings={FINDING_HEADINGS} /></li>
      </GroupRow> : null}
      {group("setups", "Setups", setups, (row) => <FactRow key={row.id} row={row} />)}
      {group("tools", "Tools", tools, (row) => <FactRow key={row.id} row={row} />)}
      {group("operations", "Operations", operations, (row) => <OperationRow key={row.id} row={row} hidden={hidden.has(row.index)} isolated={isolated === row.index}
        onToggle={onToggle} onIsolate={onIsolate} />)}
    </ul>
  </ToolPanel>;
}
