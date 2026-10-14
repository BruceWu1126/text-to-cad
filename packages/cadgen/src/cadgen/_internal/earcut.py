"""Polygon triangulation: mapbox/earcut 3.0.2, the copy three.js ships (``three/src/extras/lib/earcut.js``),
ported line for line so a face's cells triangulate exactly as the earlier tessellator triangulated them.

``earcut(data, hole_indices)`` takes flat 2D coordinates and the start (in points) of each hole and
returns flat triangle indices; ``triangulate_shape(contour, holes)`` is three.js's
``ShapeUtils.triangulateShape``: point lists in, ``[a, b, c]`` triangles over the contour's points then
the holes' out, a closing repeat of a ring's first point dropped first (in place, as there).
"""

from __future__ import annotations

import math


class _Node:
    __slots__ = ("i", "x", "y", "prev", "next", "z", "prev_z", "next_z", "steiner")

    def __init__(self, i: int, x: float, y: float) -> None:
        self.i = i
        self.x = x
        self.y = y
        self.prev: _Node | None = None
        self.next: _Node | None = None
        self.z = 0
        self.prev_z: _Node | None = None
        self.next_z: _Node | None = None
        self.steiner = False


def triangulate_shape(contour: list, holes: list) -> list[list[int]]:
    """three.js ``ShapeUtils.triangulateShape``: each ring a list of ``[x, y]``."""
    _remove_dup_end_points(contour)
    vertices: list[float] = []
    for x, y in contour:
        vertices.append(x)
        vertices.append(y)
    hole_indices = []
    hole_index = len(contour)
    for hole in holes:
        _remove_dup_end_points(hole)
    for hole in holes:
        hole_indices.append(hole_index)
        hole_index += len(hole)
        for x, y in hole:
            vertices.append(x)
            vertices.append(y)
    triangles = earcut(vertices, hole_indices)
    return [triangles[k:k + 3] for k in range(0, len(triangles), 3)]


def _remove_dup_end_points(points: list) -> None:
    if len(points) > 2 and points[-1][0] == points[0][0] and points[-1][1] == points[0][1]:
        points.pop()


def earcut(data: list[float], hole_indices: list[int] | None = None, dim: int = 2) -> list[int]:
    has_holes = bool(hole_indices)
    outer_len = hole_indices[0] * dim if has_holes else len(data)
    outer_node = _linked_list(data, 0, outer_len, dim, True)
    triangles: list[int] = []
    if outer_node is None or outer_node.next is outer_node.prev:
        return triangles
    min_x = min_y = inv_size = 0.0
    if has_holes:
        outer_node = _eliminate_holes(data, hole_indices, outer_node, dim)
    # If the shape is not too simple, a z-order curve hash is used later: the polygon's box.
    if len(data) > 80 * dim:
        min_x = data[0]
        min_y = data[1]
        max_x = min_x
        max_y = min_y
        for i in range(dim, outer_len, dim):
            x = data[i]
            y = data[i + 1]
            if x < min_x:
                min_x = x
            if y < min_y:
                min_y = y
            if x > max_x:
                max_x = x
            if y > max_y:
                max_y = y
        # min_x, min_y and inv_size later turn coordinates into integers for the z-order.
        inv_size = max(max_x - min_x, max_y - min_y)
        inv_size = 32767 / inv_size if inv_size != 0 else 0.0
    _earcut_linked(outer_node, triangles, dim, min_x, min_y, inv_size, 0)
    return triangles


