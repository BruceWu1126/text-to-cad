# Inspecting and checking documents in Python

Checks are small Python scripts against a document, an `.xbf` or a STEP; there is
no inspection command beyond `cadgen inspect`. A check reads the document as the
latest build wrote it, so it sees a source edit only after the model runs. Paths
resolve from the working directory and expand `~`.

## Opening geometry

```python
from cadgen.engine import read_xbf
import build123d as bd

kit = read_xbf("XBF/kit.xbf")                        # the document as a build123d tree
for part in kit.leaves:                               # every placed part, world coordinates
    print(part.label, part.volume)

hand = next(node for node in kit.descendants if node.label == "hand")   # an assembly: a Compound
fingers = [node for node in hand.children if node.label == "finger"]    # its components, in order
top = fingers[1].faces().sort_by(bd.Axis.Z)[-1]                         # build123d's own selectors
```

- `read_xbf(path)` and `read_step(path)` read a document into a build123d compound
  tree: each assembly a `Compound` of its components, each part named, coloured and
  given its material as the file has them, in world coordinates. Walk it by labels
  (`children`, `descendants`, `leaves`) and select within a part with build123d
  (`faces()`, `edges()`, `sort_by`, `filter_by`).
- A ref a person gave names one element exactly: `cadgen inspect` answers it
  ([below](#which-code-made-it)), and its facts (centroid, box, surface) tell which
  face of the part it is.
- `bounding_box()` is the world-aligned envelope; `bounding_box(optimal=False)` is a
  fast conservative one for curved geometry.

## References

A ref names a part, an assembly or one element of a document by the engine's own
names, resolved in the document as the latest build left it:

```text
REF      = [FILE "#"] SELECTOR ("," SELECTOR)*
SELECTOR = OWNER | [OWNER "/"] ELEMENT          no OWNER: the document's root
OWNER    = SEGMENT ("/" SEGMENT)*               an occurrence's path, or its last segments
SEGMENT  = LABEL | LABEL "." N | "QUOTED" ["." N]
ELEMENT  = NAME | face N | edge N | vertex N    NAME: f:..., e:..., v:...
```

```text
kit/hand/finger.2                       the second component labelled finger in hand
finger.2/face 7                         face 7 of it: the path may start at any level that names one occurrence
palm/f:palm.body.3;M:palm.relief        a face of palm by its name
/work/XBF/kit.xbf#palm,finger.1         two selectors, with the document's path first
"m3/screw"/f:kit.screw.1                a label holding '/', ',' or a quote is JSON-quoted
plate/face 3                            in a document of one part, its elements by the document's name
```

- **An occurrence is named by the labels on the way to it.** Siblings sharing a
  label are numbered among them, in component order, so a repeated part is always
  reached by its path, never by a number across the document. A label naming several
  occurrences is refused with their paths (`finger names 3 occurrences in kit.xbf:
  hand/finger.1, hand/finger.2, hand/finger.3`).
- **An element is named by its name or its number.** Numbers are the part's own
  face, edge and vertex numbers, the ones the viewer and `cadgen inspect` show; a
  name survives edits that a number does not. A bare element after an owner belongs
  to that owner (`palm,f:palm.body.1`).
- **`FILE` must be the document resolved in**: the same file, or the last segments of
  its path. The viewer copies a ref with the file's absolute path first.
- **A ref resolves now, never as it was.** An element a later build moved or resized
  resolves where it is; one it deleted fails, naming it; one whose making changed
  fails too, listing what its atom is named now (`plate.body.6 is now
  f:plate.body.6`), never guessing.
- **The canonical ref**, what `cadgen inspect` answers, spells an element by its name under its part's label where that label
  names one occurrence, else under the occurrence's path from the root.

### Element names

Every face, edge and vertex of a part is named by the feature history that made it,
so it keeps its name until that history changes. A name is its kind, the element it
starts from, and a step for each feature that made it out of another:
`f:plate.body.3;M:plate.relief` is face 3 of `plate`'s `body`, modified by `relief`.

- **A feature is the author's name for its result**: `relief = body - hole` in the
  function `plate` is `plate.relief`. The calls one statement made on the way are
  `<function>.<target>_<operation><n>` (`standoff.post_make_cylinder1`).
- **A reassignment is told apart by its statement's shape**: `body = body - bore`
  is `bracket.body_dnjoch`, the tag spelled from the statement with its numbers
  masked. So a dimension edit keeps every name, and inserting a statement of another
  shape renames none of the others.
- **A result assigned nowhere** (a model's return expression) is
  `<function>.<operation><n>` (`plate.fuse1`). A helper that builds a shape in its
  `return` statement hands it to its caller, whose assignment names it.
- **A label on a shape names what later features make of it** (`f:boss.3` in
  `plate + boss`), and a placed part keeps its prototype's names: its label names the
  occurrence.
- **An element read from a file** keeps the name the file gives it (a STEP or an
  `.xbf` this engine wrote names every face), else it is named by the read and its
  number within its part: `motor = read_step(...)` in `rig` names face 12 of a
  foreign STEP `f:rig.motor.12`.
- A long name folds to its last feature and a digest (`f:plate.relief~5d1e0c2f9a3b`):
  copy names whole.

Renaming a variable renames that feature's elements and nothing else; moving code
renames nothing.

### A picked element in a model's body

A ref resolves in the engine (`cadgen inspect`, the viewer), never in a model: a
body selects from the live shape it is building with build123d, by what the element
is. To change what a person picked, read the ref's answer and either edit the code
its `made_by` names or pick the same element in the body:

```bash
cadgen inspect XBF/bracket.xbf "plate/f:plate.body.6;M:plate.relief"   # centroid (0, 0, 6), a plane
```

```python
from cadgen.engine import xbf
import build123d as bd

from plate import plate


@xbf("XBF/bracket.xbf")
def bracket():
    base = plate()
    top = base.faces().sort_by(bd.Axis.Z)[-1]                 # the face the person picked: the top
    return base + bd.Pos(top.center(bd.CenterOf.MASS)) * bd.Cylinder(4, 10)
```

A model never reads a file its own build writes.

## Which code made it

`cadgen inspect DOC REF` answers what a ref names, placed where its occurrence stands:

- an element: its kind, number, name and canonical ref; the kernel's facts (a face's
  area, centroid, surface type, exact box, radius; an edge's length, curve type, class
  and endpoints; a vertex's position); and `made_by`: the feature that made it, its
  operation, and the function, file and line of the call;
- a part or an assembly: its volume, and its mass from each part's material density,
  with the parts that have no material listed under `unweighed`.

```text
$ cadgen inspect XBF/bracket.xbf "bracket/face 7"
{
  "ref": "bracket/f:bracket.bore.1;M:bracket.body_dnjoch",
  "kind": "face",
  "ordinal": 7,
  "area": 150.796...,
  "surface": "cylinder",
  "radius": 4.0,
  "made_by": {"feature": "bracket.body_dnjoch", "operation": "cut", "function": "bracket",
              "file": "/work/src/bracket.py", "line": 12},
  ...
}
```

The bore's wall is face 1 of the cylinder `bore`, modified by the cut whose result
`body` took. `made_by` comes from the worker that built the document, while it lives;
after the daemon restarts it is `null` until the model builds again. A ref of several
selectors answers a list; one that names nothing is refused (exit 2). Without a REF,
`cadgen inspect DOC` prints the document's build closure.

## Measurements

```python
# p, q: points; u, v, direction, normal: vectors
length = (q - p).length
signed_offset = (q - p).dot(direction.normalized())
angle_degrees = u.get_angle(v)
signed_angle_degrees = u.get_signed_angle(v, normal)
center = circular_edge.arc_center
radius = circular_edge.radius
size = shape.bounding_box().size
```

```python
from cadgen.geometry import closest_points, overlap_volume

result = closest_points(a, b)   # .distance, .point_a, .point_b
volume = overlap_volume(solid_a, solid_b)
```

`closest_points` returns the minimum distance and one witness pair; overlap,
contact and containment all give 0, so it does not measure penetration.
`overlap_volume` is the exact intersection volume (mm³) of two finite, positively
oriented solids, 0 for mere contact; pair bodies with a group's `solids()`, the
group found in `read_xbf`'s tree by its label. Kernel failures raise.

Two solids at `overlap_volume` 0 at most touch. A pair meant to overlap (a
press-fit pin, a bearing in its bore) is named as such, not inferred from where it
sits in the tree. A part meant to sit on, bolt to or bear against another measures
`closest_points` distance 0 against it; above 0 is a gap, and the part floats. Over
many parts, two `bounding_box(optimal=False)` envelopes that do not meet rule a pair
out, leaving the exact queries for the pairs that remain. Both measure the parts as
built: a part clear there can still pass through another in a pose
([reviewing motion](kinematics.md#reviewing-motion)).

## Geometry diagnostics

```python
from cadgen.geometry import is_sound, topology_errors, boundary_edges, self_intersections

ok = shape.is_valid                       # bool: BRepCheck passes (build123d)
clean = is_sound(shape)                   # bool: the boolean kernel's argument check passes, expensive
issues = topology_errors(shape)           # tuple[GeometryIssue, ...]
free = boundary_edges(shell)              # tuple[Edge, ...]
crossings = self_intersections(shape)     # tuple[GeometryIssue, ...], expensive
```

A `GeometryIssue` has `code` (an OCCT `BRepCheck_*` or `BOPAlgo_SelfIntersect`
status) and `entities`. An inconclusive check raises `GeometryError` rather than
passing; none of these repair geometry. `is_sound` is `BRepAlgoAPI_Check`'s verdict,
the gate a fuse or cut demands of an operand (`False` for a null or empty shape). A
reversed solid can pass topology checks with negative volume and aggregate volumes
can cancel, so signed volume is checked per solid; no free edges means closed, not
manifold.

A face the mesher could not triangulate is named wherever the document is drawn:
the snapshot warns with the part and the face, and the viewer leaves it out. It is
usually a sliver a boolean left.

## Mass properties

A part given a material (`part.material = materials.aluminium_6061`,
[materials](build123d-modeling.md#colour-and-materials)) is weighed by
`cadgen inspect DOC OWNER`. For explicit densities, a centre of mass or inertia:

```python
from cadgen.geometry import mass_properties

properties = mass_properties([
    (aluminum_body, 2.70e-6),  # kg/mm³; use the material density for the task
    (steel_insert, 7.85e-6),
])
print(properties.volume, properties.mass, properties.center_of_mass)
print(properties.inertia)
```

`mass_properties` integrates uniform density per solid and sums the bodies,
overlaps included. The 3×3 inertia is about the combined centre of mass in the
input axes; with mm and kg/mm³, outputs are mm³, kg, mm and kg·mm².
