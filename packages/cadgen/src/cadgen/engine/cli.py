"""The engine's verbs as commands -- a thin argparse over the functions of ``api`` -- and
ordinary Python scripts call decorated models explicitly.

    python SCRIPT.py                                 the models the script explicitly calls
    cadgen build SOURCE | - [--model NAME] [--name N]  source text (stdin with -), an output required
        [--xbf [F]] [--step [F]] [--glb [F]] [--stl [F]] [--threemf [F]] [--dxf [F]] [--json] [--verify]
        [--no-daemon]
    cadgen export SRC DST [DST ...] [--pose NAME|JSON] [--part OWNER] [--no-inputs] [--no-daemon] [--json]
    cadgen snapshot SRC OUT.png [--display MODE|exploded=A|JSON] [--camera PRESET|az:el|JSON|authored] [--grid]
        [--preview] [--pose NAME|JSON] [--annotation FILE] [--measurements FILE] [--width N] [--height N]
        [--size-profile P] [--json]
    cadgen serve DOC [--port N] [--three DIR]
    cadgen status [--json] | stop | daemon
    cadgen where DOC [REF]                           a document's build closure, or what a ref names (``refs``)

cadgen's command line runs each verb by its name (``parser_for``, ``run``); the engine's own entry,
``python -m cadgen.engine VERB ...``, takes the same verbs as subcommands (``main``). A verb's failure
is said once where its command ends (``answered``): under ``--json`` as ``{"ok": false, "error": ...}``
on stdout, exit 1; else its words on stderr, exit 2 for a request refused by name before anything ran
and 1 for one that failed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

__all__ = ["VERBS", "Failed", "answered", "build_parser", "main", "parser_for", "run", "weighed",
           "where_main", "where_parser"]

FILE_FLAGS = ("xbf", "step", "glb", "stl", "threemf", "dxf")
_SUFFIX = {"xbf": ".xbf", "step": ".step", "glb": ".glb", "stl": ".stl", "threemf": ".3mf", "dxf": ".dxf"}
# Where a file flag with no path writes, in the working folder.
_DEFAULT = {"xbf": "XBF/<name>.xbf", "step": "<name>.step", "glb": "<name>.glb", "stl": "<name>.stl",
            "threemf": "<name>.3mf", "dxf": "<name>.dxf"}


def _build_arguments(build: argparse.ArgumentParser) -> None:
    build.usage = ("%(prog)s SOURCE [--model NAME] [--name N] [--xbf [F]] [--step [F]] [--glb [F]] [--stl [F]] [--threemf [F]] "
                   "[--dxf [F]] [--json] [--verify] [--profile] [--no-daemon]")
    build.add_argument("source", metavar="SOURCE", help="the code (- reads it from stdin)")
    build.add_argument("--model", metavar="NAME", help="build or update the named document in memory")
    build.add_argument("--name", metavar="N",
                       help="the code's name: its model, its files' default names and its warm worker (default: model)")
    for kind in FILE_FLAGS:
        build.add_argument(f"--{kind}", nargs="?", const=True, metavar="F",
                           help=f"write the {kind} at F (default: {_DEFAULT[kind]} in the working folder)")
    build.add_argument("--profile", action="store_true", help="profile actual worker execution, preserving cache hits")
    build.add_argument("--json", action="store_true", help="print the report as JSON")
    build.add_argument("--verify", action="store_true",
                       help="run every function the engine would replay and compare its result with the logged one")
    build.add_argument("--no-daemon", action="store_true", help="build in this process (cold, no worker kept)")


def _export_arguments(export: argparse.ArgumentParser) -> None:
    export.add_argument("source", metavar="SRC", help="a model address (SCRIPT::NAME or code::NAME), an .xbf, or a .step")
    export.add_argument("destinations", nargs="+", metavar="DST",
                        help=".xbf (from a STEP, or with --part), .step, .glb, .stl, .3mf or .dxf (of a planar "
                             "document); or .cadz (from an xbf: it, its build closure's source files and its inputs, "
                             "zipped)")
    export.add_argument("--no-inputs", action="store_true", help="a .cadz without the closure's input files")
    export.add_argument("--pose", help="a configuration's name, or joint and coupling values as JSON "
                                       "('{\"elbow\": 45}', '{\"curl\": 0.6}'): the mechanism posed there; as built "
                                       "without it")
    export.add_argument("--part", metavar="OWNER",
                        help="one part or assembly, as a ref names it (hand/finger.2, palm; cadgen.engine.refs): that "
                             "subtree as a document of its own")
    export.add_argument("--no-daemon", action="store_true", help="export in this process (cold, no worker kept)")
    export.add_argument("--json", action="store_true", help="print what was written as JSON")


def _serve_arguments(serve: argparse.ArgumentParser) -> None:
    serve.add_argument("document", help="a model address (SCRIPT::NAME or code::NAME), an .xbf, or a STEP")
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--three", help="the three.js package folder (default: the nearest node_modules/three)")


def _status_arguments(status: argparse.ArgumentParser) -> None:
    status.add_argument("--json", action="store_true", help="the daemon's answer as JSON")


def _no_arguments(_parser: argparse.ArgumentParser) -> None:
    return None


def _where_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("document", help="a model address (SCRIPT::NAME or code::NAME), or an .xbf")
    parser.add_argument("ref", nargs="?", help="[FILE#][OWNER/]ELEMENT or [FILE#]OWNER (cadgen.engine.refs): OWNER a "
                                               "part's or an assembly's label, or its occurrence path (hand/finger.2); "
                                               "ELEMENT an element name (f:..., e:..., v:...) or face N (edge, vertex). "
                                               "An element answers what the kernel measures of it, placed where its "
                                               "occurrence stands, and what made it; a part or an assembly its volume "
                                               "and mass. Without it: the build closure")


def _snapshot_arguments(parser: argparse.ArgumentParser) -> None:
    from cadgen.engine.snapshot import _arguments

    _arguments(parser)


def _snapshot(args: argparse.Namespace, prog: str) -> int:
    from cadgen.engine.snapshot import run

    return run(args, prog)


def build_parser(prog: str = "python -m cadgen.engine") -> argparse.ArgumentParser:
    """The engine's own command: its verbs as subcommands, each with its options."""
    parser = argparse.ArgumentParser(prog=prog, description="Build, export, draw and serve cadgen engine documents.")
    verbs = parser.add_subparsers(dest="verb", required=True)
    for verb, (help_text, arguments, _runner) in VERBS.items():
        arguments(verbs.add_parser(verb, help=help_text, description=help_text))
    return parser


