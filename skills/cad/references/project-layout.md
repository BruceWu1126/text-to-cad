# CAD project structure

cadgen does not infer paths from folder names: each model declares its files, so
any layout works, and an existing project's layout is the one to extend. For a new
project of several models the structure below is a good default; a one-off part can
use a flat folder, and [minimal starters](project-template.md) show a part and a
small assembly. In a larger workspace, CAD work usually lives in its existing
`models/`, `cad/` or `hardware/` area.

## Source and output mapping

A script's stem carried into its files (`src/bracket.py` declaring
`XBF/bracket.xbf`, `STEP/bracket.step` or `STL/bracket.stl`) keeps each file
traceable to its source. Stacking decorators declares one model's files.

```
<project>/
  src/
    plate.py              # one model per script
    plate_drawing.py      # a drawing ($dxf), or a sheet ($engineering-drawing)
    assembly.py           # the root assembly
    chassis/              # optional grouping as the project grows
      __init__.py
      frame.py
      strut.py
    lib/                  # shared helpers and factories: plain modules
      __init__.py
      holes.py
  XBF/                    # the documents the models write
    plate.xbf
    assembly.xbf
    chassis/
      frame.xbf
  vendor/                 # vendor STEP files, each with the xbf converted from it
    motor.step
    motor.xbf
  STEP/  STL/  GLB/  3MF/  DXF/   # the exports the models declare
  tmp/                    # ignored scratch: checks, snapshots
```

Mirroring nested source paths in output folders keeps repeated names unambiguous:
`src/chassis/frame.py` → `XBF/chassis/frame.xbf`. Decorator `out=` paths are
relative to the script, so `src/chassis/frame.py` declares
`out="../../XBF/chassis/frame.xbf"`.

## Imports and shared code

Models use ordinary Python imports. A script's folder is first on the module search
path, so flat scripts import their siblings and `lib/` directly. A script inside a
package (`src/chassis/` holds an `__init__.py`) is that package's module: its
relative imports work, and the folder above its topmost package is on the path too,
so `src/chassis/frame.py` imports `lib.holes` as well. Both run alike:

```bash
python src/chassis/frame.py
cd src && python -m chassis.frame
```

Without packages, `PYTHONPATH` names the import root (`PYTHONPATH=src python
src/chassis/frame.py`); cadgen adds no paths of its own. A module and a same-named
model function shadow each other on import; an alias separates them.

What a build re-runs follows what the code reaches: an edit to a `lib/` helper
re-runs the bodies that call it and leaves the rest replaying, a constant edit
re-runs the bodies that read it, and a comment re-runs nothing
([what a build runs](model-contract.md#what-a-build-runs)).

## Assemblies and model boundaries

A parent calls its child models inside its body, and each child builds in the same
run, writing its own files: **running the root is the whole build**
(`python src/assembly.py`). Running a child alone builds the child and leaves the
assemblies that use it as they were.

A child model has its own document and files; a helper runs within its caller and
writes nothing. A sub-assembly needs no folder of its own. Mirrored geometry can stay
inline or become separate left and right models. A print-only part can declare
`@stl` or `@threemf` without `@xbf` and still compose into an assembly.

A big assembly stays fast to edit without splitting: the worker replays what an
edit does not reach. Mark a whole stage `@memo` only where it holds seconds of kernel
work ([memo functions](model-contract.md#memo-functions)). Separate root scripts
build at once, each in its own worker, so subsystem scripts let several agents
build and check their parts before the root incorporates them.

## Naming

- A model a parent imports needs an importable Python identifier as its stem; an
  external part number that is not one can go in a label.
- A drawing is a separate model: `plate_drawing.py` → `DXF/plate_drawing.dxf`.
- Filenames distinguished only by case collide on case-insensitive filesystems.
- Vendor files are inputs, not outputs, and need no script.
- Changing `out=`, renaming a script or deleting a model leaves its previous files
  where they were. Removing those exact files keeps what shares their folders.

## Version control

Every file a model writes is reproducible from its source, the same bytes on any
machine, so a new project can ignore them and keep the code and its inputs. The
`.xbf` is also the model's cache across daemon restarts, so keeping it locally saves
a cold build; a pinned deliverable is the exception to ignoring exports.

Example ignore patterns for that default:

```gitignore
/XBF/
/STEP/
/STL/
/GLB/
/3MF/
/DXF/
/tmp/
__pycache__/
```

Vendor STEP files are irreplaceable inputs: commit them. The xbf converted from one
is made again by one `cadgen export`. A file that starts with
`version https://git-lfs` is a Git LFS pointer, not CAD data: `git lfs checkout`
restores objects already in the local cache, and missing ones need fetching from
the project's LFS remote.
