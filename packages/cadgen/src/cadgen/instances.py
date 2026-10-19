"""Shared-instance assembly composition (public API).

``build123d.Shape.moved()`` deep-copies the entire shape graph per call
(``BRepBuilderAPI_Copy`` of every sub-shape), so placing N instances of a
heavy prototype costs N full copies at compose time plus N re-serializations
at content-hash time. ``compound_from_instances`` places each instance with an
OCCT location instead — O(1), sharing the prototype's underlying ``TShape`` —
and carries the assembly hierarchy (names, world placements) as an explicit
occurrence-metadata tree that the tree writer consumes directly.

The returned compound can be a model's whole ``@step`` shape or a child
nested anywhere inside a plain build123d compound tree: the packager splices
nested instance compounds into the tree walk, accumulating world locations; the
``id`` of each node here is provisional (its position), and the walk names every
node by its token (``cadgen._internal.tree_tokens``) once the hierarchy is whole.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:
    from build123d import Compound, Location

# No kernel import at module scope: `from cadgen import compound_from_instances` sits
# in a model's module body, which must stay featherweight so the @step gate and the
# warm-daemon handoff run before the ~2.5s build123d import (see cadgen.build123d).
# The annotations above are postponed strings; the one runtime use imports locally.


def compound_from_instances(
    name: str,
    instances: Sequence[tuple[Any, Location, str]],
) -> Compound:
    """Bake ``(prototype, location, name)`` placements into one labeled compound.

    Every placement shares the prototype's geometry (no per-instance copy);
    ``location`` is the instance's placement relative to the returned compound.
    Prototype labels/colors on child shapes are preserved in the assembly.json via
    the occurrence-metadata tree, which reads the UN-copied prototypes.
    """
    if not instances:
        raise RuntimeError(f"assembly {name!r} has no instances")
    from cadgen._internal.kernel_memo import composed

    instances = list(instances)

    def compose() -> Compound:
        from build123d import Compound
        from OCP.TopoDS import TopoDS_Builder, TopoDS_Compound

        builder = TopoDS_Builder()
        occt_compound = TopoDS_Compound()
        builder.MakeCompound(occt_compound)
        for prototype, location, _instance_name in instances:
            builder.Add(occt_compound, prototype.wrapped.Moved(location.wrapped))
        return Compound(occt_compound, label=name)

    def tree() -> dict[str, Any]:
        # Occurrence-metadata tree consumed by the tree writer (see
        # cadgen.store.build.build_tree_from_compound); a plain
        # build123d compound has no such attribute and falls back to the child walk.
        return {"id": "o1", "name": name, "children": [
            _occurrence_subtree(prototype, location, f"o1.{index}", str(instance_name))
            for index, (prototype, location, instance_name) in enumerate(instances, start=1)
        ]}

    # Inside a model run the composition is a kernel-memo operation: a later run's call
    # with the same prototypes, placements and names is handed the compound the earlier
    # one made, and its tree is built again from this call's instances.
    return composed(name, instances, compose, tree)


def _occurrence_subtree(node: Any, parent_world_loc: Location, path: str, name: str) -> dict[str, Any]:
    """One occurrence-tree node, mirroring the packager's compound walk: leaves
    carry the local (un-copied) shape plus accumulated world location; a
    multi-part prototype becomes a subassembly grouping its leaves."""
    node_loc = getattr(node, "location", None)
    world_loc = (parent_world_loc * node_loc) if node_loc is not None else parent_world_loc
    nested = getattr(node, "__dict__", {}).get("_occurrence_tree")
    if nested is not None:
        # A prototype that is itself a composition: its occurrences, each placed by
        # this instance's placement, as the packager splices them (``store.build``).
        return {"id": path, "name": name, "leaf": False, "children": [
            _placed_subtree(child, world_loc, f"{path}.{index}")
            for index, child in enumerate(nested["children"], start=1)
        ]}
    child_shapes = list(getattr(node, "children", []) or [])
    if not child_shapes:
        return {
            "id": path,
            "name": name,
            "leaf": True,
            "shape": node,
            "world_loc": world_loc,
            "children": [],
        }
    children = [
        _occurrence_subtree(
            child,
            world_loc,
            f"{path}.{index}",
            str(getattr(child, "label", "") or ("group" if getattr(child, "children", None) else "part")),
        )
        for index, child in enumerate(child_shapes, start=1)
    ]
    return {"id": path, "name": name, "leaf": False, "children": children}


def _placed_subtree(node: dict[str, Any], placement: Location, path: str) -> dict[str, Any]:
    """``node``, an occurrence-tree node of a composition used as a prototype, placed by
    ``placement``: its leaves' world locations composed with it, every node renumbered
    under ``path``, the shapes shared."""
    if node.get("leaf"):
        return dict(node, id=path, world_loc=placement * node["world_loc"])
    return dict(node, id=path, children=[
        _placed_subtree(child, placement, f"{path}.{index}")
        for index, child in enumerate(node["children"], start=1)
    ])