def parser_for(verb: str, prog: str | None = None) -> argparse.ArgumentParser:
    """One verb's parser, as a command of its own (``cadgen build``, ``cadgen export``, ...)."""
    help_text, arguments, _runner = VERBS[verb]
    parser = argparse.ArgumentParser(prog=prog or f"cadgen {verb}", description=help_text)
    arguments(parser)
    return parser


def run(verb: str, argv: list[str] | None = None, prog: str | None = None) -> int:
    """One verb run as a command of its own: its arguments parsed by ``parser_for``."""
    prog = prog or f"cadgen {verb}"
    args = parser_for(verb, prog).parse_args(argv)
    return answered(VERBS[verb][2], args, prog)


class Failed(Exception):
    """A verb's failure, said once where its command ends (``answered``): its words, and the status
    the command exits with -- 2 for a request refused by name before anything ran, 1 for one that
    failed -- unless it answers in JSON."""

    def __init__(self, words: str, status: int = 1) -> None:
        super().__init__(words)
        self.status = status


def answered(runner: Callable[[argparse.Namespace, str], int], args: argparse.Namespace, prog: str) -> int:
    """A verb's run, and its failure said once: under ``--json`` as ``{"ok": false, "error": ...}``
    on stdout -- the result's channel, so a caller reading stdout always finds an answer -- and the
    status 1; else its words on stderr and its own status. A failure no verb expected is said in
    JSON too, then raised as ever (its traceback the person's, and a crash the command reports)."""
    as_json = bool(getattr(args, "json", False))
    try:
        return runner(args, prog)
    except Failed as failure:
        if as_json:
            print(json.dumps({"ok": False, "error": str(failure)}, separators=(",", ":")))
            return 1
        print(f"{prog}: {failure}", file=sys.stderr)
        return failure.status
    except Exception as error:
        if as_json:
            print(json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}, separators=(",", ":")))
        raise


