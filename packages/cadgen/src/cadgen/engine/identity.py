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
