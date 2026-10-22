"""The radial's two exploded clips on its joints: `explode` and `exploded-running`.

The original played them as JavaScript that translated parts by label. Their data is
`explode_plan.json`, kept verbatim from the original's generated `lib/anim_js.py`:

- `explode`, the teardown `lib/explodeplan.py` planned: 1,360 units (a part, or parts
  that leave together), each with a track of smoothstep-eased straight moves
  `[t0, t1, dx, dy, dz]`, a fastener's track its own moves after its host's (`[host
  track, moves]`); every part is in one unit (`groups`: `[track, label, ...]`). It runs
  24 s from the rest pose (crank angle 0), nothing turning.
- `exploded-running`, the layout `lib/explodedrun.py` made: the running cycle with each
  group of parts at a constant offset (`offsets`, `groups`), some parts hidden.

Here both are joint values. `Plan` gives every unit a link: a part, its unit's other
parts fastened to it, on a prismatic joint (`x_<part>`) along its own move, held by its
host's link -- so a fastener rides its host, as it did -- or by the root; links that
move alike share one. The running groups keep their joints (`lib/mechanism.py`), each
held where its translations can be exact: a free joint whose part leaves along its axis
leaves on it (a piston up its bore; the master rod, on a cylindrical joint, off the
crankpin). The gear train moves through `drive` alone and cannot slide, so its members
leave on links of their own: four systems' compounds slide on the root (`SLIDES`), and
carriers -- parts round about a member's axis, on slides of their own -- hold the
others (`CARRIERS`), as they hold each tappet, valve, rocker and pushrod.

A link's slide is the old offset projected on its line: exact wherever the unit's
moves, relative to the link it rides, lie on one line. Where they do not, the link
makes its own moves along their straight line from rest to their end, with the old
timing, and rides its holder for the rest. `Plan.report()` lists every link off the old
clips by more than `TOL`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from lib import kin
from lib import mechanism as M

PLAN = Path(__file__).with_name("explode_plan.json")
TOL = 0.5          # mm: an offset this near the old one is the old one
MARGIN = 1.0       # mm: a slide's range past the values it takes


def load() -> dict:
    return json.loads(PLAN.read_text())


def ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


def _unit(v) -> np.ndarray | None:
    n = float(np.linalg.norm(v))
    return None if n < 1e-9 else np.asarray(v, float) / n


def _at(windows, t: float) -> np.ndarray:
    found = np.zeros(3)
    for t0, t1, v in windows:
        found = found + ease((t - t0) / (t1 - t0)) * v
    return found


def _scalar_at(amounts, t: float) -> float:
    return sum(a * ease((t - t0) / (t1 - t0)) for t0, t1, a in amounts)


def _times(*lists) -> list[float]:
    found = {0.0}
    for windows in lists:
        for t0, t1, *_rest in windows:
            for f in (0.0, 0.25, 0.5, 0.75, 1.0):
                found.add(t0 + (t1 - t0) * f)
    return sorted(found)


def _gap(target, achieved) -> float:
    """The farthest the achieved explode strays from the old, in mm."""
    return max(float(np.linalg.norm(_at(target, t) - _at(achieved, t))) for t in _times(target, achieved))


class Node:
    """A link of the exploded mechanism: `lead` carries the joint `name` that moves it on
    `holder`'s link, `members` are fastened to the lead."""

    def __init__(self, name, holder, lead, members, kind, *, group=None, point=(0.0, 0.0, 0.0), axis=None,
                 rotates=False):
        self.name, self.holder, self.lead, self.members, self.kind = name, holder, lead, list(members), kind
        self.group = group                    # the running group whose joint this is, if any
        self.point = tuple(float(c) for c in point)
        self.axis = None if axis is None else np.asarray(axis, float)
        self.rotates = rotates or bool(holder and holder.rotates)
        self.direction = None                 # the slide's line (world, as built)
        self.amounts: list[tuple[float, float, float]] = []
        self.x = 0.0                          # the slide in exploded-running
        self.A: list = [] if holder is None else list(holder.A)
        self.X = np.zeros(3) if holder is None else holder.X.copy()
        self.err_e = self.err_x = 0.0
        self.note = ""
        self.compound = False                 # the lead is a system's compound

    @property
    def slides(self) -> bool:
        return bool(self.amounts) or abs(self.x) > 1e-9

    def fit(self, target, xr, direction=None, own=None) -> "Node":
        """Slide so the lead follows `target` (explode windows) and stands at `xr` in
        exploded-running (None: hidden): along `direction` where given, each instant the
        old offset's projection on it; else along the line from rest to the old end
        relative to the holder, exact where the moves relative to the holder lie on it, and
        where they do not, the lead makes its `own` moves along their line from rest to
        their end and rides the holder for the rest."""
        holder = self.holder
        rel = list(target) + [(t0, t1, -v) for t0, t1, v in holder.A]

        def slide(d, windows):
            summed: dict[tuple, float] = {}
            for t0, t1, v in windows if d is not None else ():
                summed[(t0, t1)] = summed.get((t0, t1), 0.0) + float(v @ d)
            amounts = [(t0, t1, a) for (t0, t1), a in summed.items() if abs(a) > 1e-6]
            achieved = list(holder.A) + [(t0, t1, d * a) for t0, t1, a in amounts]
            return d, amounts, achieved, _gap(target, achieved)

        if direction is not None:
            found = slide(_unit(direction), rel)
        else:
            d = _unit(sum((v for _t0, _t1, v in rel), np.zeros(3)))
            if d is None:
                d = _unit(next((v for _t0, _t1, v in rel if np.linalg.norm(v) > 1e-9), np.zeros(3)))
            if d is None and xr is not None:
                d = _unit(xr - holder.X)
            found = slide(d, rel)
            heading = None if own is None else _unit(sum((v for _t0, _t1, v in own), np.zeros(3)))
            if found[3] > TOL and heading is not None:
                found = slide(heading, own)
        self.direction, self.amounts, self.A, self.err_e = found
        d = self.direction
        if xr is None or d is None:
            self.x, self.err_x = 0.0, 0.0 if xr is None else float(np.linalg.norm(xr - holder.X))
        else:
            rel_x = xr - holder.X
            self.x = float(rel_x @ d)
            self.err_x = float(np.linalg.norm(rel_x - d * self.x))
        if holder.rotates and abs(self.x) > 1e-9:
            off = float(np.linalg.norm(np.cross(d, holder.spin_axis())))
            self.err_x = max(self.err_x, off * abs(self.x))
        self.X = holder.X + (np.zeros(3) if d is None else d * self.x)
        return self

    def spin_axis(self) -> np.ndarray:
        node = self
        while node is not None:
            if node.kind in ("revolute", "cylindrical"):
                return node.axis
            node = node.holder
        return np.zeros(3)

    @property
    def error(self) -> float:
        return max(self.err_e, self.err_x)


