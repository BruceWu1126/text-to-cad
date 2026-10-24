"""How cadgen is run: installed by ONE ``uvx`` command, then run by the launchers it publishes.

An agent app starts cadgen's MCP server with ``uvx`` and the exact requirement, outside any
sandbox. uv keeps one installation per requirement and reuses it, so the same command means the
same installation -- and cadgen keeps one engine daemon per installation. ``--no-config`` keeps
the user's own uv settings (an index, a cache policy) from resolving it differently, and
``--managed-python --python 3.13`` makes the installation the first command creates the same on
every machine, whatever interpreter a GUI app's PATH happens to find first.

The skills cannot run ``uvx``: an agent's shell is often a sandbox, and uv writes its cache before
it starts. So that installation publishes its launchers (``cadgen._internal.launchers``),
``~/.local/bin/cadgen-<version>`` and ``~/.local/bin/cadgen-python-<version>``, and every skill
runs them: the same installation, so the server, every thread and every skill command still share
one daemon. A skill names ``uvx`` once, as the command that installs cadgen and publishes them
(``cadgen doctor``) when they are missing.

Error hints and ``cadgen doctor`` print these from here; the plugin's server configs and the
skills carry the same strings, stamped by the release and held to these by a repository test.
"""

from __future__ import annotations

LAUNCHER = ("uvx", "--no-config", "--managed-python", "--python", "3.13", "--from")


def launch_command(tool: str = "cadgen", version: str | None = None) -> str:
    """``uvx ... --from cadgen==<version> <tool>``: run ``tool`` (``cadgen`` or ``python``)
    from this version's installation, installing it first if uv has none."""
    if version is None:
        from cadgen import __version__ as version
    return " ".join((*LAUNCHER, f"cadgen=={version}", tool))


def launcher(tool: str = "cadgen", version: str | None = None) -> str:
    """``~/.local/bin/cadgen-<version>`` (``tool="cadgen"``) or ``~/.local/bin/cadgen-python-<version>``
    (``tool="python"``): the launcher a skill runs, as it names it."""
    if version is None:
        from cadgen import __version__ as version
    return f"~/.local/bin/{'cadgen' if tool == 'cadgen' else 'cadgen-python'}-{version}"
