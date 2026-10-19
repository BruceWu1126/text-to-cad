"""The warm worker pool: a worker per model, an extra when it is busy, spares in reserve.

One rule decides routing here: **nothing waits on another build.** A request
for a model whose worker is idle takes that worker. A request for a model whose
worker is busy gets an *extra* — a spare bound to the same model for the length
of one job — and runs now. A request for a model with no worker binds a spare. A
request with no spare left spawns if its memory reservation fits. Admission
counts resident worker trees (including their child processes), keeps headroom
for dependencies, and reclaims idle workers first. That reservation is not
configured: it starts at the seed and is recalibrated on every accounting pass
from the RSS of workers that are idle and have run nothing, which is what a
worker costs before geometry (``memory.worker_baseline``). The dependency
headroom follows from the same number. Exhaustion waits for the
builds in flight to finish and fails explicitly only when nothing is running
that could release memory. These are soft RSS and
reservation bounds, not a hard limit on a native operation's allocations.
Publication ordering remains the publish rule's concern.

Spares: ``CADGEN_DAEMON_SPARES`` (default 2) workers that have finished importing
build123d and are bound to nothing. Binding one starts a replacement in the
background when memory permits, so a new model's first build pays no import.
A subject-less job borrows one without replacing it. Surplus workers from a
burst remain reusable for two idle seconds, then the periodic sweep restores K.
An extra returns to the spare set when its job ends; a primary stays bound
until idle timeout, memory pressure or recycling reclaims it.

Recycle: a worker is dropped after ``CADGEN_DAEMON_RECYCLE`` jobs (default 1000)
as a leak hedge; its model binds a fresh worker on the next request.

Workers read frames on a thread so every read honours a timeout: a worker that
hangs before announcing itself, or mid-job, is reported instead of blocking its
caller forever. Either way "hangs" means silence with no CPU progress. Mid-job, a
running job beats (``worker._heartbeat``), and a worker whose heartbeat a
GIL-holding native call starves is still computing, which its CPU clock shows
(``Worker.frames``). Starting, a worker imports the kernel, a few CPU seconds that
a busy machine spreads over minutes; it is waited for while its clock moves
(``Worker._announced``).
"""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from cadgen.daemon.memory import MemoryPolicy, MIB, process_tree_bytes, worker_baseline

DEFAULT_SPARES = 2
DEFAULT_RECYCLE_AFTER = 1000
DEFAULT_IDLE_UNBIND_SECONDS = 600.0
BORROWED_SURPLUS_IDLE_SECONDS = 2.0
# A starting worker that has not announced itself after this long has its CPU clock
# read, and another window begins while the clock moves (``Worker._announced``).
SPAWN_TIMEOUT_SECONDS = 120.0
# A silent worker whose CPU clock advanced at least this much across the silent window
# is computing inside a native call that holds the GIL, not hung. A stopped process or
# a deadlock accrues ~0; one that is paging under memory pressure still accrues some.
BUSY_CPU_SECONDS = 0.1
_USE_SEQUENCE = itertools.count()


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def spare_count() -> int:
    value = _env_int("CADGEN_DAEMON_SPARES")
    return max(0, value) if value is not None else DEFAULT_SPARES


def recycle_after() -> int:
    value = _env_int("CADGEN_DAEMON_RECYCLE")
    return max(1, value) if value is not None else DEFAULT_RECYCLE_AFTER


