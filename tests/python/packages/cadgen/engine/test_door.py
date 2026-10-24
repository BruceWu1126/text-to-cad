"""The engine's door (``cadgen.engine.door``): a hello's framing, a connection over a pair of named
pipes, the owner and type check on a pair, and the sweep of a killed client's pair."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


@unittest.skipIf(os.name == "nt", "the door is POSIX's: Windows keeps its loopback port")
class DoorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="door-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.clients = self.folder / "clients"
        self.clients.mkdir(mode=0o700)

    def open_door(self, serve) -> Path:
        from cadgen.engine import door

        path = self.folder / "x.door"
        opened = door.Door(path, lambda ident: door.answer(self.clients, ident, serve))
        self.addCleanup(opened.close)
        return path

    def test_a_hello_is_one_short_line_and_only_an_id_is_a_hello(self) -> None:
        from cadgen.engine import door

        ident = "0123456789abcdef" * 2
        line = door.hello(ident)
        self.assertEqual(line, ident.encode() + b"\n")
        self.assertLess(len(line), 512, "below PIPE_BUF: hellos written at once never interleave")
        self.assertEqual(door.hello_id(line.rstrip(b"\n")), ident)
        for other in (b"", ident[:-1].encode(), ident.upper().encode(), ident.encode() + b"0",
                      b"../" + ident[3:].encode(), ident.encode()[:-1] + b"\x00"):
            self.assertIsNone(door.hello_id(other), other)

    def test_a_request_and_its_answers_cross_the_pair_and_either_side_leaving_is_the_other_s_end(self) -> None:
        from cadgen.engine import door

        served = threading.Event()
        seen: list[bytes] = []

        def serve(reader, writer) -> None:
            seen.append(reader.readline())
            writer.write(b"one\ntwo\n")
            writer.flush()
            seen.append(reader.read())  # the client gone: end of file
            served.set()

        path = self.open_door(serve)
        connection = door.dial(path)
        self.assertIsNotNone(connection)
        with connection:
            connection.writer.write(b'{"op": "status"}\n')
            connection.writer.flush()
            self.assertEqual([connection.reader.readline(), connection.reader.readline()], [b"one\n", b"two\n"])
        self.assertTrue(served.wait(10))
        self.assertEqual(seen, [b'{"op": "status"}\n', b""])
        self.assertEqual(list(self.clients.iterdir()), [], "the pair's names went once it was connected")

        def leaves(reader, writer) -> None:
            reader.readline()  # and goes, without a word

        path = self.open_door(leaves)  # the same path: the first door is replaced, as a restart's is
        with door.dial(path) as connection:
            connection.writer.write(b"{}\n")
            connection.writer.flush()
            self.assertEqual(connection.reader.read(), b"", "the daemon's side gone: end of file")

    def test_a_door_nobody_reads_answers_nothing_at_once(self) -> None:
        from cadgen.engine import door

        path = self.folder / "dead.door"
        os.mkfifo(path, 0o600)
        started = time.monotonic()
        self.assertFalse(door.answering(path))
        self.assertIsNone(door.dial(path))
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(list(self.clients.iterdir()), [])
        self.assertFalse(door.answering(self.folder / "missing.door"))

    def test_a_pair_that_is_not_this_user_s_fifo_is_refused_and_never_served(self) -> None:
        from cadgen.engine import door

        served: list[int] = []
        ident = "ab" * 16
        up, down = door.pair(self.clients, ident)
        target = self.folder / "elsewhere"
        os.mkfifo(target, 0o600)
        held = os.open(target, os.O_RDWR)  # a reader there: a followed link would open
        self.addCleanup(os.close, held)

        def pair(make_down) -> None:
            for path in (up, down):
                path.unlink(missing_ok=True)
            os.mkfifo(up, 0o600)
            make_down()

        real = os.fstat

        def foreign(fd):
            found = real(fd)
            return os.stat_result((found.st_mode, found.st_ino, found.st_dev, found.st_nlink, os.getuid() + 1,
                                   found.st_gid, found.st_size, 0, 0, 0))

        def other_users() -> None:
            os.mkfifo(down, 0o600)
            self.addCleanup(os.close, os.open(down, os.O_RDWR))  # a client's reader: the open succeeds

        for make_down, as_another in ((lambda: down.write_bytes(b""), False),  # a plain file
                                      (lambda: down.symlink_to(target), False),  # a link, never followed
                                      (other_users, True)):  # a pipe of another user's
            pair(make_down)
            with mock.patch.object(door.os, "fstat", side_effect=foreign) if as_another else mock.MagicMock():
                door.answer(self.clients, ident, lambda reader, writer: served.append(1))
            self.assertEqual(served, [])
            self.assertFalse(up.exists() or down.is_symlink() or down.exists(), "the refused pair's names went")
        os.set_blocking(held, False)
        with self.assertRaises(BlockingIOError, msg="nothing was written through the link"):
            os.read(held, 16)


    def test_the_sweep_takes_a_killed_client_s_old_pair_without_opening_it(self) -> None:
        from cadgen.engine import door

        old, young, held = "a" * 32, "b" * 32, "c" * 32
        for ident in (old, young, held):
            for path in door.pair(self.clients, ident):
                os.mkfifo(path, 0o600)
        (self.clients / "notes.txt").write_text("not a pair")
        past = time.time() - door.STALE_SECONDS - 1
        for ident in (old, held):
            for path in door.pair(self.clients, ident):
                os.utime(path, (past, past))
        with mock.patch.object(door.os, "open", side_effect=AssertionError("a pair was opened")):
            removed = door.sweep(self.clients, {held})
        self.assertEqual(sorted(removed), [f"{old}.down", f"{old}.up"])
        self.assertEqual(sorted(p.name for p in self.clients.iterdir()),
                         sorted([f"{young}.down", f"{young}.up", f"{held}.down", f"{held}.up", "notes.txt"]))


if __name__ == "__main__":
    unittest.main()
