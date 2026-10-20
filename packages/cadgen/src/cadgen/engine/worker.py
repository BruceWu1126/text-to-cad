"""A warm worker: one process per model script, the memo living in it for its life.

The daemon starts one per script and talks to it over the worker's stdin and its
original stdout, one JSON object per line: a request ``{"id", "op": "build", "script",
"functions", "cwd"}`` is answered by ``{"id", "log"}`` lines while it builds and one
``{"id", "done"}`` (the build's reports) or ``{"id", "failed"}`` at the end. What the
model prints goes to the worker's stderr, which the daemon passes on. The worker exits
when its stdin closes.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any


def main() -> int:
    sys.dont_write_bytecode = True
    protocol = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)  # the model's prints go where the daemon reads logs from
    sys.stdout = sys.stderr

    def send(message: dict[str, Any]) -> None:
        protocol.write(json.dumps(message, separators=(",", ":")) + "\n")
        protocol.flush()

    from cadgen.engine.build import BuildError, build

    send({"ready": os.getpid()})
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        ident = request.get("id")
        if request.get("op") != "build":
            send({"id": ident, "failed": f"unknown request {request.get('op')!r}"})
            continue
        try:
            cwd = request.get("cwd")
            if cwd:
                os.chdir(cwd)
            reports = build(Path(request["script"]), request.get("functions") or None,
                            log=lambda text: send({"id": ident, "log": text}), cwd=Path(cwd) if cwd else None)
            send({"id": ident, "done": reports})
        except BuildError as error:
            send({"id": ident, "failed": str(error)})
        except BaseException as error:  # noqa: BLE001 - the worker answers every request
            send({"id": ident, "failed": "".join(traceback.format_exception(type(error), error, error.__traceback__))})
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
