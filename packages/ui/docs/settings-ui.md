# Viewer interaction and settings design system

This is the binding contract for the viewer's tools, its file sidebar and its
settings, in both apps. Build from the shared pieces — the
[toolbar button](../src/primitives/toolbar-button.jsx),
[FloatingToolBar](../src/renderers/kit/tools/FloatingToolBar.js),
[SubToolbar](../src/renderers/kit/tools/SubToolbar.jsx),
[ToolPopover](../src/renderers/kit/tools/ToolPopover.jsx),
[FileSidebar](../src/renderers/kit/tools/FileSidebar.jsx),
[ToolStack](../src/renderers/kit/tools/ToolStack.jsx),
[ToolPanel](../src/renderers/kit/tools/ToolPanel.jsx),
[the floating surface](../src/lib/floatingSurface.js) and the
[FileSheet primitives](../src/renderers/kit/inspector/FileSheet.js) — and extend
them rather than recreating their layout in a renderer. Environmental effects
belong to apps through the [host contract](viewer-host.md).

## Ownership and layout

FileViewer owns the navbar — the logo's app menu ([Settings](#settings)) included, built from
the host's `links` and the `appSettings` `CadViewer` hands it — and the file explorer (a
popover under the file's name). RendererShell owns the scene's chrome: the tool strip and
the view's controls at its end (Display, Preview), the sub-toolbar of the tool in hand under
it, the file sidebar, Quick Edit, the view cube, preview mode and its playbar. Renderers supply
their tools, their tools' rows, their sidebar sections and their document state; the shell never
inspects a format's parts, joints or topology. No pick or tool opens or closes the explorer.