def _safe(label: str) -> str:
    return label.replace(":", "_")


# The gear train turns only through `drive`, so a member the clips move leaves on a link of
# its own: its system's compound, which slides on the root (`x_<system>`), as the member
# named here moved.
SLIDES = {"crankshaft": "crank:front_crank", "cam": "camring:ring", "reduction": "propshaft:carrier",
          "blower": "impeller:wheel"}
# Parts that carry a moving part's joint, on a slide of their own, and so no longer move
# with it: each is round about the joint's axis (a bearing, a bushing), or (the tappet's
# axle) small.
CARRIERS = {
    **{f"rocker{k}{v}:bearing": ("rocker", (k, v)) for k in range(1, 10) for v in kin.VALVES},
    **{f"tappet{k}{v}:axle": ("pushrod", (k, v)) for k in range(1, 10) for v in kin.VALVES},
    **{f"planet{j}:bearing": ("planet", (j,)) for j in range(1, 7)},
    **{f"blowergear{j}:bushing": ("blowergear", (j,)) for j in range(1, 4)},
}
SHAFT = re.compile(r"valvetrain:rocker_shaft_(\d)[IE]")
GUIDE = re.compile(r"cam:tappet_guide_\d[IE]")
ORDER = ("crank", "master", "artrod", "piston", "camring", "camidler", "tappet", "roller", "propshaft", "planet",
         "impeller", "blowergear", "rocker", "valve", "pushrod")


