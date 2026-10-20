"""``cadgen xbf``: build models, export documents, serve them.

    cadgen xbf build SCRIPT[::FUNCTION] ...   build in the warm worker of each script
    cadgen xbf export DOC [--step F] [--glb F] [--stl F] [--threemf F] [--pose NAME|JSON]
    cadgen xbf serve DOC [--port N] [--three DIR] [--foreground]
    cadgen xbf snapshot DOC OUT.png [--display MODE|JSON] [--grid] [--camera C] [--preview] [--pose NAME|JSON] [--json]
    cadgen xbf where DOC REF                  what made one element: its facts and ``made_by``
    cadgen xbf status | stop | daemon
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

__all__ = ["build_parser", "main"]


def build_parser(prog: str = "cadgen xbf") -> argparse.ArgumentParser:
    """The command's parser: its verbs and their options."""
    return _parser(prog)


def _parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Build, export and serve cadgen engine documents.")
    verbs = parser.add_subparsers(dest="verb", required=True)
    build = verbs.add_parser("build", help="build models: SCRIPT.py::FUNCTION, or every model of SCRIPT.py")
    build.add_argument("targets", nargs="+", metavar="SCRIPT[::FUNCTION]")
    build.add_argument("--no-daemon", action="store_true", help="build in this process (cold, no worker kept)")
    build.add_argument("--json", action="store_true", help="print each model's report as JSON")
    export = verbs.add_parser("export", help="translate a document to STEP, GLB, STL or 3MF")
    export.add_argument("document", help="an .xbf (or a STEP)")
    for kind in ("step", "glb", "stl", "threemf"):
        export.add_argument(f"--{kind}", metavar="FILE")
    export.add_argument("--pose", help="a configuration's name, or joint values as JSON ('{\"elbow\": 45}'): the "
                                       "mechanism posed there; as built without it")
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


def _targets(values: list[str]) -> list[tuple[Path, list[str] | None]]:
    grouped: dict[Path, list[str] | None] = {}
    for value in values:
        script, _sep, function = value.partition("::")
        path = Path(script).resolve()
        if path.suffix != ".py":
            raise SystemExit(f"cadgen xbf build: {script} is not a Python script")
        if not path.is_file():
            raise SystemExit(f"cadgen xbf build: no script {script}")
        if path in grouped and (grouped[path] is None or not function):
            grouped[path] = None
            continue
        if function:
            grouped.setdefault(path, [])
            assert grouped[path] is not None
            grouped[path].append(function)  # type: ignore[union-attr]
        else:
            grouped[path] = None
    return list(grouped.items())


def _describe(report: dict[str, Any], wall: float, fresh: bool | None) -> str:
    if not report.get("ok"):
        return f"failed {report.get('model')}"
    timings = report.get("timings", {})
    memo = report.get("memo", {})
    files = report.get("files", {})
    shown = []
    for path, row in files.items():
        state = "written" if row.get("written") else "unchanged"
        shown.append(f"{os.path.relpath(path)} ({state}, {row.get('bytes', 0) / 1e6:.1f} MB)")
    worker = "" if fresh is None else (" cold" if fresh else " warm")
    return (f"built {report['model']}{worker} in {wall:.2f}s: body {timings.get('body', 0):.2f}s, "
            f"{memo.get('hits', 0)} hits, {memo.get('misses', 0)} misses, {memo.get('uncached', 0)} uncached"
            + (f"; {', '.join(shown)}" if shown else "")
            + "".join(f"\n  warning: kinematics: {text}" for text in report.get("kinematics", {}).get("warnings", ())))


