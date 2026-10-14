# Findings

Every plan checks itself and lists findings, errors first, then warnings, then notes. Read them
before you report anything: an error means there is no program to hand over.

## What a finding is

Each finding is one JSON object, in the CLI's `--json` and the sidecar's `findings`:

```json
{
  "check": "cam",
  "severity": "error",
  "type": "unreachable",
  "summary": "operations[2] (contour_2d): the tool cannot come down at (-50, -33) to z -8.5: the fixture stands in the way",
  "description": "Grip the stock so the fixture stands clear of the cut, or move the cut.",
  "items": []
}
```

`severity` is `error`, `warning` or `info`; `type` is the code below; `summary` names the operation by
its place in the job and says what is wrong; `description`, when there is one, says what to do;
`items` points at what it is about (a feature's selector, a tool). The CLI prints each as one line,
`error: <summary>`.

- **error**: the job is not planned. No G-code and no GLB are written (an older one at those paths
  is removed), only the sidecar with `"ok": false` and the findings, and the command exits 1. Every
  operation is still tried, so one run lists every refusal: fix them all, then plan again.
- **warning**: planned, with something to weigh. Act on it or tell the user why not, in their words.

Two other kinds of message are not findings:

- **A malformed job, or a post that refuses an operation**, is an error message naming the field or
  the post, and nothing is written: `operations[2].stepdown_mm: must be > 0, got -1`, or
  `grbl post: operations[1] (tap) has a tap cycle, and Grbl 1.1 has no tapping cycle. ...`.
- **A warning about the run itself** (`warnings` in the sidecar), printed as `warning: ...` after the
  findings: `machine.rapid_mm_min is not stated: the cycle time assumes rapids at 5000 mm/min`. Say
  it when you quote the cycle time.

## The codes

<!-- The "Raised today" and "With the job checks" tables split what this cadgen raises from what the
     job checks will. Reconcile both, and SKILL.md's "Raised" column, with
     cadgen._internal.cam.checks.CHECK_KINDS when the job checks land. -->

### Raised today

Planning raises the first six as errors: an operation that is well formed but cannot be planned on
this part. The feeds raise the clamps, and two operations raise `rest_material`.

| Finding | Severity | Raised by | What it means | What to do |
| --- | --- | --- | --- | --- |
| `empty_operation` | error | every operation | Nothing to cut: the feature's bottom is not below the top of the material, a region with no `depth_mm` or `z_mm`, a drilling `all: true` with no hole that suits the tool. | Check the feature, the depth and what the facing before it took off; drop the operation if nothing is left to do. |
| `too_deep` | error | every operation | The cut would go into the part: a floor under the feature's own, a facing below the part's top, a spot, pilot or chamfer past what the part allows, a V-carve past its cone, a clearing tool whose flutes do not reach. | Leave `depth_mm` and `z_mm` out to use the feature's own depth, or ask for less. For a tool too short, a longer one. |
| `unreachable` | error | every operation | The tool cannot get there: the feature is not reached from the setup's direction, the fixture stands in the way of a cut, there is no room to ramp in and the tool is not in air, a counterbore on the wrong side, a back bore in a blind hole, a helical bore pitch steeper than the ramp angle. | Read the summary: add a setup from the direction that reaches the feature, hold the stock where the fixture clears the cut (a plate, more margin), use the operation it names. |
| `tool_too_large` | error | most operations | The tool does not fit the feature (a pocket, a region, a slot narrower than it, a hole it does not match within `diameter_tol_mm`), or a back bore's head cannot pass through. | A smaller tool, or the tool of the hole's own size. |
| `tool_too_small` | error | `bore` | A helical bore would leave a core standing in the middle of the hole (a hole over twice the tool's diameter). | `circular`, which spirals out. |
| `gouge` | error | `slot`, `circular` on a region | The path runs into the part above the floor. | Redraw the region inside the material, or cut the feature by its selector. |
| `rpm_clamped` | warning | the feeds | The spindle runs at `max_rpm`, under what the material and tool call for. | Usually fine (the feed follows the slower spindle); a stated feed is kept, so check the chip load. |
| `feed_clamped` | warning | the feeds | The feed or plunge is held to `max_feed_mm_min` (a tap slows its spindle to match). | Each tooth takes a thinner chip and may rub: consider a smaller or fewer-fluted tool. |
| `rest_material` | warning | the drilling operations, `engrave` | Material left where the tool did not reach: a drill's full diameter short of a flat-bottomed hole's floor, a V-carve's flat bottom in corners tighter than the clearing tool. | Finish it with another tool (an end mill for the floor, a smaller `clear_tool`), or tell the user what is left. |

### With the job checks (not in this cadgen yet)

The checks over the whole planned job are designed and coming. Until they land these are not raised,
so look for them yourself in the Viewer and say so when you hand the program over: "the plan did not
check the holder, the fixture against every move, or the machine's travel".

| Finding | Severity | What it will mean | What to do |
| --- | --- | --- | --- |
| `gouge` | error | A feed move cuts into the part beyond its tolerance. | Find the operation; usually a region or a depth that reaches into the part. |
| `holder_collision` | error | The holder or shank hits the stock, the part or the fixture. | A longer stickout or a slimmer holder; check the real tool's stickout. |
| `fixture_collision` | error | A path runs into the fixture. | Move the cut or the fixture; hold the stock on a plate or with more margin. |
| `unreachable` | error | A feature no setup reaches (an undercut). | Another setup, or a tool that reaches it. |
| `too_deep` | error | A cut deeper than the tool's flutes or stickout. | A longer tool, or a shallower cut. |
| `over_travel` | error | A move outside `machine.travel_mm`. | Move the work zero or the part on the table. |
| `no_post_support` | error | The post cannot write what the job needs (today the post refuses with an error message instead). | Change the post or the operation. |
| `empty_operation` | error | An operation that makes no cut. | Drop it, or fix its target. |
| `corner_left` | warning | An inside corner tighter than the tool's radius keeps material (today a pocket's `stats.corner_left` lists these). | A smaller tool for the corners, or tell the user the radius they get. |
| `full_slot` | warning | The tool cuts its full width outside adaptive clearing. | Lower the feed, or use a smaller stepover. |
| `long_stickout` | warning | The tool stands out more than 4 times its diameter: it will chatter and deflect. | Shorten the stickout, or take lighter cuts slower. |
| `uncut_feature` | warning | A feature no operation cuts. | Add the operation, or confirm with the user that it stays. |

Later phases add `rest_material` and `corner_left` from the stock simulation, and `thin_wall`.

### Notes

`info` findings, when a plan has them, say what it did (the cycle time, the tool changes, the post).
The same numbers are always in the sidecar's `summary`.
