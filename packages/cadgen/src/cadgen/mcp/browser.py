"""Links to page sessions on the installation's engine, for text-only hosts."""
from __future__ import annotations

import os
from urllib.parse import quote
from cadgen.engine.client import request


class ViewerUnavailable(Exception):
    """The engine could not provide a viewer session."""


def viewer_url(*, cwd: str | None = None) -> str:
    try:
        answer = request({'op': 'viewer', 'start': cwd or os.path.expanduser('~')})
    except OSError as error:
        raise ViewerUnavailable(str(error)) from error
    if not answer or 'failed' in answer:
        raise ViewerUnavailable((answer or {}).get('failed', 'engine did not answer'))
    return answer['url']


def model_link(url: str, model: str) -> str:
    """``url`` opened at ``model``, named by its absolute path: readable, with ``/`` and ``:`` kept."""
    return f"{url}{'&' if '?' in url else '?'}file={quote(model.replace(os.sep, '/'), safe='/:')}"
