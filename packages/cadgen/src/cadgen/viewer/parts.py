"""A runtime view's display data, read from the store: each component's part record
(``cadgen.store.parts``: its display mesh and selector table), which the build that
made the tree derived. A part the store lacks is derived by ONE build-pool job for the
request (``kind: display``); this server never derives.

Tokens are disposable subscribers to that job, never persistent store
entries or editing sessions. Every poll repeats and validates its immutable
inputs. Disconnect/cancellation detaches that subscriber, not another reader,
and never kills a derivation: the daemon finishes the one in hand into the store
and starts no other for a job nobody is subscribed to.
"""

from __future__ import annotations

from collections import OrderedDict
import json
import logging
import os
import threading
import time
import uuid

from cadgen.store import parts
from cadgen.store.objects import object_path
from cadgen.store.paths import store_root
from cadgen.store.trees import COMPONENT_OBJECTS, capture_tree

LOG = logging.getLogger("cadgen.viewer.parts")

MAX_COMPONENTS = 64
MAX_SUBSCRIBERS = 256
SUBSCRIBER_IDLE_SECONDS = 120

# A tree hash names its bytes, and those bytes name every component the tree pins
# with its entry, so a tree's component map cannot change. The viewer verifies it
# once per process (capture_tree, the complete closure) and keeps it, rather than
# checking the whole closure again on every request: the browser asks about each
# component separately, so that made a model's first open quadratic in its size.
# What can change is whether the objects are still there, since eviction and GC
# delete files. So every request stats the tree's object and the objects of each
# component it names, and a missing one sends the tree back through the complete
# verification, which fails as it always did. A component a request does not name
# is not looked at: a derivation verifies its whole closure before it writes.
PINNED_TREES = 8
PINNED_COMPONENTS = 100_000
_PINS: OrderedDict[tuple[str, str], "_Pin"] = OrderedDict()
_PINS_LOCK = threading.Lock()


def _is_digest(value) -> bool:
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


class _Pin:
    """One verified tree's component map (read-only) and where each object lies."""

    __slots__ = ("components", "paths", "tree_path")

    def __init__(self, tree: str, components: dict):
        self.components = components
        self.paths: dict[str, tuple[str, ...]] = {}
        for cid, entry in components.items():
            self.paths[cid] = tuple(str(object_path(entry[field])) for field in COMPONENT_OBJECTS
                                    if entry.get(field))
        self.tree_path = str(object_path(tree))

    def present(self, cids) -> bool:
        """Whether the tree's object and every object of ``cids`` are still on disk."""
        try:
            os.stat(self.tree_path)
            for cid in cids:
                for path in self.paths[cid]:
                    os.stat(path)
        except OSError:
            return False
        return True


def _pinned(tree: str, cids=()) -> _Pin:
    """``tree``'s verified component map, with the objects of ``cids`` present.

    A miss, or an object gone since the map was verified, verifies the complete
    closure again (``capture_tree``), raising for a missing or damaged one."""
    key = (str(store_root().resolve()), tree)
    with _PINS_LOCK:
        pin = _PINS.get(key)
        if pin is not None:
            _PINS.move_to_end(key)
    if pin is not None:
        if pin.present(cids):
            return pin
        with _PINS_LOCK:
            if _PINS.get(key) is pin:
                del _PINS[key]
    descriptor, _ = capture_tree(tree, retain_payloads=False)
    pin = _Pin(tree, descriptor["components"])
    with _PINS_LOCK:
        _PINS[key] = pin
        _PINS.move_to_end(key)
        total = sum(len(item.components) for item in _PINS.values())
        while len(_PINS) > 1 and (len(_PINS) > PINNED_TREES or total > PINNED_COMPONENTS):
            _old_key, old = _PINS.popitem(last=False)
            total -= len(old.components)
    return pin


def _request(body: bytes) -> tuple[dict, dict, dict, str | None]:
    if len(body) > 128 * 1024:
        raise ValueError("part request is too large")
    value = json.loads(body)
    fields = {"tree", "viewId", "producer", "components"}
    if type(value) is not dict or not fields <= set(value) or set(value) - fields - {"job"}:
        raise ValueError("part request requires tree, viewId, producer and components")
    producer = parts.producer_fields(value["producer"])
    tree = value["tree"]
    if not _is_digest(tree):
        raise ValueError("part request names no tree")
    pin = _pinned(tree)
    view_id = parts._view_id(tree, producer)
    if value["viewId"] != view_id:
        raise ValueError("part request mixes runtime views")
    components = value["components"]
    if type(components) is not list or not 0 < len(components) <= MAX_COMPONENTS:
        raise ValueError("part request must name between 1 and 64 components")
    selected = {}
    for item in components:
        if type(item) is not dict or set(item) != {"cid", "part"}:
            raise ValueError("invalid part component request")
        cid = item["cid"]
        entry = pin.components.get(cid) if type(cid) is str else None
        key = parts.part_key(entry, producer) if entry is not None else None
        if entry is None or cid in selected or item["part"] != key:
            raise ValueError("part request names an unpinned component or part")
        selected[cid] = key
    job = value.get("job")
    if job is not None and (type(job) is not str or len(job) != 32 or any(c not in "0123456789abcdef" for c in job)):
        raise ValueError("invalid part subscriber token")
    operation = {"kind": "display", "tree": value["tree"], "cids": sorted(selected), "producer": producer}
    # The named components' objects, still on disk; the map itself is read-only.
    _pinned(tree, selected)
    return {"viewId": view_id}, selected, operation, job


