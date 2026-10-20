# cadgen.engine

A model is a parameterless function that returns a build123d shape, decorated
with `@xbf`. The engine runs it in a warm worker, saves the result as an XCAF
document (`.xbf`, BinXCAF), and writes the exports the model declares from that
document.

```python
import build123d as bd
from cadgen.engine import xbf, step

@xbf                      # arm.xbf beside the script; @xbf(out="path.xbf") to choose
@step(out="arm.step")     # also @glb, @stl, @threemf; stacked or alone
def arm():
    return bd.Box(10, 20, 30)
```

## Laws

1. **Nothing to disk.** The engine writes the files a model declares (the xbf,
   its `.xbf.json` sidecar, the exports its decorators name) and the operating
   system's coordination for its warm process: a socket and a lock in
   `tempfile.gettempdir()/cadgen-engine`. It writes no cache, index, memo, mesh
   or parsed document. A restart is cold.
2. **Standalone.** `cadgen.engine` imports nothing of cadgen outside itself:
   OCP, build123d, numpy and the standard library only. A test holds it.
3. **The file is the document.** The xbf is the native document: units in
   millimetres, prototypes and instances with placements, a name on every part
   and instance, colours per part and per painted face, and element names on
   face and edge sub-shape labels. STEP, GLB, STL and 3MF are translations of it.
   `cadgen xbf export` writes any of them from the xbf alone.
4. **The memo is the only cache, and it lives in the worker.** Every kernel
   operation a model runs is keyed by its inputs. A hit hands back the objects
   the first run made, and a miss makes everything downstream of it miss. So an
   edit costs what it changed, and an element keeps the name its feature history
   gave it across edits. Tessellations live in the serving process's memory,
   keyed by a part's geometry, under a size cap.

## Layout

| Module | What it is |
| --- | --- |
| `model.py` | the decorators; identity `script.py::function`; a child model called in a body is a sub-assembly built in the same run |
| `build.py` | one build: load the script afresh, one memo run, document, element names, files |
| `document.py` | result to XCAF (prototypes, instances, names, colours, element names), atomic BinXCAF save, read-back of an xbf or a foreign STEP |
| `exports.py` | STEP through the XCAF writer; binary STL, GLB and 3MF from the engine's tessellation |
| `mesh.py` | per-part tessellation at FreeCAD's default tolerance, edge classes, element facts, and the wire format |
| `serve.py`, `page/` | HTTP: structure JSON and one binary buffer per part; a minimal three.js page |
| `daemon.py`, `worker.py`, `client.py` | one daemon per installation, one warm worker per model script, connect-or-start |
| `cli.py` | `cadgen xbf ...` (also `python -m cadgen.engine`) |
| `kernel/` | the memo (`memo.py`), maker history (`history.py`), element naming (`trace.py`), deterministic kernel settings (`determinism.py`) |

## CLI

```
cadgen xbf build SCRIPT[::FUNCTION] ... [--json]   build in the script's warm worker
cadgen xbf export DOC [--step F] [--glb F] [--stl F] [--threemf F]
cadgen xbf serve DOC [--port N] [--three DIR] [--foreground]
cadgen xbf status | stop | daemon
```

`SCRIPT` alone builds every decorated function in it. The build returns once
every file is on disk. If the document is unchanged, the xbf is not rewritten.
If a body raises, the error is reported with the model's frames and the
previous files stay. The daemon starts on demand and exits after an idle hour
(`CADGEN_ENGINE_IDLE` seconds). `CADGEN_ENGINE_DIR` moves its socket and lock.
`CADGEN_ENGINE_MEMO=0` turns memo hits off. `CADGEN_ENGINE_MESH=processes|inline`
picks how parts are meshed. The default, `processes`, uses a warm pool of
processes started with the server, each handed one part's shape as bytes.
`inline` meshes one part at a time over the kernel's own threads. Python
threads add nothing, because the kernel's mesher holds the interpreter's lock.

## Mesh

One tolerance, FreeCAD's default, configurable nowhere: a linear deflection of
`(Lx + Ly + Lz) / 300 * 0.5` of the part's own bounding box (FreeCAD's
Deviation of 0.5 %) and an angular deflection of 28.5 degrees, both absolute,
with the kernel's face parallelism on, through `IMeshTools_Parameters` with
`EnableControlSurfaceDeflectionAllSurfaces` set (analytic faces held to the
deflection; every other knob stays at its default). `BRepMesh_IncrementalMesh`
is used as it is: no check before it, no second pass, no finer retry, no other
tessellator. A face it leaves without triangles is left out and named: the
part's structure lists it under `unmeshed` (ordinals) with a verdict under
`unmeshedCheck` -- the mesher's own status first (`GetStatusFlags` of the
part's mesh), then `BRepCheck_Analyzer`'s on that face ("BRepMesh:
SelfIntersectingWire; BRepCheck: valid") -- and the rest of the part is drawn.
A part is meshed from its binary shape bytes read into a fresh shape, so its
mesh is a function of those bytes alone.

Every edge has a class read from the B-rep: 1 feature (two faces at an angle),
2 tangent (two faces meeting at least G1), 3 seam (a face closing on itself),
5 boundary (one face), 6 non-manifold (more than two). Degenerate edges are left
out.

What the kernel measures of an element is asked for, never extracted ahead:
`GET /api/doc/<id>/part/<key>/face/<ordinal>` is its area, centroid, surface
type, exact bounding box and `BRepCheck` verdict; `.../edge/<ordinal>` its
length, curve type, class, faces and endpoints; `.../vertex/<ordinal>` its
position.

## Wire format

`GET /api/doc/<id>` returns the structure as JSON: prototypes (key, name,
colour, `faceNames`, `edgeNames`, `faceColors` by ordinal, and once meshed
`unmeshed`/`unmeshedCheck`), instances (prototype, path, a 4x4 world matrix,
colour), the tree, and the document's version. The page polls the version, and
the server reads the file again when its mtime changes.

`GET /api/doc/<id>/part/<key>` returns one part's buffer. A client reads it
with typed-array views, without parsing. The header grew from 64 to 80 bytes in
`XBM2` (the vertex table's count and offset did not fit). The 80-byte
little-endian header is:

| Bytes | Field |
| --- | --- |
| 0-3 | magic `XBM2` |
| 4-7 | format version (2) |
| 8-31 | counts: nodes, indices, faces, edges, edge points, vertices (u32 each) |
| 32-59 | byte offsets of the seven arrays below (u32 each) |
| 60-67 | linear and angular deflection (f32 each) |
| 68-79 | reserved |

Then the arrays, each 4-byte aligned: positions `f32[N*3]`, surface normals
`f32[N*3]`, triangle indices `u32[I]`, the face table `u32[F*3]` (face ordinal,
first index, index count; an unmeshed face's count is 0), the edge table
`u32[E*4]` (edge ordinal, first point, point count, class), the edge polylines
`f32[P*3]`, and the vertex table `f32[X*4]` (vertex ordinal, x, y, z) of every
vertex of the part. Ordinals are the part's `TopExp.MapShapes` ordinals, the
same ones its element names use. Selecting a face reads a range of the index
buffer, an edge a range of the polyline buffer, a vertex a row of the vertex
table.

`POST /api/doc/<id>/mesh?mode=inline|processes[&drop=1]` meshes every part not
held yet (with `drop=1`, every part again), in parallel, and returns the time it
took, the bytes, the triangles and the unmeshed faces.
