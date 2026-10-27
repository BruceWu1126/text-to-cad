"""Links to the installation's viewer page, for text-only hosts, and the open the stdio peer makes.

A text host's ``cad_show`` opens the model's page in the person's browser, but never from the
engine: the engine runs in the environment of whatever started it (another agent's shell, another
``BROWSER``, no display), so its open could land in the wrong browser, or nowhere. The stdio peer
the host started (``shim.py``), which runs in the agent's own environment, opens it instead. It
says at initialize that it opens a browser for its host, and whether it can
(``transport_browser``); the engine's answer then carries the page to open in its result's
``_meta`` (``OPEN``), which the peer opens and strips before the host sees the result
(``deliver``), and before that tells the engine on its session whether the page opened
(``OPENED``): the engine counts a page as open in the person's browser only once told it opened,
so after a failed open the next ``cad_show`` opens it again. A client with no such peer is given
the URL in words and nothing opens.
"""
from __future__ import annotations

import os
from typing import Any, Callable

from cadgen.engine.client import request

# A text host's cad_show result: ``{"url", "said", "failed"}``, the page the stdio peer opens, the
# sentence of the result that says it opened, and the one it says in its place where the open
# fails (``{reason}`` the failure). Only the peer that declared ``transport_browser`` is sent it.
OPEN = 'dev.texttocad/open'
# The peer's word on that page, the notification it sends on its session once its open returned and
# before its host has the answer: ``{"url", "opened"}``, the page asked and whether it opened. The
# engine reads only the first word on a page it asked that session's peer for (``Server.notify``).
OPENED = 'notifications/dev.texttocad/opened'


class ViewerUnavailable(Exception):
    """The engine could not provide its viewer page."""


def viewer_url(model: str | None = None, *, cwd: str | None = None) -> str:
    """The viewer page of ``model`` (an absolute CAD file path, or a document id the engine holds),
    ``/<id>``, the file registered by the launch; the home's without one."""
    try:
        answer = request({'op': 'viewer', 'start': cwd or os.path.expanduser('~'),
                          'files': [model] if model else []})
    except OSError as error:
        raise ViewerUnavailable(str(error)) from error
    if not answer or 'failed' in answer:
        raise ViewerUnavailable((answer or {}).get('failed', 'engine did not answer'))
    return answer['url']


def transport_browser() -> dict[str, Any]:
    """What the stdio peer says of its browser at initialize: that it opens one for its host, and
    ``refusal``, why none can be opened from its environment (no display, a remote shell), or None."""
    from cadgen.viewer.browser_open import display_refusal

    return {'refusal': display_refusal()}


def deliver(result: Any, opener: Callable[[str], bool] | None = None,
            tell: Callable[[str, bool], None] = lambda url, opened: None) -> Any:
    """``result`` as the host is given it: a page the engine asked this process to open (``OPEN``)
    opened here, through an owner-only redirect file (``browser_open``) by ``opener``
    (``webbrowser.open``, which reads this process's ``BROWSER``), and the request removed; where
    no browser opens, the sentence that said it did replaced by the one that says why not. Whether
    it opened is told, ``tell(url, opened)`` (the engine, ``OPENED``), before the result is returned."""
    meta = result.get('_meta') if isinstance(result, dict) else None
    if not isinstance(meta, dict) or OPEN not in meta:
        return result
    asked = meta.pop(OPEN)
    if not meta:
        del result['_meta']
    if not isinstance(asked, dict):
        return result
    url, said, failed = asked.get('url'), asked.get('said'), asked.get('failed')
    if not isinstance(url, str) or not url.startswith(('http://', 'https://')):
        return result  # only a page the engine serves is opened, never a file or a program
    import webbrowser

    from cadgen.viewer.browser_open import CannotOpen, open_page
    try:
        open_page(url, opener=opener or webbrowser.open)
    except CannotOpen as failure:
        tell(url, False)
        if isinstance(said, str) and isinstance(failed, str):
            instead = failed.replace('{reason}', str(failure))
            for item in result.get('content') or []:
                if isinstance(item, dict) and isinstance(item.get('text'), str):
                    item['text'] = item['text'].replace(said, instead, 1)
    else:
        tell(url, True)
    return result
