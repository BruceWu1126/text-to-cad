"""Write the fixture's parts: each component's ``part`` in ``assembly.json`` and
its display mesh, ``components/<cid>.glb``.

The harness serves these as cadgen's part store would: one GLB body per part,
at the display tolerance (``cadgen.tessellation_policy``), named by the part key
cadgen derives from the component and the view's producer
(``cadgen.store.parts``), which this script writes into ``assembly.json`` with the
view's id. The two
parts are rebuilt here from ``hinge_block.py``'s shapes and meshed by cadgen's own
producer (``cadgen._internal.tessellate``); a part whose edges a newer build orders
differently is written in the committed selector table's ordinals
(``components/<cid>.selectors.json``), matched by each edge's centre and length, so
every id the tests use holds.

Run from the repository root with the repo's Python:

    .venv/bin/python packages/ui/src/renderers/step/__fixtures__/step/source/make_meshes.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

FIXTURE = Path(__file__).resolve().parents[1]


def shapes():
    from cadgen import build123d as bd

    return {"base": bd.Box(20, 20, 10) - bd.Cylinder(3, 20), "arm": bd.Box(10, 8, 8)}


def committed_rows(cid: str) -> dict[str, list[dict]]:
    """The committed selector table's face and edge rows, each as its ordinal, centre,
    size and (an edge's) class: the ordinals every id the tests use is written in."""
    table = json.loads((FIXTURE / "components" / f"{cid}.selectors.json").read_text(encoding="utf-8"))

    def rows(name: str, columns: str) -> list[dict]:
        return [dict(zip(table["tables"][columns], row)) for row in table[name]]

    return {
        "faces": [{"ord": row["ordinal"], "center": row["center"], "area": row["area"]}
                  for row in rows("faces", "faceColumns")],
        "edges": [{"ord": row["ordinal"], "center": row["center"], "length": row["length"], "class": row["visibilityClass"]}
                  for row in rows("edges", "edgeColumns")],
    }


def _match(rows, fresh, key):
    """Each fresh row's ordinal -> the committed row it is, by centre and size."""
    mapping = {}
    for row in fresh:
        best = min(rows, key=lambda other: np.abs(np.subtract(other["center"], row["center"])).max()
                   + abs(other[key] - row[key]))
        mapping[row["ord"]] = best["ord"]
    if sorted(mapping.values()) != [row["ord"] for row in rows]:
        raise SystemExit("the rebuilt part's topology is not the committed one")
    return mapping


def _view(view: dict) -> dict:
    """The served view under its own producer: each component names its part."""
    from cadgen.store import parts

    producer = parts.producer_fields({name: view["producer"][name] for name in ("build123d", "ocp", "cadqueryOcp")})
    view["producer"] = {**producer, "producerKey": parts.producer_key(producer)}
    view["viewId"] = parts._view_id(view["tree"], producer)
    for component in view["components"].values():
        component["part"] = parts.part_key(component, producer)
    return view


def main() -> None:
    from cadgen._internal import tessellate
    from cadgen._internal.component_package import decode_display_shape, prepare_geometry_component
    from cadgen._internal.selector_table import build_selector_table
    from cadgen._internal.topology_extract import extract_topology_facts
    from cadgen.store.meshes import decode_payload, encode_payload
    from cadgen.tessellation_policy import DISPLAY_TESSELLATION

    chord, angle = DISPLAY_TESSELLATION["chordTolerance"], DISPLAY_TESSELLATION["angleTolerance"]

    view = _view(json.loads((FIXTURE / "assembly.json").read_text(encoding="utf-8")))
    (FIXTURE / "assembly.json").write_text(json.dumps(view), encoding="utf-8")
    names = {occurrence["component"]: occurrence["name"] for occurrence in view["occurrences"]}
    built = shapes()
    for cid, component in view["components"].items():
        committed = committed_rows(cid)
        prepared = prepare_geometry_component(built[names[cid]])
        live = decode_display_shape(prepared["entry"], prepared["payload"])
        fresh = extract_topology_facts(live.wrapped)
        faces = _match(committed["faces"], fresh["faces"], "area")
        if any(fresh_ord != ordinal for fresh_ord, ordinal in faces.items()):
            raise SystemExit(f"{cid}: face order changed; this generator only re-maps edges")
        edges = _match(committed["edges"], fresh["edges"], "length")
        classes = {row["ord"]: row["class"] for row in committed["edges"]}
        topods = decode_display_shape(prepared["entry"], prepared["payload"]).wrapped
        body = tessellate.mesh_component(topods, build_selector_table(topods, fresh), {},
                                         part=component["part"], chord=chord, angle=angle)
        meshed = decode_payload(body)
        polylines = [(int(ordinal), meshed.edge_points[start:start + count])
                     for ordinal, start, count, _class in meshed.edges.tolist()]
        remapped = sorted(((edges[ordinal], polyline) for ordinal, polyline in polylines), key=lambda item: item[0])
        body = encode_payload(
            part=component["part"], positions=meshed.positions, normals=meshed.normals,
            indices=meshed.indices.astype(np.uint32), face_ranges=meshed.face_ranges(),
            edges=[(ordinal, classes[ordinal], polyline) for ordinal, polyline in remapped],
            bounds=meshed.cad["bounds"],
        )
        (FIXTURE / "components" / f"{cid}.glb").write_bytes(body)
        print(cid, names[cid], len(body), "bytes")


if __name__ == "__main__":
    main()
