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
| `mesh.py` | per-part tessellation at a deflection relative to the part's size, and its wire format |
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

## Wire format

`GET /api/doc/<id>` returns the structure as JSON: prototypes (key, name,
colour, `faceNames`, `edgeNames`, `faceColors` by ordinal), instances
(prototype, path, a 4x4 world matrix, colour), the tree, and the document's
version. The page polls the version, and the server reads the file again when
its mtime changes.

`GET /api/doc/<id>/part/<key>` returns one part's buffer. A client reads it
with typed-array views, without parsing. The 64-byte little-endian header is:

| Bytes | Field |
| --- | --- |
| 0-3 | magic `XBM1` |
| 4-7 | format version (1) |
| 8-27 | counts: vertices, indices, faces, edges, edge points (u32 each) |
| 28-51 | byte offsets of the six arrays below (u32 each) |
| 52-59 | linear and angular deflection (f32 each) |
| 60-63 | reserved |

Then the arrays, each 4-byte aligned: positions `f32[V*3]`, normals `f32[V*3]`,
triangle indices `u32[I]`, the face table `u32[F*3]` (face ordinal, first
index, index count), the edge table `u32[E*3]` (edge ordinal, first point,
point count) and the edge polylines `f32[P*3]`. Ordinals are the part's
`TopExp.MapShapes` ordinals, the same ones its element names use. Selecting a
face reads a range of the index buffer, and selecting an edge reads a range of
the polyline buffer.

`POST /api/doc/<id>/mesh?mode=inline|processes` meshes every part not held
yet, in parallel, and returns the time it took.