def idle_unbind_seconds() -> float:
    raw = os.environ.get("CADGEN_DAEMON_IDLE_UNBIND", "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_IDLE_UNBIND_SECONDS


class WorkerGone(RuntimeError):
    """The worker process ended, or its pipe closed, before the job produced its
    terminal frame. Carries the wait status when the process has been reaped."""

    def __init__(self, message: str, *, exit_status: int | None = None) -> None:
        super().__init__(message)
        self.exit_status = exit_status


class MemoryAdmissionError(RuntimeError):
    """A worker reservation could not fit after idle resources were reclaimed."""


class RequestCancelled(Exception):
    """The request a worker was being found for was withdrawn while it waited (its client
    left): no worker is seated and no kernel is started for it."""


class _WorkerFreed(Exception):
    """A worker came free while a request waited for the memory to spawn one
    (``Pool._admit_locked``): the request takes it instead (``Pool.acquire``)."""


_NTSTATUS_NAMES = {
    0xC0000005: "STATUS_ACCESS_VIOLATION",
    0xC00000FD: "STATUS_STACK_OVERFLOW",
    0xC0000409: "STATUS_STACK_BUFFER_OVERRUN",
    0xC0000374: "STATUS_HEAP_CORRUPTION",
    0xC000013A: "STATUS_CONTROL_C_EXIT",
}


def stopped(status: int | None) -> bool:
    """Whether a worker's exit status says it was asked to stop -- SIGTERM, SIGINT or SIGHUP, from a
    person quitting it, a logout or a shutdown -- rather than that it died: a native fault (SIGSEGV,
    SIGBUS, SIGABRT) or the kernel's kill for memory (SIGKILL) is a crash. None of cadgen's own stops
    signals a worker mid-job: its client leaving is a cancel, and the pool stops only idle workers
    until the daemon's own telemetry has closed (``server.serve``)."""
    if not isinstance(status, int) or status >= 0:
        return False  # an exit code, or Windows (no signals: TerminateProcess is exit code 1)
    import signal

    return -status in {getattr(signal, name) for name in ("SIGTERM", "SIGINT", "SIGHUP") if hasattr(signal, name)}


def _sweep_scratch() -> None:
    """Remove what workers that are gone left in the temp folder; never fails the pool."""
    try:
        from cadgen._internal import temp_leftovers

        temp_leftovers.sweep()
    except Exception:  # noqa: BLE001 - scratch cleanup never fails the daemon
        pass


def describe_exit(status: int | None) -> str:
    """A worker's death in words: the signal that killed it, or its exit code."""
    if status is None:
        return "closed its output while still running"
    if status < 0:
        import signal

        number = -status
        try:
            name = signal.Signals(number).name
        except ValueError:
            return f"was killed by signal {number}"
        return f"was killed by {name} (signal {number})"
    if os.name == "nt" or status > 255:
        unsigned = status & 0xFFFFFFFF
        name = _NTSTATUS_NAMES.get(unsigned)
        if name:
            return f"exited with 0x{unsigned:08X} ({name})"
        return f"exited with code {status}"
    return f"exited with code {status}"


def _parse_cpu_time(text: str) -> float:
    """``ps -o time=``: ``[[DD-]HH:]MM:SS[.ss]``; minutes may exceed 59 (BSD/macOS)."""
    days, _, clock = text.strip().rpartition("-")
    seconds = 0.0
    for part in clock.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds + (int(days) * 86400 if days else 0)


_WINDOWS_STILL_ACTIVE = 259  # GetExitCodeProcess's answer for a process that has not exited


def _windows_cpu_seconds(pid: int) -> float | None:
    """GetProcessTimes for a LIVE process; ``None`` once it has exited.

    ``OpenProcess`` succeeds on an exited process for as long as anyone still
    holds a handle to it -- its parent's ``Popen`` does -- and GetProcessTimes
    then reports the dead process's final times. That is a frozen clock, which
    is exactly what a live-but-starved heartbeat check must not see, so an
    exited process reads as "no clock", as it does from /proc and ``ps``.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE, *(ctypes.POINTER(wintypes.FILETIME),) * 4)
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return None
        # A process that itself exits with 259 reads as live here; no worker does.
        if exit_code.value != _WINDOWS_STILL_ACTIVE:
            return None
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                        ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return sum((t.dwHighDateTime << 32 | t.dwLowDateTime) for t in (kernel, user)) / 1e7
    finally:
        kernel32.CloseHandle(handle)


# Where ``ps`` lives on macOS, the BSDs and Linux. The daemon runs with whatever
# PATH started it (a launchd job, an IDE, a stripped CI environment), and a CPU
# clock it cannot read turns a busy worker into a killed one.
_PS_PATHS = ("/bin/ps", "/usr/bin/ps")


def _ps_executable() -> str:
    """``ps`` by absolute path, ``PATH`` only when it is in neither usual place."""
    for candidate in _PS_PATHS:
        if os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("ps") or "ps"


def process_cpu_seconds(pid: int) -> float | None:
    """User + system CPU seconds process ``pid`` has used, read from outside it.

    ``None`` when the platform will not say. Read only when a worker has gone silent,
    so the ``ps`` spawn on macOS costs nothing in the common case.
    """
    try:
        if sys.platform.startswith("linux"):
            with open(f"/proc/{pid}/stat", encoding="ascii") as handle:
                fields = handle.read().rsplit(")", 1)[1].split()
            return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
        if os.name == "nt":
            return _windows_cpu_seconds(pid)
        output = subprocess.check_output(
            [_ps_executable(), "-o", "time=", "-p", str(pid)],
            text=True, timeout=5, stderr=subprocess.DEVNULL,
        )
        return _parse_cpu_time(output) if output.strip() else None
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


_TIMED_OUT = object()  # _read_frame: the wait elapsed; distinct from None (pipe closed)


def interpreter() -> tuple[str, dict[str, str]]:
    """The program a worker runs, and what its environment adds: this Python itself.

    In a virtual environment on Windows -- every uv environment, the one ``uvx`` runs cadgen
    in among them -- ``sys.executable`` is a launcher that starts the environment's base
    interpreter as its child and waits on it. A worker started through it was two
    processes, and everything the pool knows of the one it started was the launcher's:
    its CPU clock, which never moves, so the hang watch killed every job whose native
    call held the GIL past the silence window; its exit, a launcher's code (106: the
    environment's ``pyvenv.cfg`` is missing) where the interpreter's was wanted. So the
    base interpreter is started directly and told, as the launcher tells it, which
    environment it runs: what ``multiprocessing`` does on Windows too."""
    executable = sys.executable
    base = getattr(sys, "_base_executable", None) or executable
    if os.name == "nt" and os.path.normcase(os.path.abspath(base)) != os.path.normcase(os.path.abspath(executable)) \
            and os.path.isfile(base):
        return base, {"__PYVENV_LAUNCHER__": executable}
    return executable, {}


def installation_gone() -> bool:
    """Whether what a worker starts from was removed under this daemon: its interpreter, its virtual
    environment's ``pyvenv.cfg``, or the module a worker runs. A package manager replacing the
    environment removes them while the daemon still runs -- on Windows only what no process holds
    open goes, leaving the environment half there -- and then no worker can start: not a crash."""
    from cadgen import daemon

    needed = [sys.executable, os.path.join(os.path.dirname(daemon.__file__), "worker.py")]
    if sys.prefix != sys.base_prefix:
        needed.append(os.path.join(sys.prefix, "pyvenv.cfg"))
    return not all(os.path.isfile(path) for path in needed)


class Worker:
    """One warm subprocess. Owned by the pool; never shared between concurrent jobs."""

    def __init__(self) -> None:
        from cadgen.daemon.client import daemon_address
        from cadgen.daemon.executors import worker_env

        env = worker_env()
        # The broker a worker's jobs take slots from is the daemon itself; name the
        # address explicitly so a worker never guesses it from its identity.
        env["CADGEN_DAEMON_SOCKET"] = daemon_address()
        # Guards against a worker's own top-level call routing back into the daemon
        # as a fresh request; a nested compile ignores this on purpose (client.run_nested).
        env["CADGEN_DAEMON_CHILD"] = "1"
        python, launched = interpreter()
        env.update(launched)
        self.proc = subprocess.Popen(
            # -P: `python -m` would put its working directory, the temp folder below,
            # first on the import path. Whatever other programs leave there would then
            # shadow a build's imports (STORE.md §9), and each import that misses it
            # lists that folder again once anything in it changes: tens of listings of a
            # folder of tens of thousands of entries per start, every start contending
            # for the same folder, so a burst of starts never finished importing.
            [python, "-P", "-m", "cadgen.daemon.worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
            # Spares can remain idle before their first request.  Start them in the
            # same stable directory worker._park uses between jobs so they never pin
            # the daemon launcher's project cwd on Windows.
            cwd=tempfile.gettempdir(),
            # utf-8 EXPLICITLY: this pipe carries JSON frames and the worker encodes
            # utf-8 (worker.serve), so neither end infers the platform code page.
            env=env, text=True, encoding="utf-8", errors="backslashreplace", bufsize=1,
        )
        self.jobs_served = 0
        self.busy = False
        self.extra = False
        self.last_used = time.monotonic()
        self.use_seq = next(_USE_SEQUENCE)
        # The MODEL this worker is bound to (its script path), "" while a spare.
        self.model = ""
        # The model it was last bound to: a spare that held a model's memo is the spare
        # that model's next build takes (``Pool._take_spare_locked``).
        self.last_model = ""
        self._frames: queue.Queue = queue.Queue()
        self._reader = threading.Thread(target=self._pump, name="cadgen-worker-frames", daemon=True)
        self._reader.start()
        self.pid = self._announced()

    def _announced(self) -> int:
        """The pid the worker announces once it has imported what its jobs need.

        Judged as a running job is (``frames``): silence is a hang only when the CPU
        clock stands still too. Importing the kernel takes a few CPU seconds, and a
        machine whose cores are busy, or that is starting many workers at once, spreads
        them over minutes, so a fixed wait failed starts that were still importing. After
        each silent window the process's clock is read from outside; a start that used
        less than ``BUSY_CPU_SECONDS`` in it (stopped, deadlocked, or on a platform that
        will not say) is killed. A process that ends first is reported by how it ended;
        what it printed is in the daemon's log, where its stderr goes.
        """
        cpu_seen = 0.0  # a new process has used no CPU
        while True:
            frame = self._read_frame(timeout=SPAWN_TIMEOUT_SECONDS)
            if frame is not _TIMED_OUT:
                break
            cpu_now = process_cpu_seconds(self.proc.pid)
            if cpu_now is not None and cpu_now - cpu_seen >= BUSY_CPU_SECONDS:
                cpu_seen = cpu_now
                continue
            self.kill()
            raise WorkerGone(
                f"worker {self.proc.pid} did not announce itself: it was silent for "
                f"{SPAWN_TIMEOUT_SECONDS:.0f}s with no CPU progress while starting, and was killed"
            )
        if isinstance(frame, dict) and "ready" in frame:
            return int(frame["ready"])
        status = self._exit_status() if frame is None else None
        self.kill()
        if frame is None:
            raise WorkerGone(f"worker {self.proc.pid} {describe_exit(status)} before announcing itself",
                             exit_status=status)
        said = frame.get("data", frame) if isinstance(frame, dict) else frame
        raise WorkerGone(f"worker {self.proc.pid} wrote {str(said).strip()[:200]!r} before announcing itself")

    def _pump(self) -> None:
        stream = self.proc.stdout
        if stream is None:
            self._frames.put(None)
            return
        try:
            for line in stream:
                try:
                    self._frames.put(json.loads(line))
                except ValueError:
                    self._frames.put({"stream": "stderr", "data": line})
        finally:
            # This thread owns stdout's buffered reader.  Closing it from a
            # retirement thread while this loop is blocked in readline waits
            # forever on TextIOWrapper's internal lock when a descendant still
            # holds the pipe open.
            with contextlib.suppress(OSError, ValueError):
                stream.close()
            self._frames.put(None)

    def _read_frame(self, timeout: float | None = None) -> dict | None | object:
        """The next frame; None when the pipe closed; ``_TIMED_OUT`` when ``timeout`` elapsed.

        The two are told apart on purpose: a worker that died is reported by its
        exit status, a worker that is merely quiet by the silence timeout. Folding
        them into one None let a SIGKILLed worker read as "went silent" whenever
        its pipe's EOF arrived a beat before the kernel let it be reaped.
        """
        try:
            return self._frames.get(timeout=timeout)
        except queue.Empty:
            return _TIMED_OUT

    def send(self, request: dict) -> None:
        if self.proc.poll() is not None or self.proc.stdin is None:
            raise WorkerGone("worker is not running")
        try:
            self.proc.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise WorkerGone(f"worker stdin closed: {exc}") from exc

    def frames(self, *, silence_timeout: float | None = None):
        """Yield frames until the terminating one, which is yielded last.

        ``silence_timeout`` bounds the wait for ANY frame, heartbeats included. A
        running job beats every few seconds, so silence that long means the
        interpreter is not being scheduled. Before calling that a hang, the worker's
        CPU clock is read from outside: if it advanced past ``BUSY_CPU_SECONDS``
        since the last sign of life, a native call holding the GIL (a long OCCT
        boolean) is starving the heartbeat and the wait continues for another
        window. Otherwise — stopped, deadlocked, or a clock this platform will not
        report — the worker is killed and reported as gone. Heartbeats are
        consumed here, never yielded: to the relay and the ledger they do not exist.
        """
        cpu_seen: float | None = None  # the worker's CPU seconds at its last sign of life
        phase: str | None = None
        while True:
            frame = self._read_frame(timeout=silence_timeout)
            if frame is _TIMED_OUT:
                # The clock of the process whose heartbeat this is: the pid it announced.
                cpu_now = process_cpu_seconds(getattr(self, "pid", self.proc.pid))
                if cpu_now is not None and (cpu_seen is None or cpu_now - cpu_seen >= BUSY_CPU_SECONDS):
                    # With no baseline (no beat yet) the next window decides.
                    cpu_seen = cpu_now
                    continue
                self.kill()
                raise WorkerGone(
                    f"worker {getattr(self, 'pid', self.proc.pid)} went silent for "
                    f"{silence_timeout:.0f}s with no CPU progress"
                    f"{f' (last phase: {phase})' if phase else ''} and was killed"
                )
            heartbeat = frame.get("heartbeat") if isinstance(frame, dict) else None
            if heartbeat is not None:
                if isinstance(heartbeat, dict):
                    cpu = heartbeat.get("cpu")
                    cpu_seen = float(cpu) if isinstance(cpu, (int, float)) else cpu_seen
                    phase = heartbeat.get("phase") or phase
                continue
            if frame is None:
                status = self._exit_status()
                raise WorkerGone(
                    f"worker {getattr(self, 'pid', self.proc.pid)} {describe_exit(status)}",
                    exit_status=status,
                )
            yield frame
            if "exit" in frame or "pong" in frame:
                return

    def _exit_status(self) -> int | None:
        try:
            return self.proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            return None

    def alive(self) -> bool:
        return self.proc.poll() is None

    def kill(self) -> None:
        proc = self.proc
        try:
            if proc.poll() is None:
                if proc.stdin is not None:
                    with contextlib.suppress(OSError):
                        proc.stdin.close()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        with contextlib.suppress(subprocess.TimeoutExpired):
                            proc.wait(timeout=2)
        except OSError:
            pass
        finally:
            # stdin has no independent reader.  stdout belongs exclusively to
            # _pump, which closes it after EOF; cross-thread close can deadlock
            # inside the buffered IO lock.
            if proc.stdin is not None:
                with contextlib.suppress(OSError, ValueError):
                    proc.stdin.close()


class Pool:
    """See the module docstring."""

    def __init__(self, clock=time.monotonic, *, policy: MemoryPolicy | None = None, memory_reader=None,
                 in_flight=None) -> None:
        self._cv = threading.Condition()
        self._clock = clock
        self._workers: list[Worker] = []
        self._retiring: list[Worker] = []
        self._active_pending = 0
        self._spares_pending = 0
        # Requests waiting for a spare that is still importing rather than spawning one
        # more (``acquire``): each pending spare is waited for by at most one of them.
        self._spare_waiters = 0
        # Requests waiting in memory admission (``_admit_locked``): while one waits, a
        # worker a job returns is its to take, and a release trims no spare.
        self._admission_waiters = 0
        self._policy = policy if policy is not None else MemoryPolicy.from_environment()
        # What one worker is estimated to cost. The seed until a never-used idle
        # worker has been measured; recalibrated by every _memory_locked.
        self._reservation = self._policy.seed_bytes
        self._memory_reader = memory_reader or process_tree_bytes
        # How many jobs hold a run slot right now. Each one hands its charge back when
        # it finishes, so admission waits on them instead of refusing.
        self._in_flight = in_flight or (lambda: 0)
        self._stats = {"jobsServed": 0, "imports": 0, "concurrent": 0, "crashes": 0, "recycles": 0, "unbinds": 0,
                       "memoryReclaims": 0, "memoryRefusals": 0}
        self._closed = False

    # --- spares -------------------------------------------------------------------

    def _spawn(self) -> Worker:
        """A fresh worker, or :class:`WorkerGone` saying why none could start: one that
        died or stayed silent before announcing itself (``Worker._announced``), or a
        process the system refused to start at all (too many open files, no memory for
        a fork) -- which is as much a failed start as the other, and ends the request
        that needed it the same way, never a thread left holding the pool's bookkeeping."""
        try:
            worker = Worker()
        except WorkerGone:
            raise
        except OSError as exc:
            raise WorkerGone(f"a worker could not be started: {exc.strerror or exc}") from exc
        with self._cv:
            self._stats["imports"] += 1
        return worker

    def _spares_locked(self) -> list[Worker]:
        return [w for w in self._workers if not w.model and not w.busy]

    def ensure_spares(self) -> None:
        """Replenish spare capacity, including workers temporarily borrowed."""
        with self._cv:
            if self._closed:
                return
            # Subject-less borrowers return after one job. Replacing them while busy
            # discards each returning warm kernel in favour of a cold import,
            # turning a stream of part requests into one import per job.
            borrowed = sum(worker.busy and not worker.model for worker in self._workers)
            want = spare_count() - len(self._spares_locked()) - borrowed - self._spares_pending
            if self._policy.limit_bytes:
                snapshot = self._memory_locked()
                available = (self._policy.limit_bytes - snapshot["dependencyReserveBytes"]
                             - snapshot["chargedBytes"])
                want = min(want, max(0, available // max(1, snapshot["workerReservationBytes"])))
            if want <= 0:
                return
            self._spares_pending += want

        def fill(count: int) -> None:
            for _ in range(count):
                worker = None
                try:
                    worker = self._spawn()
                except Exception:  # noqa: BLE001 - a spare that fails to start is simply not there
                    pass
                finally:
                    # Whatever became of the start, its reservation is released: a spare
                    # still counted as pending would hold memory for a worker that never comes.
                    with self._cv:
                        self._spares_pending -= 1
                        if worker is not None:
                            if self._closed:
                                worker.kill()
                            else:
                                self._workers.append(worker)
                        self._cv.notify_all()

        threading.Thread(target=fill, args=(want,), name="cadgen-spares", daemon=True).start()

    def _take_spare_locked(self, model: str = "") -> Worker | None:
        """An idle spare: the one last bound to ``model`` (its memo still in that process,
        README law 18) when there is one, else the first."""
        spares = self._spares_locked()
        if model:
            for spare in spares:
                if spare.last_model == model:
                    return spare
        return spares[0] if spares else None

    def _wait_for_spare_locked(self, model: str, cancelled, on_start) -> Worker | None:
        """A worker for ``model`` once a spare still importing announces itself, rather than
        one more kernel import beside it: each pending spare is waited for by one request
        (``_spare_waiters``); the next spawns. None when there is no spare to wait for, or
        the one waited for failed to start and no other is pending; the worker found when
        one arrived, or ``model``'s own worker turned idle meanwhile."""
        told = False
        while not self._closed and self._spares_pending > self._spare_waiters:
            if cancelled is not None and cancelled():
                raise RequestCancelled("the request was withdrawn while a worker was starting for it")
            if not told and on_start is not None:
                on_start()
                told = True
            self._spare_waiters += 1
            try:
                self._cv.wait(timeout=0.05)
            finally:
                self._spare_waiters -= 1
            self._reap_dead_locked()
            if model:
                idle = [w for w in self._workers if w.model == model and not w.extra and not w.busy]
                if idle:
                    return idle[0]
            spare = self._take_spare_locked(model)
            if spare is not None:
                return spare
        return None

    # --- acquire / release -------------------------------------------------------

    def acquire(self, model: str = "", *, dependency: bool = False, on_start=None, on_wait=None,
                owner: str = "", cancelled=None) -> Worker:
        """A worker for ``model``, or an explicit memory-admission failure.

        ``model`` is the script path (the routing key); "" means a request with
        no model subject, which borrows a spare without binding it. ``on_start``
        is called when no warm worker can take the request and one is started for
        it -- or one already importing is waited for -- before that wait; ``on_wait``
        with one sentence when memory admission makes it wait. ``owner`` is the model
        whose build this request serves (a job its result owes): that model's idle
        worker is never reclaimed to seat it. ``cancelled`` says whether the request
        was withdrawn meanwhile (its client left): the wait ends in
        :class:`RequestCancelled`, no kernel is started for it, and one that was already
        importing joins the spare set instead.
        """
        with self._cv:
            if self._closed:
                raise WorkerGone("worker pool is closed")
            while True:
                self._reap_dead_locked()
                bound = []
                worker = None
                if model:
                    bound = [w for w in self._workers if w.model == model and not w.extra]
                    idle = [w for w in bound if not w.busy]
                    if idle:
                        worker = idle[0]
                if worker is None:
                    worker = self._take_spare_locked(model)
                if worker is None:
                    # A spare still importing is this request's worker, not one more import.
                    worker = self._wait_for_spare_locked(model, cancelled, on_start)
                    if worker is not None and model and worker.model == model:
                        bound = [w for w in self._workers if w.model == model and not w.extra]
                if worker is not None:
                    worker.busy = True  # reserve before releasing the bookkeeping lock
                try:
                    try:
                        self._admit_locked(
                            spawning=worker is None,
                            dependency=dependency,
                            isolated_worker=worker,
                            owner=owner,
                            on_wait=on_wait,
                            cancelled=cancelled,
                            model=model,
                        )
                    except MemoryAdmissionError:
                        if worker is None:
                            raise
                        worker.busy = False
                        # Its retained cache may itself be the pressure. This was
                        # idle before our reservation, so retry with a fresh worker.
                        self._stats["memoryReclaims"] += 1
                        self._drop_locked(worker)
                        worker = None
                        self._admit_locked(spawning=True, dependency=dependency, owner=owner, on_wait=on_wait,
                                           cancelled=cancelled, model=model)
                except _WorkerFreed:
                    # A worker came free while this request waited to spawn one (a job a
                    # build owed returned its spare): it takes that one instead.
                    continue
                except RequestCancelled:
                    if worker is not None:
                        worker.busy = False
                    raise
                break
            if worker is None:
                if cancelled is not None and cancelled():
                    raise RequestCancelled("the request was withdrawn before a worker was started for it")
                self._active_pending += 1
        if worker is None:
            try:
                if on_start is not None:
                    on_start()
                worker = self._spawn()
            except BaseException:
                with self._cv:
                    self._active_pending -= 1
                    self._cv.notify_all()
                raise
            with self._cv:
                self._active_pending -= 1
                if self._closed:
                    worker.kill()
                    raise WorkerGone("worker pool closed while starting a worker")
                if cancelled is not None and cancelled():
                    # Nobody is left to use it, and its import is paid: a spare from now on.
                    self._workers.append(worker)
                    self._cv.notify_all()
                    raise RequestCancelled("the request was withdrawn while its worker started")
                worker.busy = True
                self._workers.append(worker)
        with self._cv:
            worker.busy = True
            if model:
                was_bound = worker in bound
                worker.model = model
                worker.last_model = model
                # An extra when a primary already exists; a primary otherwise.
                worker.extra = not was_bound and any(
                    w is not worker and w.model == model and not w.extra for w in self._workers
                )
                if worker.extra:
                    self._stats["concurrent"] += 1
            else:
                worker.extra = True  # borrowed; returns to the spare set on release
            self._used_locked(worker)
        self.ensure_spares()
        return worker

    def _memory_locked(self) -> dict:
        self._retiring[:] = [w for w in self._retiring if w.alive()]
        workers = [*self._workers, *self._retiring]
        measured = self._memory_reader([w.pid for w in workers]) if self._policy.limit_bytes else {}
        resident = sum(measured.values())
        # Recalibrate from the workers that have run nothing; the reader is already in hand.
        self._reservation = worker_baseline(
            (measured[w.pid] for w in self._workers
             if not w.busy and not w.jobs_served and w.pid in measured),
            seed=self._policy.seed_bytes,
            previous=self._reservation,
        )
        reservation = self._reservation
        charged = sum(
            max(measured.get(w.pid, reservation), reservation if w.busy else 0)
            for w in workers
        )
        pending = self._active_pending + self._spares_pending
        retiring_bytes = sum(measured.get(w.pid, reservation) for w in self._retiring)
        return {"limitBytes": self._policy.limit_bytes, "residentBytes": resident,
                "chargedBytes": charged + pending * reservation,
                "workerReservationBytes": reservation,
                "dependencyReserveBytes": self._policy.dependency_reserve(reservation),
                "measuredWorkers": len(measured), "unmeasuredWorkers": len(workers) - len(measured),
                "pendingWorkers": pending, "retiringWorkers": len(self._retiring),
                "retiringBytes": retiring_bytes}

    def _free_locked(self, model: str) -> bool:
        """Whether a request for ``model`` has a warm worker to take now: its own, idle, or
        a spare."""
        return any(not w.busy and (not w.model or (model and w.model == model and not w.extra))
                   for w in self._workers)

    def _reclaimable_locked(self, *, keep: str = "") -> list[Worker]:
        """The idle workers memory pressure may reclaim, in the order it takes them: spares
        first (a bound worker holds its model's memo and is reclaimed last, each set least
        recently used first), and never the idle worker bound to ``keep`` -- the model whose
        own owed jobs are being seated (README law 18: its next edit's hits are in it)."""
        idle = [w for w in self._workers if not w.busy and not (keep and w.model == keep)]
        return sorted(idle, key=lambda w: (bool(w.model), w.use_seq))

    def _reclaim_pressure_locked(self) -> None:
        """Trim the spares a pool over its budget keeps, as a job ends, without waiting for
        active work. Bound workers are not trimmed here: a model's worker is reclaimed last,
        by the admission of a request that needs its memory (``_admit_locked``), never by the
        jobs its own build hands over as it ends -- whose spawns are what is pending at the
        moment its worker is released."""
        if not self._policy.limit_bytes or self._admission_waiters:
            return  # a request waiting for memory takes what returns, or reclaims it in order
        snapshot = self._memory_locked()
        # Already-retiring workers are charged for admission until they exit,
        # but must not cause us to schedule the same reclamation twice.
        planned = snapshot["chargedBytes"] - snapshot["retiringBytes"]
        ceiling = self._policy.limit_bytes - snapshot["dependencyReserveBytes"]
        idle = [w for w in self._reclaimable_locked() if not w.model]
        measured = self._memory_reader([w.pid for w in idle])
        for worker in idle:
            if planned <= ceiling:
                break
            planned -= measured.get(worker.pid, snapshot["workerReservationBytes"])
            self._stats["memoryReclaims"] += 1
            self._drop_locked(worker)

    def _refusal_locked(self, snapshot: dict, *, usage: int, additional: int, dependency: bool) -> str:
        """One true sentence for a request memory admission refuses, from what is the case
        at that moment: nothing idle to reclaim, nothing in flight that could release any."""
        busy = sum(1 for w in self._workers if w.busy)
        wanted = usage + additional
        return (f"The build service has no memory for another geometry worker: this request needs "
                f"{wanted / MIB:.0f} MiB against a {self._policy.limit_bytes / MIB:.0f} MiB budget, of which "
                f"{usage / MIB:.0f} MiB is held by {busy} busy worker{'s' if busy != 1 else ''}"
                f"{'' if dependency else f' and {snapshot['dependencyReserveBytes'] / MIB:.0f} MiB is kept for the jobs builds owe'}, "
                "no worker is idle to reclaim, and nothing in flight will release any; raise CADGEN_MEMORY_MB.")

    def _admit_locked(
        self,
        *,
        spawning: bool,
        dependency: bool,
        isolated_worker: Worker | None = None,
        owner: str = "",
        on_wait=None,
        cancelled=None,
        model: str = "",
    ) -> None:
        if not self._policy.limit_bytes:
            return
        deadline = time.monotonic() + 5.0  # wait only for idle-process teardown
        stalled_since = None
        told = False

        def wait() -> None:
            nonlocal told
            if cancelled is not None and cancelled():
                raise RequestCancelled("the request was withdrawn while it waited for memory")
            if not told and on_wait is not None:
                on_wait(f"Waiting for memory: {usage / MIB:.0f} MiB of {self._policy.limit_bytes / MIB:.0f} MiB in use")
                told = True
            self._admission_waiters += 1
            try:
                self._cv.wait(timeout=0.05)
            finally:
                self._admission_waiters -= 1

        while True:
            if spawning and self._free_locked(model):
                # A worker came free while this request waited to spawn one: take it,
                # before pressure here could reclaim the very spare that just returned.
                raise _WorkerFreed
            # Each pass recalibrates, so a reservation and the headroom derived
            # from it track what the workers now resident actually cost.
            snapshot = self._memory_locked()
            usage = snapshot["chargedBytes"]
            additional = snapshot["workerReservationBytes"] if spawning else 0
            ceiling = self._policy.limit_bytes - (0 if dependency else snapshot["dependencyReserveBytes"])
            if usage + additional <= ceiling:
                return
            idle = self._reclaimable_locked(keep=owner)
            if idle:
                self._stats["memoryReclaims"] += 1
                self._drop_locked(idle[0])
                continue
            if self._retiring and time.monotonic() < deadline:
                wait()
                continue
            # The measured retained cache of the selected worker can be larger
            # than the normal build allowance.
            # It may use the dependency reserve only when it is literally the
            # sole charge. If it later asks for a child, that admission fails
            # explicitly while the parent and its geometry remain alive.
            if not dependency:
                sole_charge = (
                    not self._retiring
                    and not self._active_pending
                    and not self._spares_pending
                    and (
                        self._workers == [isolated_worker]
                        if isolated_worker is not None
                        else not self._workers
                    )
                )
                if sole_charge and usage + additional <= self._policy.limit_bytes:
                    return
            # A build holding a run slot finishes and releases its worker; a spawn
            # still starting becomes such a build. Wait for them. Only when nothing
            # is in flight can no wait help: every busy worker is then a parent kept
            # alive by the geometry it retains for its children.
            if self._in_flight() or self._active_pending or self._spares_pending:
                stalled_since = None
                wait()
                continue
            now = time.monotonic()
            stalled_since = stalled_since or now
            if now - stalled_since < 2.0:  # a freshly spawned worker takes a moment to claim its slot
                wait()
                continue
            self._stats["memoryRefusals"] += 1
            raise MemoryAdmissionError(self._refusal_locked(snapshot, usage=usage, additional=additional,
                                                            dependency=dependency))

    def _used_locked(self, worker: Worker) -> Worker:
        worker.last_used = self._clock()
        worker.use_seq = next(_USE_SEQUENCE)
        return worker

    def unbind_idle(self) -> None:
        """A bound worker idle for ``idle_unbind_seconds()`` returns to the spare set
        (spares beyond K exit). Its model's next build rebinds a spare -- no import
        repaid. This only releases process state;
        persistent cache objects remain available to the replacement worker.

        Subject-less burst workers get a much shorter grace so the next browser
        poll can reuse their warm kernels. Once that grace expires, this same
        periodic sweep returns the idle spare set to K.
        """
        limit = idle_unbind_seconds()
        with self._cv:
            now = self._clock()
            for worker in list(self._workers):
                if not worker.model or worker.busy or worker.extra:
                    continue
                if now - worker.last_used < limit:
                    continue
                self._stats["unbinds"] += 1
                # Back to the spare set, its model's memo with it (``_take_spare_locked``);
                # a spare the set has no room for goes instead, the least recently used
                # first: a bound worker is reclaimed last.
                worker.model = ""
                spares = sorted(self._spares_locked(), key=lambda spare: spare.last_used)
                for spare in spares:
                    if len(self._spares_locked()) + self._spares_pending <= spare_count():
                        break
                    if spare is not worker:
                        self._drop_locked(spare)
            # Keep the K most recently used spares. Any additional unbound
            # workers came from a subject-less burst: model-bound extras and
            # idle unbinding still enforce K at release/unbind time. A brief
            # grace bridges the viewer's asynchronous result poll without
            # increasing the daemon's settled worker count.
            spares = sorted(self._spares_locked(), key=lambda worker: worker.last_used, reverse=True)
            for worker in spares[spare_count():]:
                if now - worker.last_used >= BORROWED_SURPLUS_IDLE_SECONDS:
                    self._drop_locked(worker)

    def release(self, worker: Worker, *, healthy: bool = True, cancelled: bool = False) -> None:
        """Take a worker back after a job. An unhealthy one is dropped, and counted a crash
        unless its job was ``cancelled``: stopped because its caller left (or someone stopped
        the worker), which ends a job by killing its worker as a matter of course."""
        with self._cv:
            borrowed = worker.extra and not worker.model
            worker.busy = False
            worker.last_used = self._clock()
            worker.jobs_served += 1
            self._stats["jobsServed"] += 1
            if not healthy or not worker.alive():
                if not healthy and not cancelled:
                    self._stats["crashes"] += 1
                self._drop_locked(worker, sweep=True)
            elif worker.jobs_served >= recycle_after():
                self._stats["recycles"] += 1
                self._drop_locked(worker)
            elif worker.extra:
                # A subject-less compile still has model == "" after clearing
                # busy above. Let a burst retain already-admitted warm workers
                # briefly so asynchronous clients can submit their next wave
                # without repaying imports. Explicit K=0 still retires them
                # immediately. Model-bound extras keep the exact K-sized warm
                # reserve semantics below.
                other_spares = sum(spare is not worker for spare in self._spares_locked())
                if borrowed and spare_count() > 0:
                    worker.model = ""
                    worker.extra = False
                elif other_spares + self._spares_pending >= spare_count():
                    # The spare set is already full (a replacement was started when this
                    # one was taken); keeping it too would grow the set by one per extra.
                    self._drop_locked(worker)
                else:
                    # Back to the spare set: unbound, idle, warm.
                    worker.model = ""
                    worker.extra = False
            self._reclaim_pressure_locked()
            self._cv.notify_all()
        self.ensure_spares()

    def _drop_locked(self, worker: Worker, *, sweep: bool = False) -> None:
        """Retire a worker on a thread of its own. One that died or was killed (``sweep``)
        could not remove its scratch -- an import copy is a whole document -- and the sweep
        a worker runs as it starts never sees a worker that dies after it, so once this one
        is reaped its scratch is swept here (``temp_leftovers``)."""
        if worker in self._workers:
            self._workers.remove(worker)
        if worker not in self._retiring:
            self._retiring.append(worker)

        def retire():
            try:
                worker.kill()
            finally:
                with self._cv:
                    self._cv.notify_all()
            if sweep:
                _sweep_scratch()

        threading.Thread(target=retire, name="cadgen-worker-retire", daemon=True).start()

    def _reap_dead_locked(self) -> None:
        for worker in list(self._workers):
            if not worker.alive() and not worker.busy:
                self._drop_locked(worker, sweep=True)

    def reap_dead(self) -> None:
        with self._cv:
            self._reap_dead_locked()

    def shutdown(self) -> None:
        with self._cv:
            self._closed = True
            workers, self._workers = [*self._workers, *self._retiring], []
            self._retiring = []
        for worker in workers:
            worker.kill()

    def snapshot(self) -> dict:
        with self._cv:
            workers = [
                {
                    "pid": getattr(w, "pid", None),
                    "model": w.model,
                    "busy": w.busy,
                    "extra": w.extra,
                    "jobs": w.jobs_served,
                }
                for w in self._workers
            ]
            return {
                "workers": workers,
                "spares": len(self._spares_locked()),
                "sparesPending": self._spares_pending,
                "sparesAwaited": self._spare_waiters,
                "sparesWanted": spare_count(),
                "memory": self._memory_locked(),
                **self._stats,
            }