class Plan:
    """Every link of the exploded mechanism, holders first, and the two clips' values."""

    def __init__(self, order: list[str], system: dict[str, str]):
        data = load()
        self.duration = float(data["explode"]["duration"])
        tracks = data["explode"]["tracks"]
        track_of = {label: g[0] for g in data["explode"]["groups"] for label in g[1:]}
        missing = [label for label in order if label not in track_of]
        if missing:
            raise ValueError(f"explode_plan.json has no unit for {len(missing)} parts, e.g. {missing[:5]}")
        own = {i: [(float(a), float(b), np.array(d, float)) for a, b, *d in moves]
               for i, (_parent, moves) in enumerate(tracks)}
        full: dict[int, list] = {}

        def track(i: int) -> list:
            if i not in full:
                parent = tracks[i][0]
                full[i] = (track(parent) if parent >= 0 else []) + own[i]
            return full[i]

        running = data["exploded_running"]
        offset = {label: np.array(running["offsets"][g[0]], float) for g in running["groups"] for label in g[1:]}
        hidden = set(running["hidden"])
        self.hidden = sorted(hidden & set(order))

        def xr(label: str):
            return None if label in hidden else offset.get(label, np.zeros(3))

        units: dict[int, list[str]] = {}
        for label in order:
            units.setdefault(track_of[label], []).append(label)
        group_of: dict[str, tuple] = {}
        groups: dict[tuple, list[str]] = {}
        for label in order:
            found = M.classify(label)
            if found is not None and found[0] != "spring":
                group_of[label] = found
                groups.setdefault(found, []).append(label)

        self.nodes: list[Node] = []
        node_of: dict[str, Node] = {}          # part (or compound) label -> its link
        root = Node(None, None, None, [], "root")
        self.root = root

        def add(node: Node) -> Node:
            self.nodes.append(node)
            for label in [node.lead, *node.members]:
                node_of[label] = node
            return node

        def link(name, holder, lead, members, kind="prismatic", **given) -> Node:
            return Node(name, holder, lead, members, kind, **given)

        compound: dict[str, Node] = {}
        for name, member in SLIDES.items():
            i = track_of[member]
            node = link(f"x_{name}", root, name, []).fit(track(i), xr(member), own=own[i])
            node.compound = True
            compound[name] = add(node)

        def home(label: str) -> list[Node]:
            return [root] + ([compound[system[label]]] if system[label] in compound else [])

        def static_link(i: int, statics: list[str], candidates: list[Node], host: Node | None = None) -> Node:
            """The unit's static parts on its host where they keep to it exactly (a fastener
            rides its host, as it did), else on the candidate that keeps them nearest the
            old clips (the first of equals)."""
            name = f"x_{_safe(statics[0])}"
            if host is not None:
                node = link(name, host, statics[0], statics[1:]).fit(track(i), xr(statics[0]), own=own[i])
                kept = _gap(list(host.A) + own[i], node.A)
                if kept <= TOL and node.err_x <= TOL:
                    return node
                candidates = [host, *candidates]
            tried = [link(name, holder, statics[0], statics[1:]).fit(track(i), xr(statics[0]), own=own[i])
                     for holder in candidates]
            return min(tried, key=lambda node: (round(node.error, 3), tried.index(node)))

        # Static units: one link each, its static parts fastened to its first; the units
        # with no host first, then the fasteners on their hosts.
        static_units = []
        for i, labels in units.items():
            # a unit's parts in different exploded-running groups are links of their own
            # (the parts it hid go with the first)
            parts: dict = {}
            for label in labels:
                if label not in group_of:
                    held = xr(label)
                    key = None if held is None else tuple(np.round(held, 6))
                    parts.setdefault(key, []).append(label)
            hid = parts.pop(None, [])
            subsets = list(parts.values()) or [[]]
            subsets[0] += hid
            static_units += [(i, statics) for statics in subsets if statics]
        for i, statics in static_units:
            if tracks[i][0] >= 0:
                continue
            lead = statics[0]
            if SHAFT.fullmatch(lead):
                # a rocker shaft stands in its rocker's bosses in exploded-running: it rides
                # its cylinder, which stands there, after its own move
                node = static_link(i, statics, [node_of[f"heads:head_{SHAFT.fullmatch(lead).group(1)}"]])
                node.note = "rides its cylinder after its own move"
            elif GUIDE.fullmatch(lead):
                # a tappet guide carries its tappet, which the cam ring lifts in
                # exploded-running: it rides the cam section, after its own move
                node = static_link(i, statics, [compound["cam"]])
                node.note = "rides the cam section after its own move"
            else:
                node = static_link(i, statics, home(lead))
            add(node)
        for i, statics in static_units:
            if tracks[i][0] < 0:
                continue
            host = next((node_of[label] for label in units[tracks[i][0]] if label in node_of
                         and node_of[label].group is None), None)
            add(static_link(i, statics, home(statics[0]), host))

        def carrier(label: str, holder: Node) -> Node:
            i = track_of[label]
            node = link(f"x_{_safe(label)}", holder, label, [], group=CARRIERS[label]).fit(track(i), xr(label),
                                                                                          own=own[i])
            node.note = "carries a moving part's joint"
            return add(node)

        # The running groups: each keeps its joint, held where it rides now.
        lead_node: dict[tuple, Node] = {}
        for kind_ in ORDER:
            for key, labels in groups.items():
                kind, args = key
                if kind != kind_:
                    continue
                labels = [label for label in labels if label not in CARRIERS]
                lead = labels[0]
                joint, point, direction = M._axis_of(kind, args)
                name = M.joint_name(kind, args)
                kv = "".join(str(a) for a in args)
                if kind == "rocker":
                    holder = carrier(f"rocker{kv}:bearing", root)
                elif kind == "blowergear":
                    holder = carrier(f"blowergear{kv}:bushing", compound["blower"])
                elif kind == "planet":
                    holder = carrier(f"planet{kv}:bearing", lead_node[("propshaft", ())])
                elif kind == "pushrod":
                    holder = node_of[f"tappet{kv}:axle"]
                elif kind == "tappet":
                    holder = node_of[f"cam:tappet_guide_{kv}"]
                elif kind == "valve":
                    holder = node_of[f"heads:guide_{kv}"]
                elif kind in ("crank", "camring", "camidler", "propshaft", "impeller"):
                    holder = compound[system[lead]]
                else:
                    parent = M._parent_of(kind, args)
                    holder = root if parent is None else lead_node[parent]
                same = [label for label in labels if track_of[label] == track_of[lead]]
                node = link(name, holder, lead, same[1:], "linear" if joint == "linear" else "revolute",
                            group=key, point=point, axis=direction, rotates=joint != "linear")
                target = track(track_of[lead])
                if name in M.DRIVE or kind == "pushrod":
                    # a member of the gear train turns only through `drive`, and a pushrod
                    # turns on its axle: neither slides, so what is left is the error
                    node.A, node.X = list(holder.A), holder.X.copy()
                    node.err_e = _gap(target, node.A)
                    off = xr(lead)
                    node.err_x = 0.0 if off is None else float(np.linalg.norm(off - holder.X))
                else:
                    node.fit(target, xr(lead), direction)
                    if node.kind == "revolute" and node.slides:
                        node.kind = "cylindrical"
                lead_node[key] = add(node)
                # the group's parts that leave on their own: links on the lead, or on their host's
                subs: dict[int, Node] = {}
                for label in sorted(labels, key=lambda label: tracks[track_of[label]][0] >= 0):
                    i = track_of[label]
                    if i == track_of[lead] or label in node_of:
                        continue
                    if i in subs:
                        subs[i].members.append(label)
                        node_of[label] = subs[i]
                        continue
                    sub = link(f"x_{_safe(label)}", subs.get(tracks[i][0], node), label, [], group=key)
                    subs[i] = add(sub.fit(track(i), xr(label), own=own[i]))
                if kind == "tappet":
                    # the axle carries the pushrod, on the line from the cam section's
                    # exploded-running offset to its cylinder's
                    pushrod = f"pushrod{kv}:rod"
                    axle = link(f"x_tappet{kv}_axle", node, f"tappet{kv}:axle", [], group=CARRIERS[f"tappet{kv}:axle"])
                    axle.fit(track(track_of[pushrod]), xr(pushrod), xr(pushrod) - node.X)
                    axle.note = "carries the pushrod"
                    add(axle)
        unplaced = [label for label in order if label not in node_of]
        if unplaced:
            raise ValueError(f"no link for {len(unplaced)} parts, e.g. {unplaced[:5]}")
        self._merge()
        self.node_of = {label: node for node in self.nodes for label in (node.lead, *node.members)}

    def _merge(self) -> None:
        """Static links that move as one (same holder, line and slides) become one link."""
        kept: dict[tuple, Node] = {}
        merged: dict[int, Node] = {}
        nodes = []
        for node in self.nodes:
            if node.holder is not None and id(node.holder) in merged:
                node.holder = merged[id(node.holder)]
            if node.kind == "prismatic" and node.direction is None:
                # it never moves on its holder: fastened to it, or (on the root) a slide that stays
                if node.holder.kind == "prismatic" and not node.holder.compound and node.holder.lead not in CARRIERS:
                    node.holder.members += [node.lead, *node.members]
                    merged[id(node)] = node.holder
                    continue
                node.direction = np.array([0.0, 0.0, 1.0])
            if node.kind != "prismatic" or node.compound or node.lead in CARRIERS:
                nodes.append(node)
                continue
            key = (id(node.holder), node.group, tuple(np.round(node.direction, 9)) if node.direction is not None else None,
                   tuple((round(a, 6), round(b, 6), round(c, 6)) for a, b, c in node.amounts), round(node.x, 6))
            first = kept.get(key)
            if first is None:
                kept[key] = node
                nodes.append(node)
                continue
            first.members += [node.lead, *node.members]
            first.err_e, first.err_x = max(first.err_e, node.err_e), max(first.err_x, node.err_x)
            merged[id(node)] = first
        self.nodes = nodes

    # --- values -------------------------------------------------------------------------
    def joints(self) -> list[Node]:
        return list(self.nodes)

    def cylindrical(self) -> set[str]:
        return {node.name for node in self.nodes if node.kind == "cylindrical"}

    def explode_slides(self, t: float) -> dict[str, float]:
        """Each slide the teardown moves, at `t` s (mm along its joint)."""
        return {node.name: _scalar_at(node.amounts, t) for node in self.nodes if node.amounts}

    def running_slides(self) -> dict[str, float]:
        """Each slide exploded-running holds."""
        return {node.name: node.x for node in self.nodes if abs(node.x) > 1e-9}

    def values(self, turns: dict, slides: dict) -> dict:
        """Joint values: `turns` (the running joints, and `drive`) and `slides`; a
        cylindrical joint takes (turn, slide), a piston its stroke plus its slide."""
        found = dict(turns)
        for name in self.cylindrical():
            found[name] = (float(found.get(name, 0.0)), float(slides.get(name, 0.0)))
        for name, value in slides.items():
            if name in self.cylindrical():
                continue
            found[name] = float(found.get(name, 0.0)) + float(value)
        return found

    def slide_range(self, node: Node) -> tuple[float, float]:
        values = [0.0, node.x] + [_scalar_at(node.amounts, t) for t in _times(node.amounts)]
        return min(values) - MARGIN, max(values) + MARGIN

    def report(self) -> str:
        rows = self.joints()
        exact = [node for node in rows if node.error <= TOL]
        lines = [f"{len(rows)} links ({sum(1 for n in rows if n.group is None)} static, "
                 f"{sum(1 for n in rows if n.group is not None)} on the running groups), {len(exact)} within "
                 f"{TOL} mm of both clips"]
        for node in sorted(rows, key=lambda n: -n.error):
            if node.error <= TOL:
                break
            lines.append(f"  {node.name}: explode {node.err_e:.1f} mm, exploded-running {node.err_x:.1f} mm "
                         f"(on {node.holder.name or 'the root'}){' -- ' + node.note if node.note else ''}")
        return "\n".join(lines)
