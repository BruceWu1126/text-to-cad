# Findings

Every plan checks itself and lists findings, errors first, then warnings, then notes. Read them
before you report anything: an error means there is no program to hand over.

## What a finding is

Each finding is one JSON object, in the CLI's `--json` and the sidecar's `findings`:

```json
{
  "check": "cam",
  "severity": "error",
  "type": "fixture_collision",
  "summary": "operations[3] (drill): T2's cutter runs 1.803 mm into the plate near X-35 Y15 Z-13.803 (setup top)",
  "description": "the plate is modelled from setup top's fixture (border_mm 25, thickness_mm 12; assumed, as the job does not state them: thickness_mm, border_mm); move the cut, change how the stock is held (taller parallels, a sacrificial plate) or shorten the cut",
  "items": [{"text": "T2 cutter against the plate", "operation": 3, "setup": "top", "tool": "T2",
             "at": [-35.0, 15.0, -2.803], "fixture": "plate", "part": "cutter"}]
}
```

`severity` is `error`, `warning` or `info`; `type` is the code below; `summary` names the operation by
its place in the job (`operations[3] (drill)`, counted from 0) and says what is wrong, with any
position in the setup's work coordinates (what the G-code says); `description` says why and what to
do. `items` points at what it is about: an item that names an operation carries its index
(`operation`), one that names a feature its selector (`ref`), and one with a place that point in the
part's frame, mm (`at`). The CLI prints each finding as one line: `error: <summary>`,
`warning: <summary>` or `info: <summary>`.

- **error**: the job is not planned. No G-code and no GLB are written (an older one at those paths
  is removed), only the sidecar with `"ok": false` and the findings, and the command exits 1. Every
  operation is still tried, so one run lists every refusal: fix them all, then plan again.
