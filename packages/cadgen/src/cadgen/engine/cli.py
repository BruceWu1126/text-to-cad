"""``cadgen xbf``: build code, export documents, draw and serve them -- a thin argparse over
the functions of ``api``; and ``python SCRIPT.py``'s run (``run_script``).

    cadgen xbf build SOURCE | - [--name N]           one model from source text (stdin with -), its files
        [--xbf [F]] [--step [F]] [--glb [F]] [--stl [F]] [--threemf [F]] [--dxf [F]] [--json] [--verify]
        [--no-daemon]
    python SCRIPT.py                                 every model of a script, as its decorators declare
    cadgen xbf export SRC DST [DST ...] [--pose NAME|JSON] [--part NAME] [--no-inputs]
    cadgen xbf snapshot SRC OUT.png [--display MODE|exploded=A|JSON] [--camera PRESET|az:el|JSON|authored] [--grid]
        [--preview] [--pose NAME|JSON] [--annotation FILE] [--measurements FILE] [--width N] [--height N]
        [--size-profile P] [--json]
    cadgen xbf serve DOC [--port N] [--three DIR] [--foreground]
    cadgen xbf status [--json] | stop | daemon
    cadgen where DOC [REF]                           a document's build closure, or what a ref names (``refs``)

``where`` is the first verb at the top level of ``cadgen`` (``where_main``); the others move up
when the ``xbf`` level goes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

__all__ = ["build_parser", "main", "run_script", "weighed", "where_main", "where_parser"]

FILE_FLAGS = ("xbf", "step", "glb", "stl", "threemf", "dxf")
_SUFFIX = {"xbf": ".xbf", "step": ".step", "glb": ".glb", "stl": ".stl", "threemf": ".3mf", "dxf": ".dxf"}
# Where a file flag with no path writes, in the working folder: where a decorator with none puts it.
_DEFAULT = {"xbf": "XBF/<name>.xbf", "step": "<name>.step", "glb": "<name>.glb", "stl": "<name>.stl",
            "threemf": "<name>.3mf", "dxf": "<name>.dxf"}


def build_parser(prog: str = "cadgen xbf") -> argparse.ArgumentParser:
    """The command's parser: its verbs and their options."""
    return _parser(prog)


def _parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Build, export, draw and serve cadgen engine documents.")
    verbs = parser.add_subparsers(dest="verb", required=True)
    build = verbs.add_parser(
        "build", help="build source text into the files its flags name (a script builds by running it)",
        usage="%(prog)s SOURCE [--name N] [--xbf [F]] [--step [F]] [--glb [F]] [--stl [F]] [--threemf [F]] [--dxf [F]] "
              "[--json] [--verify] [--no-daemon]",
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
    export = verbs.add_parser("export", help="write a document to STEP, GLB, STL, 3MF, DXF or xbf files, by suffix; a "
                                             "GLB carries the document's motions as clips, a DXF draws a planar one")
    export.add_argument("source", metavar="SRC", help="an .xbf, a .step, or SCRIPT.py::MODEL (built first)")
    export.add_argument("destinations", nargs="+", metavar="DST",
                        help=".xbf (from a STEP, or with --part), .step, .glb, .stl, .3mf or .dxf (of a planar "
                             "document); or .cadz (from an xbf: it, its build closure's source files and its inputs, "
                             "zipped)")
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
    status = verbs.add_parser("status", help="the daemon's workers, what each is building, and its served documents")
    status.add_argument("--json", action="store_true", help="the daemon's answer as JSON")
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
                                                                        "glb", "stl", "threemf", "dxf")
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


def weighed(structure: dict[str, Any], hit: Any, volumes: dict[str, float]) -> dict[str, Any]:
    """What the occurrence a ref names (``refs.Hit``, from ``refs.resolve_structure``) weighs: each
    part below it, its instance's volume (its prototype's, mm^3, by key in ``volumes``) times its
    material's density, in kg, summed; ``mass`` None when no part of it has a material with a
    density, and the parts that have none listed by their occurrence paths."""
    from cadgen.engine.refs import path_text

    prototypes = structure["prototypes"]
    total_volume = 0.0
    mass = 0.0
    weighed_any = False
    unweighed = []
    held = [(structure["instances"][node.payload["instance"]], path_text(hit.root, node)) for node in hit.node.parts()]
    for instance, path in held:
        prototype = prototypes[instance["prototype"]]
        volume = volumes[prototype["key"]]
        total_volume += volume
        density = (prototype.get("material") or {}).get("density")
        if density is None:
            unweighed.append(path)
            continue
        mass += volume * 1e-9 * density
        weighed_any = True
    found: dict[str, Any] = {"ref": hit.ref, "parts": len(held), "volume": total_volume,
                             "mass": mass if weighed_any else None, "unweighed": unweighed}
    if len(held) == 1:
        instance, path = held[0]
        material = prototypes[instance["prototype"]].get("material")
        found.update(part=prototypes[instance["prototype"]]["name"], path=path,
                     material=material["name"] if material else None,
                     density=material.get("density") if material else None)
    return found


