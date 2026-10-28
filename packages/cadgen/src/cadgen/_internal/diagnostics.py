"""Read-only doctor report, schema 1. No setup, pruning, engine start or work requests.

A project probe imports only cadgen in the explicitly named Python. Engine probes send only
hello, in a bounded child so a broken listener cannot hang doctor. POSIX hello uses the normal
transport's temporary FIFO pair, removed on close; no persistent state is changed.
"""
from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path

from cadgen._internal import doctor_files, installations

SCHEMA = 1
PROBE_SECONDS = 3
MAX_ENGINES = 16
MAX_RECORDS = 128
# Supplied by the application composition root; the report knows no provider.
HOST_INVENTORY = None
# Suppress import chatter, including custom cadgen imports; never return stderr or exception text.
_PROJECT_PROBE = """
import contextlib, io, json, sys
result = {'status': 'unusable'}
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    try:
        import cadgen
        result = {'status': 'ok', 'release': cadgen.__version__, 'path': cadgen.__file__,
                  'python': sys.executable, 'python_version': sys.version.split()[0]}
    except ModuleNotFoundError as error:
        result = {'status': 'missing' if error.name == 'cadgen' else 'unusable'}
    except BaseException:
        pass
sys.__stdout__.write('CADGEN_DIAGNOSTIC=' + json.dumps(result) + '\\n')
"""


def _child(command: list[str], *, seconds: float = PROBE_SECONDS) -> dict:
    try:
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=seconds, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    except OSError:
        return {"status": "unusable"}
    if done.returncode or len(done.stdout) > 65536:
        return {"status": "unusable"}
    try:
        line = done.stdout.splitlines()[-1]
        data = json.loads(line.removeprefix("CADGEN_DIAGNOSTIC=")) if line.startswith("CADGEN_DIAGNOSTIC=") else None
        return data if isinstance(data, dict) else {"status": "unusable"}
    except (ValueError, IndexError, RecursionError):
        return {"status": "unusable"}


def project(python: str) -> dict:
    path = os.path.abspath(os.path.expanduser(python))
    data = _child([path, "-B", "-P", "-c", _PROJECT_PROBE])
    result = {"requested_python": path, "status": data.get("status", "unusable")}
    if result["status"] not in {"ok", "missing", "unusable", "timeout"}:
        result["status"] = "unusable"
    if result["status"] == "ok":
        release = data.get("release")
        if not isinstance(release, str) or not installations.VERSION.fullmatch(release):
            return {**result, "status": "unusable"}
        result["release"] = release
        for key in ("path", "python", "python_version"):
            value = data.get(key)
            if isinstance(value, str) and len(value) <= 4096:
                result[key] = value
    return result


def _list(folder: Path) -> tuple[list[Path], str]:
    try:
        with os.scandir(folder) as entries:
            paths = []
            for index, entry in enumerate(entries):
                if entry.name.endswith(".json"):
                    paths.append(Path(entry.path))
                if index >= MAX_RECORDS:
                    return paths[:MAX_RECORDS], "limited"
        return sorted(paths), "ok"
    except FileNotFoundError:
        return [], "missing"
    except OSError:
        return [], "unreadable"


def registered(state: Path) -> dict:
    folder = state / "installations"
    paths, status = _list(folder)
    found = []
    for path in paths:
        data, state_of_file = doctor_files.read(path)
        item = {"path": str(path), "status": state_of_file}
        if data is not None:
            parsed = installations._parse(json.dumps(data).encode())
            if parsed is None or parsed["version"] != path.stem or not installations.VERSION.fullmatch(path.stem):
                item["status"] = "invalid"
            else:
                item.update({key: parsed[key] for key in ("version", "python", "cadgen", "prefix")})
                # Valid means the registered paths exist, not that every interpreter was executed.
                item["status"] = "valid" if installations.alive(parsed) else "stale"
        found.append(item)
    return {"path": str(folder), "status": status, "records": found}


def _listener(port: int) -> str:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return "conflicting_listener"
    except ConnectionRefusedError:
        return "missing"
    except OSError:
        return "unknown"


