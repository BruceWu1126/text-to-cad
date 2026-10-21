"""``cadgen xbf``: build models, export documents, draw and serve them -- a thin argparse over
the functions of ``api``.

    cadgen xbf build SCRIPT[::MODEL] ...             every model of the script, or one
    cadgen xbf build --code 'SOURCE' | - [--name N]  one model from source text (stdin with -)
        [--xbf [F]] [--step [F]] [--glb [F]] [--stl [F]] [--threemf [F]] [--snapshot [F]]
        [--pose NAME|JSON] [--part NAME] [--json] [--no-daemon]
    cadgen xbf export SRC DST [DST ...] [--pose NAME|JSON] [--part NAME]
    cadgen xbf snapshot SRC OUT.png [--display MODE|JSON] [--camera PRESET|az:el] [--grid] [--preview]
        [--pose NAME|JSON] [--width N] [--height N] [--size-profile P] [--json]
    cadgen xbf serve DOC [--port N] [--three DIR] [--foreground]
    cadgen xbf where DOC REF                         what made one element: its facts and ``made_by``
    cadgen xbf status | stop | daemon
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

__all__ = ["build_parser", "elements", "main"]

FILE_FLAGS = ("xbf", "step", "glb", "stl", "threemf", "snapshot")
_SUFFIX = {"xbf": ".xbf", "step": ".step", "glb": ".glb", "stl": ".stl", "threemf": ".3mf", "snapshot": ".png"}


def build_parser(prog: str = "cadgen xbf") -> argparse.ArgumentParser:
    """The command's parser: its verbs and their options."""
    return _parser(prog)


def _parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Build, export, draw and serve cadgen engine documents.")
    verbs = parser.add_subparsers(dest="verb", required=True)
    build = verbs.add_parser("build", help="build models: SCRIPT.py::MODEL, every model of SCRIPT.py, or --code")
    build.add_argument("targets", nargs="*", metavar="SCRIPT[::MODEL]")
    build.add_argument("--code", metavar="SOURCE", help="build source text (- reads it from stdin): it assigns its "
                                                        "final shape to `result`; its files are the flags alone")
    build.add_argument("--name", help="the code's name: its model and its warm worker (default: model)")
    for kind in FILE_FLAGS:
        build.add_argument(f"--{kind}", nargs="?", const=True, metavar="F",
                           help=f"write the {kind} (at F, else where the model declares it); any of these flags "
                                "selects the files a script writes")
    build.add_argument("--pose", help="a configuration's name, or joint values as JSON: every derived file posed there")
    build.add_argument("--part", help="an occurrence's path or a node's name: every derived file of that subtree")
    build.add_argument("--no-daemon", action="store_true", help="build in this process (cold, no worker kept)")
    build.add_argument("--json", action="store_true", help="print each model's report as JSON")
    export = verbs.add_parser("export", help="write a document to STEP, GLB, STL, 3MF or xbf files, by suffix")
    export.add_argument("source", metavar="SRC", help="an .xbf, a .step, or SCRIPT.py::MODEL (built first)")
    export.add_argument("destinations", nargs="+", metavar="DST",
                        help=".xbf (from a STEP, or with --part), .step, .glb, .stl or .3mf")
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
    where = verbs.add_parser("where", help="what made one element of a document: its facts, and the feature, "
                                           "function, file and line behind it")
    where.add_argument("document", help="an .xbf")
    where.add_argument("ref", help="PART/face N (or edge, vertex), PART a part's or an instance's name (or the "
                                   "document's, for a document of one part); "
                                   "or an element name (f:..., e:..., v:...)")
    verbs.add_parser("status", help="the daemon's workers and served documents")
    verbs.add_parser("stop", help="stop the daemon")
    verbs.add_parser("daemon", help="run the daemon in this process")
    return parser


def _pose(value: str | None) -> Any:
    from cadgen.engine.snapshot import parse_pose

    return parse_pose(value)


def _targets(values: list[str], verb: str) -> list[tuple[str, list[str] | None]]:
    grouped: dict[Path, list[str] | None] = {}
    for value in values:
        script, _sep, function = value.partition("::")
        path = Path(script).resolve()
        if path.suffix != ".py":
            raise SystemExit(f"cadgen xbf {verb}: {script} is not a Python script")
        if not path.is_file():
            raise SystemExit(f"cadgen xbf {verb}: no script {script}")
        if path in grouped and (grouped[path] is None or not function):
            grouped[path] = None
            continue
        if function:
            grouped.setdefault(path, [])
            assert grouped[path] is not None
            grouped[path].append(function)  # type: ignore[union-attr]
        else:
            grouped[path] = None
    return [(str(path), functions) for path, functions in grouped.items()]