def where_parser(prog: str = "cadgen where") -> argparse.ArgumentParser:
    """``cadgen where``'s parser: a document, and a ref into it or none."""
    parser = argparse.ArgumentParser(prog=prog, description="A document's build closure (its source, modules, inputs "
                                                            "and key), or what one thing a ref names is and what made it.")
    parser.add_argument("document", help="an .xbf")
    parser.add_argument("ref", nargs="?", help="[FILE#][OWNER/]ELEMENT or [FILE#]OWNER (cadgen.engine.refs): OWNER a "
                                               "part's or an assembly's label, or its occurrence path (hand/finger.2); "
                                               "ELEMENT an element name (f:..., e:..., v:...) or face N (edge, vertex). "
                                               "An element answers what the kernel measures of it, placed where its "
                                               "occurrence stands, and what made it; a part or an assembly its volume "
                                               "and mass. Without it: the build closure")
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
        print("null" if getattr(args, "json", False) else "no engine daemon is running")
        return 0
    if args.verb == "stop":
        from cadgen.engine.client import wait_gone

        # Stopped means gone: the next command starts a fresh daemon, never reaches this one.
        if not wait_gone():
            print("cadgen xbf stop: the engine daemon did not exit", file=sys.stderr)
            return 1
    if args.verb == "status" and not args.json:
        print(status_text(answer))
        return 0
    print(json.dumps(answer, indent=2))
    return 0


def _span(seconds: Any) -> str:
    """A span of time at a glance: ``41s``, ``4m``, ``2h10m``."""
    seconds = int(max(0.0, float(seconds or 0)))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    hours, minutes = divmod(seconds // 60, 60)
    return f"{hours}h{minutes:02d}m" if minutes else f"{hours}h"


def _gb(megabytes: Any) -> str:
    return "-" if megabytes is None else f"{float(megabytes) / 1024:.1f} GB"


def status_text(answer: dict[str, Any]) -> str:
    """``cadgen xbf status``: the daemon and its pool's rules, a line per worker -- busy or idle,
    its script, process, builds and memory after its last build, where a busy one's build is
    (its last progress frame), how long it has been busy or idle -- the builds waiting for a
    worker, the last retirements, and what it serves."""
    import time

    workers = answer.get("workers") or []
    cap = answer.get("rssCapMb")
    lines = [" · ".join([f"engine daemon {answer.get('pid')}, up {_span(answer.get('up'))}",
                         f"workers {len(workers)}/{answer.get('cap')}",
                         f"idle timeout {_span(answer.get('idleTimeout'))}",
                         f"RSS cap {_gb(cap)}" if cap else "no RSS cap"])]
    names = [Path(str(worker["script"])).name for worker in workers]
    width = max((len(name) for name in names), default=0)
    for worker, name in zip(workers, names):
        builds = f"{worker.get('builds', 0)} build{'' if worker.get('builds') == 1 else 's'}"
        doing = _span(worker.get("seconds"))
        frame = worker.get("progress")
        if worker.get("busy") and frame:
            from cadgen.engine.progress import describe

            # Where its build is: ``building tom.py::tom · Building geometry · 3/7 stages · 41s``.
            doing = f"building {frame.get('model')} · {describe(frame)} · {doing}"
        lines.append(f"  {'busy' if worker.get('busy') else 'idle'}  {name:<{width}}  {str(worker.get('pid')):>6}  "
                     f"{builds:>10}  {_gb(worker.get('rssMb')):>7}  {doing}")
    if answer.get("waiting"):
        waiting = answer["waiting"]
        lines.append(f"  waiting  {waiting} build{'' if waiting == 1 else 's'}, first come first served")
    retired = answer.get("retired") or []
    if retired:
        now = time.time()
        lines.append("retired: " + " · ".join(f"{Path(str(row['script'])).name} ({row['reason']}) "
                                              f"{_span(now - row['at'])} ago" for row in retired))
    serving = answer.get("serving")
    if serving:
        documents = len(serving.get("documents") or [])
        renders = len(answer.get("renders") or [])
        lines.append(f"serving {serving['url']}: {documents} document{'' if documents == 1 else 's'}"
                     + (f" · {renders} render process{'' if renders == 1 else 'es'}" if renders else ""))
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
