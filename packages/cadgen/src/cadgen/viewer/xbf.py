"""``GET /__cad/xbf?file=``: where the cadgen engine serves an ``.xbf`` document.

The viewer does not read an engine document, mesh it or proxy it. It asks the engine's daemon
to serve the file (``cadgen.engine.client``, connect-or-start, the ``serve`` op every ``cadgen xbf
serve`` sends) and answers the engine's origin and the document's id there. The page then reads
the structure and one buffer per part from the engine directly (``@text-to-cad/core``
``lib/xbf/xbfDocument.js``), and waits on the engine for each new version a build sends it. So
the bytes go from the engine's memory to the page once, the viewer's server is out of that path,
and an edit reaches the page the moment the engine has it, without a file being read again.

The engine answers a page of this viewer's origin (``cadgen.engine.access``: loopback origins
only). Nothing here imports the kernel: the daemon is a process of its own, spoken to over its
socket.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .backend import absolute_path

__all__ = ["engine_document"]


def engine_document(file: str) -> dict:
    """The engine serving ``file`` (an ``.xbf``): ``{"engine", "doc", "url"}``.

    Raises ``ValueError`` for a request that names no ``.xbf`` file, and ``RuntimeError`` when the
    engine would not serve it (the engine's own words)."""
    if not file:
        raise ValueError("name the .xbf to open: ?file=<path>")
    path = Path(absolute_path(file))
    if path.suffix.lower() != ".xbf":
        raise ValueError(f"{path.name} is not a cadgen engine document (.xbf)")
    if not path.is_file():
        raise ValueError(f"no document {path}")
    from cadgen.engine.client import request

    answer = request({"op": "serve", "path": str(path)})
    if not answer or "failed" in answer:
        raise RuntimeError(str((answer or {}).get("failed") or "the cadgen engine did not answer"))
    served = urlparse(str(answer["url"]))
    ident = (parse_qs(served.query).get("doc") or [""])[0]
    if not ident:
        raise RuntimeError(f"the cadgen engine served {path.name} without naming it: {answer['url']}")
    engine = f"{served.scheme}://{served.netloc}"
    return {"engine": engine, "doc": ident, "url": f"{engine}/api/doc/{ident}"}