def _probe_port(port: int) -> dict:
    """Runs in a bounded child. A refused hello never becomes permission to send status/work."""
    from cadgen.engine import daemon, door
    from cadgen.engine.previous import version

    folder = daemon.state_dir()
    if not folder.exists():
        return {"status": _listener(port)}
    if daemon.folder_refusal(folder):
        return {"status": "unreadable"}
    address = folder / (daemon._tag(port=port) + (".port" if daemon.WINDOWS else ".door"))
    if not address.exists():
        return {"status": _listener(port)}
    connection = None
    try:
        message = {"op": "hello", "protocol": daemon.PROTOCOL, "release": version(), "port": port}
        if daemon.WINDOWS:
            raw, status = doctor_files.read_bytes(address)
            if raw is None:
                return {"status": "invalid_endpoint"}
            data = raw.decode("utf-8").split()
            if len(data) != 2:
                return {"status": "invalid_endpoint"}
            sock = socket.create_connection(("127.0.0.1", int(data[0])), timeout=0.5)
            sock.settimeout(0.5)
            stream = sock.makefile("rwb")
            connection = door.Connection(stream, stream, stream, sock)
            # Windows authenticates the token prefix before reading the rest of the line.
            message = {"token": data[1], **message}
        else:
            if not stat.S_ISFIFO(address.stat().st_mode):
                return {"status": "invalid_endpoint"}
            if not door.answering(address):
                listener = _listener(port)
                return {"status": "stale_endpoint" if listener == "missing" else listener}
            connection = door.dial(address, timeout=0.5, repair=False)
        if connection is None:
            status = _listener(port)
            return {"status": "stale_endpoint" if status == "missing" else status}
        connection.writer.write((json.dumps(message) + "\n").encode())
        connection.writer.flush()
        answer = json.loads(connection.reader.readline(65537))
        if not isinstance(answer, dict):
            return {"status": "unknown_protocol"}
        identity = {}
        release = answer.get("release")
        if isinstance(release, str) and installations.VERSION.fullmatch(release):
            identity["release"] = release
        for key in ("protocol", "port"):
            if type(answer.get(key)) is int:
                identity[key] = answer[key]
        compatible = (not answer.get("failed") and answer.get("ok") is True
                      and identity == {key: message[key] for key in ("protocol", "release", "port")}
                      and isinstance(answer.get("instance"), str) and bool(answer["instance"]))
        return {"status": "compatible" if compatible else "incompatible" if identity else "unknown_protocol",
                **identity}
    except PermissionError:
        return {"status": "unreadable"}
    except (OSError, ValueError, TypeError):
        return {"status": "unknown_protocol"}
    finally:
        if connection is not None:
            connection.close()


def engine(port: int) -> dict:
    code = ("import contextlib,io,json,sys; "
            "from cadgen._internal.diagnostics import _probe_port; "
            "result=_probe_port(int(sys.argv[1])); "
            "sys.__stdout__.write('CADGEN_DIAGNOSTIC='+json.dumps(result)+'\\n')")
    data = _child([sys.executable, "-B", "-P", "-c", code, str(port)])
    allowed = {"compatible", "incompatible", "unknown_protocol", "invalid_endpoint", "stale_endpoint", "missing",
               "conflicting_listener", "unknown", "unreadable", "timeout", "unusable"}
    result = {"port": port, "status": data.get("status") if data.get("status") in allowed else "unknown"}
    release = data.get("release")
    if isinstance(release, str) and installations.VERSION.fullmatch(release):
        result["release"] = release
    if type(data.get("protocol")) is int:
        result["protocol"] = data["protocol"]
    if type(data.get("port")) is int and data["port"] != port:
        result["advertised_port"] = data["port"]
    return result


