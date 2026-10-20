"""``cadgen xbf``: build models, export documents, serve them.

    cadgen xbf build SCRIPT[::FUNCTION] ...   build in the warm worker of each script
    cadgen xbf export DOC [--step F] [--glb F] [--stl F] [--threemf F]
    cadgen xbf serve DOC [--port N] [--three DIR] [--foreground]
    cadgen xbf snapshot DOC OUT.png [--display MODE|JSON] [--grid] [--camera C] [--preview] [--json]
    cadgen xbf status | stop | daemon
"""

from __future__ import annotations

import argparse
import json
import os
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
            + (f"; {', '.join(shown)}" if shown else ""))


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
    for kind, out in wanted:
        clock = time.perf_counter()
        size = exports.write_from_loaded(kind, loaded, Path(out))
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
