# Security Policy

## Scope

text-to-cad is a local development tool: agent skills and `cadgen`, the
distribution that builds, views and exports CAD models on your own machine.
Everything below runs as you, with your files.

**The engine.** One warm `cadgen` daemon per installation does the building,
viewing and exporting. Its clients reach it through its door, a named pipe
with a pair of pipes per connection, that only you can open (on Windows, a
loopback port and a token in a file only you can read). The door lives in the
engine's folder (`CADGEN_ENGINE_DIR`, by default
`cadgen-engine` in `$XDG_RUNTIME_DIR`, else in a temporary folder that is yours alone (on macOS
the system's per-user one, else `$TMPDIR`),
else `/tmp/cadgen-<uid>`). The client and the daemon refuse a folder that
another user owns or that others can write to. A shell inside an agent's
sandbox that can open the door builds with the engine's own rights, outside
the sandbox, by design (the door is how a sandboxed agent reaches the
engine), so the boundary is your user account, not the sandbox.

**The CAD Viewer and the MCP endpoint.** The engine serves the CAD Viewer and
the CAD app's MCP endpoint over HTTP on `127.0.0.1`. Every route needs the
engine's token. A request can carry it as a Bearer header, or as the
HttpOnly, SameSite=Strict cookie that the printed launch URL's `?token=` is
exchanged for. Behind the token:

- A Host check refuses DNS rebinding.
- A state-changing request also needs a custom header, so a cross-site form or
  fetch cannot make one.
- No CORS headers are sent.

**The token is the boundary.** Whoever holds it can read any CAD file on your
machine by its path, list folders, export, and use every Viewer action. So keep
the launch URL private. Do not bind a non-loopback `--host` unless you mean to
offer exactly that to everyone who can reach the port.

**The token lasts.** It belongs to you, not to one run of the engine or one
installation of cadgen: it is made once, kept in a file only you can read in
your state folder (`engines/token`), and reused at every start of every
installation's engine, so a link survives an upgrade, on the same port (3245 unless you name another; when
another program holds 3245, the next free port, which your installation keeps and
names in `cadgen status`). So a shared link keeps working on this machine, across restarts and
reboots, until you rotate it. It reaches your folders whenever the engine runs.
`cadgen server --rotate-token`, with every engine stopped (`cadgen stop`, with
each version that runs one), replaces it and retires every link shared before.

**The CAD app in agent hosts.** The page's own tools (`cad_sync`,
`cad_capture_reply`) and its full `cad_http` access need a per-session
capability. The server delivers it in tool-result metadata, which MCP Apps
hosts keep out of the model's context. Without it, an agent's `cad_http`
reaches only the telemetry status and opt-out. The page answers only messages
from its host's window.

**Files from others.** A STEP, xbf, GLB, STL, 3MF, DXF, URDF, SDF or SRDF
written by someone else is untrusted input. The engine reads it in separate
processes and bounds its work: assembly, node and block expansion, archive
unpacking, and buffer reads. It fetches nothing a file names over the network.
`cadgen sdf validate` runs `gz sdf --check` on a document that names a remote
reference only when asked (`--gz-remote`).

### What we treat as a vulnerability

- Reaching the engine, its files or its actions without the token, or taking
  the token, from another origin, another local user, or a file you open.
- A file from someone else that reads, writes or runs anything beyond what
  opening, viewing, exporting or snapshotting it should. This includes a file
  that makes the engine do unbounded work or hold unbounded memory.
- An MCP tool that the model can use to act as the CAD app's page, or a frame
  that can act as its host.
- A way to publish to PyPI or the install branches (`latest`, `claude-plugin`)
  other than a merged release.
- `api.texttocad.dev`: anything beyond its documented behaviour (version feed,
  consented analytics).

Out of scope: what someone who already has your token, or already runs code as
you, can do; and effects on your own machine that you start yourself, such as
building your own script.

## Reporting a Vulnerability

Report it privately:

1. Use the repository's **Security tab → Report a vulnerability**
   (GitHub Security Advisories).
2. Do **NOT** open a public issue or pull request for a security
   vulnerability.
3. Include a description, reproduction steps, and potential impact. A small
   file or command that shows the effect is the most useful. Do not run
   automated scans or load against `api.texttocad.dev`.

We aim to acknowledge reports within 48 hours and provide a fix timeline
within 7 days. We ask that you give us time to address the issue before
disclosing it publicly.

## Supported Versions

Only the latest release is supported. No older versions receive security
fixes; update to the newest tagged release to stay covered.

| Version | Supported |
|---------|-----------|
| latest  | Yes       |
| older   | No        |
