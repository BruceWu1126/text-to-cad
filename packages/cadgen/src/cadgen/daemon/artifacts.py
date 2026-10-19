"""Build-pool artifact jobs, independent of source models: this runtime's producer
(``kind: producer``); the display data of parts the store lacks, for a build's result or a
view (``kind: display``: one request, one job, one worker); one face's exact area and
centroid, measured on its component's stored BREP for a view or a mate's axis that asks
(``kind: measure``, ``store.parts.measure_face``); a model's STEP written from its stored
result and published as its saved file, then its declared meshes cut (``kind: export``,
``store.exports.run_export``); a model's declared meshes alone (``kind: mesh``,
``store.exports.run_meshes``). A job runs on a daemon worker, never in the process that
asks: the caller's own thread never imports the native producer. Requests contain
immutable pins -- a tree, a model's identity and the digest of the result it names, the
files to write and the bytes found there -- never a script or an instruction to run
arbitrary Python.
"""

from __future__ import annotations

import contextlib
import copy
from concurrent.futures import Future
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading

from cadgen.daemon import broker

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_CID = re.compile(r"[0-9a-f]{16}\Z")
_PRODUCER_FIELDS = {"build123d", "ocp", "cadqueryOcp"}


class ArtifactJobError(RuntimeError):
    """Artifact work failed; no unaccounted fallback has been attempted."""


class ArtifactDetached(ArtifactJobError):
    """This subscriber left; another subscriber may still need the operation."""


def _digest(value, name, pattern=_HASH):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"artifact {name} must be a lowercase {64 if pattern is _HASH else 16}-digit hex pin")
    return value


def _producer(value):
    if not isinstance(value, dict) or set(value) != _PRODUCER_FIELDS:
        raise ValueError("artifact producer requires exactly build123d, ocp, cadqueryOcp")
    result = {}
    for name in ("build123d", "ocp", "cadqueryOcp"):
        item = value[name]
        if not isinstance(item, str) or not item.strip() or item.lower() in {"unknown", "none"}:
            raise ValueError(f"artifact producer {name} must identify the actual loaded version")
        result[name] = item
    return result


def _path(value, name, suffixes=(".step", ".stp")):
    if not isinstance(value, str) or not value or not os.path.isabs(value):
        raise ValueError(f"artifact {name} must be an absolute path")
    if Path(value).suffix.lower() not in suffixes:
        raise ValueError(f"artifact {name} must name a {'/'.join(suffixes)} file")
    return str(Path(value))


def _optional_digest(value, name):
    return None if value is None else _digest(value, name)


def _model(value):
    """A model's identity (``store.index.model_ref``): its absolute script path, with
    ``::function``, or an absolute document path."""
    if not isinstance(value, str) or not value:
        raise ValueError("artifact model must be a model's identity")
    script = value.rpartition("::")[0] if "::" in value else value
    if not os.path.isabs(script):
        raise ValueError("artifact model must name its script by an absolute path")
    return value


def _tolerance(value, name):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"artifact {name} must be a positive finite number or null")
    return float(value)


def _display(request):
    if not isinstance(request, dict) or request.get("kind") != "display" \
            or set(request) != {"kind", "tree", "cids", "producer"}:
        raise ValueError("artifact display must have exactly kind, tree, cids and producer")
    cids = request["cids"]
    if not isinstance(cids, (list, tuple)) or not cids:
        raise ValueError("artifact cids must be a nonempty list")
    cids = [_digest(cid, "cid", _CID) for cid in cids]
    if len(cids) != len(set(cids)):
        raise ValueError("artifact cids must not contain duplicates")
    return {"kind": "display", "tree": _digest(request["tree"], "tree"), "cids": sorted(cids),
            "producer": _producer(request["producer"])}


_MESH_SUFFIX = {"stl": ".stl", "3mf": ".3mf", "glb": ".glb"}


def _meshes(value, *, required):
    if not isinstance(value, list) or (required and not value):
        raise ValueError("artifact meshes must be a list" + (", not empty" if required else ""))
    meshes = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"fmt", "path", "chord", "angle"} \
                or item["fmt"] not in _MESH_SUFFIX:
            raise ValueError("artifact mesh must have exactly fmt (stl, 3mf or glb), path, chord and angle")
        meshes.append({"fmt": item["fmt"], "path": _path(item["path"], "mesh path", (_MESH_SUFFIX[item["fmt"]],)),
                       "chord": _tolerance(item["chord"], "chord"), "angle": _tolerance(item["angle"], "angle")})
    if len({mesh["path"] for mesh in meshes}) != len(meshes):
        raise ValueError("artifact mesh paths must be distinct")
    return meshes


def _files(step, meshes):
    """The files an export or mesh request writes, each by its absolute path: the STEP and its
    sidecar, then the meshes."""
    files = [step, f"{step}.json"] if step is not None else []
    return files + [mesh["path"] for mesh in meshes]


