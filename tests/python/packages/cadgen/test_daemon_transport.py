"""Daemon channels have one close owner even when cancellation races cleanup, no one
peer's handshake holds up a listener's other peers, and a daemon stops whatever became of
its socket file."""

from __future__ import annotations

import json
import os
import multiprocessing.connection as mpc
import shutil
import socket
import struct
import subprocess
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock
import uuid

from cadgen.daemon import client, transport
from cadgen.daemon.transport import Channel


class PrivateAddressTest(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "Windows private brokers use named pipes")
    def test_deep_tmpdir_falls_back_to_short_authenticated_socket_path(self) -> None:
        deep = "/tmp/" + "private-profile/" * 10
        with mock.patch.object(tempfile, "gettempdir", return_value=deep):
            address = transport.private_address("0123456789ab")
        self.assertEqual(Path(address).parent, Path("/tmp"))
        self.assertLess(len(os.fsencode(address)), 100)
        listener = transport.Server(address, b"private-key")
        self.addCleanup(transport.clear_address, address)
        self.addCleanup(listener.close)
        received: list[bytes | None] = []
        errors: list[BaseException] = []

        def accept() -> None:
            try:
                channel = listener.accept()
                if channel is None:
                    raise AssertionError("listener closed before accepting the authenticated client")
                with channel:
                    received.append(channel.recv(1))
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=accept, daemon=True)
        thread.start()
        with transport.connect(address, b"private-key") as channel:
            channel.send(b"authenticated")
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(received, [b"authenticated"])


class _BlockingCloseConnection:
    def __init__(self) -> None:
        self.close_entered = threading.Event()
        self.release_close = threading.Event()
        self.closed = threading.Event()
        self.close_calls = 0
        self._guard = threading.Lock()

    def close(self) -> None:
        with self._guard:
            self.close_calls += 1
        self.close_entered.set()
        self.release_close.wait(2)
        self.closed.set()


class _BlockingReceiveConnection:
    def __init__(self) -> None:
        self.receive_entered = threading.Event()
        self.closed = threading.Event()
        self.close_calls = 0

    def recv_bytes(self) -> bytes:
        self.receive_entered.set()
        if not self.closed.wait(2):
            raise TimeoutError("close did not unblock receive")
        raise OSError("connection closed")

    def close(self) -> None:
        self.close_calls += 1
        self.closed.set()