def _pose(value: str | None) -> Any:
    from cadgen.engine.snapshot import parse_pose

    return parse_pose(value)


def _shown(path: Any) -> str:
    """A path relative to the working folder, or whole where none can be: on Windows, a path on
    another drive than the folder's."""
    try:
        return os.path.relpath(path)
    except ValueError:
        return str(path)


def _files(report: dict[str, Any]) -> list[str]:
    found = []
    for path, row in (report.get("files") or {}).items():
        state = "written" if row.get("written") else "kept"
        found.append(f"{_shown(path)} ({state}, {row.get('bytes', 0) / 1e6:.1f} MB)")
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
            raise Failed(f"--{kind} {value}: the file must end in {_SUFFIX[kind]}")
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
            if report.get("profile"):
                from cadgen.engine.profiling import describe

                print(describe(report["profile"]))
            if report.get("address"):
                print(f"  model: {report['address']}")
                if report.get("url"):
                    print(f"  view: {report['url']}")
        if not report.get("ok"):
            failed = True
            print(report.get("error", ""), file=sys.stderr)
    return 1 if failed else 0


def _log(text: str) -> None:
    print(text, file=sys.stderr)


def _build(args: argparse.Namespace, prog: str) -> int:
    """``cadgen build SOURCE``: code, its flags its declarations (law 12)."""
    from cadgen.engine import api

    flags = _file_flags(args)
    if not args.model and not any(flags.values()):
        raise Failed("build requires an output: --model NAME or --xbf/--step/--glb/--stl/--threemf/--dxf", 2)
    if args.model and args.no_daemon:
        raise Failed("--model needs the engine to hold its document; omit --no-daemon", 2)
    code = sys.stdin.read() if args.source == "-" else args.source
    try:
        built = api.build(code, model=args.model, name=args.name, **flags, verify=args.verify, profile=args.profile, log=_log, daemon=not args.no_daemon)
    except api.EngineError as error:
        raise Failed(str(error)) from None
    return _reported([built.report], built.report.get("wall", 0.0), None, args.json)


def _export(args: argparse.Namespace, prog: str) -> int:
    """``cadgen export SRC DST...``: an ``.xbf`` or a ``.step`` written to each destination; with
    ``--json``, what was written as one JSON object."""
    from cadgen.engine import api
    from cadgen.engine.export import SOURCES, ExportRefused

    from cadgen.engine.addresses import key, parse

    if parse(args.source) is None and Path(args.source).suffix.lower() not in SOURCES:
        raise Failed(f"{args.source} is no document: an export reads an .xbf or a .step", 2)
    try:
        written = api.export(args.source, *args.destinations, pose=_pose(args.pose), part=args.part,
                             inputs=not args.no_inputs, log=lambda text: print(text, file=sys.stderr),
                             daemon=not args.no_daemon)
    except (ExportRefused, ValueError) as error:
        raise Failed(str(error), 2) from None
    except api.EngineError as error:
        raise Failed(str(error)) from None
    if args.json:
        print(json.dumps({"ok": True, "source": str(key(args.source)),
                          "files": [{"path": str(row.path), "kind": row.kind, "bytes": row.bytes, "seconds": row.seconds}
                                    for row in written]}))
        return 0
    for row in written:
        print(f"wrote {_shown(row.path)} ({row.bytes / 1e6:.1f} MB) in {row.seconds:.2f}s")
    return 0


