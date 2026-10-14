# Drawing

Draw is the CAD viewer's own ink, with no drawing SDK behind it. A view has one
drawing: its marks are projected into the model in 3D, so they stay where they
were drawn from every angle. The STEP renderer is the one that offers Draw
([Draw](cad-renderer.md#draw)).

## Pieces

- `drawing/session.js` — `useDrawingSession`: the drawing's ink, what the tools
  put down next (tool, color, stroke width, line style, placement), the
  selection and an undo history (200 steps). Each add, erase, move or restyle is
  one step. Placement is not a style: choosing it changes only what is drawn
  next, never ink already down, selected or not.
  `clear()` is an undoable step; `discard()` drops the ink and its history, for
  a model that changed under it. The session also keeps the views ink was drawn
  in (`views`, "view 1", "view 2"…): `noteView(view)` names the one a mark goes
  down in — a view drawn in before that is the same (`drawing/views.js`'s
  `sameView`: less than 2° of turn, 5% of zoom or pan, nothing else changed),
  or a new one.
- `drawing/views.js` — a view as a prompt carries it, in a snapshot job's own
  words: the camera (always orthographic), Clip and Explode when on, the pose,
  the parts hidden and isolated, and the picture's size. Never how it looks.
- `drawing/toolbar.jsx` — `DrawingToolbar`, the shared controls built from the
  floating-toolbar button primitive. Pen, Line, Arrow, Rectangle, Ellipse, Text,
  Fill area and Eraser come first, then Select and move drawings and Move view;
  then Color, Stroke width, Line style, Placement (On model, the default, or In
  space), Undo, Redo and Clear drawing. The swatches are neon, so ink stands out
  from a shaded model.
- `drawing/inkGeometry.js` — each tool's mark in screen space:
  - The pen is smoothed as it is drawn, and each point's width comes from its
    pressure (a stylus's own, or simulated from speed with a mouse), using
    `perfect-freehand`.
  - Rectangles get rounded corners.
  - A fill finds the area around the press with
    `@text-to-cad/core/lib/drawing/fillRegion.js`. The outline does not need to
    be closed.
- `renderers/kit/tools/draw/InkOverlay.jsx` — the surface over the viewport
  while Draw is up. It turns every gesture into marks, previews a mark on its
  own canvas as it is made, and puts it into the model when the gesture ends.
  Text is written where the surface is pressed: Enter, or a press elsewhere,
  puts it down as a text mark; Shift+Enter makes a new line; Escape drops it. The
  surface stops the press's mousedown, which would otherwise take the keyboard
  back from the text it just opened.
  Select picks ink (Shift adds to the pick), drags it, and Delete removes it.
  Each mark names the view it was put down (or moved) in.
  The camera stays the person's between strokes: the wheel zooms, and Move view
  hands the pointer to the view.
- `renderers/kit/tools/draw/ink3d.js` — projects a mark into the model.
  - What lies under the marks comes from one render of the parts' depth and
    ids over the marks' bounds, read back. No ray is cast per point: on a large
    assembly that took most of a second per stroke.
  - Every point stays on the ray through where it was drawn, so from that view
    the ink is exactly what was drawn. Only its depth comes from the model.
  - A stroke lies over the nearest of what is within a few pixels of its line,
    so nothing it was drawn over cuts through it. Where it leaves the model it
    carries on in the air from the model's depth there.
  - A fill lies on the surface when all of it is over the model, else whole on a
    sheet in front of what it covers. A text mark stands in front of what it covers.
  - A mark belongs to the part most of it lies on, in that part's frame, so
    Explode and Position carry it, Clip cuts it, and hiding the part hides it.
    Marks drawn as one thing (`group`: an arrow's line and its head) belong to
    the part most of them all lies on, so they never come apart. `touches` lists
    the parts it was drawn over, most first.
  - A mark drawn **in space** ignores the model: every point lies on the plane
    facing the view just in front of the model's bounds, so nothing of the model
    hides it from where it was drawn. It belongs to no part (`part: null`,
    `placement: "space"`), its points are model coordinates, and it stays put
    through Explode, Position and hiding parts. Clip still cuts it. Moved with
    Select, it lands on that plane as the view is then. When every mark is in
    space, the parts are not rendered for depth at all.
  - The projected ink is plain data (`InkItem`): a sketch mark
    (`@text-to-cad/core/common/sketch.js`) with its part's placement as it was
    drawn, kept across scene rebuilds. `mountInk` draws it in a viewer runtime and
    `update` draws only the marks a change adds; the objects and how they follow
    their parts are core's, the ones a snapshot draws a sketch with. The preview
    holds a finished mark until the scene has drawn it. `inkSketch` writes the
    drawing as a sketch file.

## Lifetime and capture

The drawing is session-only: it is never written to tab or file state. It
outlives Draw being put down, because the ink stays in the model and the next
Draw adds to it. It goes when the person clears it, or when the model it was
drawn on changes.

The **Drawing** panel's Copy writes the view as it is on screen, ink included,
to the clipboard as a PNG.

A Quick Edit carries the drawing as files the viewer server makes at the press
(`host.promptFiles`): the drawing as a sketch file, the views it was drawn in (with
the view on screen as it goes) as a snapshot job file, and a picture of each view
rendered by the snapshot's own code, ink included. `cadgen snapshot --job <file>`
renders the same pictures again, over the model or an edit of it. Quick Edit's
Drawing chip copies the sketch file's JSON. Queue and Send clear the drawing with
the note; Copy Prompt keeps it.
