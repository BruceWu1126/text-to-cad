"""A model's previous document is its cache: the key it was built with, on its main label.

When a build writes a model's xbf, the document's main label carries the model's key
(``record``): a digest of what the model's body is made of -- the source key body
retention computes (``model.BuildContext.source``: the symbols its function reaches,
transitively, every child model's among them, and the engine's own code), the versions
of the libraries it runs on -- and of every file it read, through the engine or itself,
by its bytes; and those files, each by its path relative to the document's folder, so
their bytes can be checked again. A digest and data paths: no source text, no script
path, no line (**the file is the document**, **names are the durable tie to code**).

A build asks the document before it runs a model's body (``consult``). Where the key
the model has now -- its source key now, each file read again -- is the one the document
holds, the body is not run: the document on disk is what it would make, and is not
written. The document read cleanly once, written by this engine, settles it; the files
the model declares are left as they are. Anything else -- no document, another engine's,
one that does not read, a key that differs -- is a build as it was, and the report says
why (``cache``).

Only the whole document is taken, never a part of it. OCCT's BinXCAF reader does not
give back the doubles it was given: it builds each direction and placement anew, and a
direction made again is normalized again, a rotation orthogonalized again, a unit in the
last place apart (a document read and saved is other bytes, and read and saved again,
other bytes again). A document holding a body's result read back would so drift from the
cold build's bytes, build after build. So what is written is always made from what bodies
made in this process, and a document read is never written: an edit after a restart runs
the model.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

SCHEMA = "cadgen.engine key/1"
# The prefix of the record's text: ``record`` sorts its keys, ``bodies`` first.
_RECORD = re.compile(rb'\{"bodies":\d+,"code":"[0-9a-f]+","inputs":\[[ -~]*?\],"key":"[0-9a-f]+",'
                     rb'"schema":"cadgen\.engine key/1"\}')
_WRITER = re.compile(rb"cadgen\.engine xbf/1 \(cadgen [ -~]{1,64}?\)")
# How far into a document its main label's attributes are: right after the header.
_HEAD_BYTES = 1 << 20


@functools.cache
def _libraries() -> str:
    """The versions of what a model's body runs on beyond its own code and the engine's:
    a key made with another build123d or OCP is another key."""
    from importlib import metadata

    found = []
    for name in ("build123d", "cadquery-ocp", "cadquery_ocp", "numpy"):
        try:
            found.append(f"{name}={metadata.version(name)}")
        except metadata.PackageNotFoundError:
            continue
    return ";".join(found)


def _digest(text: str) -> str:
    return hashlib.blake2b(text.encode(), digest_size=16).hexdigest()


def code_key(source: str) -> str:
    """The digest of what a body is made of, without the files it read."""
    return _digest(f"{SCHEMA}\n{_libraries()}\n{source}")


def key(source: str, values: list) -> str:
    """A model's key: its code (``code_key``) and what each file it read holds."""
    return _digest(f"{code_key(source)}\n{json.dumps(values, separators=(',', ':'), default=list)}")


def closure(keys: dict[str, tuple], name: str) -> list[str]:
    """The bodies a body is: itself and every body it called, once each, in order."""
    found: list[str] = []
    pending = [name]
    while pending:
        current = pending.pop()
        if current in found or current not in keys:
            continue
        found.append(current)
        pending.extend(reversed(keys[current][3]))
    return found


def record(keys: dict[str, tuple], name: str, folder: Path) -> str | None:
    """The record a model's document carries: its key, the files it read (relative to
    ``folder``, the document's), how many bodies it is; None for a body whose key does
    not say all it read, or whose reads a document cannot name."""
    from cadgen.engine.kernel import memo

    found = keys.get(name)
    if found is None or not found[2]:
        return None
    source, checks = found[0], found[1]
    inputs, values = [], []
    for check, expected in checks:
        recipe = memo.recipe(check)
        if recipe is None:
            return None
        shown = _relative(recipe, folder)
        if shown not in inputs:  # a child placed again holds its reads again
            inputs.append(shown)
            values.append(expected)
    return json.dumps({"bodies": len(closure(keys, name)), "code": code_key(source), "inputs": inputs,
                       "key": key(source, values), "schema": SCHEMA},
                      sort_keys=True, ensure_ascii=True, separators=(",", ":"), default=list)


def _relative(recipe: tuple, folder: Path) -> list:
    def shown(path: str | None) -> str | None:
        if path is None:
            return None
        try:
            return Path(os.path.relpath(path, folder)).as_posix()
        except ValueError:  # another drive
            return Path(path).as_posix()

    kind = recipe[0]
    if kind in ("file", "read"):
        return [kind, shown(recipe[1])]
    return [kind, recipe[1], shown(recipe[2]), recipe[3]]


def _absolute(recipe: list, folder: Path) -> tuple:
    def placed(path: str | None) -> str | None:
        return None if path is None else os.path.normpath(os.path.join(folder, path))

    kind = recipe[0]
    if kind in ("file", "read"):
        return (kind, placed(recipe[1]))
    return (kind, recipe[1], placed(recipe[2]), recipe[3])


def current(found: dict[str, Any], source: str, folder: Path) -> str:
    """The key the model has now, by the record's reads: its source now, each file read
    again. Raises for a file that cannot be read."""
    from cadgen.engine.kernel import memo

    values = []
    for recipe in found["inputs"]:
        values.append(memo.recheck(_absolute(recipe, folder)))
    return key(source, values)


def scan(path: Path) -> tuple[str | None, str | None]:
    """What wrote the document at ``path`` and the record its main label holds, read from
    its head, where the main label's attributes are: ``(writer, record)``, each None where
    the head holds none. A first look only: a document is taken once it reads whole."""
    with open(path, "rb") as handle:
        head = handle.read(_HEAD_BYTES)
    writer = _WRITER.search(head)
    found = _RECORD.search(head)
    return (writer.group(0).decode() if writer else None, found.group(0).decode() if found else None)


def read_record(doc: Any) -> tuple[str | None, str | None]:
    """The writer and the record a document read whole holds on its main label."""
    from cadgen.engine.document import KEY_RECORD_GUID, WRITER_GUID, _label_text

    return _label_text(doc.Main(), WRITER_GUID), _label_text(doc.Main(), KEY_RECORD_GUID)
