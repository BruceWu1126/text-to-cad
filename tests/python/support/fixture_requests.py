"""Keep a fixture's private stores until the requests writing them have ended."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from unittest import mock


def cleanup_after_requests(temporary: tempfile.TemporaryDirectory[str], stores: Iterable[str | os.PathLike[str]],
                           *, environment: dict[str, str]) -> None:
    """Drain every owned request before normal temporary-directory cleanup.

    An early script/compile answer can leave display and exports running. A lost
    ledger cannot prove they ended, so retain the directory on that failure.
    """
    from cadgen.daemon.client import watch_jobs

    owned = {os.path.realpath(store) for store in stores}
    try:
        if owned:
            with mock.patch.dict(os.environ, environment):
                update = watch_jobs()
                while True:
                    if update is None:
                        raise RuntimeError(f"the fixture's daemon disconnected; retaining {temporary.name}")
                    if not any(os.path.realpath(job.get("storeRoot", "")) in owned and job["finishedAt"] is None
                               for job in update["jobs"]):
                        break
                    update = watch_jobs(update["jobsCursor"])
    except BaseException:
        # Skipping cleanup alone would still let TemporaryDirectory's GC finalizer
        # delete a live store after unittest drops this callback's arguments.
        temporary._finalizer.detach()
        raise
    temporary.cleanup()
