"""Write the fixture's selector tables: ``components/<cid>.selectors.json``.

The harness serves these as cadgen's part store does (each part record's
``selectors``, read at ``/__tess_cache/<part>.selectors.json``): one table per component, as
``cadgen._internal.selector_table`` builds it from the part's exact BREP and
its topology facts. The two parts are rebuilt here from ``hinge_block.py``'s
shapes; a part whose edges a newer build orders differently is written in the
committed table's ordinals, each edge matched by its token as ``make_meshes.py``
matches them, so the tables name the committed meshes' edges. The tokens
themselves are the geometry's and need no matching: a face's token is the same
whatever ordinal a build gives it.

Run from the repository root with the repo's Python, after ``make_meshes.py``
when the parts are rebuilt, or whenever the table's schema or scheme moves:

    .venv/bin/python packages/ui/src/renderers/step/__fixtures__/step/source/make_selectors.py
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1]


def _remap_edges(table: dict, mapping: dict[int, int]) -> dict:
    """The table with its edge rows in the committed ordinals (``mapping``: fresh -> committed)."""
    columns = table["tables"]["edgeColumns"]
    at = {name: columns.index(name) for name in ("ordinal", "vertexStart", "vertexCount", "chain")}
    relations = table["relations"]
    rows = sorted(table["edges"], key=lambda row: mapping[row[at["ordinal"]]])
    fresh_row_of = {row[at["ordinal"]] - 1: index for index, row in enumerate(rows)}
    edge_vertex_rows: list[int] = []
    chains: dict[int, int] = {}
    for row in rows:
        ordinal = mapping[row[at["ordinal"]]]
        start, count = row[at["vertexStart"]], row[at["vertexCount"]]
        row[at["vertexStart"]] = len(edge_vertex_rows)
        edge_vertex_rows.extend(relations["edgeVertexRows"][start:start + count])
        row[at["ordinal"]] = ordinal
        if row[at["chain"]] is not None:
            row[at["chain"]] = chains.setdefault(row[at["chain"]], len(chains) + 1)
    relations["edgeVertexRows"] = edge_vertex_rows
    relations["faceEdgeRows"] = [fresh_row_of[row] for row in relations["faceEdgeRows"]]
    vertex_columns = table["tables"]["vertexColumns"]
    v_start, v_count = vertex_columns.index("edgeStart"), vertex_columns.index("edgeCount")
    remapped = []
    for vertex in table["vertices"]:
        start, count = vertex[v_start], vertex[v_count]
        vertex[v_start] = len(remapped)
        remapped.extend(sorted(fresh_row_of[row] for row in relations["vertexEdgeRows"][start:start + count]))
    relations["vertexEdgeRows"] = remapped
    table["edges"] = rows
    return table


def main() -> None:
    from cadgen._internal.component_package import decode_display_shape, prepare_geometry_component
    from cadgen._internal.geometry_fingerprint import geometry_identity
    from cadgen._internal.selector_table import build_selector_table, read_selector_table, selector_table_bytes
    from cadgen._internal.topology_extract import extract_topology_facts

    from make_meshes import _match, committed_tokens, shapes

    view = json.loads((FIXTURE / "assembly.json").read_text(encoding="utf-8"))
    names = {occurrence["component"]: occurrence["name"] for occurrence in view["occurrences"]}
    built = shapes()
    for cid in view["components"]:
        committed = committed_tokens(cid)
        prepared = prepare_geometry_component(built[names[cid]])
        topods = decode_display_shape(prepared["entry"], prepared["payload"]).wrapped
        fresh = extract_topology_facts(topods)
        identity = geometry_identity(topods)
        faces = _match(committed["faces"], identity["faces"])
        if any(fresh_ord != ordinal for fresh_ord, ordinal in faces.items()):
            raise SystemExit(f"{cid}: face order changed; this generator only re-maps edges")
        edges = _match(committed["edges"], identity["edges"])
        table = _remap_edges(build_selector_table(topods, fresh), edges)
        payload = selector_table_bytes(table)
        read_selector_table(payload)
        (FIXTURE / "components" / f"{cid}.selectors.json").write_bytes(payload + b"\n")
        print(cid, names[cid], len(payload), "bytes", table["stats"])


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