class PartSubscribers:
    def __init__(self):
        self._guard = threading.Lock()
        self._changed = threading.Condition(self._guard)
        self._jobs = {}
        self._reaper = None

    @staticmethod
    def _detach(future):
        future.detach()

    def cancel(self, token: str) -> None:
        with self._guard:
            job = self._jobs.pop(token, None)
            self._changed.notify_all()
        if job is not None:
            self._detach(job["future"])

    def _prune(self):
        now = time.monotonic()
        with self._guard:
            expired = [key for key, value in self._jobs.items() if now - value["touched"] > SUBSCRIBER_IDLE_SECONDS]
            jobs = [self._jobs.pop(key) for key in expired]
        for job in jobs:
            self._detach(job["future"])

    def _start_reaper_locked(self):
        if self._reaper is not None:
            return

        def reap():
            # A crashed tab cannot send cancellation. Deadline expiry must
            # release its subscription even when no subsequent HTTP call arrives.
            while True:
                with self._changed:
                    if not self._jobs:
                        self._reaper = None
                        return
                    deadline = min(job["touched"] for job in self._jobs.values()) + SUBSCRIBER_IDLE_SECONDS
                    self._changed.wait(max(.001, deadline - time.monotonic()))
                self._prune()

        self._reaper = threading.Thread(target=reap, name="cadgen-part-subscribers", daemon=True)
        self._reaper.start()

    def resolve(self, body: bytes) -> dict:
        from cadgen.daemon.artifacts import request_key, submit_artifact

        view, selected, operation, token = _request(body)
        self._prune()
        request_identity = (str(store_root().resolve()), request_key(operation))
        future = None
        if token is not None:
            with self._guard:
                job = self._jobs.get(token)
                if job is None or job["request"] != request_identity:
                    raise ValueError("part subscriber is expired, cancelled, or belongs to different inputs")
                job["touched"] = time.monotonic()
                self._changed.notify_all()
                future = job["future"]
        response = {"viewId": view["viewId"], "components": {}}

        def settle(cids) -> list[str]:
            """Answer each of ``cids`` the store holds the part of; the rest are missing."""
            missing = []
            for cid in cids:
                record = parts.probe(selected[cid])
                if record is not None:
                    # The part's record: what a probe of the mesh store answers, so the client
                    # reads the part's mesh, and later its selector table, without asking again.
                    response["components"][cid] = {"part": selected[cid], "state": "ready", "record": record}
                else:
                    missing.append(cid)
            return missing

        missing = settle(selected)
        if not missing:
            if token is not None:
                self.cancel(token)
            return response
        if future is None:
            with self._guard:
                if len(self._jobs) >= MAX_SUBSCRIBERS:
                    raise ValueError("part request capacity reached; retry after an active request finishes")
                # The one job for this request's missing components (its first answer's: a poll
                # names the same inputs). Each HTTP consumer owns one future; the daemon
                # coalesces the native work, so cancelling this token cannot detach another.
                future = submit_artifact({**operation, "cids": sorted(missing)}, store_root=store_root())
                token = uuid.uuid4().hex
                self._jobs[token] = {"future": future, "request": request_identity, "touched": time.monotonic()}
                self._start_reaper_locked()
                self._changed.notify_all()
        if future.done():
            try:
                future.result()
                error = "display derivation completed without its requested output"
            except Exception as exc:
                LOG.warning("display derivation failed for %s: %r", operation["tree"][:16], exc)
                error = str(exc) or type(exc).__name__
            # Recheck once after completion, including a completion that raced the
            # first lookup. A deleted result is then a failure. So is a failed job's,
            # for only the components it left missing: one component's failure is
            # raised once the others are stored (parts.derive), and they are ready.
            missing = settle(missing)
            for cid in missing:
                response["components"][cid] = {"part": selected[cid], "state": "failed", "error": error}
            self.cancel(token)
            if parts.producer_unavailable(error):
                from cadgen.daemon.artifacts import resolve_artifact
                from cadgen.store.view import descriptor_for_view

                replacement = resolve_artifact({"kind": "producer"})
                if parts.producer_key(replacement) != parts.producer_key(operation["producer"]):
                    from cadgen.store.view import note_replaced_producer

                    # The next view of any document hinting the refused producer starts under
                    # this one (and re-attests it), instead of a refused job per part per open.
                    note_replaced_producer(operation["producer"], replacement)
                    response["replacementView"] = descriptor_for_view(operation["tree"], producer=replacement)
                    for cid in missing:
                        response["components"][cid]["code"] = "producer-unavailable"
            return response
        response["job"] = token
        for cid in missing:
            response["components"][cid] = {"part": selected[cid], "state": "pending", "job": token}
        return response
