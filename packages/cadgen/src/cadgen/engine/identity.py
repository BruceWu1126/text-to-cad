"""Namespace/name declarations and registry identity, without the CAD kernel."""
from __future__ import annotations

import os
from typing import Any

from cadgen.engine.ids import canonical, derived_id
from cadgen.engine.registry import Registry, home


def claimed(held: Registry, source: dict[str, Any], name: str) -> None:
    """Refuse a name another extant declaration in this folder holds, naming both locations."""
    script = canonical(source["script"])
    namespace = os.path.dirname(script)
    record = held.get(derived_id(namespace, name))
    if record is None or record.get("kind") != "model":
        return
    other = str(home(held, record))
    if not os.path.isfile(other) or (canonical(other), record.get("entry")) == (script, source["name"]):
        return
    raise ValueError(f"name {name!r} in namespace {namespace} is claimed by "
                     f"{other}:{record.get('line', '?')} and {script}:{source.get('line', '?')}; "
                     "give one declaration another name")


def fingerprint(script: str | os.PathLike) -> str | None:
    """The script's exact bytes, SHA-256; unavailable code has no fingerprint."""
    import hashlib
    from pathlib import Path

    try:
        return hashlib.sha256(Path(script).read_bytes()).hexdigest()
    except OSError:
        return None


def repoint(held: Registry, record: dict[str, Any], found: dict[str, Any]) -> None:
    """Land the new cache and record before forgetting the old identity. Never parse an XBF."""
    import shutil

    old, new = record["id"], found["id"]
    if old == new:
        held.put(found)
        return
    if held.state.path(f"documents/{new}/document.json").exists():
        raise ValueError(f"cannot move {old} to {new}: that record already exists")
    if held.cache is not None:
        source = held.cache.path(f"documents/{old}/document.xbf")
        if source.is_file():
            if held.cache.path(f"documents/{new}/document.xbf").exists():
                raise ValueError(f"cannot move {old} to {new}: that cache file already exists")
            held.cache._land(f"documents/{new}/document.xbf", lambda target: shutil.copyfile(source, target))
    held.put(found)
    held.delete(old)


def reattach(held: Registry, ident: str, source: dict[str, Any], *, before=lambda _record: None,
             log=lambda _line: None) -> str | None:
    """Adopt a missing script's record by name and exact bytes; an extant original is a copy."""
    from cadgen.engine.registry import relative
    from pathlib import Path

    script = Path(canonical(source["script"]))
    name = source.get("explicit_name") or source["name"]
    digest = source.get("fingerprint")
    existing = held.get(ident)
    if not digest:
        return None
    candidates = []
    for record in [existing] if existing is not None else held.list():
        try:
            if (record.get("kind") == "model" and record.get("name") == name
                    and record.get("entry") == source["name"] and record.get("fingerprint") == digest
                    and not home(held, record).exists()):
                candidates.append(record)
        except (KeyError, TypeError, ValueError):
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row.get("built") or 0, row["id"]), reverse=True)
    old = candidates[0]
    before(old)
    # A synchronous save may have added the cache metadata.
    old = held.get(old["id"]) or old
    found = {**old, "id": ident, "root": str(script.parent), "namespace": str(script.parent),
             "home": relative(script, script.parent), "name": name,
             "relocated": old.get("relocated") or {"script": str(home(held, old)),
                                                      "module": old.get("module") or home(held, old).stem}}
    repoint(held, old, found)
    choice = f"; chose most recently updated of {len(candidates)} candidates" if len(candidates) > 1 else ""
    log(f"identity: reattached {old['id']} to {ident} ({script}::{name}){choice}")
    return old["id"]


def relocated(held: Registry, ident: str, closure: dict[str, Any], script, folder) -> dict[str, Any]:
    """Project a moved document's closure paths for comparison only; its bytes/key stay intact."""
    from pathlib import Path
    from cadgen.engine.registry import relative

    record = held.get(ident) or {}
    origin = (record.get("relocated") or {}).get("script")
    if not origin:
        return closure
    old_script = Path(origin)
    old_folder = old_script.parent / os.path.relpath(folder, script.parent)

    def moved(path):
        if path is None:
            return None
        absolute = Path(os.path.normpath(old_folder / path))
        if absolute == old_script:
            target = script
        elif absolute.is_relative_to(old_script.parent):
            target = script.parent / absolute.relative_to(old_script.parent)
        else:
            target = absolute
        return relative(target, folder)

    inputs = []
    for row in closure.get("inputs") or []:
        row = list(row)
        at = 1 if row[0] in ("file", "read") else 2
        row[at] = moved(row[at])
        inputs.append(row)
    return {**closure, "source": {**closure["source"], "path": relative(script, folder)},
            "modules": sorted([moved(path), digest] for path, digest in closure["modules"]),
            "symbols": {moved(path): value for path, value in closure.get("symbols", {}).items()},
            "inputs": inputs if closure.get("inputs") is not None else None}


def migrate(held: Registry | None, *, log=None) -> dict[str, Any]:
    """One independent startup pass over JSON. Unreadable records stay, named in one log line."""
    import sys
    import uuid
    from pathlib import Path
    from cadgen.engine.ids import NAMESPACE
    from cadgen.engine.registry import DOCUMENTS, RECORD

    filled, moved, skipped = 0, 0, []
    if held is not None:
        for ident in held.state.names(DOCUMENTS):
            location = f"{DOCUMENTS}/{ident}/{RECORD}"
            try:
                record = held.get(ident)
                if record is None:
                    raise ValueError("unreadable record or unsupported schema/id")
                if record.get("kind") not in ("model", "shape", "file"):
                    raise ValueError("unknown document kind")
                if not all(isinstance(record.get(field), str) and record[field] for field in ("root", "home")):
                    raise ValueError("missing root/home")
                if record["kind"] != "file" and not isinstance(record.get("entry"), str):
                    raise ValueError("missing entry")
                found = dict(record)
                found.setdefault("namespace", record["root"])
                legacy = record.get("key")
                keyed = isinstance(legacy, str) and ident == str(uuid.uuid5(NAMESPACE, "key::" + legacy))
                name = legacy if keyed and record["kind"] == "model" else record.get("entry") or Path(record["home"]).name
                found.setdefault("name", name)
                if "fingerprint" not in found:
                    found["fingerprint"] = fingerprint(home(held, record)) if record["kind"] == "model" else None
                if keyed and record["kind"] == "model" and "line" not in found:
                    import ast

                    try:
                        tree = ast.parse(home(held, record).read_bytes())
                        function = next(node for node in ast.walk(tree)
                                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                                        and node.name == record["entry"])
                        found["line"] = min([function.lineno, *(d.lineno for d in function.decorator_list)])
                    except (OSError, SyntaxError, StopIteration):
                        pass  # a missing/unparsable script still has a relocatable record
                if keyed:
                    scope = found["namespace"] if record["kind"] == "model" else home(held, record)
                    found["id"] = derived_id(scope, found["name"])
                    found.pop("key", None)
                    repoint(held, record, found)
                    moved += 1
                elif held.put(found):
                    filled += 1
            except (OSError, ValueError, TypeError, KeyError) as error:
                skipped.append(f"{location}: {error}")
    result = {"filled": filled, "moved": moved, "skipped": skipped}
    line = f"identity migration: filled {filled}, moved {moved}, skipped {len(skipped)}"
    if skipped:
        line += "; " + "; ".join(skipped)
    (log or (lambda message: print(f"cadgen: {message}", file=sys.stderr)))(line)
    return result
