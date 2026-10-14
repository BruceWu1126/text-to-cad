"""CalculiX on first use: download the pinned build once, like the snapshot browser.

A solve that CalculiX should run calls :func:`ensure_calculix`. The first time on a machine it
downloads the files :mod:`cadgen._internal.fea.calculix_pins` names for this platform (9 to 21 MB),
checks each one's sha256, and unpacks them into one folder in cadgen's cache; every later solve
finds that folder and starts at once. Nothing is installed system-wide and nothing needs conda:
a conda package is a zip holding a zstd tarball, and the binaries find their libraries through
relative rpaths, so unpacking is the whole install.

Where the solver comes from, in order:

1. ``CADGEN_CCX``: a path to a ``ccx`` binary, used as it is; ``0`` turns CalculiX off.
2. The pinned build in the cache, once downloaded.
3. A ``ccx`` already on PATH (someone's own install).
4. The pinned build, downloaded now.

Any step that cannot finish (no pinned build for this platform, no network, no zstd) answers
``None`` with one sentence why, and the solve runs on the built-in solver and says so. Nothing
here imports beyond the stdlib at module scope.
"""

from __future__ import annotations

import functools
import hashlib
import io
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cadgen._internal.fea.calculix_pins import PINS, VERSION

__all__ = ["Calculix", "conda_platform", "ensure_calculix", "installed_dir"]

@dataclass(frozen=True)
class Calculix:
    """A CalculiX to run, or why there is none (``path`` None)."""

    path: Path | None
    #: Where it came from: ``CADGEN_CCX``, ``cache``, ``PATH`` or ``download``; or why it is missing.
    source: str


def conda_platform() -> str | None:
    """This machine as a conda platform (``osx-arm64``), or None when cadgen pins no build for it."""
    machine = platform.machine().lower()
    arch = {"arm64": "arm64", "aarch64": "aarch64", "x86_64": "64", "amd64": "64"}.get(machine)
    system = {"darwin": "osx", "linux": "linux", "win32": "win"}.get(sys.platform) or (
        "linux" if sys.platform.startswith("linux") else None
    )
    if arch is None or system is None:
        return None
    name = f"{system}-{arch}"
    if system == "osx" and arch == "aarch64":
        name = "osx-arm64"
    return name if name in PINS else None


def installed_dir(conda: str) -> Path:
    """The cache folder the pinned build for ``conda`` unpacks into; named by its pins, so a new pin
    is a new folder and an old one is never half-replaced."""
    from cadgen._internal.cache_paths import cache_root

    digest = hashlib.sha256("".join(sha for _, sha, _ in PINS[conda]).encode()).hexdigest()[:12]
    return cache_root() / "tools" / f"calculix-{VERSION}-{conda}-{digest}"


def _binary(prefix: Path) -> Path:
    return prefix / "Library" / "bin" / "ccx.exe" if sys.platform == "win32" else prefix / "bin" / "ccx"


def ensure_calculix(*, say: Callable[[str], None] | None = None, download: bool = True) -> Calculix:
    """The CalculiX this machine should run, downloading the pinned build the first time.

    ``say`` hears the one progress line a download prints; ``download=False`` only looks.
    """
    override = os.environ.get("CADGEN_CCX", "").strip()
    if override == "0":
        return Calculix(None, "CalculiX is turned off (CADGEN_CCX=0)")
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return Calculix(path, "CADGEN_CCX")
        return Calculix(None, f"CADGEN_CCX names {override}, which is not a file")
    return _found(download, say)


@functools.lru_cache(maxsize=None)
def _cached_lookup() -> Calculix | None:
    """The cache or PATH answer, looked up once per process (a found binary stays found)."""
    conda = conda_platform()
    if conda is not None and _binary(installed_dir(conda)).is_file():
        return Calculix(_binary(installed_dir(conda)), "cache")
    from cadgen._internal.fea.calculix import NAMES

    for name in NAMES:
        found = shutil.which(name)
        if found:
            return Calculix(Path(found), "PATH")
    return None


def _found(download: bool, say: Callable[[str], None] | None) -> Calculix:
    if (known := _cached_lookup()) is not None:
        return known
    conda = conda_platform()
    if conda is None:
        return Calculix(None, f"cadgen has no CalculiX build for {sys.platform} {platform.machine()}")
    if not download:
        return Calculix(None, "CalculiX is not downloaded yet")
    try:
        prefix = _download(conda, say)
    except Exception as exc:  # noqa: BLE001 - any failure falls back to the built-in solver, said once
        return Calculix(None, f"CalculiX could not be downloaded ({exc})")
    _cached_lookup.cache_clear()
    return Calculix(_binary(prefix), "download")


def _download(conda: str, say: Callable[[str], None] | None) -> Path:
    """Fetch, check and unpack every pinned file into a fresh folder, then move it into place whole,
    under a lock so two solves starting together download once."""
    from cadgen._internal.file_lock import exclusive

    target = installed_dir(conda)
    target.parent.mkdir(parents=True, exist_ok=True)
    with exclusive(target.parent / f"{target.name}.lock"):
        if _binary(target).is_file():  # another process finished it while this one waited
            return target
        decompress = _zstd_reader()
        size_mb = sum(size for _, _, size in PINS[conda]) / 1e6
        if say is not None:
            say(f"cadgen: downloading the CalculiX solver (once, about {size_mb:.0f} MB)...")
        staging = Path(tempfile.mkdtemp(prefix=f"{target.name}.", dir=target.parent))
        try:
            for url, sha256, _ in PINS[conda]:
                with urllib.request.urlopen(url, timeout=60) as response:
                    data = response.read()
                if hashlib.sha256(data).hexdigest() != sha256:
                    raise ValueError(f"{url.rsplit('/', 1)[-1]} does not match its pinned sha256")
                _unpack(data, staging, decompress)
            if not _binary(staging).is_file():
                raise FileNotFoundError(f"the pinned packages hold no {_binary(Path()).as_posix()}")
            staging.rename(target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return target


def _zstd_reader() -> Callable[[bytes], io.BufferedIOBase]:
    """A zstd stream reader: the stdlib's from Python 3.14, else the ``zstandard`` the fea extra brings."""
    try:
        from compression import zstd  # type: ignore[import-not-found]

        return lambda data: zstd.ZstdFile(io.BytesIO(data))
    except ImportError:
        pass
    try:
        import zstandard
    except ImportError as exc:
        raise RuntimeError("unpacking it needs zstandard, which the fea extra installs") from exc
    return lambda data: zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data))


def _unpack(data: bytes, into: Path, decompress: Callable[[bytes], io.BufferedIOBase]) -> None:
    """Unpack one ``.conda`` (a zip holding ``pkg-*.tar.zst``; ``info-*`` is conda's metadata, unneeded)."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        inner = next(name for name in archive.namelist() if name.startswith("pkg-") and name.endswith(".tar.zst"))
        with tarfile.open(fileobj=decompress(archive.read(inner)), mode="r|") as tar:
            # The data filter refuses absolute paths and links leaving the folder; it keeps exec bits.
            if hasattr(tarfile, "data_filter"):
                tar.extractall(into, filter="data")
            else:  # Python 3.11 before 3.11.4
                tar.extractall(into)