def _publication(request, kind):
    """An export or mesh request: what its job publishes, pinned (``store/exports.py``)."""
    if set(request) != {"kind", "model", "tree", "state", "step", "name", "expected", "previous", "displays",
                        "meshes"}:
        raise ValueError(f"artifact {kind} must have exactly model, tree, state, step, name, expected, previous, "
                         "displays and meshes")
    step = _path(request["step"], "step") if kind == "export" or request["step"] is not None else None
    meshes = _meshes(request["meshes"], required=kind == "mesh")
    files = set(_files(step, meshes))
    name = request["name"]
    if not isinstance(name, str) or not name:
        raise ValueError("artifact name must name the meshes' document")
    expected, previous = request["expected"], request["previous"]
    if not isinstance(expected, dict) or not set(expected) <= files:
        raise ValueError("artifact expected must map files the request writes to the digests found there")
    if not isinstance(previous, dict) or set(previous) != {"key", "outputs"} \
            or not isinstance(previous["outputs"], dict) or not set(previous["outputs"]) <= files:
        raise ValueError("artifact previous must have exactly key and the outputs it recorded for these files")
    if not isinstance(request["displays"], list):
        raise ValueError("artifact displays must be a list of display requests")
    return {
        "kind": kind,
        "model": _model(request["model"]),
        "tree": _digest(request["tree"], "tree"),
        "state": _digest(request["state"], "state"),
        "step": step,
        "name": name,
        "expected": {path: _optional_digest(digest, "expected digest") for path, digest in sorted(expected.items())},
        "previous": {"key": _optional_digest(previous["key"], "previous key"),
                     "outputs": {path: _digest(digest, "previous digest")
                                 for path, digest in sorted(previous["outputs"].items())}},
        "displays": [_display(display) for display in request["displays"]],
        "meshes": meshes,
    }


def publication_files(request):
    """The files a normalized export or mesh request writes, in order: the STEP, then the
    meshes (its sidecar is published with the STEP and owes nothing of its own)."""
    return ([request["step"]] if request["kind"] == "export" else []) + [mesh["path"] for mesh in request["meshes"]]


def normalize_request(request):
    """Own a closed, canonical JSON request without consulting any source/store."""
    if not isinstance(request, dict):
        raise ValueError("artifact request must be an object")
    kind = request.get("kind")
    if kind == "producer" and set(request) == {"kind"}:
        return {"kind": "producer"}
    if kind in ("export", "mesh"):
        return _publication(request, kind)
    if kind == "measure" and set(request) == {"kind", "tree", "cid", "face"}:
        face = request["face"]
        if type(face) is not int or face < 1:
            raise ValueError("artifact face must be a face ordinal, an integer from 1")
        return {"kind": kind, "tree": _digest(request["tree"], "tree"), "cid": _digest(request["cid"], "cid", _CID),
                "face": face}
    if kind != "display":
        raise ValueError("artifact request must be producer; display with exactly tree, cids and producer; "
                         "measure with tree, cid and face; or export or mesh with what they publish")
    return _display(request)