class ChannelCloseOwnershipTest(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX socket descriptor reuse")
    def test_cancelled_partial_frame_does_not_consume_a_replacement_connection(self):
        receiver, sender = socket.socketpair()
        connection = mpc.Connection(receiver.detach())
        channel = Channel(connection)
        entered, release = threading.Event(), threading.Event()
        received, failures = [], []
        descriptor = connection.fileno()
        native_read = mpc.Connection._read
        reads = 0

        def read(fd, size):
            nonlocal reads
            if fd == descriptor:
                reads += 1
                if reads == 2:
                    entered.set()
                    if not release.wait(2):
                        raise RuntimeError("test did not release the frame reader")
            return native_read(fd, size)

        def receive():
            try:
                received.append(channel.recv())
            except BaseException as error:
                failures.append(error)

        replacement_receiver = replacement_sender = None
        # CPython captures its native read function in _recv defaults. This
        # private hook synchronizes a partial frame; revisit it on Python upgrades.
        with mock.patch.object(mpc.Connection._recv, "__defaults__", (read,)):
            thread = threading.Thread(target=receive, daemon=True)
            thread.start()
            try:
                sender.sendall(struct.pack("!i", 4))
                self.assertTrue(entered.wait(2), "reader did not consume the partial frame header")
                channel.close()
                replacement_receiver, replacement_sender = socket.socketpair()
                replacement_sender.sendall(b"next")
                release.set()
                thread.join(2)
                self.assertFalse(thread.is_alive(), "cancelled receive did not finish")
                self.assertEqual(failures, [])
                self.assertEqual(received, [b""], "cancelled channel stole the replacement connection's bytes")
                self.assertEqual(replacement_receiver.recv(4), b"next")
            finally:
                release.set()
                channel.close()
                sender.close()
                if replacement_sender is not None:
                    replacement_sender.close()
                thread.join(2)
                if replacement_receiver is not None:
                    replacement_receiver.close()

    @unittest.skipIf(os.name == "nt", "POSIX socket cancellation")
    def test_cancel_wakes_a_reader_waiting_for_its_first_frame(self):
        receiver, sender = socket.socketpair()
        connection = mpc.Connection(receiver.detach())
        channel = Channel(connection)
        entered = threading.Event()
        received, failures = [], []
        native_poll = connection.poll

        def poll(timeout):
            entered.set()
            return native_poll(timeout)

        def receive():
            try:
                received.append(channel.recv(60))
            except BaseException as error:
                failures.append(error)

        with mock.patch.object(connection, "poll", side_effect=poll):
            thread = threading.Thread(target=receive, daemon=True)
            thread.start()
            try:
                self.assertTrue(entered.wait(2), "receive did not start polling")
                channel.close()
                channel.close()
                thread.join(2)
                self.assertFalse(thread.is_alive(), "cancel did not wake the native reader")
                self.assertEqual(failures, [])
                self.assertEqual(received, [b""])
                self.assertTrue(connection.closed)
                self.assertEqual(channel.recv(0), b"")
                with self.assertRaises(OSError):
                    channel.send(b"after-close")
            finally:
                channel.close()
                sender.close()
                thread.join(2)


    def test_concurrent_close_has_one_underlying_owner_without_serializing_callers(self) -> None:
        connection = _BlockingCloseConnection()
        channel = Channel(connection)
        first = threading.Thread(target=channel.close)
        first.start()
        self.assertTrue(connection.close_entered.wait(1), "first close did not reach the connection")

        second_done = threading.Event()

        def close_again() -> None:
            channel.close()
            second_done.set()

        second = threading.Thread(target=close_again)
        second.start()
        try:
            self.assertTrue(second_done.wait(1), "duplicate close waited behind the blocking owner")
            self.assertEqual(connection.close_calls, 1)
        finally:
            connection.release_close.set()
            first.join(2)
            second.join(2)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())

    def test_close_unblocks_receive_and_repeated_close_is_idempotent(self) -> None:
        connection = _BlockingReceiveConnection()
        channel = Channel(connection)
        received: list[bytes | None] = []
        receiver = threading.Thread(target=lambda: received.append(channel.recv()))
        receiver.start()
        self.assertTrue(connection.receive_entered.wait(1), "receive did not begin")

        channel.close()
        channel.close()
        receiver.join(2)

        self.assertFalse(receiver.is_alive())
        self.assertEqual(received, [b""])
        self.assertEqual(connection.close_calls, 1)


class _PendingListener:
    """A native listener whose pending accept returns only when woken, as a pipe's pending
    connect does: closing the listener under it cancels nothing.

    ``accepted`` peers are returned at once, before the accept that waits; the woken
    accept returns ``connection``, a peer that connected as the listener closed, or None.
    """

    def __init__(self, connection=None, *, accepted=()):
        self.entered = threading.Event()
        self.woken = threading.Event()
        self.connection = connection
        self.accepted = list(accepted)
        self.close_calls = 0
        self.accept_calls = 0
        self.wake_calls = 0
        self.close_thread = None

    def accept(self):
        self.accept_calls += 1
        if self.accepted:
            return self.accepted.pop(0)
        self.entered.set()
        if not self.woken.wait(2):
            raise TimeoutError("pending accept was not woken")
        self.woken.clear()
        return self.connection

    def wake(self):
        self.wake_calls += 1
        self.woken.set()

    def close(self):
        self.close_calls += 1
        self.close_thread = threading.get_ident()


class ConnectTest(unittest.TestCase):
    # A daemon exiting (its version changed, or it idled out) can take a connection and close it
    # mid-handshake. That is no daemon, as a refused connection is: the caller spawns or waits for
    # the successor. An EOFError used to escape that path and fail the request outright (the
    # viewer's "Part derivation failed").
    def test_a_daemon_closing_the_connection_while_it_opens_is_no_daemon(self):
        with mock.patch.object(transport.mpc, "Client", side_effect=EOFError()):
            with self.assertRaises(OSError):
                transport.connect("cadgen-test.sock", b"key")


