"""A script's already-built shape transferred in memory to the normal document writer.

The caller collects native geometry and metadata. The server forwards plain JSON;
only the worker decodes geometry. No pickle, source file or intermediate export is
used. Operations that produced the shape have already run in the caller, so they
have no worker memo or feature provenance to replay.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import types
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any


def _value(value: Any) -> Any:
    """The mechanism as JSON. Every tuple, dict and dataclass is a one-key wrapper (``t``,
    ``d``, ``c``), so an author's name -- a joint, a configuration or a motion called
    ``tuple`` -- is never read back as structure."""
    if is_dataclass(value):
        return {"c": [type(value).__name__, [[f.name, _value(getattr(value, f.name))] for f in fields(value)]]}
    if isinstance(value, tuple):
        return {"t": [_value(item) for item in value]}
    if isinstance(value, list):
        return [_value(item) for item in value]
    if isinstance(value, dict):
        return {"d": [[_value(key), _value(item)] for key, item in value.items()]}
    return value


def _restore(value: Any) -> Any:
    from cadgen.engine.mechanism import Coupling, Link, Mechanism, Motion, Pair

    kinds = {kind.__name__: kind for kind in (Coupling, Link, Mechanism, Motion, Pair)}
    if isinstance(value, list):
        return [_restore(item) for item in value]
    if isinstance(value, dict):
        [(tag, held)] = value.items()
        if tag == "t":
            return tuple(_restore(item) for item in held)
        if tag == "d":
            return {_restore(key): _restore(item) for key, item in held}
        kind, given = held
        return kinds[kind](**{key: _restore(item) for key, item in given})
    return value


def pack(shape: Any, name: str) -> dict:
    from build123d import Shape
    from cadgen.engine import document
    from cadgen.engine.mechanism import _numpy
    from cadgen.engine.mesh import shape_bytes

    if not isinstance(shape, Shape) or shape.wrapped is None:
        raise TypeError("build takes source text or a nonempty build123d Shape")
    tree = document.collect(shape, name)
    index = {id(node): at for at, node in enumerate(tree.order)}
    nodes = []
    for node in tree.order:
        if isinstance(node, document.Part):
            nodes.append({"name": node.name, "shape": base64.b64encode(shape_bytes(node.shape, read=True)).decode("ascii"),
                          "color": node.color, "face_colors": node.face_colors,
                          "material": document.material_json(node.material),
                          "face_materials": {str(k): document.material_json(v) for k, v in node.face_materials.items()}})
        else:
            nodes.append({"name": node.name, "components": [
                {"prototype": index[id(c.prototype)], "matrix": _numpy(c.location).tolist(),
                 "name": c.name, "color": c.color} for c in node.components]})
    return {"root": index[id(tree.root)], "nodes": nodes, "mechanism": _value(tree.mechanism)}


def build(payload: dict, script: Path, name: str, outputs: dict | None, cwd: Path, log: Any) -> list[dict]:
    """Decode in the worker and use the same document/output writer as a decorated model."""
    from cadgen.engine import build as building, document
    from cadgen.engine.export.tree import _located
    from cadgen.engine.mesh import read_shape
    import numpy as np

    started = time.perf_counter()
    declared = building._declared(name, outputs, cwd)
    from cadgen.engine.model import Model, Output, _MODELS

    # A supplied shape has no Python body to import, execute or replay.
    function = types.FunctionType((lambda: None).__code__, {}, name)
    model = Model(function)
    _MODELS.pop(f"{model.script}::{model.name}", None)
    model.script = script
    model._address = f"code::{name}"
    model.outputs = [Output(kind, given["out"]) for kind, given in declared.items()]
    nodes, parts = [], []
    for node in payload["nodes"]:
        if "shape" in node:
            shape = document.direct(read_shape(base64.b64decode(node["shape"], validate=True)))
            made = document.Part(shape, node["name"], tuple(node["color"]) if node["color"] else None,
                                 {int(k): tuple(v) for k, v in node["face_colors"].items()},
                                 key=document.geometry_key(shape), material=document.material_from_json(node["material"]),
                                 face_materials={int(k): document.material_from_json(v) for k, v in node["face_materials"].items()})
            parts.append(made)
        else:
            made = document.Assembly(node["name"], [document.Component(
                nodes[c["prototype"]], _located(np.array(c["matrix"])), c["name"],
                tuple(c["color"]) if c["color"] else None) for c in node["components"]])
        nodes.append(made)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    tree = document.Tree(nodes[payload["root"]], nodes, parts, ("shape", digest), _restore(payload["mechanism"]))
    document.merge(tree)
    report = {"ok": True, "model": f"<shape>::{name}", "parts": len(tree.parts), "named": 0,
              "timings": {"collect": round(time.perf_counter() - started, 4)}}
    if tree.mechanism and tree.mechanism.warnings:
        report["kinematics"] = {"warnings": list(tree.mechanism.warnings)}
    building._write(model, model.plan(), tree, tree.signature, report, log)
    # An address reused after a source build cannot retain its previous feature provenance.
    building._BUILT.pop(os.path.normcase(model.address), None)
    for kind, path in model.targets():
        if kind == "xbf":
            building._BUILT.pop(os.path.normcase(str(path)), None)
    report["timings"]["total"] = round(time.perf_counter() - started, 4)
    return [report]
