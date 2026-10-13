---
name: kernels
description: Build a cadgen model on alternate CAD kernels (NVIDIA usd-brep, Emergent Matter SDM, signed distance fields, three.js mesh CSG) to compare them with the default build123d/OpenCASCADE result, and add or try new kernels as plugins. Use when the user asks to rebuild a model on another kernel, compare kernels, or add, test or swap a modeling kernel.
license: MIT
---

# Alternate CAD kernels

Provenance: maintained in [earthtojake/text-to-cad](https://github.com/earthtojake/text-to-cad).
Use the installed local skill files for the current interface.

A model's own build (`python model.py`, the `$cad` skill) runs build123d on
OpenCASCADE. This skill runs the same model program on another kernel and
writes what it builds as an ordinary generated file beside the model's output:

```
STEP/bracket.step                      # python src/bracket.py (OpenCASCADE)
STEP/.kernels/usd-brep/bracket.step    # the same model on NVIDIA usd-brep
STEP/.kernels/sdm/bracket.stl          # ... on Emergent Matter SDM
```

The CAD Viewer (`cadgen viewer`) lists these next to the model's own file and
switches between them from its **Kernel** menu. A kernel's STEP opens like any
STEP (faces, edges and refs work); a kernel's STL is a mesh.

## Setup and paths

Run the skill with cadgen's Python, so a kernel runs a model with the same
build123d the model's own build uses:

- `cadgen` below means `uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.21 cadgen`
- `python` below means `uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.21 python`

A kernel's own runtime (a venv, Node packages) is installed once by its
`setup.sh` into `~/.cache/text-to-cad/kernels/<kernel>` (or
`$CAD_KERNELS_HOME/<kernel>`), never into the skill.

## Commands

Run from the CAD project root:

```bash
python <skill>/scripts/kernels.py list
python <skill>/scripts/kernels.py run usd-brep src/bracket.py
python <skill>/scripts/kernels.py run sdm src/parts.py::bracket
python <skill>/scripts/kernels.py check            # every kernel on the fixtures
python <skill>/scripts/kernels.py check sdm        # one kernel
```

`run` prints the file it wrote, the kernel's volume and its notes (`--json`
for the report). Rerun it after editing the model: a kernel's file is not
rebuilt by `python model.py`. A failed run leaves no file.

## Kernels

| id | Kernel | Takes | Writes | Setup |
| --- | --- | --- | --- | --- |
| `usd-brep` | [NVIDIA usd-brep](https://github.com/NVIDIA-Omniverse/usd-brep) through [keel](https://github.com/amywork777/keel)'s build123d layer | the model | exact STEP | keel on PATH or `CAD_KEEL_BIN` |
| `sdm` | [Emergent Matter SDM](https://github.com/EmergentMatter/emergent-matter-sdm), JAX signed distance fields | the recorded program | STL | `kernels/sdm/setup.sh` or `CAD_SDM_PYTHON` |
| `implicit` | signed distance field, surface nets | the recorded program | STL | Node.js 20+ |
| `three` | three.js primitives with `three-bvh-csg` | the recorded program | STL | Node.js 20+, `kernels/three/setup.sh` |

`list` shows them with their setup notes. A kernel whose runtime is missing
fails with that note.

- **The recorded program.** Kernels that can't run build123d get the model
  recorded as a CSG tree ([references/program.md](references/program.md)). The
  recorder covers the common builder and algebra subset; a model using
  anything else fails with the name of what's missing, never a guess.
- **Edges.** Fillets and chamfers on edges parallel to an axis become rounded
  or cut sketch corners in `implicit` and `three`. SDM has no edge fillets, so
  it draws them sharp. Every approximation is listed in the run's notes:
  report them with the result.
- **Comparing.** Compare kernels by volume and bounds within a tolerance,
  never by file bytes. `check` prints each kernel's volume error on eight
  fixture parts against OpenCASCADE's. A kernel that can't run fails the
  check: set it up, or name only the kernels you have.

## Add or try a kernel

A kernel is one folder in `kernels/`: a `kernel.json` and a runner in any
language. Copy `kernels/_template-python/` or `kernels/_template-node/` to
`kernels/<id>/`, set `"id"` to the folder name, replace the template's build,
and run `check <id>`. [references/plugins.md](references/plugins.md) is the
contract. Folders starting with `_` are not kernels.