class ServerShutdownTest(unittest.TestCase):
    def accept_in_thread(self, listener):
        results, errors = [], []

        def accept():
            try:
                results.append(listener.accept())
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=accept, daemon=True)
        thread.start()
        return thread, results, errors

    def test_pending_accept_is_woken_then_closed_by_its_owner(self):
        pending = _PendingListener()
        with mock.patch.object(transport, "_listen", return_value=pending) as listen:
            listener = transport.Server("private", b"secret", backlog=4)
            thread, results, errors = self.accept_in_thread(listener)
            try:
                self.assertTrue(pending.entered.wait(1))
                listener.close()
                listener.close()
            finally:
                pending.woken.set()
                thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, [None])
        self.assertTrue(listener.closed)
        self.assertEqual(pending.wake_calls, 1)
        # Closed once, by the accept thread once its wait returned: never under the wait.
        self.assertEqual(pending.close_calls, 1)
        self.assertEqual(pending.close_thread, thread.ident)
        listen.assert_called_once_with("private", 4)

    def test_connection_returning_across_close_is_discarded_unauthenticated(self):
        connection = mock.Mock()
        pending = _PendingListener(connection)
        with mock.patch.object(transport, "_listen", return_value=pending), \
                mock.patch.object(transport, "_authenticate") as authenticate:
            listener = transport.Server("private", b"secret")
            thread, results, errors = self.accept_in_thread(listener)
            try:
                self.assertTrue(pending.entered.wait(1))
                listener.close()
            finally:
                pending.woken.set()
                thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(results, [None])
            connection.close.assert_called_once_with()
            authenticate.assert_not_called()

    def test_rejected_authentication_repairs_the_owned_key_and_keeps_accepting(self):
        stranger, connection = mock.Mock(), mock.Mock()
        native = mock.Mock()
        native.accept.side_effect = [stranger, connection]
        repair = mock.Mock()

        def authenticate(peer, authkey, timeout, cancelled):
            self.assertEqual((authkey, timeout), (b"secret", transport.HANDSHAKE_TIMEOUT_SECONDS))
            if peer is stranger:
                raise transport.mpc.AuthenticationError("stale")

        with mock.patch.object(transport, "_listen", return_value=native), \
                mock.patch.object(transport, "_authenticate", side_effect=authenticate):
            listener = transport.Server(
                "private",
                b"secret",
                on_authentication_error=repair,
            )
            channel = listener.accept()
        self.assertIs(channel._conn, connection)
        repair.assert_called_once_with()
        stranger.close.assert_called_once_with()
        channel.close()

    def test_close_without_accept_is_immediate_and_repeated_accept_stays_closed(self):
        pending = _PendingListener()
        with mock.patch.object(transport, "_listen", return_value=pending):
            listener = transport.Server("private", b"secret")
            listener.close()
            listener.close()
            self.assertIsNone(listener.accept())
            self.assertIsNone(listener.accept())
        self.assertEqual(pending.accept_calls, 0)
        self.assertEqual(pending.close_calls, 1)
        self.assertEqual(pending.wake_calls, 0)

    def test_close_does_not_wait_for_a_stalled_handshake(self):
        peer = mock.Mock()
        dropped = threading.Event()
        peer.close.side_effect = lambda: dropped.set()
        pending = _PendingListener(accepted=[peer])
        stalled, answer = threading.Event(), threading.Event()

        def authenticate(*_):
            stalled.set()
            if not answer.wait(2):
                raise TimeoutError("the stalled peer was never released")

        with mock.patch.object(transport, "_listen", return_value=pending), \
                mock.patch.object(transport, "_authenticate", side_effect=authenticate):
            listener = transport.Server("private", b"secret")
            thread, results, errors = self.accept_in_thread(listener)
            try:
                self.assertTrue(stalled.wait(1))
                self.assertTrue(pending.entered.wait(1), "accept waited on the stalled handshake")
                listener.close()
                thread.join(2)
                self.assertFalse(thread.is_alive(), "close waited on the stalled handshake")
                self.assertEqual(errors, [])
                self.assertEqual(results, [None])
                self.assertEqual(pending.close_calls, 1)
                peer.close.assert_not_called()
            finally:
                answer.set()
            # Authenticated after close: dropped, never handed out.
            self.assertTrue(dropped.wait(2))
            peer.close.assert_called_once_with()

    def test_interrupted_accept_preserves_exception_and_releases_native_ownership(self):
        pending = mock.Mock()
        interruption = KeyboardInterrupt("accept interrupted")
        pending.accept.side_effect = interruption
        with mock.patch.object(transport, "_listen", return_value=pending):
            listener = transport.Server("private", b"secret")
            with self.assertRaises(KeyboardInterrupt) as caught:
                listener.accept()
            self.assertIs(caught.exception, interruption)
            listener.close()
        pending.close.assert_called_once_with()
        pending.wake.assert_not_called()

    def real_listener(self, **options):
        address = transport.private_address(transport.identity_digest(uuid.uuid4().hex))
        listener = transport.Server(address, b"test-key", **options)
        self.addCleanup(transport.clear_address, address)
        self.addCleanup(listener.close)
        return listener

    def silent_peer(self, listener):
        """A peer that connects, receives its challenge and never answers it."""
        peer = Channel(transport.mpc.Client(listener.address, family=transport._family()))
        self.addCleanup(peer.close)
        challenge = peer.recv(10)
        self.assertTrue(challenge and challenge.startswith(b"#CHALLENGE#"), challenge)
        return peer

    def connect_in_thread(self, listener):
        """A client connecting on a thread of its own, so a listener that never serves
        it fails the test by its join rather than hanging it."""
        connected, failed = [], []

        def connect():
            try:
                connected.append(transport.connect(listener.address, b"test-key"))
            except BaseException as exc:
                failed.append(exc)

        thread = threading.Thread(target=connect, daemon=True)
        thread.start()
        return thread, connected, failed

    def test_real_pending_accept_exits_without_an_application_channel(self):
        listener = self.real_listener()
        entered = threading.Event()
        native_accept = listener._listener.accept

        def accept():
            entered.set()
            return native_accept()

        with mock.patch.object(listener._listener, "accept", side_effect=accept):
            thread, results, errors = self.accept_in_thread(listener)
            try:
                self.assertTrue(entered.wait(1))
                start = time.monotonic()
                listener.close()
                self.assertLess(time.monotonic() - start, 1, "close waited on a peer handshake")
            finally:
                listener.close()
                thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, [None])
        self.assertIsNone(listener.accept())

    def test_real_bad_authentication_does_not_stop_accepting_valid_clients(self):
        listener = self.real_listener()
        thread, results, errors = self.accept_in_thread(listener)
        try:
            with self.assertRaises(OSError):
                transport.connect(listener.address, b"wrong-key")
            with transport.connect(listener.address, b"test-key") as client:
                client.send(b"authenticated request")
                thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(len(results), 1)
                self.assertIsInstance(results[0], Channel)
                with results[0] as accepted:
                    self.assertEqual(accepted.recv(1), b"authenticated request")
        finally:
            listener.close()
            thread.join(2)

    def test_real_peer_that_never_answers_does_not_hold_up_the_next(self):
        # The daemon's accept thread once waited 28 minutes in the handshake of a viewer
        # connection whose challenge another viewer thread had read off a reused file
        # descriptor; every other client queued behind it. The silent peer's timeout is
        # a minute here, so the next client is served while it is still pending.
        listener = self.real_listener(handshake_timeout=60)
        thread, results, errors = self.accept_in_thread(listener)
        self.silent_peer(listener)
        client, connected, failed = self.connect_in_thread(listener)
        client.join(10)
        self.assertFalse(client.is_alive(), "a silent peer's handshake held up the next client's")
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual((errors, failed), ([], []))
        with connected[0] as sender, results[0] as accepted:
            sender.send(b"served")
            self.assertEqual(accepted.recv(10), b"served")

    def test_real_peer_that_never_answers_is_dropped_after_the_handshake_timeout(self):
        listener = self.real_listener(handshake_timeout=0.2)
        thread, results, errors = self.accept_in_thread(listener)
        silent = self.silent_peer(listener)
        self.assertEqual(silent.recv(10), b"", "the listener kept a peer that never answered")
        listener.close()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual((results, errors), ([None], []))

    def test_real_peer_that_answers_late_is_handed_out_by_a_later_accept(self):
        listener = self.real_listener()
        native_accept = listener._listener.accept
        native_waits = threading.Semaphore(0)

        def accept():
            native_waits.release()
            return native_accept()

        with mock.patch.object(listener._listener, "accept", side_effect=accept):
            thread, results, errors = self.accept_in_thread(listener)
            late = transport.mpc.Client(listener.address, family=transport._family())
            self.addCleanup(late.close)
            # The accept that took it, then the next: accept() stopped waiting for it.
            self.assertTrue(native_waits.acquire(timeout=10))
            self.assertTrue(native_waits.acquire(timeout=10))
            if os.name != "nt":
                # Its handshake wakes that accept without the address: no file to connect to.
                os.unlink(listener.address)
            transport.mpc.answer_challenge(late, b"test-key")
            transport.mpc.deliver_challenge(late, b"test-key")
            thread.join(10)
        self.assertFalse(thread.is_alive(), "the late peer was never handed out")
        self.assertEqual(errors, [])
        with results[0] as accepted:
            late.send_bytes(b"late")
            self.assertEqual(accepted.recv(10), b"late")

    def test_real_close_ends_a_silent_peers_handshake_and_join_waits_for_it(self):
        # A handshake thread still waiting on its peer when the process exits wakes into a
        # finalizing interpreter, which CPython before 3.14 can crash on (#528). close()
        # ends a pending handshake within a poll slice, not at the handshake timeout, and
        # join() returns once its thread has.
        listener = self.real_listener(handshake_timeout=60)
        before = set(threading.enumerate())
        thread, results, errors = self.accept_in_thread(listener)
        silent = self.silent_peer(listener)
        handshakes = [t for t in set(threading.enumerate()) - before if t.name == "cadgen-handshake"]
        self.assertEqual(len(handshakes), 1, "the silent peer's handshake was not running")
        listener.close()
        self.assertTrue(listener.join(10), "a handshake thread outlived close and join")
        self.assertFalse(handshakes[0].is_alive())
        self.assertEqual(silent.recv(10), b"", "the closed listener kept a peer mid-handshake")
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual((results, errors), ([None], []))

    def test_real_handshake_thread_that_cannot_start_drops_its_peer_and_keeps_accepting(self):
        listener = self.real_listener()
        original_start = threading.Thread.start
        refused = []

        def start(thread):
            if thread.name == "cadgen-handshake" and not refused:
                refused.append(thread)
                raise RuntimeError("can't start new thread")
            return original_start(thread)

        with mock.patch.object(threading.Thread, "start", new=start):
            thread, results, errors = self.accept_in_thread(listener)
            with self.assertRaises(OSError):
                transport.connect(listener.address, b"test-key")
            client, connected, failed = self.connect_in_thread(listener)
            client.join(10)
        self.assertFalse(client.is_alive(), "the listener stopped accepting after a thread failed to start")
        self.assertEqual((len(refused), errors, failed), (1, [], []))
        thread.join(10)
        self.assertFalse(thread.is_alive())
        with connected[0] as sender, results[0] as accepted:
            sender.send(b"served")
            self.assertEqual(accepted.recv(10), b"served")

    def test_real_accept_holds_for_no_new_peer_while_one_is_stalled(self):
        # With 50 silent peers connecting at once, the next client waited about 5 s:
        # accept() held a tenth of a second for each in turn. Once one handshake has
        # outlived its hold, accept() takes the next peers without one. The hold is a
        # minute for the second silent peer here, so a hold for it fails by the join.
        listener = self.real_listener(handshake_timeout=60)
        native_accept = listener._listener.accept
        native_waits = threading.Semaphore(0)

        def accept():
            native_waits.release()
            return native_accept()

        with mock.patch.object(listener._listener, "accept", side_effect=accept):
            thread, results, errors = self.accept_in_thread(listener)
            self.assertTrue(native_waits.acquire(timeout=10))
            self.silent_peer(listener)
            # The accept that took it, then the next: its handshake outlived the hold.
            self.assertTrue(native_waits.acquire(timeout=10))
            with mock.patch.object(transport, "HANDSHAKE_HOLD_SECONDS", 60):
                self.silent_peer(listener)
                client, connected, failed = self.connect_in_thread(listener)
                client.join(10)
            self.assertFalse(client.is_alive(), "accept held for a peer while another was stalled")
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual((errors, failed), ([], []))
        with connected[0] as sender, results[0] as accepted:
            sender.send(b"served")
            self.assertEqual(accepted.recv(10), b"served")

    def test_real_rejected_stale_key_is_republished_and_retried(self):
        with tempfile.TemporaryDirectory(prefix="cadgen-auth-repair-") as tmp:
            with mock.patch.object(transport, "state_dir", return_value=Path(tmp)):
                address = transport.private_address(transport.identity_digest(uuid.uuid4().hex))
                try:
                    self._assert_real_key_repair(address, replace=True)
                finally:
                    transport.clear_address(address)
                    transport._authkey_path(address).unlink(missing_ok=True)

    def test_real_missing_key_is_republished_and_retried(self):
        with tempfile.TemporaryDirectory(prefix="cadgen-auth-repair-") as tmp:
            with mock.patch.object(transport, "state_dir", return_value=Path(tmp)):
                address = transport.private_address(transport.identity_digest(uuid.uuid4().hex))
                try:
                    self._assert_real_key_repair(address, replace=False)
                finally:
                    transport.clear_address(address)
                    transport._authkey_path(address).unlink(missing_ok=True)

    def _assert_real_key_repair(self, address: str, *, replace: bool) -> None:
        owned_key = b"owned-key"
        if replace:
            transport.publish_authkey(address, b"stale-key")
        listener = transport.Server(
            address,
            owned_key,
            on_authentication_error=lambda: transport.publish_authkey(address, owned_key),
        )
        thread, results, errors = self.accept_in_thread(listener)
        try:
            with client._connect(address) as connection:
                connection.send(b"recovered")
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(transport.read_authkey(address), owned_key)
            with results[0] as accepted:
                self.assertEqual(accepted.recv(1), b"recovered")
        finally:
            listener.close()
            thread.join(2)