def _build(args: argparse.Namespace) -> int:
    failed = False
    for script, functions in _targets(args.targets):
        started = time.perf_counter()
        if args.no_daemon:
            from cadgen.engine.build import BuildError, build

            try:
                reports = build(script, functions, log=lambda text: print(f"  {text}", file=sys.stderr),
                                cwd=Path.cwd())
            except BuildError as error:
                print(f"cadgen xbf build: {error}", file=sys.stderr)
                failed = True
                continue
            fresh = None
        else:
            from cadgen.engine.client import request

            def log(message: dict[str, Any]) -> None:
                text = message["log"]
                print(text if message.get("stream") == "model" else f"  {text}", file=sys.stderr)

            answer = request({"op": "build", "script": str(script), "functions": functions, "cwd": os.getcwd(),
                              "env": dict(os.environ)}, log)
            if answer is None or "failed" in answer:
                print(f"cadgen xbf build: {(answer or {}).get('failed', 'no daemon')}", file=sys.stderr)
                failed = True
                continue
            reports = answer["done"]
            fresh = answer.get("worker", {}).get("fresh")
        wall = time.perf_counter() - started
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
    from cadgen.engine import document, exports

    wanted = [(kind, getattr(args, kind)) for kind in ("step", "glb", "stl", "threemf") if getattr(args, kind)]
    if not wanted:
        print("cadgen xbf export: name at least one of --step, --glb, --stl, --threemf", file=sys.stderr)
        return 2
    source = Path(args.document)
    if not source.is_file():
        print(f"cadgen xbf export: no document {args.document}", file=sys.stderr)
        return 2
    started = time.perf_counter()
    loaded = document.load(source)
    print(f"read {source} in {time.perf_counter() - started:.2f}s", file=sys.stderr)
    pose = None
    if args.pose is not None:
        from cadgen.engine import mechanism
        from cadgen.engine.snapshot import parse_pose

        # The pose is checked whole before any file is written: a name, a joint or a value it gets
        # wrong is refused by name, and a mechanism that is not a tree has no pose.
        try:
            pose = parse_pose(args.pose)
            structure = loaded.structure()
            values, _named = mechanism.resolve(structure.get("kinematics"), pose)
            mechanism.link_worlds(structure["kinematics"], values)
        except ValueError as error:
            print(f"cadgen xbf export: --pose: {error}", file=sys.stderr)
            return 2
    for kind, out in wanted:
        clock = time.perf_counter()
        size = exports.write_from_loaded(kind, loaded, Path(out), pose=pose)
        print(f"wrote {out} ({size / 1e6:.1f} MB) in {time.perf_counter() - clock:.2f}s")
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
    from cadgen.engine.client import request

    answer = request({"op": "serve", "path": str(path), "port": args.port, "three": three})
    if answer is None or "failed" in answer:
        print(f"cadgen xbf serve: {(answer or {}).get('failed', 'no daemon')}", file=sys.stderr)
        return 1
    print(answer["url"])
    return 0


_REF = re.compile(r"(?P<part>.+?)/(?P<kind>face|edge|vertex)[ /]?(?P<ordinal>[1-9][0-9]*)")
_LETTERS = {"f": "face", "e": "edge", "v": "vertex"}


def _elements(structure: dict[str, Any], ref: str) -> list[tuple[dict[str, Any], str, int]]:
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
        raise SystemExit(f"cadgen xbf where: {ref!r} is neither PART/face N (edge, vertex) nor an element name")
    kind = _LETTERS[ref[0]]
    return [(prototype, kind, int(ordinal)) for prototype in prototypes
            for ordinal, text in prototype.get(f"{kind}Names", {}).items() if text == ref]


def _where(args: argparse.Namespace) -> int:
    """Serve the document (through the daemon, on demand), find the elements the ref names
    and print, for each, what the kernel measures of it and what made it (``made_by``,
    null for a document no live worker built)."""
    import urllib.request

    from cadgen.engine.client import request

    path = Path(args.document).resolve()
    if not path.is_file():
        print(f"cadgen xbf where: no document {args.document}", file=sys.stderr)
        return 2
    answer = request({"op": "serve", "path": str(path)})
    if answer is None or "failed" in answer:
        print(f"cadgen xbf where: {(answer or {}).get('failed', 'no daemon')}", file=sys.stderr)
        return 1
    base, _sep, ident = answer["url"].partition("/?doc=")

    def get(route: str) -> Any:
        with urllib.request.urlopen(f"{base}/api/doc/{ident}{route}", timeout=300) as response:
            return json.loads(response.read())

    structure = get("")
    elements = _elements(structure, args.ref)
    if not elements:
        print(f"cadgen xbf where: {path.name} has no element {args.ref!r}", file=sys.stderr)
        return 1
    found = []
    for prototype, kind, ordinal in elements:
        facts = get(f"/part/{prototype['key']}/{kind}/{ordinal}")
        found.append({"part": prototype["name"], "key": prototype["key"], "kind": kind, "ordinal": ordinal,
                      "name": prototype.get(f"{kind}Names", {}).get(str(ordinal)), **facts})
    print(json.dumps(found[0] if len(found) == 1 else found, indent=2))
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
