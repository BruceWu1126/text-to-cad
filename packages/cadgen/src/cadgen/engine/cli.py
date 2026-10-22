"""``cadgen xbf``: build code, export documents, draw and serve them -- a thin argparse over
the functions of ``api``; and ``python SCRIPT.py``'s run (``run_script``).

    cadgen xbf build SOURCE | - [--name N]           one model from source text (stdin with -), its files
        [--xbf [F]] [--step [F]] [--glb [F]] [--stl [F]] [--threemf [F]] [--json] [--verify] [--no-daemon]
    python SCRIPT.py                                 every model of a script, as its decorators declare
    cadgen xbf export SRC DST [DST ...] [--pose NAME|JSON] [--part NAME] [--no-inputs]
    cadgen xbf snapshot SRC OUT.png [--display MODE|exploded=A|JSON] [--camera PRESET|az:el|JSON|authored] [--grid]
        [--preview] [--pose NAME|JSON] [--annotation FILE] [--measurements FILE] [--width N] [--height N]
        [--size-profile P] [--json]
    cadgen xbf serve DOC [--port N] [--three DIR] [--foreground]
    cadgen xbf status | stop | daemon
    cadgen where DOC [REF]                           a document's build closure, or what made one element

``where`` is the first verb at the top level of ``cadgen`` (``where_main``); the others move up
when the ``xbf`` level goes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

__all__ = ["build_parser", "elements", "main", "run_script", "where_main", "where_parser"]

FILE_FLAGS = ("xbf", "step", "glb", "stl", "threemf")
_SUFFIX = {"xbf": ".xbf", "step": ".step", "glb": ".glb", "stl": ".stl", "threemf": ".3mf"}
# Where a file flag with no path writes, in the working folder: where a decorator with none puts it.
_DEFAULT = {"xbf": "XBF/<name>.xbf", "step": "<name>.step", "glb": "<name>.glb", "stl": "<name>.stl",
            "threemf": "<name>.3mf"}


def build_parser(prog: str = "cadgen xbf") -> argparse.ArgumentParser:
    """The command's parser: its verbs and their options."""
    return _parser(prog)


def _parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Build, export, draw and serve cadgen engine documents.")
    verbs = parser.add_subparsers(dest="verb", required=True)
    build = verbs.add_parser(
        "build", help="build source text into the files its flags name (a script builds by running it)",
        usage="%(prog)s SOURCE [--name N] [--xbf [F]] [--step [F]] [--glb [F]] [--stl [F]] [--threemf [F]] [--json] "
              "[--verify] [--no-daemon]",
        description="Build source text: one model, which assigns its final shape to `result`. Code declares "
                    "nothing, so its file flags are its declarations, additive among themselves; a child model it "
                    "calls writes the files that model declares. A script builds by running it: python SCRIPT.py.")
    build.add_argument("source", metavar="SOURCE", help="the code (- reads it from stdin)")
    build.add_argument("--name", metavar="N",
                       help="the code's name: its model, its files' default names and its warm worker (default: model)")
    for kind in FILE_FLAGS:
        build.add_argument(f"--{kind}", nargs="?", const=True, metavar="F",
                           help=f"write the {kind} at F (default: {_DEFAULT[kind]} in the working folder)")
    build.add_argument("--json", action="store_true", help="print the report as JSON")
    build.add_argument("--verify", action="store_true",
                       help="run every function the engine would replay and compare its result with the logged one")
    build.add_argument("--no-daemon", action="store_true", help="build in this process (cold, no worker kept)")
    export = verbs.add_parser("export", help="write a document to STEP, GLB, STL, 3MF or xbf files, by suffix; a GLB "
                                             "carries the document's motions as clips")
    export.add_argument("source", metavar="SRC", help="an .xbf, a .step, or SCRIPT.py::MODEL (built first)")
    export.add_argument("destinations", nargs="+", metavar="DST",
                        help=".xbf (from a STEP, or with --part), .step, .glb, .stl or .3mf; or .cadz (from an "
                             "xbf: it, its build closure's source files and its inputs, zipped)")
    export.add_argument("--no-inputs", action="store_true", help="a .cadz without the closure's input files")
    export.add_argument("--pose", help="a configuration's name, or joint values as JSON ('{\"elbow\": 45}'): the "
                                       "mechanism posed there; as built without it")
    export.add_argument("--part", help="an occurrence's path or a node's name: that subtree as a document of its own")
    export.add_argument("--no-daemon", action="store_true", help="export in this process (cold, no worker kept)")
    serve = verbs.add_parser("serve", help="serve a document over HTTP and print its URL")
    serve.add_argument("document", help="an .xbf, or a STEP")
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--three", help="the three.js package folder (default: the nearest node_modules/three)")
    serve.add_argument("--foreground", action="store_true", help="serve from this process until interrupted")
    from cadgen.engine.snapshot import add_parser as add_snapshot

    add_snapshot(verbs)
    verbs.add_parser("status", help="the daemon's workers and served documents")
    verbs.add_parser("stop", help="stop the daemon")
    verbs.add_parser("daemon", help="run the daemon in this process")
    return parser


