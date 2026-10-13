#!/usr/bin/env python3
"""Build a cadgen model on alternate CAD kernels, and try new ones.

    python scripts/kernels.py list
    python scripts/kernels.py run <kernel> <model.py>[::function]
    python scripts/kernels.py check [kernel ...]

``run`` runs the model program on one kernel plugin and writes an ordinary
generated file beside the model's own output, under ``.kernels/<kernel>/``.
``check`` builds the skill's fixture models on each kernel and compares every
volume with OpenCASCADE's. Add ``--json`` for machine-readable output.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kernel_tools import manifest  # noqa: E402
from kernel_tools.run import KernelError, run  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def _print(args, payload, text):
    print(json.dumps(payload) if args.json else text)


def cmd_list(args) -> int:
    kernels = manifest.load_all()
    rows = [{"id": k.id, "label": k.label, "detail": k.detail, "exact": k.exact,
             "input": k.input, "output": k.output, "setup": k.setup} for k in kernels]
    text = "\n".join(f"{k.id:<10} {k.label} ({k.detail}): {k.input} -> {k.output.upper()}" for k in kernels)
    _print(args, {"kernels": rows}, text)
    return 0


def cmd_run(args) -> int:
    try:
        kernel = manifest.by_id(args.kernel)
        report = run(kernel, args.model)
    except (KernelError, manifest.ManifestError) as exc:
        _print(args, {"ok": False, "error": str(exc)}, f"error: {exc}")
        return 1
    notes = "".join(f"\n  note: {w}" for w in report.get("warnings", []))
    volume = f", {report['volume']:.4g} mm3" if isinstance(report.get("volume"), (int, float)) else ""
    _print(args, report, f"{report['output']} ({kernel.label}{volume}){notes}")
    return 0


def cmd_check(args) -> int:
    reference = json.loads((FIXTURES_DIR / "reference.json").read_text(encoding="utf-8"))
    kernels = manifest.load_all()
    if args.kernels:
        unknown = set(args.kernels) - {k.id for k in kernels}
        if unknown:
            print(f"error: no kernel {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        kernels = [k for k in kernels if k.id in args.kernels]
    failed = False
    results = []
    # Fixtures are copied to a scratch project so outputs never land in the skill.
    with tempfile.TemporaryDirectory(prefix="kernels-check-", dir=os.getcwd()) as tmp:
        project = Path(tmp)
        for fixture in sorted(FIXTURES_DIR.glob("*.py")):
            shutil.copy(fixture, project / fixture.name)
        for kernel in kernels:
            if not args.json:
                print(f"\n{kernel.label} ({kernel.id}, {kernel.input} -> {kernel.output}, tolerance {kernel.tolerance:.2%})")
            for name, expected in reference.items():
                started = time.perf_counter()
                row = {"kernel": kernel.id, "fixture": name, "expected": expected["volume"]}
                try:
                    report = run(kernel, str(project / f"{name}.py"), cwd=project)
                except KernelError as exc:
                    if "can't run" in str(exc):
                        row.update(skipped=str(exc))
                        results.append(row)
                        if not args.json:
                            print(f"  skipped: {exc}")
                        break
                    failed = True
                    row.update(ok=False, error=str(exc))
                    results.append(row)
                    if not args.json:
                        print(f"  ERR  {name:<9} {str(exc).splitlines()[0]}")
                    continue
                error = report["volume"] / expected["volume"] - 1
                ok = abs(error) <= kernel.tolerance
                failed |= not ok
                ms = round((time.perf_counter() - started) * 1000)
                row.update(ok=ok, volume=report["volume"], error=error, ms=ms, warnings=report.get("warnings", []))
                results.append(row)
                if not args.json:
                    notes = "; ".join(w for w in report.get("warnings", []) if not w.startswith("meshed at"))
                    print(f"  {'ok  ' if ok else 'FAIL'} {name:<9} {report['volume']:>9.2f} vs {expected['volume']:>9.2f}"
                          f"  {error:>+8.2%}  {ms:>6} ms" + (f"  ({notes})" if notes else ""))
    if args.json:
        print(json.dumps({"ok": not failed, "results": results}))
    return 1 if failed else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="kernels.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="print JSON")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="the kernel plugins")
    run_parser = sub.add_parser("run", help="build a model on a kernel")
    run_parser.add_argument("kernel")
    run_parser.add_argument("model", help="model.py, or model.py::function in a file with several models")
    check_parser = sub.add_parser("check", help="build the fixtures on kernels and compare with OpenCASCADE")
    check_parser.add_argument("kernels", nargs="*")
    for p in (run_parser, check_parser, sub.choices["list"]):
        p.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args(argv)
    try:
        return {"list": cmd_list, "run": cmd_run, "check": cmd_check}[args.command](args)
    except manifest.ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
