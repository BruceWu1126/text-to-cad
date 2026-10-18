"""Orderly retirement of a test-owned daemon, never the ambient endpoint."""

from __future__ import annotations

import os
import sys
import time

# A daemon finishes the jobs it has in flight before it lets go of its address: an
# answer still owed to a build that has returned. Longer than that is a hung drain.
DRAIN_SECONDS = 120.0


def retire_owned_daemon(address: str) -> None:
    from cadgen.daemon import client, transport

    if not address or os.environ.get("CADGEN_DAEMON_SOCKET") != address:
        raise ValueError("daemon cleanup requires the explicit test-owned address")
    try:
        channel = client._connect(address)
    except OSError:
        return  # This test never started its default daemon, or already stopped it.
    try:
        # Version retirement is the daemon's existing orderly shutdown path:
        # close its listener, drain work, and shut down its worker pool. Unlike
        # SIGTERM on Windows it also releases the workers. Never retry/spawn.
        if not client._send_json(channel, {"token": "test-owned-daemon-retirement"}):
            raise RuntimeError("could not request test daemon retirement")
        result = client._recv_json(channel, 10.0)
        if result != {"restart": True}:
            raise RuntimeError(f"unexpected test daemon retirement response: {result!r}")
    finally:
        channel.close()
    # Retired is gone from its address: its lock is free. A daemon draining its jobs still
    # serves the address -- their workers reach it there, with the key beside it -- and
    # those jobs answer into stores, so the caller removes the stores and the folder
    # holding the address only after.
    lock = transport.daemon_lock(address)
    deadline = time.monotonic() + DRAIN_SECONDS
    while not lock.acquire():
        if time.monotonic() >= deadline:
            raise RuntimeError(f"the test daemon at {address} still serves {DRAIN_SECONDS:.0f}s after retiring")
        time.sleep(0.05)
    lock.release()


if __name__ == "__main__":
    retire_owned_daemon(sys.argv[1])
