"""Model addresses and script declarations for the viewer, without kernel imports."""
from __future__ import annotations

from pathlib import Path
from cadgen.engine.addresses import declarations, parse
from cadgen.engine.client import request


def in_memory() -> list[dict]:
    answer = request({"op": "models"}, start=False)
    return (answer or {}).get("models", [])


def entry(value: str) -> dict | None:
    address = parse(value)
    if address is not None:
        if not address.script.is_file() or address.name not in declarations(address.script):
            return None
        return {"file": str(address), "kind": "xbf", "sourceFormat": "model", "url": "", "hash": "model", "bytes": 0}
    script = Path(value)
    if script.suffix.lower() == ".py" and script.is_file():
        stat = script.stat()
        return {"file": str(script.resolve()), "kind": "script", "url": "", "bytes": stat.st_size,
                "hash": f"{stat.st_mtime_ns}-{stat.st_size}",
                "models": [{"name": name, "address": f"{script.resolve()}::{name}"} for name in declarations(script)]}
    return None