def report(python: str | None, skill: Path | None, *, requested_skill: str | None = None) -> dict:
    import cadgen
    from cadgen.cli import _PIN_RE, _RUN_PIN_RE
    from cadgen.engine import daemon
    from cadgen.viewer.recents import state_dir

    release = cadgen.__version__
    state = state_dir()
    installation_data = registered(state)
    named = os.environ.get(installations.NAME_ENV)
    named = named if named and installations.VERSION.fullmatch(named) else None
    if named is None:
        for record in installation_data["records"]:
            if (record["status"] == "valid" and installations._same_place(record["prefix"], sys.prefix)
                    and (record["version"] == release or record["version"].startswith(release + "-dev."))):
                named = record["version"]
    if named is None and not installations.editable():
        named = release
    current = {"release": release, "installation": named, "python": sys.executable,
               "python_version": sys.version.split()[0], "path": str(Path(cadgen.__file__).resolve()),
               "executable": os.path.abspath(sys.argv[0]), "proxy": installations.proxy_path()}
    issues = []

    def issue(code: str, detail: str, remedy: str, *, error: bool = False, mismatch: bool = False):
        issues.append({"code": code, "severity": "error" if error else "warning", "detail": detail,
                       "remedy": remedy, "exit_code": 3 if mismatch else 5 if error else 0})

    pin = None
    skill_unreadable = False
    if skill:
        contents, status = doctor_files.read_bytes(skill)
        if contents is None:
            skill_unreadable = True
        else:
            try:
                text = contents.decode("utf-8")
                match = _PIN_RE.search(text) or _RUN_PIN_RE.search(text)
                pin = match.group("pin") if match else None
            except UnicodeError:
                skill_unreadable = True
    if pin is not None and not installations.VERSION.fullmatch(pin):
        pin = None
        skill_state = "invalid"
    else:
        skill_state = "unreadable" if skill_unreadable else "pinned" if pin else "unpinned" if skill else "missing" if requested_skill else "not_requested"
    skill_data = {"path": str(skill) if skill else None, "release": pin, "status": skill_state}
    if skill_state in {"invalid", "missing", "unreadable"}:
        issue("skill_unavailable", "The requested skill or its release pin cannot be read.",
              "Pass a readable SKILL.md or its directory.", error=True)
    if pin and pin not in {release, current["installation"]}:
        issue("skill_mismatch", "The requested skill pins a different CLI installation.",
              f"Run the skill's cadgen +{pin} command, then repeat doctor.", error=True, mismatch=True)
    project_data = project(python) if python is not None else {"status": "not_requested"}
    if python is not None and project_data["status"] != "ok":
        issue("project_unavailable", "The requested Python cannot import cadgen.",
              f"Check that interpreter path and install cadgen=={release} in that environment.", error=True)
    elif python is not None and project_data.get("release") != release:
        issue("project_mismatch", "The project Python imports a different cadgen release from this CLI.",
              f"Install cadgen=={release} in the project Python, or select its matching CLI and engine.",
              error=True, mismatch=True)
    for item in installation_data["records"]:
        if item["status"] != "valid":
            issue("installation_" + item["status"], "An installation record is stale or cannot be read: " + item["path"],
                  "Re-run that release's cadgen doctor to register it; inspect stale records before doctor --prune.")
    if installation_data["status"] in {"limited", "unreadable"}:
        issue("installation_inventory_incomplete", "The installation inventory is incomplete.",
              "Inspect the registered installation folder locally; no environments were scanned.")
    hosts = HOST_INVENTORY() if HOST_INVENTORY else {
        "coverage": "No host inventory capability was supplied.", "sources": []}
    ports = set()
    try:
        selected = daemon.selected_port()
        ports.add(selected)
    except ValueError:
        selected = None
        issue("invalid_port", "CADGEN_PORT is invalid.", "Set CADGEN_PORT to an integer from 1 to 65535.", error=True)
    paths, records_state = _list(state / "engines")
    records = []
    for path in paths:
        data, status = doctor_files.read(path)
        port = doctor_files._port(data.get("port")) if data else None
        item = {"path": str(path), "status": status if data is None else "known" if port and data.get("schema") == 3 else "legacy" if port else "invalid"}
        if port:
            item["port"] = port
            ports.add(port)
        records.append(item)
        if item["status"] in {"invalid", "unreadable", "too_large", "legacy"}:
            issue("engine_record_" + item["status"], "An engine record is invalid, unreadable or from an older schema: " + str(path),
                  "Inspect the record locally; it is historical evidence and never selects or starts a server.")
    for source in hosts["sources"]:
        if source.get("port"):
            ports.add(source["port"])
        if source["status"] in {"invalid", "unreadable", "unknown", "limited", "too_large"}:
            issue("host_" + source["status"], "A supported host source could not be fully interpreted: " + source["path"],
                  "Inspect that source locally; the inventory does not establish what the host has loaded.")
    ordered = sorted(ports, key=lambda port: (port != selected, port))
    engines = [engine(port) for port in ordered[:MAX_ENGINES]]
    if len(ordered) > MAX_ENGINES or records_state in {"limited", "unreadable"}:
        issue("engine_inventory_incomplete", "The known-engine inventory reached its bound or could not be read.",
              "Inspect the engine records locally and repeat for a specific CADGEN_PORT.")
    for item in engines:
        item["selected"] = item["port"] == selected
        item["expected_release"] = release
        item["configured_releases"] = sorted({source["release"] for source in hosts["sources"]
                                               if source.get("release") and source.get("port") == item["port"]
                                               and source.get("kind") != "cached_plugin"})
        item["records"] = [record["path"] for record in records if record.get("port") == item["port"]]
        if item["selected"] and item["status"] != "compatible":
            issue("engine_" + item["status"], "The selected engine is " + item["status"] + ".",
                  f"Use cadgen {release} on port {selected}. Start it explicitly if absent; stop only an engine you own.",
                  error=True, mismatch=item["status"] == "incompatible")
        elif item["records"] and item["status"] in {"missing", "stale_endpoint"}:
            issue("stale_engine_record", f"A recorded engine on port {item['port']} does not answer.",
                  "A record is historical evidence; start an engine explicitly only when you need that port.")
    provider_ports = {}
    for source in hosts["sources"]:
        if source.get("release") and source.get("port") and source.get("kind") != "cached_plugin":
            provider_ports.setdefault(source["port"], set()).add(source["release"])
    for port, releases in provider_ports.items():
        if len(releases) > 1:
            issue("provider_port_mismatch", f"Discoverable host configurations pin different releases on port {port}.",
                  "Use matching cadgen releases on a shared port, or explicitly configure separate ports.")
    exit_code = 3 if any(item["exit_code"] == 3 for item in issues) else 5 if any(
        item["severity"] == "error" for item in issues) else 0
    return {"schema": SCHEMA, "status": "error" if exit_code else "warning" if issues else "ok", "exit_code": exit_code,
            "current": current, "skill": skill_data, "project": project_data, "installations": installation_data,
            "selected_port": selected, "engines": engines, "engine_records": {"status": records_state, "records": records},
            "hosts": hosts, "issues": issues}