def _describe(report: dict[str, Any], wall: float, fresh: bool | None) -> str:
    if not report.get("ok"):
        return f"failed {report.get('model')}"
    timings = report.get("timings", {})
    memo = report.get("memo", {})
    bodies = list((report.get("bodies") or {}).values())
    files = []
    for path, row in (report.get("files") or {}).items():
        state = "written" if row.get("written") else "kept"
        files.append(f"{os.path.relpath(path)} ({state}, {row.get('bytes', 0) / 1e6:.1f} MB)")
    worker = "" if fresh is None else (" cold" if fresh else " warm")
    if memo.get("bodies_loaded"):
        line = (f"took {report['model']}{worker} in {wall:.2f}s: its file holds the model's key, "
                f"{memo.get('bodies_loaded', 0)} bodies loaded, nothing ran")
    else:
        counted = {status: bodies.count(status) for status in ("run", "replayed") if bodies.count(status)}
        shown = ", ".join(f"{count} {status}" for status, count in counted.items()) or "none"
        stages = ", ".join(f"{name} {timings[name]:.2f}s" for name in ("body", "names", "document", "xbf", "step",
                                                                        "glb", "stl", "threemf", "snapshot")
                           if timings.get(name))
        line = (f"built {report['model']}{worker} in {wall:.2f}s: bodies {shown}; {memo.get('misses', 0)} misses, "
                f"{memo.get('hits', 0)} hits" + (f"; {stages}" if stages else ""))
    return (line + "".join(f"\n  {text}" for text in files)
            + "".join(f"\n  warning: kinematics: {text}" for text in report.get("kinematics", {}).get("warnings", ())))


def _file_flags(args: argparse.Namespace) -> dict[str, Any]:
    """The file flags, each True (the declared path) or a path. A value that is a script --
    ``--glb model.py`` -- is the flag alone and a target after it."""
    flags = {}
    for kind in FILE_FLAGS:
        value = getattr(args, kind)
        if isinstance(value, str) and (value.endswith(".py") or "::" in value):
            args.targets.append(value)
            value = True
        if isinstance(value, str) and Path(value).suffix.lower() != _SUFFIX[kind] and not (
                kind == "step" and Path(value).suffix.lower() == ".stp"):
            raise SystemExit(f"cadgen xbf build: --{kind} {value}: the file must end in {_SUFFIX[kind]}")
        flags[kind] = value
    return flags


def _build(args: argparse.Namespace) -> int:
    from cadgen.engine import api

    flags = _file_flags(args)
    try:
        pose = _pose(args.pose)
    except ValueError as error:
        print(f"cadgen xbf build: {error}", file=sys.stderr)
        return 2
    code = args.code
    if code is not None and args.targets:
        print("cadgen xbf build: give scripts or --code, not both", file=sys.stderr)
        return 2
    if code is None and not args.targets:
        print("cadgen xbf build: name a script (SCRIPT.py[::MODEL]) or give --code", file=sys.stderr)
        return 2
    if code is None and args.name is not None:
        print("cadgen xbf build: --name names --code; a script's models are named by their functions", file=sys.stderr)
        return 2
    if code == "-":
        code = sys.stdin.read()

    def log(text: str) -> None:
        print(text, file=sys.stderr)

    jobs: list[tuple[Any, dict[str, Any]]] = []
    if code is not None:
        jobs.append((None, {"code": code, "name": args.name or "model"}))
    else:
        # One build per script, its models named or all of them: one run, its children shared.
        jobs.extend(((script, functions), {}) for script, functions in _targets(args.targets, "build"))
    failed = False
    for target, extra in jobs:
        try:
            found = api.build(target, **extra, **flags, pose=pose, part=args.part, log=log, daemon=not args.no_daemon)
        except api.EngineError as error:
            print(f"cadgen xbf build: {error}", file=sys.stderr)
            failed = True
            continue
        if isinstance(found, api.Built):
            reports, wall, fresh = [found.report], found.report.get("wall", 0.0), None
        else:
            reports, wall, fresh = found.models, found.wall, found.fresh
        for report in reports:
            if args.json:
                print(json.dumps({**report, "wall": round(wall, 4)}))
            else:
                print(_describe(report, wall, fresh))
            if not report.get("ok"):
                failed = True
                print(report.get("error", ""), file=sys.stderr)
    return 1 if failed else 0


def _export(args: argparse.Namespace) -> int:
    from cadgen.engine import api
    from cadgen.engine.export import ExportRefused

    try:
        written = api.export(args.source, *args.destinations, pose=_pose(args.pose), part=args.part,
                             log=lambda text: print(text, file=sys.stderr), daemon=not args.no_daemon)
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


def _where(args: argparse.Namespace) -> int:
    """What the kernel measures of the elements a ref names, and what made each (``made_by``,
    null for a document no live worker built)."""
    from cadgen.engine import api

    path = Path(args.document).resolve()
    if not path.is_file():
        print(f"cadgen xbf where: no document {args.document}", file=sys.stderr)
        return 2
    try:
        found = api.where(path, args.ref)
    except ValueError as error:
        print(f"cadgen xbf where: {error}", file=sys.stderr)
        return 2
    except api.EngineError as error:
        print(f"cadgen xbf where: {error}", file=sys.stderr)
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
    if args.verb == "where":
        return _where(args)
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