def _pose(value: str | None) -> Any:
    from cadgen.engine.snapshot import parse_pose

    return parse_pose(value)


def _files(report: dict[str, Any]) -> list[str]:
    found = []
    for path, row in (report.get("files") or {}).items():
        state = "written" if row.get("written") else "kept"
        found.append(f"{os.path.relpath(path)} ({state}, {row.get('bytes', 0) / 1e6:.1f} MB)")
    return found


def _describe(report: dict[str, Any], wall: float, fresh: bool | None) -> str:
    # Each child model the run ran, and the files it wrote (``child_models``).
    children = "".join(f"\n  {name}, a child{'' if row.get('ok') else ': failed'}"
                       + "".join(f"\n    {text}" for text in _files(row))
                       for name, row in (report.get("child_models") or {}).items())
    if not report.get("ok"):
        return f"failed {report.get('model')}" + children
    timings = report.get("timings", {})
    memo = report.get("memo", {})
    bodies = list((report.get("bodies") or {}).values())
    files = _files(report)
    worker = "" if fresh is None else (" cold" if fresh else " warm")
    if memo.get("bodies_loaded"):
        line = (f"took {report['model']}{worker} in {wall:.2f}s: its file holds the model's key, "
                f"{memo.get('bodies_loaded', 0)} bodies loaded, nothing ran")
    else:
        counted = {status: bodies.count(status) for status in ("run", "replayed") if bodies.count(status)}
        shown = ", ".join(f"{count} {status}" for status, count in counted.items()) or "none"
        stages = ", ".join(f"{name} {timings[name]:.2f}s" for name in ("body", "names", "document", "xbf", "step",
                                                                        "glb", "stl", "threemf")
                           if timings.get(name))
        line = (f"built {report['model']}{worker} in {wall:.2f}s: bodies {shown}; {memo.get('misses', 0)} misses, "
                f"{memo.get('hits', 0)} hits" + (f"; {stages}" if stages else ""))
        if (report.get("cache") or {}).get("changed"):
            line += f"\n  rebuilt: {report['cache']['reason']}"
    if report.get("retired"):
        files.append(f"retired: {report['retired']}")
    return (line + "".join(f"\n  {text}" for text in files) + children
            + "".join(f"\n  warning: kinematics: {text}" for text in report.get("kinematics", {}).get("warnings", ()))
            + _verified(report.get("verify")))


def _verified(found: dict[str, Any] | None) -> str:
    if found is None:
        return ""
    lines = [f"\n  verify: {len(found['checked'])} replayable bodies run again, {len(found['mismatches'])} differ"]
    lines += [f"\n    {name}: {found['checked'][name]}; it now runs on every build" for name in found["mismatches"]]
    return "".join(lines)


def _file_flags(args: argparse.Namespace) -> dict[str, Any]:
    """The file flags, each True (its default path) or a path ending in its kind's suffix."""
    flags = {}
    for kind in FILE_FLAGS:
        value = getattr(args, kind)
        if isinstance(value, str) and Path(value).suffix.lower() != _SUFFIX[kind] and not (
                kind == "step" and Path(value).suffix.lower() == ".stp"):
            raise SystemExit(f"cadgen xbf build: --{kind} {value}: the file must end in {_SUFFIX[kind]}")
        flags[kind] = value
    return flags


def _reported(reports: list[dict[str, Any]], wall: float, fresh: bool | None, as_json: bool) -> int:
    """Each report on stdout, described or as JSON, a failed one's error on stderr; 1 if any failed."""
    failed = False
    for report in reports:
        if as_json:
            print(json.dumps({**report, "wall": round(wall, 4)}))
        else:
            print(_describe(report, wall, fresh))
        if not report.get("ok"):
            failed = True
            print(report.get("error", ""), file=sys.stderr)
    return 1 if failed else 0