class DaemonStopsTest(unittest.TestCase):
    """A daemon stops when it decides to, whatever became of its socket file, and removes
    no socket but its own.

    It woke its own blocked accept by connecting to its address. With the socket file
    gone -- its state directory deleted, a tmp cleaner, a test tearing down -- that connect
    failed silently and the daemon waited in accept forever, with its workers: nothing
    could reach it to stop it, and its idle timeout could not either. Test runs left three
    to five behind. No build and no kernel here: a worker's slot is a job in flight that
    needs neither."""

    STOP_SECONDS = 30.0

    def test_a_retired_daemon_stops_when_its_last_job_ends_though_its_socket_file_is_gone(self):
        _, address, daemon, status = self._start_daemon()
        # A job in flight: a worker's slot, held for as long as its connection is open.
        lease = client._connect(address)
        self.addCleanup(lease.close)
        lease.send(json.dumps({"kind": "slot", "label": "held", "token": status["token"]}).encode("utf-8"))
        self.assertEqual(json.loads(lease.recv(30.0)), {"slot": "granted"})
        # Retired while it runs: it keeps serving its address until the job ends.
        with client._connect(address) as retire:
            retire.send(json.dumps({"token": "retired"}).encode("utf-8"))
            self.assertEqual(json.loads(retire.recv(30.0)), {"restart": True})
        if os.name != "nt":
            os.unlink(address)  # a named pipe has no file to lose
        lease.close()  # the last job ends, so the daemon stops
        self.assertEqual(self._await_exit(daemon, address), 0, self._log(address))

    @unittest.skipIf(os.name == "nt", "a named pipe has no file another daemon could take over")
    def test_a_daemon_retires_when_its_address_names_a_successors_socket(self):
        # Its state directory removed under it, socket, key and locks with it, a client
        # starts a successor: a fresh lock file lets it bind the same address. Stopping,
        # the first daemon unlinked that address, the successor's socket, and every client
        # then found no daemon while the successor held the lock. Losing ownership itself
        # retires the predecessor, without a signal, request or idle timeout.
        state, address, daemon, _ = self._start_daemon(idle_timeout=3600)
        shutil.rmtree(state)
        successor = transport.Server(address, transport.ensure_authkey(address))
        self.addCleanup(successor.close)
        theirs = os.lstat(address).st_ino
        self.assertEqual(self._await_exit(daemon, address), 0)
        left = os.lstat(address).st_ino if os.path.lexists(address) else None
        self.assertEqual(left, theirs, "the daemon that stopped removed its successor's socket")

    @unittest.skipIf(os.name == "nt", "a named pipe has no socket file to lose")
    def test_a_daemon_retires_when_its_socket_is_deleted_even_with_a_job_in_flight(self):
        _, address, daemon, status = self._start_daemon(idle_timeout=3600)
        lease = client._connect(address)
        self.addCleanup(lease.close)
        lease.send(json.dumps({"kind": "slot", "label": "held", "token": status["token"]}).encode("utf-8"))
        self.assertEqual(json.loads(lease.recv(30.0)), {"slot": "granted"})
        os.unlink(address)
        # Keep the lease open: ownership loss, not completion or idleness, ends the daemon.
        self.assertEqual(self._await_exit(daemon, address), 0, self._log(address))

    def _start_daemon(self, *, idle_timeout: int = 4) -> tuple[Path, str, subprocess.Popen, dict]:
        """A daemon of this test's own on a private address: its state directory, address,
        process and status."""
        state = Path(tempfile.mkdtemp(prefix="cgs-", dir=None if os.name == "nt" else "/tmp"))
        self.addCleanup(shutil.rmtree, state, ignore_errors=True)
        address = rf"\\.\pipe\cadgen-stops-{uuid.uuid4().hex}" if os.name == "nt" else str(state / "d.sock")
        self.enterContext(mock.patch.dict(os.environ, {
            "CADGEN_DAEMON_STATE_DIR": str(state),
            "CADGEN_DAEMON_SOCKET": address,
            "CADGEN_DAEMON_SPARES": "0",  # no worker, so no kernel import
            "CADGEN_DAEMON_IDLE_TIMEOUT": str(idle_timeout),
        }))
        daemon = client._spawn_daemon(address)
        self.addCleanup(self._kill, daemon)
        return state, address, daemon, self._await_status(daemon)

    def _await_exit(self, daemon: subprocess.Popen, address: str) -> int:
        try:
            return daemon.wait(self.STOP_SECONDS)
        except subprocess.TimeoutExpired:
            self.fail(f"the daemon still runs {self.STOP_SECONDS:.0f} s after it was told to stop:\n{self._log(address)}")

    def _await_status(self, daemon: subprocess.Popen) -> dict:
        deadline = time.monotonic() + self.STOP_SECONDS
        while (status := client.status()) is None:
            if daemon.poll() is not None or time.monotonic() >= deadline:
                self.fail(f"the daemon never answered (exit {daemon.poll()}):\n{self._log(client.daemon_address())}")
            time.sleep(0.05)
        return status

    @staticmethod
    def _log(address: str) -> str:
        try:
            return client.log_path(address).read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            return f"(no log: {error})"

    @staticmethod
    def _kill(daemon: subprocess.Popen) -> None:
        if daemon.poll() is None:
            daemon.kill()
            daemon.wait(DaemonStopsTest.STOP_SECONDS)


if __name__ == "__main__":
    unittest.main()