def request_key(request):
    encoded = json.dumps(normalize_request(request), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def store_path(value=None):
    if value is None:
        from cadgen.store.paths import store_root

        value = store_root()
    if not isinstance(value, (str, os.PathLike)) or not str(value):
        raise ValueError("artifact store_root must be an explicit nonempty path")
    return str(Path(value).resolve())


def result_frame(request, result):
    # Round-tripping rejects native wrappers and nonfinite values, and prevents a
    # producer from mutating a result after publication to attached clients.
    value = json.loads(json.dumps(result, allow_nan=False))
    return {"request": request_key(request), "result": value}


def validate_result(request, payload):
    if not isinstance(payload, dict) or set(payload) != {"request", "result"} or payload["request"] != request_key(request):
        raise ArtifactJobError("artifact result does not match the complete requested inputs")
    return result_frame(request, payload["result"])["result"]


_WORKER = threading.local()


@contextlib.contextmanager
def worker_context(root):
    """The store this thread's job is for: a daemon worker's request. An artifact job the
    thread submits while it runs is that job's dependency (``client.artifact_payload``)."""
    previous = getattr(_WORKER, "root", None)
    _WORKER.root = store_path(root)
    try:
        yield
    finally:
        _WORKER.root = previous


def execute(request, *, keep_going=None, published=None):
    """Worker-only native entry. Source and model lookup are absent by design.

    ``keep_going`` is asked before each part (``parts.derive``), and before an export or a
    mesh job writes and before it publishes: a daemon worker's asks its supervisor whether
    anyone still wants the job. ``published`` hears each file an export or mesh job saves,
    the moment it lands (the worker sends it to the supervisor, ``daemon/worker.py``)."""
    request = normalize_request(request)
    from cadgen.store import parts

    if request["kind"] == "producer":
        return parts.producer_identity()
    if request["kind"] == "measure":
        return parts.measure_face(request["tree"], request["cid"], request["face"])
    if request["kind"] in ("export", "mesh"):
        from cadgen.store import exports

        run = exports.run_export if request["kind"] == "export" else exports.run_meshes
        return run(request, keep_going=keep_going, published=published)
    return parts.derive(request["tree"], request["cids"], producer=request["producer"], keep_going=keep_going)


class ArtifactFuture(Future):
    """Completion/result for one artifact operation, with no source-model state."""

    def __init__(self):
        super().__init__()
        self._subscriber_lock = threading.Lock()
        self._detached = False
        self._settled = False
        self._detach_action = None

    @property
    def detached(self):
        with self._subscriber_lock:
            return self._detached

    def detach(self):
        """Retire only this subscription, including when its work is running."""
        with self._subscriber_lock:
            if self._settled or self.done():
                return False
            self._detached = True
            self._settled = True
            action, self._detach_action = self._detach_action, None
        try:
            if action is not None:
                action()
        finally:
            self.set_exception(ArtifactDetached("artifact subscriber detached"))
        return True

    def _bind_detach(self, action):
        with self._subscriber_lock:
            detached = self._detached
            self._detach_action = None if detached else action
        if detached and action is not None:
            action()

    def _begin(self):
        with self._subscriber_lock:
            return not self._detached and self.set_running_or_notify_cancel()

    def _complete(self, value=None, error=None):
        with self._subscriber_lock:
            self._detach_action = None
            if self._settled or self.done():
                return
            self._settled = True
        if error is None:
            self.set_result(value)
        else:
            self.set_exception(error)

    def result(self, timeout=None):
        # The calling thread owns its lease. A background dispatcher must never
        # release it, and an already finished result need not yield it.
        if self.done():
            return copy.deepcopy(super().result(timeout))
        with broker.yielded():
            return copy.deepcopy(super().result(timeout))


# The refusals an artifact job answers with (``store.parts``, ``store.exports``): reported by
# their message alone.
_REFUSALS = frozenset({("cadgen._internal.tessellate", "MeshProductionError"),
                       ("cadgen.store.parts", "ProducerUnavailable"),
                       ("cadgen.store.exports", "PublicationRefused")})


def failure_text(error):
    """What a failed artifact job says: a refusal by its message alone -- a part the geometry
    refused, a producer this runtime cannot implement -- anything else by its traceback. Told by
    name: a job that failed before loading those modules must not load them now."""
    if (type(error).__module__, type(error).__name__) in _REFUSALS:
        return f"{type(error).__name__}: {error}\n"
    import traceback

    return "".join(traceback.format_exception(type(error), error, error.__traceback__))


def _execute_here(request, root, future):
    """A job of a process built without the daemon (``client.NO_DAEMON_FLAG``): run now, on the
    submitting thread, through the worker's own entry (:func:`execute`) and result binding,
    against the store it names, what it says kept for its failure -- as a worker's job answers
    its caller (``client.run_artifact``)."""
    import io

    said = io.StringIO()
    try:
        if store_path() != root:
            raise RuntimeError("artifact job store does not match its request")
        with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
            value = result_frame(request, execute(request))
    except Exception as error:  # noqa: BLE001 - the future carries it, as a worker's exit does
        said.write(failure_text(error))
        future._complete(error=ArtifactJobError(f"artifact request failed: {said.getvalue().strip()}"))
    else:
        future._complete(validate_result(request, value))
    return future


def submit_artifact(request, *, store_root=None):
    """Start a typed artifact operation as ONE build-pool job, a daemon worker's, against
    the store it names, from a thread of its own: the caller's thread never imports the
    CAD kernel. A caller that waits for it yields the slot it holds (``result``). In a process
    built without the daemon the job runs here, before this returns (:func:`_execute_here`)."""
    request = normalize_request(request)
    root = store_path(store_root)
    future = ArtifactFuture()
    from cadgen.daemon import client

    if client.no_daemon_active():
        return _execute_here(request, root, future)

    dependency = getattr(_WORKER, "root", None) is not None or broker.current_lease() is not None
    # Captured before the dispatch thread: every request's root/dependency/env is
    # its caller's, not whichever HTTP thread happens to run next.
    payload = client.artifact_payload(request, store_root=root, dependency=dependency)

    def run():
        try:
            if not future._begin():
                return
            future._complete(client.run_artifact(payload, subscriber=future))
        except Exception as exc:
            future._complete(error=exc)
        finally:
            future._bind_detach(None)

    threading.Thread(target=run, name="cadgen-artifact", daemon=True).start()
    return future


def resolve_artifact(request, *, store_root=None):
    """Resolve one operation; waits yield the caller's CPU lease."""
    return submit_artifact(request, store_root=store_root).result()