def _log(text: str) -> None:
    print(text, file=sys.stderr)


def _build(args: argparse.Namespace) -> int:
    """``cadgen xbf build SOURCE``: code, its flags its declarations (law 12)."""
    from cadgen.engine import api

    flags = _file_flags(args)
    code = sys.stdin.read() if args.source == "-" else args.source
    try:
        built = api.build(code, name=args.name, **flags, verify=args.verify, log=_log, daemon=not args.no_daemon)
    except api.EngineError as error:
        print(f"cadgen xbf build: {error}", file=sys.stderr)
        return 1
    return _reported([built.report], built.report.get("wall", 0.0), None, args.json)


def run_script(script: Path) -> int:
    """``python SCRIPT.py``: every model the script defines, built through the daemon as its
    decorators declare -- the script takes no options (law 11) -- each model's report on stdout
    and the build's log on stderr; the build's status."""
    from cadgen.engine import api

    try:
        found = api.build_script(Path(script), log=_log)
    except api.EngineError as error:
        print(f"python {Path(script).name}: {error}", file=sys.stderr)
        return 1
    return _reported(found.models, found.wall, found.fresh, False)


def _export(args: argparse.Namespace) -> int:
    from cadgen.engine import api
    from cadgen.engine.export import ExportRefused

    try:
        written = api.export(args.source, *args.destinations, pose=_pose(args.pose), part=args.part,
                             inputs=not args.no_inputs, log=lambda text: print(text, file=sys.stderr),
                             daemon=not args.no_daemon)
    except (ExportRefused, ValueError) as error:
        print(f"cadgen xbf export: {error}", file=sys.stderr)
        return 2
    except api.EngineError as error:
        print(f"cadgen xbf export: {error}", file=sys.stderr)
        return 1
    for row in written:
        print(f"wrote {os.path.relpath(row.path)} ({row.bytes / 1e6:.1f} MB) in {row.seconds:.2f}s")
    return 0


def _serve(args: argparse.Namespace) -> int:
    path = Path(args.document).resolve()
    if not path.is_file():
        print(f"cadgen xbf serve: no document {args.document}", file=sys.stderr)
        return 2
    three = args.three
    if three is None:
        from cadgen.engine.serve import find_three

        found = find_three()
        three = str(found) if found else None
    if args.foreground:
        from cadgen.engine.serve import Server

        server = Server(port=args.port, three=three)
        ident = server.add(path)
        print(f"{server.url}/?doc={ident}", flush=True)
        try:
            server.http.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    from cadgen.engine import api

    try:
        print(api.serve(path, args.port, three).url)
    except api.EngineError as error:
        print(f"cadgen xbf serve: {error}", file=sys.stderr)
        return 1
    return 0


_REF = re.compile(r"(?P<part>.+?)/(?P<kind>face|edge|vertex)[ /]?(?P<ordinal>[1-9][0-9]*)")
_LETTERS = {"f": "face", "e": "edge", "v": "vertex"}


def elements(structure: dict[str, Any], ref: str) -> list[tuple[dict[str, Any], str, int]]:
    """The elements ``ref`` names in a document's structure: ``(prototype, kind, ordinal)``
    for ``PART/face N`` -- PART a prototype's name, else an instance's path -- or for every
    element bearing an element name."""
    prototypes = structure["prototypes"]
    found = _REF.fullmatch(ref.strip())
    if found is not None:
        part, kind, ordinal = found["part"], found["kind"], int(found["ordinal"])
        named = [p for p in prototypes if p["name"] == part]
        if not named:
            named = [prototypes[i["prototype"]] for i in structure["instances"] if i["path"] == part][:1]
        if not named and part == structure["name"] and len(prototypes) == 1:
            named = prototypes  # a document of one part: the model's own name
        return [(prototype, kind, ordinal) for prototype in named]
    from cadgen.engine.kernel.trace import ELEMENT_NAME

    if not ELEMENT_NAME.fullmatch(ref):
        raise ValueError(f"{ref!r} is neither PART/face N (edge, vertex) nor an element name")
    kind = _LETTERS[ref[0]]
    return [(prototype, kind, int(ordinal)) for prototype in prototypes
            for ordinal, text in prototype.get(f"{kind}Names", {}).items() if text == ref]


