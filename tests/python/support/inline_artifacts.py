"""Build-pool work run where it is submitted, for tests of what a job does rather
than how it is dispatched (the dispatch has its own tests: test_daemon_artifacts*)."""

from __future__ import annotations

import contextlib
from concurrent.futures import Future
from unittest import mock


@contextlib.contextmanager
def inline_artifacts(run=None):
    """Every artifact job this process submits (``artifacts.submit_artifact``, which
    ``resolve_artifact`` goes through) runs at once, on the
    submitting thread, through the worker's own entry (``artifacts.execute``) and
    result binding -- or through ``run(request)`` when given. Yields the mock, whose
    calls are the requests."""
    from cadgen.daemon import artifacts

    def submit(request, *, store_root=None):
        future = Future()
        try:
            value = run(request) if run is not None else artifacts.execute(request)
            future.set_result(artifacts.validate_result(request, artifacts.result_frame(request, value)))
        except Exception as error:  # noqa: BLE001 - the future carries it, as a worker's would
            future.set_exception(error)
        return future

    with mock.patch.object(artifacts, "submit_artifact", side_effect=submit) as submitted:
        yield submitted


@contextlib.contextmanager
def inline_compiles():
    """Every document compile this process submits (``executors.submit_compile``, which a
    read of a document with no tree goes through) runs at once, on the submitting thread,
    as ``cadgen step compile`` does on a daemon spare, its own jobs inline. Yields the mock,
    whose calls are the documents."""
    from pathlib import Path

    from cadgen import step
    from cadgen.daemon import executors

    class Done:
        def __init__(self, code: int) -> None:
            self.code = code

        def wait(self) -> int:
            return self.code

        def output(self) -> str:
            return ""

    def submit(document, *, store_root=None, force=False, parent=None):
        with inline_artifacts():
            return Done(0 if step.compile(Path(document), force=force).ok else 1)

    with mock.patch.object(executors, "submit_compile", side_effect=submit) as submitted:
        yield submitted