| The host (web, desktop) supplies | The shared UI decides |
| --- | --- |
| Where the tab record lives (`TabRecordStorage`: the web's sessionStorage, the CAD app's memory) | The record: its settings (the file sidebar's and tool stack's layout, the appearance, the home's layout) and the view of the file on screen (its camera, Display settings, preview's Orbit and playbar settings, and renderer slices), what is written when, and what is never stored (`@text-to-cad/ui/tab-store`, `kit/shell/fileView.js`) |
| `navigation.home`, `links` (its version, GitHub, Discord, where a new issue opens, how a link opens), `appSettings` (the person's own settings) and `colorSchemeControl` (its own light/dark control, where it has one) | The navbar's order and look, the app menu (one menu in the viewer and on the home) and what a new issue says |
| `host.files`, `fileActions`, `navigation`, `clipboard`, `promptContext`, `promptFiles` | When a copy, capture or open happens and what it carries |
| `host.environment`: color scheme, keyboard `platform`, `reducedMotion` | How the chrome honours them |
| `onError`, for errors the viewer hands up | Preview, tooltips, keyboard scope, the tool strip and sidebar, the camera |

Hosts pass no preview, chrome-visibility or notification props; there are
none.

- **Navbar.** One row, the same in every app, over every file. Left: the text-to-cad "C"
  logo, named Menu, the size of the row's icon buttons, with no chevron, which opens the
  **app menu** (see [Settings](#settings)) — a host with no home has it too — then, where the
  host keeps the view's own history (`history`: the CAD app's views, which no browser holds),
  **Back** and **Forward**, two arrows walking it as a browser's do, from file to file (the home
  is no step: it is a way to pick a recent file), each there always and disabled where it has
  nowhere to go; then the open file's name and its ⋯. Each of these that
  is a control (`NAV_ITEM_CLASS`) is transparent at rest, takes the same accent background
  while the pointer is on it and while its menu is open, has a pointer cursor and no tooltip:
  it says what it is. The name
  is that control where it opens the explorer (below), where the host's files can be
  browsed; where they cannot it is plain text. The ⋯
  is the file menu: Copy path, and Reveal in Finder / Show in Explorer / Show in file
  manager, each only where the host can do it, and no ⋯ where it can do none. The name has
  no right-click menu and there are no crumbs. The left's controls sit edge to edge, spaced
  by their own padding alone: the logo's 24px button, Back, Forward and the ⋯ in 20px ones,
  and the name 4px in from its edges, so about 7px separate one glyph from the next. Right, only what a host or a file adds: first, the
  update — a blue download button, there only when the host
  found a newer release, whose menu says the step to it, how this host updates and what
  is new. Then, while a person has put an alert card away: the card's own icon (a circle
  with an exclamation mark, red for an error and amber for a warning, named and hinted by
  the alert's title), which brings the card back and goes with it — the renderer's one
  navbar action. Then, where the host shows the view small (inline in a conversation,
  where the view is the whole viewer), its **Full size**, corner brackets (Preview's
  diagonal arrows are the tool strip's). Last, in the page's top-right corner, the host's
  colour scheme control (`colorSchemeControl`), where it has one: the web viewer's
  `ViewerAppearance`, an icon button whose dropdown is System, Light and Dark; the CAD app
  has none, since it takes its host's theme. A CAD file publishes no navbar action but that
  icon. The person's own settings are never here: they are the app menu's.

  *Design note: the navbar is the host's and the file's; the view's controls are the
  strip's.* What a host or the file adds (the update, an alert's icon, Full size) is the
  navbar's right end. The view's own controls, **Display** and **Preview**, end the tool
  strip after a thin rule: they act on the whole view, whatever tool is in hand, so they
  are no tools, but they belong with the chrome over the view, and preview's own row takes
  the strip's place with Display in it. A new per-file button that is no tool goes after
  that rule.

  A host's home has no navbar: under its TEXTTOCAD wordmark and byline, its update, when there is
  one, is a row of its own (the blue button, labeled **Update**, its card centred under it), then
  GitHub (its mark, a link to the project: it is open source), Discord and X, as icon links, and,
  where the host shows the view small, **Full size**, then the host's colour scheme control,
  where it has one, last in that row, above its Recent Files: the models opened before,
  pinned first, as cards or rows, with Open where the host has a file chooser.
  The update shows only for a release
  later than the version the page names, whatever the host says. A file that will not open
  shows a page under the navbar: "File does not exist" and the file's path, or "Could not
  open that file" and the reason, each with **Go home** where the host has a home. A view
  drawn as a picture (`compact`, the home's thumbnails) has no navbar either, and draws the
  model alone: no tools, view actions, cube or Quick Edit.
- **File explorer** is a popover under the file's name (`FolderExplorer.jsx`), aligned to
  the name's left edge, 18rem wide and never resized, as tall as its rows up to 28rem or
  the room below it: past that its list scrolls. It floats over the view and the file
  sidebar, so opening it resizes and moves nothing. It starts in the open file's folder and
  lists one folder at a time — its subfolders, then its CAD files — in compact rows: one 24px
  line of 12px text each, its kind a small light glyph in one column 5px in from the row's
  left end and 4px before the name (a folder its disclosure chevron, with no folder icon; a
  file its type's icon), as the file sidebar's trees draw theirs. A press on a subfolder opens it
  inline, under itself, its rows 10px further in and hung from a faint line under its
  chevron, read once when it first opens; another press closes it, and Right and Left do the
  same from the keyboard. A double-click opens it in the explorer's place, as a crumb does —
  the breadcrumb then names it. The breadcrumb is
  as many of the last three folders as fit whole: every crumb is its folder's whole name, the
  farthest giving way first to the "…" (an ellipsis icon), which lists every folder above the crumbs to jump to,
  and only the folder it is in, left alone beside the "…", is cut (its name a hint while it
  is). The folder it is in reads in the foreground color, the others muted; there is no up
  arrow, and the open file is highlighted, the list opening scrolled to centre its row, once (a
  long folder's too). Opened, its filter has the keyboard, so typing
  finds a file at once; on a phone the explorer itself holds focus, keeping the keyboard
  down. "Filter files..." under the breadcrumb searches
  every file nested under the folder (the server's bounded search, debounced 150ms, each
  keystroke cancelling the search before it); a search that stopped early says "Stopped
  early. Keep typing to narrow the search." Up and Down move between the filter and the
  rows on screen, the rows of a folder open inline among them. A pick opens the file and
  closes it, as do Escape and a press anywhere outside it.
- **Tool strip** at the top centre, 8px down, never wider than the view less 8px at each
  side (it wraps before it would be): the tools, left to right, then a thin rule and the
  view's controls, **Display** and **Preview** (below). 24px tiles with 14px icons, an even
  4px apart, a tool's chevron touching its button, in a 34px row (`FloatingToolBar`,
  `data-cad-toolbar`). A file with no tools and a 3D view has a strip of Display and Preview
  alone; one with neither has no strip.
- **Sub-toolbar** under the strip, 8px below it, centred: the tool in hand's one row of
  controls (`SubToolbar`, `data-cad-sub-toolbar`), there for as long as the tool is up (see
  [Tools and lifecycle](#tools-and-lifecycle)). Select and Measure have none.
- **Tool stack** under the sub-toolbar, in the same centred column: a renderer's own
  `toolPanels`, where it hands any (see
  [The file sidebar and the tool stack](#the-file-sidebar-and-the-tool-stack)). The column
  stops 8px above the cube's line (`VIEWPORT_STACK_BOTTOM`), so it never runs past the
  view.
- **File sidebar** down the left edge, 8px in from the top, left and bottom: one floating
  bar of what there is to read of the file — a STEP assembly's Features, a robot's Links,
  Position, an SDF's metadata — whatever tool is in hand (see
  [The file sidebar and the tool stack](#the-file-sidebar-and-the-tool-stack)).
- **Quick Edit** at top-right, 8px in, a STEP's or a robot's: the box of the note a
  person writes to their agent, there only while something is picked, drawn or measured
  (see [Quick Edit](#quick-edit)). The rest of the right side of the view is clear.
- **View cube** at bottom-right, 2px from the right and 8px off the bottom so the axes
  drawn in its lower corner never touch the edge (`VIEWPORT_CUBE_BOTTOM`), in a 6rem area:
  enlarged face/edge/corner hit areas and neutral hover and XYZ guides. A host whose own
  control floats over that corner lifts it clear with `--cad-viewport-corner-bottom`, the
  room that control takes from the bottom edge. Preview omits it.
- **Display and Preview** end the tool strip, after its rule, for a 3D file: **Display**
  (the active mode's sphere: see [Display settings](#display-settings-and-section-primitives))
  and **Preview** (two diagonal arrows, outward), the strip's tiles, hinted above them
  (`data-view-controls`). They are no tools: pressing one leaves the tool in hand as it is.
  Display's dropdown opens down from its button, centred, never taller than the viewer
  below it. A view that is not 3D has none of them, and neither does a view drawn as a
  picture, one that is loading or failed to load, or preview, whose own row stands in the
  strip's place (see [preview](#camera-animation-and-preview)).
- **Playbar** (preview's, for a file with routines) sits at bottom-centre, on a line 1.75rem
  up (a host whose control floats over the view's bottom moves it with
  `--cad-viewport-bottom-center`), and nothing else does: the tools view has no playbar,
  and a static file has nothing at the bottom. The routines lead it at its left end, as a
  playlist, and the routine's settings end it at its right (see
  [preview](#camera-animation-and-preview)).
- **Model update status** sits under the strip and its sub-toolbar, centred, and where
  there is no strip (preview, a compact view, a load) at the top centre, in the strip's
  34px row, in every host. It is renderer chrome: the navbar carries no status. On mobile
  it is a tappable progress icon whose popover names what is loading.

## Tools and lifecycle

| File | Tool strip, left to right |
| --- | --- |
| STEP | Select, Annotate, Measure, Explode (two or more parts), Clip, Animation (routines only); then Display and Preview |
| URDF / SRDF / SDF | Select; then Display and Preview |
| GLB / STL / 3MF | no tools: Display and Preview alone (a GLB with clips has the Animation tool up from the open, its sub-toolbar under them) |
| DXF | none: a 2D canvas, with no strip |

There is no activity dot. A thin rule parts the tools from the view's controls, which
are no tools (see [Ownership and layout](#ownership-and-layout)). Position is no tool:
its knobs are Select's, while Select's filter picks Position, and its controls are a
section of the [file sidebar](#the-file-sidebar-and-the-tool-stack). Routines play under
the Animation tool and in [preview](#camera-animation-and-preview). A file with no tools
and no 3D view has no strip at all.

**A tool the file cannot offer is not on the strip**: never shown disabled.
Renderers leave it out of the `tools` they hand over; `FloatingToolBar` draws
what it is given. Until a file has loaded, what it offers is not known: a tool
that depends on it (Explode) is shown idle meanwhile, a robot's filter offers
Position meanwhile, and the whole strip is disabled while loading. Unavailable and
idle are different states.

One tool owns pointer input, and it alone is lit on the strip. An effect a person set —
an explosion, a cut, a pose, a routine's paused frame — stays on the model once its tool
is put down, with nothing on screen for it but the model. **A press on the tool in hand
does nothing**: a tool is put down by taking up another (Select, most often). A press on
Explode or Clip with its effect set takes it up again, as it is; unset, it starts neutral.

| Tool | Pressing it | Its sub-toolbar | Leaving it |
| --- | --- | --- | --- |
| Select | The default tool of STEP and robots; picks what its filter ticks (Shift adds; Delete deletes annotations and measurements); what is picked is Quick Edit's chips | None: its filter is the chevron after its button | The selection stays, lit and in Quick Edit, under any tool |
| Annotate | Draws on the view | Its tools, what is drawn next, Undo, Redo and Clear annotations | The annotations stay in the model, their chip in Quick Edit; the pen is the annotation tool again |
| Measure | Arms picking between points on the model; each result a chip in Quick Edit, in its colour | None | Unfinished picks are cancelled; completed measurements stay, Quick Edit's chips |
| Explode / Clip | The effect as it is set, or neutral; an edit applies it (Explode's, as a motion: see below) | The slider, its amount beside it, Reset (Clip's: its axis first, and Flip clip before Reset) | The effect stays on the model |
| Animation | Takes the tool up and plays nothing | Routines, Play/Pause, the scrubber, Playback settings, Reset animation | The routine pauses where it is: a frame past its start stays on the model, one at its start is at rest; the Routine, Speed and Loop stay |

**One motion at a time.** Every tool's state stands beside every other's — the selection,
measurements, the annotations, Clip — except three that move the parts: **Explode**, the
**Position** pose and a routine's **frame**. They replace each other: taking up Explode's or
Animation's tool, or reaching for Position (a knob dragged, a control of its section
pressed), puts the other two back at rest at once — Explode off, the pose at its default, the
routine rewound — with nothing set aside to come back, and using one does the same (Explode's
amount moved off zero; a joint dragged, a value set or a named pose chosen; a routine played
or scrubbed). Put down, a motion keeps its own: an explosion, a pose or a paused frame stays
under any other tool but the other two motions. Preview's routine is its own and moves
nothing of the tools view's.

A tree is the file sidebar's, on screen under every tool; a pick in it takes Select up
before it acts.

**Removing an effect.** Explode's, Clip's and the Animation tool's sub-toolbars end in
Reset ("Reset explode", "Reset clip", "Reset animation"): always there, disabled while there
is nothing to take away, it removes the effect and leaves the tool up — Reset animation stops
the routine and puts the model at rest. Position's way back is its Pose row's Default. A
measurement's chip X deletes it; the Annotation chip's X clears the annotations.

**Select is the one tool with a menu on the strip**: the chevron right after its button
(`ToolSettingsMenu`'s "chevron" trigger, `kit/tools/ToolModeMenu.jsx`), a small button of
its own as tall as the tile and touching it, lit by its own hover and while its menu is
open, never with the tool. A press on any other tool is its only action. Whatever a tool can
be set to while it is up is its sub-toolbar. A dropdown is ordinary — under its own button,
free to overlap the view — and closes with no exit animation, so a quick second tap (touch
included) always reaches its trigger. Choosing a value closes it; ticking an option leaves
it open. Menu checks sit on the right. The other dropdowns over the viewport are Display,
the sub-toolbars' (Clip's axis, Annotate's colour, weight, line style and placement, the
Animation tool's Routines and Playback settings), its context menu and preview's menus —
Orbit, and the playbar's Routines and Playback settings (`ToolPopover`, `PlaybackMenu.jsx`).

**Select's filter** (`kit/tools/select/SelectFilterMenu.jsx`), as a CAD tool's selection
filter is: the chevron's dropdown, headed "Selection filter", then **Select all** — ticked
when every kind is, a dash (indeterminate) when some are; pressing it ticks every kind, or,
with every kind ticked, none — then, after a rule, a checkbox per kind of thing Select picks,
each with its glyph and each on its own. A STEP's (`SelectionModes.jsx`): **Parts** (a whole
cube; an assembly's only — a part file's filter offers none, and a press that reaches no face
or edge picks the whole part), **Faces** (a faint cube, its top face filled), **Edges** (a
faint cube, one edge heavy), **Position** (where the file has joints to move: its knobs),
**Annotations** and **Measurement**. A robot's: **Links** and **Position** (where a
joint can be driven). Every kind is ticked when a file opens; the filter holds while the file
is open, across updates of it, and another file starts with every kind. Ticking leaves the
menu open, so several change in one go, and takes Select up. The chevron's name says what is
picked ("Selection filter: everything", "Selection filter: Faces, Edges", "…: nothing").
Select's button is the pointer, whatever the filter. After another rule, a STEP's filter ends
in how a face or edge pick grows, each with its glyph and independent of the filter and of
each other: **Group edges** (the connected edges with a picked edge), shown while Edges is
ticked, and **Group faces** (the smoothly joined faces with a picked face), shown while Faces
is; a hidden one keeps its choice for when it shows again.

The geometry ticked is picked as one mode (`step/workbench/selectionFilter.js`'s
`selectGeometryMode`): Parts with Faces or Edges, a press takes the face or edge it lands on
where the part's topology can be picked, and the part elsewhere; Parts alone, the part;
Faces or Edges without Parts, that topology alone. The mode sets the Features tree's shape:
with Parts and Faces or Edges, or no geometry at all, it is the person's own (put back as it
was when they left it, with the owners of what is still selected kept open); Parts alone
opens every assembly and shuts every part; Faces or Edges without Parts open every assembly. A
part is a leaf in every shape: nothing opens under it, and a face or edge picked in the
viewport is revealed at its part's row. Outside the person's own shape the disclosure is
locked (chevrons shown, not pressable) and Expand/Collapse leave the row menus. Under Faces
or Edges a part's topology is asked for as its row comes on screen in the tree, never for a
whole large assembly at once; a viewport press on a part not yet loaded loads that part and
picks, and the Features heading says "Loading…" meanwhile. In a large assembly (more than 300
rows of assemblies and parts, `LARGE_TREE_ROWS`) a part's row coming on screen asks for
nothing: the pointer resting on the part in the viewport (150ms) or pressing it asks.

**Sub-toolbars** (`kit/tools/SubToolbar.jsx`): the tool in hand's controls, one row hung
under the strip, centred, 8px below it, on the strip's surface, its buttons the strip's
24px tiles, its groups — what the tool does, how it looks, what it can undo — parted by a
thin rule. It stays up for as long as its tool does, and has no heading: the tool's lit
button above names it. Explode's, Clip's and the Animation tool's are one shape — what leads
the slider, the slider (a solid round thumb), its value right beside it and only as wide as
its text, the tool's actions, then Reset, last, at the same spot in all three, with no rules —
and one width (`SLIDER_SUB_TOOLBAR_CLASS`, `w-56`). The slider takes the rest of the row, so a
button that comes and goes (Animation's Routines) shortens the slider rather than the row.

**Annotate.** Its sub-toolbar, "Annotation controls", while Annotate is up
(`annotation/toolbar.jsx`'s `AnnotationToolbar`): **Annotation tools**, a button each — Pen,
Line, Arrow, Rectangle, Ellipse, Text (a mark put down in place) and Eraser — then a rule and
**Annotation style**, what is drawn next, each a dropdown whose button shows its value, no
chevron: **Color** (a grid of swatches, unnamed but for assistive technology: the neons and
one neutral that reads against the viewer, white in dark mode and near-black in light, then
the colour wheel, which opens the system's colour picker for any other colour, kept once the
picker closes), **Stroke width** (Thin, Medium, Bold — a shape's 1, 2 or 4px, the pen drawn to
look the same weight, its glyph a dot 3, 5 or 7px across) and **Line style** (Solid, Dashed,
Dotted — a shape's line; the pen's freehand ink is always solid; its glyph a diagonal line
drawn in the style), each a row of glyphs alone, the current one marked, and **Placement**
(**Draw on surface**, the default, its glyph a sphere: ink carried with the part under it;
**Draw on plane**, on a plane facing the view through the orbit point, on no part, its glyph a
plane), its glyphs with their names; then **Annotation history** — **Undo**, **Redo** — and
**Clear annotations** (the trash), each disabled while there is nothing for it, with no rule
before them. In a view narrower than 32rem the seven tools and the seven after them are two
rows, column for column, with no rule. Annotate has no Select or Pan of its own (Select picks
annotations, a right drag or Space orbits). Never a restyle of ink already down: the
annotations Select picks have their own bar, a small floating toolbar on the tool strip's
surface just above them — Color, Stroke width and Line style dropdowns, Fill for a closed
shape, a rule, Delete. An update of the model forgets the annotations too, their history with
them (Undo has nothing to bring back), and leaves Annotate the tool, on the tool, colour,
weight, line style and placement in hand. The pen and the shapes share one default stroke
width. The annotations have no panel: while there are any they are Quick Edit's
**Annotation** chip (its X clears them), and while Annotate is up the copy shortcut writes
the view with its annotations to the clipboard as a PNG. A first stroke opens
[Quick Edit](#quick-edit), whose header says **annotation** while there is ink. Annotate
disables the cube without hiding it.

**Animation** plays a file's routines without leaving the tools view; only a file with
routines has it, last of the tools (its icon a plain Play). Pressing it takes the
tool up and plays nothing: the tools view never plays by itself — Autoplay is preview's,
applied on entering it — and a kept frame is taken up as it was, paused. Its sub-toolbar
(`kit/tools/playbar/AnimationPanel.jsx`'s `AnimationToolbar`) is the transport: **Routines**
(a playlist, with two or more routines), **Play** or **Pause**, the scrubber, and **Playback
settings** (a cog: the routine's Speed, a submenu, then Loop and Autoplay) — preview's
playbar's own menus (`RoutineMenu`, `PlaybackMenu`), opening down from the row — and **Reset
animation**, which stops the routine and puts the model at rest, disabled while it is. The Speed and
Loop chosen here are the file's, kept in its view; Autoplay is the file's too (preview's Speed
and Loop are preview's own, and never these: see [preview](#camera-animation-and-preview)).
There is no playbar under the model in the tools view. While it is up nothing on the model is
picked or hovered; the other motions stay as they are until the routine is played or
scrubbed, which puts the pose back to its default and Explode off
([one motion at a time](#tools-and-lifecycle)). Preview leaves the tool's routine as it is
and plays its own (see [preview](#camera-animation-and-preview)): leaving preview with
Animation up finds the routine where it was, playing or paused at its time. Putting it down
pauses the routine where it is: a frame past its start is **kept** — the model stays there
under every other tool, which work on it as it stands (picks, hover, Measure and the annotations
follow the parts where they are drawn) — and one at its start is at rest. The Routine, Speed
and Loop stay for the next time. A prompt's view records a kept frame
(`animation: {clip, time}`, the snapshot job's). An update of the model that changes its
routines keeps Animation up and its Routine chosen (while the model still has it), at rest;
one that leaves no routine puts it down.

**Measure.** It has no sub-toolbar and no menu: it measures between points on the model,
snapping to no edge or face, and its button is the ruler. Each completed measurement
is a chip in [Quick Edit](#quick-edit): the ruler in the measurement's own colour (the
colour of its line on the model) and its reading; its X deletes it, resting on it lights
its line and labels it, and its card is the full reading (the distance, the angle where
there is one, the delta along each axis, what each end bound to). Measurements stay under
every other tool; a press on Measure while it is up does nothing.

**Explode and Clip** have no enabled checkbox, and their sub-toolbars are drawn alike.
Explode's: its slider, the amount (a percentage), **Reset explode**. Clip's: its axis — a
borderless button reading the axis in hand, "X ⌄", whose dropdown lists X, Y and Z — its
slider, the amount, **Flip clip** (which side of the plane is cut away, the cut keeping its
share; disabled while nothing is cut), **Reset clip**. Explode opens at 0%, Clip at no
cut; an edit applies the effect, and the slider back at zero removes it. An axis alone is not
an effect. The view settings own both effects, so a mode reset preserves them and a restored
effect reappears on the model. Clip's slider, left to right, cuts deeper through the original
bounding box; pose, animation and Explode never redefine the range. It shows the share cut
away, while the view stores where the plane stands (`clip.at`, in millimetres), so a model
edit leaves the plane where it was and the slider reads 0% or 100% if the plane ends up
outside the new box. With fewer than two parts Explode is not on the strip.

## The file sidebar and the tool stack

**The file sidebar** (`kit/tools/FileSidebar.jsx`) is one floating bar down the view's left
edge, in a room 8px in from the top, the left and the bottom. It holds what there is to read
of the file, whatever tool is in hand — the strip is for using tools, the sidebar for reading
the file: a STEP assembly's **Features** (a part file, whose tree would be its one row, has
none), then **Position**, where the file has joints to move; a robot's **Links**, an `.sdf`'s
**SDF** (folded at first), then **Position**, where a joint can be driven. A file with none of
them has no sidebar. It is read for as long as the file is open, so it has the opaque floating
surface (`FLOATING_SURFACE_CLASS`). Preview hides it, kept mounted, so a tree keeps its
expansion, find and scroll.

- **Sections.** Each section is a `ToolPanel` drawn as one: a 24px heading row under a faint
  rule (none above the first: the bar's edge is there), its name 12px in from the bar's edge,
  then its body. A section is its content's height, a folded one its heading alone, and none
  scrolls on its own: the bar is as tall as its sections, up to its cap, and past that the
  whole bar scrolls, as one. A tree's rows are mounted as that scroll brings them into view
  (`kit/inspector/VirtualRows.jsx`).
- **Folding.** Sections fold only where there is more than one: one alone is its heading and
  its body. The heading row is the fold, a press anywhere on it but on its buttons (its name is
  the fold's button, with `aria-expanded`), and a small chevron in the padding left of the name
  shows while the pointer is on the row or the fold has the keyboard: right while folded, down
  while open. Which sections are folded is the person's, by id, across files.
- **Pinned and docked heads.** The head of the section the view is in pins to the bar's top
  until the next one pushes it off; the head of a section below the view docks at the bar's
  bottom, over the heads after it, so every section is a press away: a press on a docked head
  unfolds its section if it is folded and brings it to the top at once. The pinned and docked
  heads pad the scroller, so a row scrolled into view in a section is never left under them.
- **Find.** A tree's heading ends in a magnifier ("Find in model", "Find in links"): pressing
  it puts the find field in the heading's place, bordered and focused, its ring while it has
  focus, then what the heading said meanwhile (Loading…) and an X that clears and closes it
  (`primitives/tree-filter.jsx`'s `TreeFindButton` and `TreeFilterInput`). Features' heading
  also carries "Loading…" while a part's faces load and **Show all** while parts are hidden.
  Every tree's find is one ranked flat search (`kit/inspector/modelTreeSearch.js`).
- **Size.** The bar opens a tool panel's width (`TOOL_PANEL_WIDTH`, 164px) and as tall as its
  sections, capped at all of its room. It is sized from its bottom-right corner alone, by Quick
  Edit's grip (`kit/tools/ResizeGrip.jsx`): width and cap together, by pointer, or by keyboard
  (a separator named "Resize file sidebar": Left/Right nudge the width and Up/Down the cap by
  16px, Home and End take both to their bounds). Its width runs from 120px
  (`FILE_SIDEBAR_MIN_WIDTH`: room for a heading's name and its buttons; a tree's names
  truncate) to half the viewer, its cap from 64px to its room. One write, when the gesture
  lets go (or per key), never per pointer move.
- **Put away.** The panel button at the right end of the first section's head ("Hide file
  sidebar") slides the whole bar out past the viewer's left edge (200ms, none under reduced
  motion), where nothing in it can be reached; while it is away the same button, bare ("Show
  file sidebar"), sits in the corner it hung from, centred under the navbar's logo, and brings
  it back. A find field up in the first section's heading keeps the button after it.
- **The layout is the person's.** The bar's size, whether it is put away, and the folded
  sections are one of the tab's settings, across its files (`CadPreferences.toolStack`:
  `{ panels: { [id]: { width?, height? } }, collapsed: { [id]: boolean } }`, the bar under
  `sidebar`, kept beside the appearance; any other key a stored record carries is dropped).
  `panels` holds only what a person set; `collapsed` only what differs from a section's start
  (the SDF section starts folded). A new tab (a cleared record) puts everything back at its
  defaults.

**The tool stack** (`ToolStack`) is the column under the strip and the sub-toolbar, centred
with them, for a renderer's own `toolPanels`. STEP and the robot renderer hand none: their
tools' controls are sub-toolbars and what there is to read of their files is the sidebar's.
Its panels follow these rules:

- **Two kinds of panel.** A `resizable` panel is the person's to size, on its own; every
  other panel is *fixed*: one width, its content's height, and no grip.
- **One width.** Every panel opens at `TOOL_PANEL_WIDTH`: 164px, a strip of six tools (six
  24px buttons, 2px gaps, 4px padding and a 1px border). A fixed panel is exactly that wide; a
  resizable one is only ever made wider, up to half the viewer. The panels hang left-aligned
  in the column, each at its own width. Content truncates to fit; it never widens a panel.
- **Heights.** A panel is exactly its content's height — never padded to a minimum. A
  resizable panel has a *cap* the content grows up to and then scrolls inside: it opens capped
  at the stack's own height (`toolPanelDefaultHeight`). A cap is never a floor.
- **One grip, on a resizable panel only**, the sidebar's: its bottom-right corner, Quick
  Edit's grip, moving its width and cap at once, a separator in the tab order named after its
  panel, keyed as the sidebar's is. A folded panel has no grip: there is no height to set.
- **Never past the viewer.** The column stops 8px above the cube's line
  (`VIEWPORT_STACK_BOTTOM`, `calc(6rem + 16px)`). When the panels need more, a `"tree"` panel
  gives way first, down to 128px or its content, then the `"details"` panels together, each in
  proportion to its height, down to 96px or its content; a `"fixed"` panel keeps its height.
  If what cannot give way still does not fit, the column itself scrolls — a panel is never cut.
- **Headings.** A panel's heading row reads as Display's section headings do (11px, regular,
  `TOOL_PANEL_HEADING_TEXT_CLASS`), 28px tall, 8px in: a title, a summary where there is one,
  then at its right end its own actions, the chevron where it folds (up while open, down while
  folded) and an X where it has something to remove. Every small button in a heading is 20px,
  2px apart and 4px from the edge. A panel can end in one full-row button under its body,
  which never scrolls (`footer`, `ToolPanelFooterButton`).

- **Surfaces.** Two, defined once (`lib/floatingSurface.js`), with one border: the
  tool strip, its sub-toolbars and the stack's panels, which stay up beside the model, share
  `FLOATING_CHROME_SURFACE_CLASS` — the background at 45.5% and barely blurred
  (2px), so the model behind them is easy to make out; every menu and popover
  over the viewport, the app menu and Display included, and the file sidebar, which is read
  as they are, share `FLOATING_SURFACE_CLASS` (the background at 75%, blurred), so their text
  never competes with the model.
- **Scrolling.** Every scroll region in the viewer's chrome — the file sidebar, a panel's
  body, the stack's column, the explorer, a menu, the alert card — is the
  `ScrollArea` primitive (`primitives/scroll-area.jsx`, shadcn's): thin overlay
  bars in the theme's colours, shown while the pointer is over the region. No
  native `overflow-auto` scroller in chrome; `src/designSystem.test.js` holds it.
  The home (the model library) is a page, not chrome: it scrolls as a page does,
  with the platform's scrollbar, and only when there is more of it than fits.
- **Nothing opens elsewhere.** A pick shows as Quick Edit's chips; the sidebar is on screen
  under every tool. No pick or tool opens or closes the explorer, on desktop or mobile.

Tree rows inset their backgrounds 4px from the bar's edges, and the find field
shares that inset. A tree in the file sidebar (Features, Links) is dense (`TreeRowSurface`'s
`dense`): 24px rows in the panels' 11px text, and ONE light 12px glyph per row in one
column, as the explorer has: a subassembly's or folded group's disclosure chevron, turned down
while open, or a part's box (Links draws its chevrons alone), 4px before the name. The glyph
column starts 5px in from the row's left end (`TREE_ROW_INSET_PX`), centred under the find
field's search icon and as far in as the icons of a row's actions sit from its right end, in the
STEP tree and the explorer alike; each level adds
the explorer's compact indent, 10px (`TREE_INDENT_PX`), its rows hung from a faint line under
their owner's glyph (`TreeRowGuides`), as the explorer's are; its find field stands in its heading's row. An assembly row's
actions, shown on hover and kept while they are on, are **Isolate** then the **Hide/Reveal** eye; a part file has no
Isolate. A Links row that draws something — a link with visuals, or one visual —
has the eye alone, in its search hit too: a link's hides its own visuals, never its
child links, and on a partly hidden link hides the rest. They float over the row's right end rather than taking width from it:
the name runs the row's full width and, while an action shows, fades out half a
rem before them (a mask, `ROW_NAME_UNDER_ACTIONS`), so nothing is drawn behind the
buttons and the row keeps its own colour.
Model and link finds and the explorer's filter share `TreeFilterInput`; the explorer's rows are compact
24px lines, as the trees' are, but in 12px text.

**Mobile** is below 720px of FileViewer width — the one viewer breakpoint
(`useViewerMobile`); chrome never uses window breakpoints. The tool strip and
the file sidebar are the same: a STEP's Features and a robot's Links show as they
do on a desktop. The
explorer is the same popover, never wider than the window less 1rem. The navbar is the
same row; the shortcut hint is hidden.
Touch works for every tool: one finger orbits, two pan and
zoom, a tap picks; a pinch, a cancelled pointer or a camera drag is never a
pick.

## Display settings and section primitives

Display is not a tool. Its tile at the strip's end shows the current mode's sphere icon and
opens a menu labelled **Display mode**. The strip order is Display, Preview. Below the **Display Mode** section, a **Projection** section
contains Orthographic and Perspective radio choices, disabled while Render forces perspective.
Preview uses this same menu over the same file settings. Opening it leaves the active tool
and its controls in place. A 2D view has no Display or Preview button.

The modes are Solid, Render, X-ray, Hidden line and Wireframe, filtered by the view's injected
capabilities. Each row has a radio item with the mode's sphere and name, a settings trigger
(`Settings2`) immediately left of the check at the right edge, and that mode's submenu.
Clicking the name selects its remembered settings. Opening or editing another mode's submenu
does not select it. There is no Custom state and Grid is a setting, not a mode.

| Mode | Settings |
| --- | --- |
| Solid | Part colours, Flat shading, Edges and edge colour, Grid, Axes |
| Render | Part colours, Lighting, Background, Floor |
| X-ray | Part colours, Surface opacity, edge colour, Grid, Axes |
| Hidden line / Wireframe | Edge colour, Grid, Axes |

On/off controls are menu checkboxes that keep the menu open. Choices use radio submenus.
Continuous values use compact label/slider/value rows whose keyboard input stays with the
slider. Colours use swatch grids and a custom colour wheel; By part keeps an editable palette.
Lighting has its own submenu. Grid and Axes are independent, off by default; each enabled
guide exposes its appearance. Floor is on by default in Render, at the model's lowest point.
Settings the view does not offer are absent. Nested submenus own Escape before their parent.

The last item is **Reset <Mode>**, disabled at that mode's defaults. It removes only that
mode's overrides, preserving every other mode, the shared camera and appearance, Clip and
Explode. Per-file storage remembers each mode independently. Old-shaped stored display
settings are dropped, never migrated. Projection is shared, orthographic by default; Render
resolves to perspective while preserving the shared choice for the other modes.

Other inspector sections still use shared FileSheet primitives; Display uses menu-native
controls rather than collapsible settings sections. Hover, focus and rendering never write
settings. Light and dark remain the host's colour scheme control.

Type comes from the token scale only (`text-micro` 10px, `text-tiny` 11px,
`text-xs` 12px, `text-ui` 13px); no pixel font sizes. Settings controls and the
menu labels use `text-tiny`, regular weight;
panel headings use 12px. Sections use 8px gutters and bottom insets
and 4px row and column gaps. `FileSheetFieldGrid` owns horizontal spacing; its
children never pad again. Menu, select and context-menu typography comes from
the primitives, portals included. Selected values show their icon where it
means something.

Use `FileSheetSliderField` for a useful bounded range with a committed number
input, `FileSheetNumberProperty` for other scalars, `FileSheetColorProperty` for
color with opacity, and ordinary Select controls for choices. Drafts stay local
until Enter or blur; Escape cancels. Clamp at the owner's write boundary, which
sliders and number inputs share. Omit a visible label only where the value or
icon is unambiguous, and always keep the accessible name.

## Settings

Settings are the person's, never a file's, and live in the app menu: one dropdown
(`file-viewer/navigation/AppMenu.jsx`) on the floating surface, 15rem wide, from the
navbar's logo over every file (FileViewer builds it from the host's `links`, the
`appSettings` `CadViewer` hands it and `environment.platform`, while a model loads and after
it failed to as well); the home has no menu. Its rows, a rule between each group, are only
what the host supplies:

- **Back to files**, where the host has a home
  (`navigation.home`): it shows the home in this view. The home's own menu has none.
- The host's own on/off settings (`appSettings`), a checkbox item each, in the order they
  come, checked at the right; a press turns one without closing the menu. **Share
  usage stats**, in both apps.
- **Send feedback**, where the host has a tracker (`links.issues`): a link to a new issue
  titled "Feedback: " for the person to finish, naming the version and the platform, with
  no label (what is said may be a bug, a request or a question). Then **GitHub** and
  **Discord**.
- Last, in gray, a footer: "v<version> · Made by @<handle>" — the version a link to its
  release notes, the handle one to the host's X account. A build that is not the release's
  own adds its id to the version: `v0.7.15-dev.b80844940`, the commit it was built from.

Nothing of a file is in it: a file's view is
[Display](#display-settings-and-section-primitives)'s. Every setting is the person's, not a
view's or a tab's: the host keeps it where every view of theirs reads it (both apps: their
settings, `settings.json` in cadgen's state directory), and a view reads it again whenever
the person comes back to the page.

## Position and references

**Position** is a section of the [file sidebar](#the-file-sidebar-and-the-tool-stack), on
screen under every tool, headed "Position", with no X: a STEP's where the file has joints to
move, a robot's where a joint can be driven. Its knobs in the viewport are Select's, while
Select's filter ticks Position. Its first row is "Pose" — a label beside its dropdown
(`KinematicsPoseRow`): **Default**, which puts the joints back, then the named poses, and
Custom while the joints are set by hand. No divider under the pose row and no Reset footer.
Each joint's label sits tight above its
slider in the flexible left column, with a compact value field (24px tall, about
five characters wide, tabular figures; degrees read "90.0°") in the right column
of the same row; joint rows are 4px apart. Long labels truncate with a full-name
hint and never take the slider's width. Slider thumbs are named after their
joint, and every slider reports its value at its step's precision (never float
noise). Writes pose the model at once.

Position persists across tools, and across an update of the model whose joints and
named poses are unchanged, the named pose chosen included; an update that changed them
starts the pose at the new defaults, never fitting the old values onto other joints.
Default restores the authored
values (an SRDF's home included), stops motion and hands control back to
Position. A routine played or scrubbed in the tools view puts the pose back to its default
for good ([one motion at a time](#tools-and-lifecycle)); preview's routine leaves it as it is,
and preview never shows it at all (it draws the model at rest, see
[preview](#camera-animation-and-preview)). A Position edit, Default included, stops and rewinds a
routine, a kept frame too, but keeps its Routine, Speed and Loop for the next play. Kinematics, named poses and
animation are separate capabilities; the absence of one never leaves empty
controls for another.

What is picked is [Quick Edit](#quick-edit)'s chips, and a chip's card is what a person
reads of it (`kit/tools/quick-edit/ReferenceChips.jsx`). A component or subassembly is a chip
of its own; faces, edges and solids are a chip for each kind on each part — "base · face 3"
alone, "base · 6 faces" for several, however they were picked — so a large selection stays a
few chips. A chip names its reference: its own label when it has one (a part's or
subassembly's name, a named face), otherwise its part and kind ("base · face 3") — never the
raw id, which its card shows under the name. In a STEP the card's rows are the reference's key
measurements, nothing else: a face's area, and a round face's diameter and radius (a torus's
two radii); an edge's length, or a round one's diameter, radius and circumference or arc
length, with an arc's sweep; a part's size and volume; a subassembly's part count, size and
volume; a run on one part, one row each. Its rows are the UI font at `text-tiny`, numbers in
tabular figures — no monospace. A click on a chip copies its references, each naming its file
by absolute path; resting on it lights its picks; its X drops them.

Viewport picks reach individual faces and edges. In an assembly, faces and edges load per part, when first
needed: under a face or edge filter, a press on a part whose faces are not
loaded loads that part alone and then picks what is under the pointer — one
press, with "Loading…" in the Features heading meanwhile. Until
then, hovering such a part lights the whole part.
Shift-click adds to a selection in the viewport; Shift, Ctrl or Cmd does in a
tree (robots select several links this way). Double-click, or a row's Isolate,
isolates a component or subassembly; double-click on empty space leaves
isolation, as do the isolation bar's Exit and the lit Isolate. Only topology that
cannot be isolated copies on double-click, and it stays selected.

## Quick Edit

Quick Edit is a person's note to their agent about the file on screen, and the
one way the viewer hands it over: a box at the viewport's top-right, 8px in, on the
floating surface. It is there only while it has something to carry or a note to keep:
nothing stands in for it otherwise, not even a button. Only a view whose picks and
annotations a note can carry has it (a STEP's, a robot's); a compact host gets none, and
preview puts it away with the tools. While the view loads it is out of sight, its note
and its state kept. It is essential, so no setting turns it off.

Below 64rem, the sidebar and Quick Edit start beneath the full tool strip and
sub-toolbar, including Annotate's two rows. Each side column is capped at half
the view minus the gutter, so neither covers the other; the sidebar keeps its
collapse button and corner resizer. Quick Edit stays within its right column,
which scrolls above the view cube when space is short.

The box opens 15rem wide, capped to its column, its note growing with what is written up to 10rem. Its
bottom-left corner — the grip every resizable box over the viewport has
(`kit/tools/ResizeGrip.jsx`) — drags it wider to the left and its note taller, for as long as the
box is open: once it closes, however it closes, the next box opens at 15rem again, and
so does another file's. A drag keeps the box at the corner, a frame at a time and
never eased, and renders nothing: the size is written to the box itself, so nothing
else in the viewer redraws under the pointer. Its header is the title, one step up
the type scale ("Quick Edit"); then what goes with the note, in muted text: **N
ref(s)**, the selection counted one by one (a reference naming several picks counts
each; hovering lists their ids, one a line), and **annotation** while there are annotations;
then an X. The file always goes and is not named. Under the header, the note
("Describe your changes"), its border blue while it has the keyboard, and the
buttons at its bottom-right.

While the note is empty the box follows what it would carry: it opens when something
is picked, annotated or measured, and closes when that goes. Every chip leads with its
icon — the Annotation chip's pen, a measurement's ruler in its colour, a reference's pick glyph
(a part's cube, a face, an edge, a vertex) — then its words, and they stand in the order
they came, whatever their kind. A chip's copy pasted into the note is a pill there,
drawn as the chip is, 20px like the note's lines so it sits in its rows without spacing
them out. A click on a pill selects it whole, highlighted in the selection's colour, so
Backspace, Delete or typing replaces it as it would selected text. Its chips are
**Annotation** (a click copies the path of its `.annotation.json` file, what `cadgen snapshot
--annotation` takes), each measurement (the ruler in its colour and its reading; a click copies the path
of a file holding it as a snapshot job's `measurements` entry, what `cadgen snapshot
--measurements` takes; the views the note carries draw every one, and its job file lists
them), then the selection. A host that keeps no prompt files copies the JSON itself. A written note keeps it open whatever
is picked, even with nothing picked or annotated, until the X. The X clears everything:
the note, the selection (as a press on the model's background does), the annotations and the
measurements, so the box goes. A note that has gone (Copy Prompt, Queue, Send) clears the same way.
Escape in an empty box is the viewer's Escape (clearing the selection, and so the box);
in a box with a note it only hands the keyboard back to the model.

The person's picks put the keyboard in the note, each one (a pick right after their
primary press in this view), and so does a first annotation stroke once the pen lifts. A
selection an agent or host makes through the live controller (`select`) opens it without
taking the keyboard.

The buttons, left to right, are only those the host can carry out, and the rightmost
is the primary one, which Enter presses (Shift+Enter is a new line). They are the
`Button` primitive in its own shape — 28px squares with its rounded corners, each an
icon with a name and a hint — and every one is solid: the primary in the `default`
variant, the others in `secondary`. Where Copy Prompt is the only one (a host that
takes no message, as the web), it is a button in words, "Copy Prompt" with its icon
first. While there is no note they are disabled, dimmed but still drawn:

- **Copy Prompt** (copy icon), always. It copies the note as text: its references
  spelled as copied references are, and its files by the paths they were saved at,
  since text carries no file.
- **Queue** (list-plus icon), where the destination is a composer: the note goes
  into the context of the person's next message.
- **Send** (up arrow), where the host can post a message: the note goes now, as the
  person's message.

Every way the note goes carries the same files, made at the press: a picture of each
view it carries — the view on screen as it goes, and each its annotations were drawn in,
one such view the same as the one on screen standing for it — rendered as a snapshot
renders (solid, light, orthographic, the ink in its own colours) whatever the view
showed; those views as a snapshot job file; and the annotations as an annotation file
(`.annotation.json`).

Queue and Send clear the note and what it carries, and so close the box, once the
note has gone. Copy Prompt keeps it all, since nothing has gone yet: its button
shows a tick for a moment. A failure keeps the note and says why in the box. The message is
one format, sent, queued or copied: what the person wrote; then `File: <path>`; then
`References:` and one reference per line; then each file that travels as a file by its
label and path — `Current view:`, `View 1:`, `Views (cadgen snapshot --job):`,
`Annotation:` (a picture sent beside the text is not named).

## Camera, animation and preview

Cube face, edge and corner clicks turn the view and keep pan and zoom; dragging
the cube orbits. Reloading the page restores the camera the file on screen was left
at, and its Display settings, Explode/Clip, the pose, hidden and isolated parts and
the tree's expansion come back with it. Only the model on screen keeps that view:
leaving it — for another file or the home — drops it, so opening a file,
for the first time or again, fits it at the defaults. An update of the model on
screen keeps its Display settings, Explode/Clip, hidden and isolated parts and its
tree, and the pose while its joints are unchanged. The tool, the selection and
measurements never come back — every open starts in the default tool with nothing
selected.

Zoom to Fit recenters and frames the whole original model at the current angle;
Zoom to Selection frames the selection and is unavailable without one. Both are
STEP context-menu items; the live `resetCamera` command takes the same fit path.

**Preview** is available for every 3D file, animated or not, and for nothing
else: a renderer declares it (`useRendererShell`'s `previewable`: STEP, GLB, STL/3MF,
URDF/SRDF/SDF), and a view whose renderer does not (a DXF, 2D) has no Preview button,
not a disabled one, and never enters it, whatever asks. It is the shell's
own state (`previewing`); hosts neither start nor observe it, beyond giving the
page over to it. Its button is two diagonal arrows, outward ("Preview"), last on the
tool strip, after Display. It is fullscreen: the renderer says so
(`onFullscreenChange`), and the navbar (its menu and the explorer with it) steps aside
while it lasts; the tool strip, its sub-toolbar, the file sidebar, the tool stack and
their resize grips, Quick Edit, joint handles, cube and context menu are gone. Its
controls are its own row at the view's top centre, 8px down, where the strip was, on the
strip's surface and of its tiles (`PreviewChrome`): **Orbit** (the orbit icon) first, then
**Display**, then the way out, two diagonal arrows, inward ("Exit preview"), where Preview
was; their hints show below them. Orbit's dropdown opens down from it: Orbit on or off, and
its Speed. Display's is the one the strip's opens, over the same settings (see
[Display settings](#display-settings-and-section-primitives)). The row fades with the
playbar; Escape leaves preview too, once an open dropdown has closed. It starts orbiting,
unless the file's Orbit is off. Under the model, an animated file shows its playbar: its
**Routines** (a playlist icon, with more than one routine: the routines, the one in hand
checked, a choice playing next) at its left end, then play/pause and the scrubber, then
**Playback settings** (a cog) at its right end — the routine's Speed, Loop and Autoplay.
Both open up from their buttons (`PlaybackMenu.jsx`). A static file shows nothing there.
The row and the playbar share one one-second idle deadline and a 150ms fade: movement wakes
them, and hovering them or any open dropdown of theirs, Display's included, holds them.

Routines play in preview and under the [Animation tool](#tools-and-lifecycle).
Preview's routine is its own: it opens at rest on the tools view's Routine, whatever the
Animation tool was doing, and starts when Autoplay is on (off by default). Leaving
preview stops its routine — a routine chosen there, its time, whether it played — and
hands the tools view's back as preview found it: at rest, a kept frame, or under Animation
where it was, paused at its time or playing on from it (`savePlayback` and `restorePlayback`
on the playbar runtime, `kit/tools/playbar/ViewportAnimationBar.js`). An update of the model that leaves its routines as they were
neither stops nor rewinds one that is playing; a changed routine starts at rest.
Orbit's settings and Autoplay are the file's own and are remembered between leaving
and re-entering preview and across a reload of the tab: Orbit on or off (on by
default) and its speed (1×) — an Orbit turned off stays off in every later preview, and
one turned on stays on — and Autoplay, which the Animation tool's settings and preview's
Playback settings both show and set. Speed and Loop are each mode's own. The tools
view's, the Animation tool's, are the file's, kept in its view once chosen — until then
the routine's own apply. Preview's start at the routine's own every time it opens,
whatever the tools view chose; what is chosen in its Playback settings plays there and
is forgotten on the way out, never written to the tools view's. Another file has its
own, and a file the tab left starts at the defaults again. Nothing of the routine —
which one, its time, whether it plays — is saved. Orbit is not an animation setting.

**Preview and the tools view are two states.** Entering preview leaves the tools view's
state exactly as it is — the tool in hand and its sub-toolbar, the Position pose, the
selection, hidden and isolated parts, Explode and Clip, Measure's results, the annotations,
the camera, the Animation tool's routine — and preview starts clean, from the model as
authored: at its opening pose, every part shown, nothing isolated, picked, hovered,
measured, exploded or clipped, its own camera fitted at the default angle, its routine
at rest at the routine's own Speed and Loop. Leaving preview throws away what was done
there — its camera, its routine, the Speed and Loop chosen in it — and the tools view comes back exactly as it was, its camera in the projection and lens
the Display settings now hold. Nothing of the tools view is reset to make room for
preview and nothing of preview's is kept: the next preview starts clean again. A
renderer holds this by drawing preview from its opening view rather than resetting
its own state (`useRendererShell`'s `preview`); the routine, which both modes play on
the one model, is saved on the way in and handed back on the way out (above). The
viewport stays mounted throughout. What is not either mode's state is the file's
settings, shared by both and kept: its Display settings (an edit in preview's Display
is the tools view's too), Orbit (on or off, and its speed) and Autoplay.

**Render profiles.** One viewport draws the same Display settings two ways
(`kit/viewport/renderProfile.js`). The tools view is drawn for working on the
model: the scene quality its settings resolve to, and a lower pixel ratio while
the camera moves so a gesture stays responsive. Preview is drawn for looking at
it: one scene-quality tier up (Interactive to Standard, Standard to High — a
higher idle pixel ratio, and at High larger shadow and environment maps, at the
same meshes), its full pixel ratio kept while it orbits, and nothing
of the tools view's (no picking, overlays, cube or tool effects). The profile
never changes a Display setting.

## Keyboard

Escape and Copy belong to one viewer: the one with focus, or the one last
pressed in while focus is on the page. Editable targets keep their own keys.

- **Escape**, innermost first: an open popup in this viewer (a menu, the app menu
  included, a Select, a color picker, Display, the explorer) closes itself; then preview exits;
  then Annotate's canvas spends its own Escape; then the renderer's (STEP: an
  unfinished measurement, then the Measure tool, then the selection, then
  isolation; robots: the selection). The file sidebar and the tool stack's panels are
  never Escape's to close.
- **The file sidebar's grip**, and a resizable panel's (its bottom-right corner), is a
  separator in the tab order: Left/Right nudge its width and Up/Down its cap by 16px, and Home and
  End take both to their bounds.
- **Copy** (⌘C or Ctrl+C, and Ctrl+Insert) copies the view with its annotations
  while Annotate is up, otherwise the selection's references — unless a text field
  has focus or text is selected.
- **Quick Edit's box** keeps its own keys: Enter presses the primary button and
  Shift+Enter is a new line. Escape hands the keyboard back to the view and keeps
  a note; in an empty box it closes the box and is also the viewer's own Escape
  (it clears the selection). ⌘C or Ctrl+C with none of its text selected is the
  viewer's copy.
- **Arrow keys and WASD** orbit the viewer that has focus or the pointer over
  it, never every mounted viewport; they do nothing in preview or with a
  modifier held.

## Tooltips and feedback

Every hint is a `TooltipHint`: compact text, one surface and arrow, a 400ms
delay. No native `title` anywhere in chrome. Prefer one or two words; show a
full technical name only when it is truncated (`overflowOnly`). Quick Edit's buttons, the
strip's buttons (Display and Preview among them), the navbar's right-hand controls (the
update, an alert's icon, Full size), preview's row (Orbit, Display, Exit preview) and the
playbar's Routines are hinted by name — the strip's and the playbar's hints above their
buttons, the others' below; there is no hint on the navbar's left (the logo,
the file's name and the ⋯ say what they are), on Playback settings, X,
the rest of the transport (play, pause, the scrubber), annotation tools or labelled text
buttons (Quick Edit's alone say what they do). A disabled, selected or expanded control has none; pressing or leaving cancels a
pending one. A hint appears on focus only for keyboard navigation (a Tab), never
when a closing menu hands focus back.

The viewer shows no toasts or notifications: copy, snapshot and prompt actions
complete silently, the Copy and Quick Edit buttons showing a tick for a moment.
Progress stays in the viewport; a failed action is the
viewport's alert card, whose **Retry** reloads the file and whose **Report Issue**,
where the host has a tracker, opens a new issue titled "Issue: ", labelled `bug`, filled
in from the card. Its **Details** open into a box that scrolls, with a copy icon at the
box's top-right corner, hinted **Copy**: it copies all of the details and shows a tick for a
moment, or, where the clipboard refuses, a line under the box saying so. Errors handed to
the host's `onError` are the host's to show. A card the
model survives (a failed update, a warning beside the model) has an X, **Dismiss**: it puts
the card away for as long as that alert stands, and its icon, first of the navbar's
right-hand controls after the update button, brings it back. The dismissal goes once the alert changes or clears, or
another file opens; with the card back, so does the icon. Preview has no navbar, and so no
icon.

## Verification

Verify state transitions, touch cancellation, keyboard operation, nested popup
dismissal, feature availability, narrow panels and empty or error states.
Camera and kept-tool tests exercise real transitions, not class names. Do not
keep tests for discarded layouts: replace their assertions with coverage of this
contract. `src/designSystem.test.js` holds chrome to the type scale and the one
breakpoint. The core settings semantics are in
[render-mode.md](render-mode.md); update scheduling is in
[view-updates.md](view-updates.md).