def _serve(args: argparse.Namespace, prog: str) -> int:
    from cadgen.engine.addresses import key

    path = key(args.document)
    if isinstance(path, Path) and not path.is_file():
        raise Failed(f"no document {args.document}", 2)
    from cadgen.engine import api

    try:
        print(api.serve(path, args.port, args.three).url)
    except api.EngineError as error:
        raise Failed(str(error)) from None
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
    return parser_for("where", prog)


def where_main(argv: list[str] | None = None, prog: str = "cadgen where") -> int:
    """``cadgen where DOC``: the build closure the xbf holds; ``cadgen where DOC REF``: what the
    kernel measures of the elements a ref names, and what made each (``made_by``, null for a
    document no live worker built)."""
    return run("where", argv, prog)


def _where(args: argparse.Namespace, prog: str) -> int:
    from cadgen.engine import api

    try:
        found = api.where(args.document, args.ref)
    except ValueError as error:
        raise Failed(str(error), 2) from None
    except api.EngineError as error:
        raise Failed(str(error)) from None
    print(json.dumps(found, indent=2))
    return 0


def _daemon(_args: argparse.Namespace, _prog: str) -> int:
    from cadgen.engine.daemon import main as daemon

    return daemon([])


def _status(args: argparse.Namespace, _prog: str) -> int:
    from cadgen.engine.client import request

    answer = request({"op": "status"}, start=False)
    if answer is None:
        print("null" if args.json else "no engine daemon is running")
        return 0
    if args.json:
        print(json.dumps(answer, indent=2))
    else:
        print(status_text(answer))
    return 0


def _stop(_args: argparse.Namespace, prog: str) -> int:
    from cadgen.engine.client import request, wait_gone

    answer = request({"op": "stop"}, start=False)
    if answer is None:
        print("no engine daemon is running")
        return 0
    # Stopped means gone: the next command starts a fresh daemon, never reaches this one.
    if not wait_gone():
        raise Failed("the engine daemon did not exit")
    print(json.dumps(answer, indent=2))
    return 0


def main(argv: list[str] | None = None, prog: str = "python -m cadgen.engine") -> int:
    """The engine's own command: ``VERB [args]``, every verb of ``VERBS``."""
    args = build_parser(prog).parse_args(argv)
    return answered(VERBS[args.verb][2], args, f"{prog} {args.verb}")


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
    """``cadgen status``: the daemon and its pool's rules, a line per worker -- busy or idle,
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


# Each verb: its help, the function adding its arguments to a parser, and the function running it.
VERBS: dict[str, tuple[str, Callable[[argparse.ArgumentParser], None], Callable[[argparse.Namespace, str], int]]] = {
    "build": ("build source text into the files its flags name (a script builds by running it)", _build_arguments,
              _build),
    "export": ("write an .xbf or a .step to STEP, GLB, STL, 3MF, DXF or xbf files, by suffix; a GLB carries the "
               "document's motions as clips, a DXF draws a planar one", _export_arguments, _export),
    "snapshot": ("draw a document or a file the viewer opens as the CAD Viewer does, into a PNG", _snapshot_arguments,
                 _snapshot),
    "serve": ("serve a document over HTTP and print its URL", _serve_arguments, _serve),
    "where": ("a document's build closure (its source, modules, inputs and key), or what one thing a ref names is "
              "and what made it", _where_arguments, _where),
    "status": ("the engine daemon's workers, what each is building, and its served documents", _status_arguments,
               _status),
    "stop": ("stop the engine daemon", _no_arguments, _stop),
    "daemon": ("run the engine daemon in this process", _no_arguments, _daemon),
}


if __name__ == "__main__":
    sys.exit(main())
