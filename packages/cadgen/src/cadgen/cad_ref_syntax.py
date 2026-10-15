"""The ref grammar: one language, shared with the client (``@text-to-cad/core/lib/cadRefs.js``).

A ref names geometry, never a position (README law 20): an occurrence by its
token (``o:`` and twelve hexadecimal characters, minted from its place in the
tree: ``_internal/tree_tokens.py``), an element by the occurrence's token, a
slash and the element's token (``f:``, ``e:``, ``v:`` and twelve hexadecimal
characters, minted from the element's geometry within its body:
``_internal/geometry_fingerprint.py``) -- ``#o:4b1f9e2a7c03/f:9d2e8c1b5a70``. A
part label stands in for an occurrence token (``#base``, ``#base/f:9d2e8c1b5a70``);
a bare element token (``#f:9d2e8c1b5a70``) resolves in a document with one leaf.
Mates (``m1``) are not part of the structured grammar. Both parsers assert the
cases of ``cadRefs.parity.json``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass


# `<file>#<selectors>`, where the file half is optional. The prefix is the file's path relative
# to the root of the viewer the ref was copied from, or its absolute path, and it exists so a ref
# stays meaningful when it is pasted into a prompt that spans several files. It lives LEFT of the
# '#' on purpose: the selector grammar owns everything to the right, so a prefix can never collide
# with a label, its ':' qualifiers, or the '/' between an occurrence and its element.
# JSON-quoted paths mirror the JS parser, including literal filename delimiters.
QUOTED_PATH_PATTERN = r'"(?:[^"\\\r\n]|\\.)*"'
QUOTED_PREFIX_RE = re.compile(rf"^({QUOTED_PATH_PATTERN})#")
OCCURRENCE_TOKEN_PATTERN = r"o:[0-9a-f]{12}"
ELEMENT_TOKEN_PATTERN = r"[fev]:[0-9a-f]{12}"
OCCURRENCE_SELECTOR_RE = re.compile(rf"^({OCCURRENCE_TOKEN_PATTERN})$")
OCCURRENCE_ELEMENT_SELECTOR_RE = re.compile(rf"^({OCCURRENCE_TOKEN_PATTERN})/({ELEMENT_TOKEN_PATTERN})$")
ELEMENT_SELECTOR_RE = re.compile(rf"^({ELEMENT_TOKEN_PATTERN})$")

# A build123d part label used in place of an occurrence token. Deliberately narrow: it must not
# start with a digit, and it excludes "/" because that separates an occurrence from its element.
# Real labels in the repo are alphanumerics plus "_" and ":" (mounting_eye:lower,
# piston_rod:chrome), so this covers them. A token matches it too, which is why the token forms
# are tried first.
LABEL_PATTERN = r"[A-Za-z_][A-Za-z0-9_:]*"
LABEL_SELECTOR_RE = re.compile(rf"^({LABEL_PATTERN})$")
LABEL_ELEMENT_SELECTOR_RE = re.compile(rf"^({LABEL_PATTERN})/({ELEMENT_TOKEN_PATTERN})$")
# Mates are not part of the structured grammar -- consumers handle them -- and "m1" would
# otherwise be swallowed by LABEL_SELECTOR_RE.
MATE_SELECTOR_RE = re.compile(r"^m\d+$", re.IGNORECASE)

_TYPE_FOR_KIND = {"f": "face", "e": "edge", "v": "vertex"}
_KIND_FOR_TYPE = {"face": "f", "edge": "e", "vertex": "v"}


@dataclass(frozen=True)
class ParsedSelector:
    selector_type: str
    occurrence_id: str
    # The element's token (``f:…``, ``e:…``, ``v:…``) for a face, edge or vertex selector; "".
    token: str
    canonical: str
    # Set when the occurrence was named by label rather than by token. Resolution against a
    # loaded artifact turns this into an occurrence_id; the parser cannot, because it has no
    # index to look in.
    label: str = ""


def element_kind(token: str) -> str:
    """``"face"``, ``"edge"`` or ``"vertex"`` for an element token."""
    return _TYPE_FOR_KIND[token[0]]


def element_ref(occurrence_id: str, token: str) -> str:
    """The one composition rule: ``<occurrence token>/<element token>``."""
    return f"{occurrence_id}/{token}"


def split_element_ref(selector: object) -> tuple[str, str] | None:
    """``(owner, token)`` of an element ref -- ``o:…/f:…``, or ``label/f:…`` -- with any
    leading ``#`` dropped; None for anything else. Mirrors ``splitElementRef`` in the JS
    grammar: the one place a ref is taken apart, as :func:`element_ref` is the one place it
    is put together."""
    text = str(selector or "").strip()
    text = text[1:] if text.startswith("#") else text
    slash = text.find("/")
    if slash <= 0:
        return None
    owner, token = text[:slash], text[slash + 1:]
    return (owner, token) if ELEMENT_SELECTOR_RE.match(token) else None


def is_native_selector(value: object) -> bool:
    """Whether ``value`` is a ref in the token grammar itself (an occurrence token, an
    occurrence/element pair, a bare element token or a mate), as opposed to a label or
    an opaque string. Mirrors ``isNativeCadSelector`` in the JS grammar."""
    parsed = parse_selector(str(value or ""))
    if parsed is None or parsed.label:
        return False
    return parsed.selector_type != "opaque" or MATE_SELECTOR_RE.match(parsed.canonical) is not None


def _path_segments(path: str) -> list[str]:
    return [part for part in str(path or "").replace("\\", "/").split("/") if part]


def path_has_suffix(path: str, suffix: str) -> bool:
    """Does ``suffix`` name ``path``? Segment-aligned, never a substring match.

    ``plate.stl`` names ``a/b/plate.stl``; ``late.stl`` names nothing.
    """
    path_segments = _path_segments(path)
    suffix_segments = _path_segments(suffix)
    if not suffix_segments or len(suffix_segments) > len(path_segments):
        return False
    return path_segments[len(path_segments) - len(suffix_segments) :] == suffix_segments


def ref_prefix_names(prefix: str, document_path: str) -> bool:
    """Whether a ref's file prefix names ``document_path``, read as a path.

    ``~`` expands and links resolve, so any spelling of the same file names it. An absolute
    prefix names the document only if it is that file. A relative one is read from the working
    directory: if it names a file from there, that file has to be the document. If it names
    nothing from there, it can still be the end of the document's path, segment by segment, so a
    path under the viewer's root names its file wherever the command runs.
    """
    prefix = str(prefix or "").strip()
    document = str(document_path or "").strip()
    if not prefix or not document:
        return False
    candidate = os.path.expanduser(prefix)
    try:
        return os.path.samefile(candidate, document)
    except (OSError, ValueError):
        pass
    if os.path.isabs(candidate):
        # Neither path is a file here (a document named but not on disk): compare them as paths.
        return os.path.normcase(os.path.realpath(candidate)) == os.path.normcase(os.path.realpath(document))
    return path_has_suffix(document, prefix)


def split_cad_ref(text: str) -> tuple[str, str]:
    """One ref as ``(file prefix, selector text)``; the prefix is ``""`` when it has none.

    A prefix naming a path with a space, a quote, a ``#`` or a backslash -- any absolute Windows
    path -- is JSON-quoted (the viewer copies it so: ``buildCadRefToken`` in
    ``@text-to-cad/core/lib/cadRefs.js``), so it is decoded here rather than cut at its first ``#``.
    """
    stripped = str(text or "").strip()
    quoted = QUOTED_PREFIX_RE.match(stripped)
    if quoted:
        try:
            return json.loads(quoted.group(1)), stripped[quoted.end() :]
        except ValueError:
            pass
    prefix, hash_mark, selectors = stripped.partition("#")
    return (prefix.strip(), selectors) if hash_mark else ("", stripped)


def ensure_ref_file_matches(file_prefix: str, document_path: str, *, source_label: str = "ref") -> None:
    """Reject a ref whose file prefix names a file this command is not looking at.

    Copied refs may carry a file prefix (``STEP/plate.step#o:4b1f9e2a7c03``) so they stay
    meaningful in a prompt spanning several files: the file's path relative to the viewer's root,
    or its absolute path. It has to name ``document_path``, the file the command was given, as a
    path does (:func:`ref_prefix_names`). A CLI only ever inspects that file, so a prefix naming a
    DIFFERENT file has to be an error: silently ignoring it would inspect the wrong file and report
    a confident answer about geometry the user never asked about.

    CLIs do not resolve prefixes to paths -- the agent does that, then passes the file and the
    ref separately.
    """
    prefix = str(file_prefix or "").strip()
    document = str(document_path or "").strip()
    if not prefix or not document or ref_prefix_names(prefix, document):
        return
    raise ValueError(
        f"{source_label} names file {prefix!r} but this command targets {document!r}; "
        f"pass the file as the entry argument and the '#...' part as the ref"
    )


def parse_selector(raw_selector: str) -> ParsedSelector | None:
    # Only a LEADING "#" is the token marker. str.replace() takes the first "#"
    # anywhere; the JS parser strips /^#/ and leaves the rest opaque, and the two are one language.
    selector = str(raw_selector or "").strip()
    if selector.startswith("#"):
        selector = selector[1:]
    if not selector:
        return None

    occurrence_element = OCCURRENCE_ELEMENT_SELECTOR_RE.match(selector)
    if occurrence_element:
        occurrence_id, token = occurrence_element.group(1), occurrence_element.group(2)
        return ParsedSelector(
            selector_type=element_kind(token),
            occurrence_id=occurrence_id,
            token=token,
            canonical=element_ref(occurrence_id, token),
        )

    occurrence_match = OCCURRENCE_SELECTOR_RE.match(selector)
    if occurrence_match:
        return ParsedSelector(
            selector_type="occurrence",
            occurrence_id=occurrence_match.group(1),
            token="",
            canonical=occurrence_match.group(1),
        )

    element_match = ELEMENT_SELECTOR_RE.match(selector)
    if element_match:
        token = element_match.group(1)
        return ParsedSelector(
            selector_type=element_kind(token),
            occurrence_id="",
            token=token,
            canonical=token,
        )

    # Label forms are tried only after every token form has had its chance, so a token can never
    # be read as a label. Mates are excluded explicitly: they look like labels.
    if not MATE_SELECTOR_RE.match(selector):
        label_element = LABEL_ELEMENT_SELECTOR_RE.match(selector)
        if label_element:
            label, token = label_element.group(1), label_element.group(2)
            return ParsedSelector(
                selector_type=element_kind(token),
                occurrence_id="",
                token=token,
                canonical=element_ref(label, token),
                label=label,
            )

        label_match = LABEL_SELECTOR_RE.match(selector)
        if label_match:
            label = label_match.group(1)
            return ParsedSelector(
                selector_type="label",
                occurrence_id="",
                token="",
                canonical=label,
                label=label,
            )

    return ParsedSelector(
        selector_type="opaque",
        occurrence_id="",
        token="",
        canonical=selector,
    )
