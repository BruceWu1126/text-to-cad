# Kernel plugins

Each folder in `kernels/` is one kernel: a `kernel.json` and the runner its
`command` names. `scripts/kernels.py` finds them, so adding a folder adds a
kernel to `list`, `run`, `check` and the CAD Viewer's Kernel menu (which reads
the files a run writes, not the plugins).

## kernel.json

Every field is checked; an unknown field is an error.

| Field | Meaning |
| --- | --- |
| `id` | Lowercase letters, digits and dashes. Must equal the folder name. |
| `label`, `detail` | Names shown by `list`. |
| `exact` | `true` for exact B-rep kernels. |
| `input` | `"model"`: the runner gets the model script and its function, and runs the program itself. `"program"`: it gets the model recorded as a CSG tree ([program.md](program.md)). |
| `output` | `"step"` or `"stl"`: the one file the runner writes. |
| `command` | The command to run, as an array of strings (placeholders below). |
| `setup` | What to install, shown when the command can't run. |
| `tolerance` | Allowed relative volume error against OpenCASCADE in `check` (default `0.01`). |

## The command

`kernels.py` runs `command` from the working directory. Paths are passed
relative to it when they're under it, so a runner inside Docker (which shares
only the working directory) sees the same files.

| Placeholder | Value |
| --- | --- |
| `{model}` | the model script |
| `{function}` | the model's function name |
| `{program}` | the recorded program, as JSON |
| `{step}`, `{stl}` | where to write the output |
| `{here}` | this plugin's folder |
| `{runtime}` | this kernel's runtime folder, `$CAD_KERNELS_HOME/<id>` (default `~/.cache/text-to-cad/kernels/<id>`) |
| `{python}` | the Python running `kernels.py` |
| `{node}` | `node` on PATH |
| `{env:NAME\|fallback}` | an environment variable, else the fallback, in which `<here>` is this folder and `<runtime>` its runtime folder |

A runner that needs its own environment keeps it in its runtime folder, never
in the skill (which may be installed read-only): `sdm` has a Python 3.13 venv
at `{runtime}/.venv` and `three` its packages at `{runtime}/node_modules`, each
made by its `setup.sh`. A Node runner whose packages live there registers
`kernels/_shared/resolveFrom.mjs` (see `three/runner.mjs`) so bare imports
resolve from it.

## The runner

Write the output file, then print one JSON line last:

```json
{"ok": true, "volume": 1206.37, "valid": true, "warnings": ["1.5 mm fillet drawn sharp"]}
{"ok": false, "error": "what went wrong"}
```

`volume` is in mm³. Put every approximation in `warnings`: they are printed
with the result. Report a failure as `{"ok": false}` rather than writing a
plausible-wrong file; `kernels.py` deletes any partial output.

- **Model runners** load the script with `kernels/_shared/cadgen_stub.py`
  (stdlib only): `install(build123d_module)` makes `cadgen` and `build123d`
  resolve to the kernel's build123d layer, and `load(script, function)`
  returns the model to call. `usd-brep/runner.py` is the example.
- **JavaScript program runners** pass a `build(program)` to
  `runMeshKernel` from `kernels/_shared/meshRunner.mjs`, which reads the
  program, writes the binary STL and prints the result. `_shared/program.mjs`
  has helpers (bounds, sketch outlines, revolve frames, lowering simple
  fillets into profile corners) and `_shared/meshData.mjs` builds and measures
  meshes.

## Trying it

`python scripts/kernels.py check <id>` copies the eight fixture models in
`fixtures/` to a scratch folder, builds each on the kernel, and prints its
volume against OpenCASCADE's (`fixtures/reference.json`). A kernel whose
runtime is missing is skipped with its `setup` note.