- **warning**: planned, with something to weigh. Act on it or tell the user why not, in their words.
- **info**: a note on what the plan assumed or comes to ([Notes](#notes)). Nothing to fix; say the
  assumptions when you hand the program over.

The sidecar's `checks` lists every code in the table below with its verdict: `pass`, or the worst
severity it found.

Two other kinds of message are not findings:

- **A malformed job** is an error message naming the field, and nothing is written, not even the
  sidecar: `operations[2].stepdown_mm: must be > 0, got -1`. So is a job file at the path the plan
  writes its sidecar to: the sidecar of `part.cam.glb` is `part.cam.json`, so name the job
  `part.job.json`.
- **A warning about the run itself** (`warnings` in the sidecar), printed as `warning: ...` after the
  findings: `machine.rapid_mm_min is not stated: the cycle time assumes rapids at 5000 mm/min`. Say
  it when you quote the cycle time.

## The codes

"Raised by": **planning** is an operation refusing to plan (it is not planned; every other one still
is); **the checks** run over the whole planned job; **the post** refuses what its controller cannot
run; **the feeds** hold the speeds to the machine's limits.

| Finding | Severity | Raised by | What it means | What to do |
| --- | --- | --- | --- | --- |
| `gouge` | error | the checks; planning (a `slot` or `circular` region running into the part) | The tool would cut into the part past the operation's tolerance. The checks sweep the tool's cutting edges along every move, rapids too, against the part as modelled, less what the operation is meant to cut that the model does not show (a chamfer, a thread, an engraving, a traced groove). | Find the operation and the place: usually a region drawn into the part, a depth past the feature, or the wrong tool size. Redraw the region inside the material, or cut the feature by its selector. |
| `holder_collision` | error | the checks | The shank above the flutes, or the holder, hits the part, or comes down into the stock below the top of the material the operation starts from. | A longer stickout, a slimmer holder, or a shallower cut. Against the stock it may be pessimistic ([What the checks see](#what-the-checks-see)); a longer stickout clears it for sure. |
| `fixture_collision` | error | the checks; planning (the tool coming down onto a fixture) | Some part of the tool (cutter, shank or holder) runs into the fixture: a profile into a vise jaw, a through drill into the plate under the stock, a tool that would come down onto a jaw standing above the cut's floor. | Move the cut, hold the stock differently (taller parallels, more margin under the part, a sacrificial plate), or cut less deep. |
| `unreachable` | error | planning | The tool cannot get there: the feature is not reached from the setup's direction, there is no room to ramp in and the tool is not in air, a counterbore on the wrong side, a back bore in a blind hole, a helical bore pitch steeper than its ramp angle. | Read the summary: add a setup from the direction that reaches the feature, or use the operation it names. |
| `tool_too_large` | error | planning | The tool does not fit the feature (a pocket, a region, a slot narrower than it, a hole it does not match within `diameter_tol_mm`), or a back bore's head cannot pass through the hole. | A smaller tool, or the tool of the hole's own size. |
| `tool_too_small` | error | planning (`bore`) | A helical bore would leave a core standing in the middle of the hole (a hole over twice the tool's diameter). | `circular`, which spirals out. |
| `too_deep` | error | planning; the checks | Planning: the cut would go into the part (a floor under the feature's own, a facing below the part's top, a spot, pilot or chamfer past what the part allows, a V-carve past its cone). The checks: the tool cuts deeper below the top of its material than its flutes are long (than its reach, for a boring bar, back bore or thread mill), so the shank would rub in the cut. | Leave `depth_mm` and `z_mm` out to use the feature's own depth, or ask for less. For a tool too short, a longer one. |
| `over_travel` | error | the checks | A setup's toolpaths span more than `machine.travel_mm` on an axis. Only the span is compared: the work zero is assumed free to sit anywhere on the table. | No placement of the work makes it fit: split the work into setups, or use a bigger machine. |
| `no_post_support` | error | the post | The post cannot write what the job needs: a tap on grbl (no tapping cycle), a rigid tap on Mach3, an oriented boring bar on a controller that cannot orient its spindle, arcs or helices on a controller without them. The summary names the operation; no G-code is written. | Change the post (`--post`), or the operation (thread mill instead of tapping). [machines-and-posts.md](machines-and-posts.md) says what each post refuses. |
| `empty_operation` | error | planning; the checks | The operation cuts nothing: the feature's bottom is not below the top of the material, a region with no `depth_mm` or `z_mm`, a drilling `all: true` with no hole that suits the tool, a toolpath of rapids alone. | Check the feature, the depth and what the facing before it took off; drop the operation if nothing is left to do. |
| `corner_left` | warning | the checks | A pocket's inside corner is tighter than the tool's radius (plus `stock_to_leave_mm`): material stays in each corner. | A tool of the corner's diameter or less for the corners, or tell the user the radius they get. |
| `rest_material` | warning | the drilling operations, `engrave` | Material left where the tool did not reach: a drill's full diameter stopping short of a flat-bottomed hole's floor (its point is in the way), a V-carve's flat bottom in corners tighter than the clearing tool. | Finish it with another tool (an end mill for the floor, a smaller `clear_tool`), or tell the user what is left. |
| `rpm_clamped` | warning | the feeds | The spindle is capped at `machine.max_rpm`, under what the tool and material (or the operation) call for. A worked-out feed follows the slower spindle, so the chip load is kept and the cut runs slower; a feed the operation states is kept, so each tooth takes a bigger chip. | Usually fine: say so. With a stated feed, lower it in step (the description gives the number). A larger tool, which needs fewer rpm, may suit the machine better. |
| `feed_clamped` | warning | the feeds | The feed or plunge is capped at `machine.max_feed_mm_min`, so each tooth takes a thinner chip than planned. A tap slows its spindle instead, so its feed stays `rpm x pitch`. | A chip too thin rubs and wears the tool: set a lower `rpm` on the operation, or use a tool with fewer flutes, to keep the planned chip at this feed. |
| `full_slot` | warning | the checks | The tool cuts at its full width for more than five of its diameters in one operation (a pocket's links from one ring or row to the next). A full-width cut loads the tool hardest and packs the chips in. | A smaller stepdown, or a lower feed, is gentler on the tool. Say it when the tool is small or the material hard. |
| `thin_wall` | warning | the checks | A hole or pocket an operation cuts leaves less than 1 mm of wall to the part's outside. A thin wall bends away from the cutter and chatters. | Light finishing passes on it, support it, or check the part needs it that thin. |
| `long_stickout` | warning | the checks | A tool stands out of its holder more than 4 times its diameter: expect it to deflect and chatter. The description says when the stickout was assumed (flute length plus one diameter). | Hold it shorter, or take lighter cuts. State the real `stickout_mm`. |
| `uncut_feature` | warning | the checks | A hole, pocket or slot one of the job's setups reaches, that no operation cuts (a spot drill or a chamfer does not count as cutting it). | Add the operation, or confirm with the user it stays as it is. |
| `through_stock_bottom` | warning | the checks | A cut goes below the stock's bottom (a through drill's point): whatever is under the stock there must allow it. Not said when the same cut is a `fixture_collision`. | A spoilboard or sacrificial plate, or a gap between parallels, under that place; tell the user. |
| `assumes_prepared_hole` | warning | the checks | A tap or an internal thread mill works a hole no earlier operation of the job drills, bores or mills, so the job assumes the hole is already there at its size. | Add the drilling first, or confirm with the user the hole is made before the job runs. |
| `unmodelled_earlier_cut` | warning | the checks | A setup is placed on the raw blank though an earlier setup faced the stock in a way phase 1 cannot carry over: a facing of a region (not the whole stock), of a bar or a blank read from a STEP, or in a setup along a face selector. The setup's bottom (or the face its fixture grips) may stand on a side already cut, and its fixture, heights and origin are worked out from the raw blank. | Check by hand where the stock sits in the fixture and where its top is; face the whole stock square in an earlier setup when you can, which cadgen does carry over. |

### What the checks see

- **The part, the stock each setup starts from, and the fixture you described.** Phase 1 does not
  simulate the stock. The one thing it carries from setup to setup is a facing: a facing of the whole
  stock in an earlier setup takes a box or sheet stock down to its floor (a plane across the stock,
  exact for a box seen square), so a later setup turned over stands on the faced side, its vise grips
  from there, and its `stock_top_*` origin and heights sit on the stock as it is. Any other earlier
  facing is an `unmodelled_earlier_cut` warning. Nothing else an earlier operation cut away is taken
  off the stock. So a holder coming down into a
  pocket an earlier operation cleared is still a `holder_collision` (its description says so), and
  material an earlier tool left is not seen (`corner_left` is worked out from the feature's corners
  and the tool, not from what is left).
- **A tool plunges straight down only in air**: its whole circle off the stock's footprint and clear
  of the fixture. Everywhere else it ramps or helixes in. What an earlier operation cleared is not
  air to the plan, so an operation into an already cleared pocket still ramps in.
- **The fixture is a model**: a vise's jaws and floor, a plate, a chuck or a collet, at the sizes the
  job states or the defaults ([job-file.md](job-file.md#fixture)). An `assumed_fixture` note lists
  each default used; check them against the real fixture.

## Notes

`info` findings say what the plan assumed or comes to, after the warnings. The same numbers are in
the sidecar's `summary`.

| Note | What it says |
| --- | --- |
| `assumed_fixture` | The fixture sizes the collision checks used that the job does not state, with the defaults: `Setup top's plate is modelled with sizes the job does not state: border_mm 25`. |
| `unused_tool` | A tool the job lists that no operation runs. It is not drawn or checked, so its type may be one cadgen has no tool shape for yet. |
| `cycle_time` | The cycle time: the moves at their feeds and the rapids (`Cycle time 2 min 07 s`). Tool changes, spindle spin-up and setup changes are not in it. |
| `tool_changes` | How many times the running order switches tool, across setups too, and the order (`1 tool change: T1 then T2`). Loading the first tool is not one. |
| `post` | The controller the G-code is for (`G-code for Grbl 1.1 (grbl post)`), and to prove the program out before cutting. |