def placements(structure: dict[str, Any], ref: str) -> list[dict[str, Any]]:
    """The instances ``ref`` names as a part or an assembly: those at the path it names or
    below it (``kit/arm``), those of the prototype of that name, or every one for the
    document's own name; none for an element's ref."""
    ref = ref.strip().strip("/")
    instances = structure["instances"]
    if ref == structure["name"]:
        return list(instances)
    found = [i for i in instances if i["path"] == ref or i["path"].startswith(ref + "/")]
    if not found:
        named = {at for at, p in enumerate(structure["prototypes"]) if p["name"] == ref}
        found = [i for i in instances if i["prototype"] in named]
    if not found:  # a path below the root, given without the root's name
        found = [i for i in instances if i["path"].partition("/")[2] == ref
                 or i["path"].partition("/")[2].startswith(ref + "/")]
    return found


def weighed(structure: dict[str, Any], held: list[dict[str, Any]], volumes: dict[str, float],
            ref: str) -> dict[str, Any]:
    """What ``held`` (``placements``) weighs: each instance's volume (its prototype's, mm^3, by
    key in ``volumes``) times its material's density, in kg, summed; ``mass`` None when no part
    of it has a material with a density, and the parts that have none listed by path."""
    prototypes = structure["prototypes"]
    total_volume = 0.0
    mass = 0.0
    weighed_any = False
    unweighed = []
    for instance in held:
        prototype = prototypes[instance["prototype"]]
        volume = volumes[prototype["key"]]
        total_volume += volume
        density = (prototype.get("material") or {}).get("density")
        if density is None:
            unweighed.append(instance["path"])
            continue
        mass += volume * 1e-9 * density
        weighed_any = True
    found: dict[str, Any] = {"ref": ref, "parts": len(held), "volume": total_volume,
                             "mass": mass if weighed_any else None, "unweighed": unweighed}
    if len(held) == 1:
        material = prototypes[held[0]["prototype"]].get("material")
        found.update(part=prototypes[held[0]["prototype"]]["name"], path=held[0]["path"],
                     material=material["name"] if material else None,
                     density=material.get("density") if material else None)
    return found


def where_parser(prog: str = "cadgen where") -> argparse.ArgumentParser:
    """``cadgen where``'s parser: a document, and an element of it or none."""
    parser = argparse.ArgumentParser(prog=prog, description="A document's build closure (its source, modules, inputs "
                                                            "and key), or what made one element of it.")
    parser.add_argument("document", help="an .xbf")
    parser.add_argument("ref", nargs="?", help="PART/face N (or edge, vertex), PART a part's or an instance's name (or "
                                               "the document's, for a document of one part); an element name "
                                               "(f:..., e:..., v:...); or a part or an assembly (its name or path): "
                                               "its volume and mass. Without it: the build closure")
    return parser


def where_main(argv: list[str] | None = None, prog: str = "cadgen where") -> int:
    """``cadgen where DOC``: the build closure the xbf holds; ``cadgen where DOC REF``: what the
    kernel measures of the elements a ref names, and what made each (``made_by``, null for a
    document no live worker built)."""
    from cadgen.engine import api

    args = where_parser(prog).parse_args(argv)
    path = Path(args.document).resolve()
    if not path.is_file():
        print(f"{prog}: no document {args.document}", file=sys.stderr)
        return 2
    try:
        found = api.where(path, args.ref)
    except ValueError as error:
        print(f"{prog}: {error}", file=sys.stderr)
        return 2
    except api.EngineError as error:
        print(f"{prog}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(found, indent=2))
    return 0


def main(argv: list[str] | None = None, prog: str = "cadgen xbf") -> int:
    args = _parser(prog).parse_args(argv)
    if args.verb == "build":
        return _build(args)
    if args.verb == "export":
        return _export(args)
    if args.verb == "serve":
        return _serve(args)
    if args.verb == "snapshot":
        from cadgen.engine.snapshot import run as snapshot

        return snapshot(args)
    if args.verb == "daemon":
        from cadgen.engine.daemon import main as run

        return run([])
    from cadgen.engine.client import request

    answer = request({"op": args.verb}, start=False)
    if answer is None:
        print("no engine daemon is running")
        return 0
    if args.verb == "stop":
        from cadgen.engine.client import wait_gone

        # Stopped means gone: the next command starts a fresh daemon, never reaches this one.
        if not wait_gone():
            print("cadgen xbf stop: the engine daemon did not exit", file=sys.stderr)
            return 1
    print(json.dumps(answer, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