def _linked_list(data: list[float], start: int, end: int, dim: int, clockwise: bool) -> _Node | None:
    """A circular doubly linked list of the ring's points in the given winding."""
    last = None
    if clockwise == (_signed_area(data, start, end, dim) > 0):
        for i in range(start, end, dim):
            last = _insert_node(i // dim, data[i], data[i + 1], last)
    else:
        for i in range(end - dim, start - 1, -dim):
            last = _insert_node(i // dim, data[i], data[i + 1], last)
    if last is not None and _equals(last, last.next):
        _remove_node(last)
        last = last.next
    return last


def _filter_points(start: _Node | None, end: _Node | None = None) -> _Node | None:
    """Drop collinear and repeated points."""
    if start is None:
        return start
    if end is None:
        end = start
    p = start
    while True:
        again = False
        if not p.steiner and (_equals(p, p.next) or _area(p.prev, p, p.next) == 0):
            _remove_node(p)
            p = end = p.prev
            if p is p.next:
                break
            again = True
        else:
            p = p.next
        if not (again or p is not end):
            break
    return end


def _earcut_linked(ear: _Node | None, triangles: list[int], dim: int, min_x: float, min_y: float,
                   inv_size: float, pass_: int) -> None:
    """The ear slicing loop over a polygon as a linked list."""
    if ear is None:
        return
    # Interlink the polygon's nodes in z-order.
    if not pass_ and inv_size:
        _index_curve(ear, min_x, min_y, inv_size)
    stop = ear
    # Iterate through the ears, slicing them one by one.
    while ear.prev is not ear.next:
        prev = ear.prev
        next_ = ear.next
        if _is_ear_hashed(ear, min_x, min_y, inv_size) if inv_size else _is_ear(ear):
            triangles.append(prev.i)
            triangles.append(ear.i)
            triangles.append(next_.i)
            _remove_node(ear)
            # Skipping the next vertex leaves fewer slivers.
            ear = next_.next
            stop = next_.next
            continue
        ear = next_
        # Through the whole remaining polygon without finding an ear:
        if ear is stop:
            if not pass_:
                # filter the points and slice again;
                _earcut_linked(_filter_points(ear), triangles, dim, min_x, min_y, inv_size, 1)
            elif pass_ == 1:
                # cure the small self-intersections locally;
                ear = _cure_local_intersections(_filter_points(ear), triangles)
                _earcut_linked(ear, triangles, dim, min_x, min_y, inv_size, 2)
            elif pass_ == 2:
                # as a last resort, split the remaining polygon in two.
                _split_earcut(ear, triangles, dim, min_x, min_y, inv_size)
            break


def _is_ear(ear: _Node) -> bool:
    """Whether the node makes a valid ear with its neighbours."""
    a = ear.prev
    b = ear
    c = ear.next
    if _area(a, b, c) >= 0:
        return False  # reflex: no ear
    ax, bx, cx, ay, by, cy = a.x, b.x, c.x, a.y, b.y, c.y
    # The triangle's box.
    x0 = min(ax, bx, cx)
    y0 = min(ay, by, cy)
    x1 = max(ax, bx, cx)
    y1 = max(ay, by, cy)
    # No other point may lie inside the ear.
    p = c.next
    while p is not a:
        if (x0 <= p.x <= x1 and y0 <= p.y <= y1
                and _point_in_triangle_except_first(ax, ay, bx, by, cx, cy, p.x, p.y)
                and _area(p.prev, p, p.next) >= 0):
            return False
        p = p.next
    return True


def _is_ear_hashed(ear: _Node, min_x: float, min_y: float, inv_size: float) -> bool:
    a = ear.prev
    b = ear
    c = ear.next
    if _area(a, b, c) >= 0:
        return False  # reflex: no ear
    ax, bx, cx, ay, by, cy = a.x, b.x, c.x, a.y, b.y, c.y
    x0 = min(ax, bx, cx)
    y0 = min(ay, by, cy)
    x1 = max(ax, bx, cx)
    y1 = max(ay, by, cy)
    # The z-order range of the triangle's box.
    min_z = _z_order(x0, y0, min_x, min_y, inv_size)
    max_z = _z_order(x1, y1, min_x, min_y, inv_size)
    p = ear.prev_z
    n = ear.next_z
    # Points inside the triangle, in both directions.
    while p is not None and p.z >= min_z and n is not None and n.z <= max_z:
        if (x0 <= p.x <= x1 and y0 <= p.y <= y1 and p is not a and p is not c
                and _point_in_triangle_except_first(ax, ay, bx, by, cx, cy, p.x, p.y)
                and _area(p.prev, p, p.next) >= 0):
            return False
        p = p.prev_z
        if (x0 <= n.x <= x1 and y0 <= n.y <= y1 and n is not a and n is not c
                and _point_in_triangle_except_first(ax, ay, bx, by, cx, cy, n.x, n.y)
                and _area(n.prev, n, n.next) >= 0):
            return False
        n = n.next_z
    # The rest in decreasing z-order,
    while p is not None and p.z >= min_z:
        if (x0 <= p.x <= x1 and y0 <= p.y <= y1 and p is not a and p is not c
                and _point_in_triangle_except_first(ax, ay, bx, by, cx, cy, p.x, p.y)
                and _area(p.prev, p, p.next) >= 0):
            return False
        p = p.prev_z
    # and in increasing z-order.
    while n is not None and n.z <= max_z:
        if (x0 <= n.x <= x1 and y0 <= n.y <= y1 and n is not a and n is not c
                and _point_in_triangle_except_first(ax, ay, bx, by, cx, cy, n.x, n.y)
                and _area(n.prev, n, n.next) >= 0):
            return False
        n = n.next_z
    return True


def _cure_local_intersections(start: _Node, triangles: list[int]) -> _Node | None:
    """Cure the polygon's small local self-intersections."""
    p = start
    while True:
        a = p.prev
        b = p.next.next
        if (not _equals(a, b) and _intersects(a, p, p.next, b) and _locally_inside(a, b)
                and _locally_inside(b, a)):
            triangles.append(a.i)
            triangles.append(p.i)
            triangles.append(b.i)
            # Remove the two nodes involved.
            _remove_node(p)
            _remove_node(p.next)
            p = start = b
        p = p.next
        if p is start:
            break
    return _filter_points(p)


def _split_earcut(start: _Node, triangles: list[int], dim: int, min_x: float, min_y: float,
                  inv_size: float) -> None:
    """Split the polygon in two along a valid diagonal and triangulate each."""
    a = start
    while True:
        b = a.next.next
        while b is not a.prev:
            if a.i != b.i and _is_valid_diagonal(a, b):
                # Split the polygon in two by the diagonal,
                c = _split_polygon(a, b)
                # filter the collinear points around the cuts
                a = _filter_points(a, a.next)
                c = _filter_points(c, c.next)
                # and run earcut on each half.
                _earcut_linked(a, triangles, dim, min_x, min_y, inv_size, 0)
                _earcut_linked(c, triangles, dim, min_x, min_y, inv_size, 0)
                return
            b = b.next
        a = a.next
        if a is start:
            break


def _eliminate_holes(data: list[float], hole_indices: list[int], outer_node: _Node, dim: int) -> _Node:
    """Link every hole into the outer ring: one ring with no holes."""
    queue = []
    count = len(hole_indices)
    for i in range(count):
        start = hole_indices[i] * dim
        end = hole_indices[i + 1] * dim if i < count - 1 else len(data)
        ring = _linked_list(data, start, end, dim, False)
        if ring is ring.next:
            ring.steiner = True
        queue.append(_get_leftmost(ring))
    queue.sort(key=_XYSlope)
    # Holes from left to right.
    for hole in queue:
        outer_node = _eliminate_hole(hole, outer_node)
    return outer_node


class _XYSlope:
    """``compareXYSlope`` as a sort key: by x, then y, then the slope to the next point, so the
    holes whose leftmost points meet at a vertex sort counter-clockwise and each bridges to the
    point they meet at."""

    __slots__ = ("node",)

    def __init__(self, node: _Node) -> None:
        self.node = node

    def __lt__(self, other: "_XYSlope") -> bool:
        return _compare_xy_slope(self.node, other.node) < 0


def _compare_xy_slope(a: _Node, b: _Node) -> float:
    result = a.x - b.x
    if result == 0:
        result = a.y - b.y
        if result == 0:
            a_slope = _divide(a.next.y - a.y, a.next.x - a.x)
            b_slope = _divide(b.next.y - b.y, b.next.x - b.x)
            result = a_slope - b_slope
    return result


def _divide(numerator: float, denominator: float) -> float:
    """JavaScript's division: by zero it is an infinity or NaN, not an error."""
    if denominator != 0:
        return numerator / denominator
    if numerator == 0 or math.isnan(numerator):
        return math.nan
    return math.copysign(math.inf, numerator) * math.copysign(1.0, denominator)


def _eliminate_hole(hole: _Node, outer_node: _Node) -> _Node:
    """Find a bridge between the hole and the outer ring and link it."""
    bridge = _find_hole_bridge(hole, outer_node)
    if bridge is None:
        return outer_node
    bridge_reverse = _split_polygon(bridge, hole)
    # Filter the collinear points around the cuts.
    _filter_points(bridge_reverse, bridge_reverse.next)
    return _filter_points(bridge, bridge.next)


def _find_hole_bridge(hole: _Node, outer_node: _Node) -> _Node | None:
    """David Eberly's bridge between a hole and the outer polygon."""
    p = outer_node
    hx = hole.x
    hy = hole.y
    qx = -math.inf
    m = None
    # A segment a ray from the hole's leftmost point to the left meets: its endpoint with the
    # lesser x is the candidate, unless they meet at a vertex, which is then taken.
    if _equals(hole, p):
        return p
    while True:
        if _equals(hole, p.next):
            return p.next
        elif hy <= p.y and hy >= p.next.y and p.next.y != p.y:
            x = p.x + (hy - p.y) * (p.next.x - p.x) / (p.next.y - p.y)
            if x <= hx and x > qx:
                qx = x
                m = p if p.x < p.next.x else p.next
                if x == hx:
                    return m  # the hole touches the outer segment: its leftmost end
        p = p.next
        if p is outer_node:
            break
    if m is None:
        return None
    # Points inside the triangle of the hole's point, the meeting point and the endpoint: with
    # none, the connection is valid; else the point at the least angle to the ray.
    stop = m
    mx = m.x
    my = m.y
    tan_min = math.inf
    p = m
    while True:
        if (hx >= p.x and p.x >= mx and hx != p.x
                and _point_in_triangle(hx if hy < my else qx, hy, mx, my, qx if hy < my else hx, hy, p.x, p.y)):
            tan = abs(hy - p.y) / (hx - p.x)  # tangential
            if _locally_inside(p, hole) and (
                    tan < tan_min or (tan == tan_min and (p.x > m.x or (p.x == m.x and _sector_contains_sector(m, p))))):
                m = p
                tan_min = tan
        p = p.next
        if p is stop:
            break
    return m


def _sector_contains_sector(m: _Node, p: _Node) -> bool:
    """Whether the sector at vertex m contains the sector at vertex p, in the same coordinates."""
    return _area(m.prev, m, p.prev) < 0 and _area(p.next, m, m.next) < 0


def _index_curve(start: _Node, min_x: float, min_y: float, inv_size: float) -> None:
    """Interlink the polygon's nodes in z-order."""
    p = start
    while True:
        if p.z == 0:
            p.z = _z_order(p.x, p.y, min_x, min_y, inv_size)
        p.prev_z = p.prev
        p.next_z = p.next
        p = p.next
        if p is start:
            break
    p.prev_z.next_z = None
    p.prev_z = None
    _sort_linked(p)


def _sort_linked(list_: _Node | None) -> _Node | None:
    """Simon Tatham's linked-list merge sort, by z."""
    in_size = 1
    while True:
        p = list_
        list_ = None
        tail = None
        num_merges = 0
        while p is not None:
            num_merges += 1
            q = p
            p_size = 0
            for _ in range(in_size):
                p_size += 1
                q = q.next_z
                if q is None:
                    break
            q_size = in_size
            while p_size > 0 or (q_size > 0 and q is not None):
                if p_size != 0 and (q_size == 0 or q is None or p.z <= q.z):
                    e = p
                    p = p.next_z
                    p_size -= 1
                else:
                    e = q
                    q = q.next_z
                    q_size -= 1
                if tail is not None:
                    tail.next_z = e
                else:
                    list_ = e
                e.prev_z = tail
                tail = e
            p = q
        tail.next_z = None
        in_size *= 2
        if num_merges <= 1:
            break
    return list_


def _z_order(x: float, y: float, min_x: float, min_y: float, inv_size: float) -> int:
    """The point's z-order, its coordinates taken into the 15-bit integer range."""
    x = _int32((x - min_x) * inv_size)
    y = _int32((y - min_y) * inv_size)
    x = (x | (x << 8)) & 0x00FF00FF
    x = (x | (x << 4)) & 0x0F0F0F0F
    x = (x | (x << 2)) & 0x33333333
    x = (x | (x << 1)) & 0x55555555
    y = (y | (y << 8)) & 0x00FF00FF
    y = (y | (y << 4)) & 0x0F0F0F0F
    y = (y | (y << 2)) & 0x33333333
    y = (y | (y << 1)) & 0x55555555
    return _int32(x | (y << 1))


def _int32(value: float) -> int:
    """JavaScript's ``value | 0``: toward zero, wrapped to a signed 32-bit integer."""
    if not math.isfinite(value):
        return 0
    integer = int(value) & 0xFFFFFFFF
    return integer - 0x100000000 if integer & 0x80000000 else integer


def _get_leftmost(start: _Node) -> _Node:
    """The ring's leftmost node."""
    p = start
    leftmost = start
    while True:
        if p.x < leftmost.x or (p.x == leftmost.x and p.y < leftmost.y):
            leftmost = p
        p = p.next
        if p is start:
            break
    return leftmost


def _point_in_triangle(ax, ay, bx, by, cx, cy, px, py) -> bool:
    """Whether the point lies in a convex triangle."""
    return ((cx - px) * (ay - py) >= (ax - px) * (cy - py)
            and (ax - px) * (by - py) >= (bx - px) * (ay - py)
            and (bx - px) * (cy - py) >= (cx - px) * (by - py))


def _point_in_triangle_except_first(ax, ay, bx, by, cx, cy, px, py) -> bool:
    """As above, false where the point is the triangle's first."""
    return not (ax == px and ay == py) and _point_in_triangle(ax, ay, bx, by, cx, cy, px, py)


def _is_valid_diagonal(a: _Node, b: _Node) -> bool:
    """Whether a diagonal between two nodes lies inside the polygon."""
    return (a.next.i != b.i and a.prev.i != b.i and not _intersects_polygon(a, b)  # crosses no edge
            and ((_locally_inside(a, b) and _locally_inside(b, a) and _middle_inside(a, b)  # locally visible
                  and bool(_area(a.prev, a, b.prev) or _area(a, b.prev, b)))  # no opposite-facing sectors
                 or (_equals(a, b) and _area(a.prev, a, a.next) > 0 and _area(b.prev, b, b.next) > 0)))  # zero length


def _area(p: _Node, q: _Node, r: _Node) -> float:
    """The triangle's signed area."""
    return (q.y - p.y) * (r.x - q.x) - (q.x - p.x) * (r.y - q.y)


def _equals(p1: _Node, p2: _Node) -> bool:
    return p1.x == p2.x and p1.y == p2.y


def _intersects(p1: _Node, q1: _Node, p2: _Node, q2: _Node) -> bool:
    """Whether two segments meet."""
    o1 = _sign(_area(p1, q1, p2))
    o2 = _sign(_area(p1, q1, q2))
    o3 = _sign(_area(p2, q2, p1))
    o4 = _sign(_area(p2, q2, q1))
    if o1 != o2 and o3 != o4:
        return True  # the general case
    if o1 == 0 and _on_segment(p1, p2, q1):
        return True  # p1, q1 and p2 collinear, p2 on p1q1
    if o2 == 0 and _on_segment(p1, q2, q1):
        return True  # p1, q1 and q2 collinear, q2 on p1q1
    if o3 == 0 and _on_segment(p2, p1, q2):
        return True  # p2, q2 and p1 collinear, p1 on p2q2
    if o4 == 0 and _on_segment(p2, q1, q2):
        return True  # p2, q2 and q1 collinear, q1 on p2q2
    return False


def _on_segment(p: _Node, q: _Node, r: _Node) -> bool:
    """For collinear p, q and r: whether q lies on segment pr."""
    return max(p.x, r.x) >= q.x >= min(p.x, r.x) and max(p.y, r.y) >= q.y >= min(p.y, r.y)


def _sign(num: float) -> int:
    return 1 if num > 0 else -1 if num < 0 else 0


def _intersects_polygon(a: _Node, b: _Node) -> bool:
    """Whether the diagonal crosses any of the polygon's segments."""
    p = a
    while True:
        if p.i != a.i and p.next.i != a.i and p.i != b.i and p.next.i != b.i and _intersects(p, p.next, a, b):
            return True
        p = p.next
        if p is a:
            break
    return False


def _locally_inside(a: _Node, b: _Node) -> bool:
    """Whether the diagonal lies inside the polygon where it leaves a."""
    if _area(a.prev, a, a.next) < 0:
        return _area(a, b, a.next) >= 0 and _area(a, a.prev, b) >= 0
    return _area(a, b, a.prev) < 0 or _area(a, a.next, b) < 0


def _middle_inside(a: _Node, b: _Node) -> bool:
    """Whether the diagonal's middle lies inside the polygon."""
    p = a
    inside = False
    px = (a.x + b.x) / 2
    py = (a.y + b.y) / 2
    while True:
        if (((p.y > py) != (p.next.y > py)) and p.next.y != p.y
                and (px < (p.next.x - p.x) * (py - p.y) / (p.next.y - p.y) + p.x)):
            inside = not inside
        p = p.next
        if p is a:
            break
    return inside


def _split_polygon(a: _Node, b: _Node) -> _Node:
    """Bridge two vertices: within one ring it splits the polygon in two; between the outer ring
    and a hole it merges them into one."""
    a2 = _Node(a.i, a.x, a.y)
    b2 = _Node(b.i, b.x, b.y)
    an = a.next
    bp = b.prev
    a.next = b
    b.prev = a
    a2.next = an
    an.prev = a2
    b2.next = a2
    a2.prev = b2
    bp.next = b2
    b2.prev = bp
    return b2


def _insert_node(i: int, x: float, y: float, last: _Node | None) -> _Node:
    """A node, linked after ``last`` (a ring of one without it)."""
    p = _Node(i, x, y)
    if last is None:
        p.prev = p
        p.next = p
    else:
        p.next = last.next
        p.prev = last
        last.next.prev = p
        last.next = p
    return p


def _remove_node(p: _Node) -> None:
    p.next.prev = p.prev
    p.prev.next = p.next
    if p.prev_z is not None:
        p.prev_z.next_z = p.next_z
    if p.next_z is not None:
        p.next_z.prev_z = p.prev_z


def _signed_area(data: list[float], start: int, end: int, dim: int) -> float:
    total = 0.0
    j = end - dim
    for i in range(start, end, dim):
        total += (data[j] - data[i]) * (data[i + 1] + data[j + 1])
        j = i
    return total
