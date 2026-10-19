"""``cadgen daemon status`` — is anything warm, and what is it doing?

Warm compute you cannot see is warm compute you stop trusting. The daemon is on by
default and starts itself, so the question "why was that build slow" needs an answer
that is not "read the log file beside a hashed socket path".

Asks the running daemon rather than inspecting the filesystem: the socket's existence
proves nothing (a stale file outlives a killed daemon), and only the supervisor knows
which workers are busy.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence

from cadgen.daemon import client as daemon_client

DEFAULT_PROG = "cadgen daemon status"


def _age(started: float) -> str:
    if not started:
        return ""
    seconds = max(0, int(time.time() - started))
    if seconds >= 3600:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    return f"{seconds // 60}m{seconds % 60:02d}s"


def _render(status: dict) -> str:
    workers = status.get("workers") or []
    bound = [w for w in workers if w.get("model")]
    # The parenthetical qualifies the BOUND count, so it counts busy among those --
    # counting busy across the whole pool made the summary disagree with the rows
    # below it, because a worker can be busy with no model bound (an import job).
    busy = sum(1 for w in bound if w.get("busy"))
    # A busy unbound worker is neither bound nor a spare, so without this the pool's
    # own workers go missing from the one line that is supposed to account for them.
    unbound_busy = sum(1 for w in workers if w.get("busy") and not w.get("model"))
    pending = status.get("sparesPending") or 0
    starting = f" (+{pending} starting)" if pending else ""
    unbound = f", {unbound_busy} unbound busy" if unbound_busy else ""
    lines = [
        f"CAD daemon running (pid {status.get('pid')}, up {_age(status.get('startedAt') or 0)})",
        f"  socket   {status.get('socket')}",
        f"  version  cadgen {status.get('version') or '?'}  token {status.get('token') or '?'}",
        f"  workers  {len(bound)} bound ({busy} busy), "
        f"{status.get('spares', 0)} spare{starting}{unbound}",
    ]
    jobs = status.get("jobsRunning") or {}
    if jobs:
        lines.append(
            f"  slots    running {jobs.get('running', 0)}/{jobs.get('limit', '?')}, "
            f"queued {jobs.get('queued', 0)}, coalesced {jobs.get('coalesced', 0)}"
        )
    # The ledger's work in flight, which a slot count cannot show: a build whose worker has
    # answered still owes the jobs the supervisor runs for it (its display data, its
    # files), and a request waiting for memory or a kernel holds no slot yet.
    ledger = [job for job in (status.get("jobs") or []) if isinstance(job, dict)]
    active = [job for job in ledger if job.get("state") in ("submitted", "queued", "building")]
    if active:
        builds = [job for job in active if job.get("tool") != "artifact"]
        owed = [job for job in active if job.get("tool") == "artifact"]
        waiting = [job for job in active if job.get("state") in ("submitted", "queued")]
        saving = sum(len(job.get("saving") or {}) for job in active)
        parts = [f"{len(builds)} build{'s' if len(builds) != 1 else ''}"]
        if owed:
            kinds = sorted({str((job.get("artifact") or {}).get("kind") or "artifact") for job in owed})
            parts.append(f"{len(owed)} owed job{'s' if len(owed) != 1 else ''} ({', '.join(kinds)})")
        if saving:
            parts.append(f"{saving} file{'s' if saving != 1 else ''} saving")
        if waiting:
            detail = next((job.get("detail") for job in waiting if job.get("detail")), "")
            parts.append(f"{len(waiting)} waiting" + (f": {detail}" if detail else ""))
        lines.append("  jobs     " + ", ".join(parts))
    memory = status.get("memory") or {}
    if memory:
        limit = int(memory.get("limitBytes") or 0)
        mib = 1024 * 1024
        if limit:
            reclaimed = (f"; reclaims {status.get('memoryReclaims', 0)}, refusals {status.get('memoryRefusals', 0)}, "
                         f"unbinds {status.get('unbinds', 0)}")
            lines.append(
                f"  memory   {int(memory.get('chargedBytes') or 0) // mib} MiB of {limit // mib} MiB budget charged "
                f"({int(memory.get('residentBytes') or 0) // mib} MiB resident, "
                f"{int(memory.get('workerReservationBytes') or 0) // mib} MiB per worker reserved, "
                f"{int(memory.get('dependencyReserveBytes') or 0) // mib} MiB kept for owed jobs){reclaimed}"
            )
        else:
            lines.append(f"  memory   no budget; unbinds {status.get('unbinds', 0)}")
    for worker in bound:
        state = "busy" if worker.get("busy") else "idle"
        # A worker is bound to ONE model, so which model it holds is the first thing
        # to know about it -- "3 workers" says nothing about whether the build you are
        # waiting on has a warm one. An extra is a second worker on a busy model.
        extra = "  extra" if worker.get("extra") else ""
        lines.append(
            f"    pid {worker.get('pid')}  {state}  {worker.get('jobs', 0)} jobs{extra}  {worker.get('model')}"
        )
    lines.append(
        # `jobs` in the payload is the job LEDGER (a list, the viewer's progress feed);
        # the pool's lifetime count is `jobsServed`.
        f"  totals   {status.get('jobsServed', 0)} jobs, "
        f"{status.get('imports', 0)} imports, "
        f"{status.get('concurrent', 0)} concurrent (extras), "
        f"{status.get('recycles', 0)} recycles, "
        f"{status.get('crashes', 0)} crashes"
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, *, prog: str = DEFAULT_PROG) -> int:
    parser = argparse.ArgumentParser(prog=prog, description="Show the warm CAD daemon's state")
    parser.add_argument("--json", action="store_true", dest="json_result")
    args = parser.parse_args(list(argv) if argv is not None else None)

    status = daemon_client.status()
    if args.json_result:
        sys.stdout.write(json.dumps(status or {}, separators=(",", ":")) + "\n")
        return 0
    if not status:
        # "One starts on the next build" is false where none can start, and a user
        # following it would keep waiting for a daemon that never appears.
        reason = daemon_client.unavailable()
        if reason is not None:
            sys.stdout.write(f"No CAD daemon is running, and none can start here: {reason}\n")
            return 0
        sys.stdout.write("No CAD daemon is running. One starts on the next build.\n")
        return 0
    sys.stdout.write(_render(status) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