def human(data: dict) -> str:
    current = data["current"]
    lines = [f"cadgen {current['installation'] or current['release']} diagnostics ({data['status']})",
             f"  python {current['python_version']} ({current['python']})", f"  import {current['path']}",
             f"  skill {data['skill']['status']}" + (f" cadgen {data['skill']['release']}" if data['skill']['release'] else "")]
    project_data = data["project"]
    lines.append(f"  project {project_data['status']}" + (f" cadgen {project_data['release']} ({project_data.get('path', '')})"
                                                        if project_data.get("release") else ""))
    for record in data["installations"]["records"]:
        lines.append(f"  installation {record.get('version', '?')}: {record['status']} ({record['path']})")
    for item in data["engines"]:
        lines.append(f"  engine port {item['port']}: {item['status']}" + (" (selected)" if item['selected'] else "")
                     + (f" cadgen {item['release']}" if item.get("release") else ""))
    for source in data["hosts"]["sources"]:
        if source["status"] != "missing":
            lines.append(f"  host {source['provider']} {source['kind']}: {source['status']}"
                         + (f" cadgen {source['release']} port {source.get('port')}" if source.get("release") else
                            f" installation {source['installation']} port {source.get('port')}" if source.get("installation") else "")
                         + f" ({source['path']})")
    lines.append("  coverage " + data["hosts"]["coverage"])
    for issue in data["issues"]:
        lines.extend([f"  {issue['severity']}: {issue['detail']}", "    " + issue["remedy"]])
    return "\n".join(lines) + "\n"
