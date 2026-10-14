"""CalculiX on first use: the pinned build downloaded once into cadgen's cache, checked, and found after.

No network: each test serves a small package it builds itself in place of conda-forge's.
- every pinned platform names one ``calculix`` package, and this machine maps to a pinned platform or to none;
- the first lookup downloads, checks each file's sha256, unpacks the ``pkg-*.tar.zst`` inside the ``.conda`` and
  finds ``bin/ccx``; the next lookup finds it in the cache without the network;
- a file that does not match its sha256 is refused, and nothing is left half-unpacked;
- a failed download is a reason, never an exception, and ``CADGEN_CCX=0`` turns CalculiX off before any lookup;
- the module is stdlib-only at import.
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from cadgen._internal.fea import calculix_install, calculix_pins

try:
    import zstandard
except ImportError:  # the fea extra brings it; the stdlib has zstd from 3.14
    zstandard = None


def _conda_package(files: dict[str, bytes]) -> bytes:
    """A ``.conda``: a zip holding ``pkg-*.tar.zst`` (the files) beside ``info-*.tar.zst`` (conda's metadata)."""
    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
    packed = zstandard.ZstdCompressor().compress(tar_bytes.getvalue())
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("metadata.json", "{}")
        archive.writestr("pkg-calculix.tar.zst", packed)
        archive.writestr("info-calculix.tar.zst", zstandard.ZstdCompressor().compress(b""))
    return out.getvalue()


class _Served:
    """``urlopen`` over a dict of url -> bytes, counting the requests."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files, self.requests = files, 0

    def __call__(self, url, timeout=None):
        self.requests += 1
        if url not in self.files:
            raise OSError(f"no route to {url}")
        return io.BytesIO(self.files[url])


class Pins(unittest.TestCase):
    def test_every_platform_pins_one_calculix_and_this_machine_maps_to_a_pin_or_none(self):
        for platform, files in calculix_pins.PINS.items():
            names = [url.rsplit("/", 1)[-1] for url, _, _ in files]
            self.assertEqual(sum(name.startswith("calculix-") for name in names), 1, platform)
            self.assertTrue(all(len(sha) == 64 for _, sha, _ in files), platform)
        self.assertIn(calculix_install.conda_platform(), (*calculix_pins.PINS, None))

    def test_the_module_is_stdlib_only_at_import(self):
        import subprocess
        import sys

        code = ("import sys; import cadgen._internal.fea.calculix_install; "
                "print(','.join(m for m in ('numpy', 'zstandard', 'OCP') if m in sys.modules))")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, "")


@unittest.skipIf(zstandard is None, "zstandard is not installed (the fea extra brings it)")
class Download(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.TemporaryDirectory()
        self.addCleanup(self.cache.cleanup)
        package = _conda_package({"bin/ccx": b"#!/bin/sh\necho 'This is Version 2.23'\n",
                                  "Library/bin/ccx.exe": b"MZ"})
        self.url = "https://conda.example/calculix-2.23.conda"
        self.pins = {"test-64": ((self.url, hashlib.sha256(package).hexdigest(), len(package)),)}
        self.served = _Served({self.url: package})
        env = {"CADGEN_CACHE_DIR": self.cache.name, "PATH": ""}
        for patch in (mock.patch.dict(os.environ, env), mock.patch.object(calculix_install, "PINS", self.pins),
                      mock.patch.object(calculix_install, "conda_platform", return_value="test-64"),
                      mock.patch.object(calculix_install.urllib.request, "urlopen", self.served)):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("CADGEN_CCX", None)
        calculix_install._cached_lookup.cache_clear()
        self.addCleanup(calculix_install._cached_lookup.cache_clear)

    def test_the_first_lookup_downloads_and_the_next_finds_it_in_the_cache(self):
        said: list[str] = []
        first = calculix_install.ensure_calculix(say=said.append)
        self.assertEqual(first.source, "download")
        self.assertTrue(first.path.is_file())
        self.assertEqual(len(said), 1)
        self.assertIn("downloading the CalculiX solver (once", said[0])
        calculix_install._cached_lookup.cache_clear()
        again = calculix_install.ensure_calculix(say=said.append)
        self.assertEqual((again.source, again.path, self.served.requests, len(said)), ("cache", first.path, 1, 1))

    def test_a_file_that_does_not_match_its_sha256_is_refused_and_nothing_is_left(self):
        self.served.files[self.url] += b"tampered"
        found = calculix_install.ensure_calculix()
        self.assertIsNone(found.path)
        self.assertIn("does not match its pinned sha256", found.source)
        tools = Path(self.cache.name) / "tools"
        self.assertEqual([p.name for p in tools.iterdir() if not p.name.endswith(".lock")], [])

    def test_a_failed_download_is_a_reason_and_ccx_0_turns_calculix_off_first(self):
        self.served.files.clear()
        failed = calculix_install.ensure_calculix()
        self.assertIsNone(failed.path)
        self.assertTrue(failed.source.startswith("CalculiX could not be downloaded"), failed.source)
        with mock.patch.dict(os.environ, {"CADGEN_CCX": "0"}):
            off = calculix_install.ensure_calculix()
        self.assertEqual((off.path, self.served.requests), (None, 1))


if __name__ == "__main__":
    unittest.main()
