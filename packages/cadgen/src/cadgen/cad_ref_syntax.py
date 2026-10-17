"""The ref grammar: one language, shared with the client (``@text-to-cad/core/lib/cadRefs.js``).

A ref names an occurrence by its token (``o:`` and twelve hexadecimal
characters, minted from its place in the tree: ``_internal/tree_tokens.py``) and
an element by the occurrence's token, a slash and the element's **name**
(README law 20): its kind (``f:``, ``e:``, ``v:``) and its derivation from the
features that made it (``_internal/kernel_trace.py``). A derivation starts at an
atom -- the n-th element of its kind a feature made, ``dial.make_box1.6``, or a
derivation too long to spell, folded to the feature of its last step and a digest,
``dial.cut3~5d1e0c2f9a3b``, which the component's lineage spells -- and adds a step
per feature that made the element out of another: ``;M:<feature>`` modified,
``;G<kind>:<feature>`` generated from an element of that kind, ``;MG<kind>:``
both -- with a number after it for the n-th of a result's elements the step made alike --
``;U[n]:<feature>`` the (n-th) unnamed sub-element of its parent, ``;L:<feature>``
named from its lower elements, each with up to four further sources or lower
elements as ``+<kind>:<atom>``, and ``;D<n>`` the n-th of a component's elements
otherwise named alike: ``#o:4b1f9e2a7c03/f:dial.make_box1.6;M:dial.cut1``. A
feature the author labelled goes by the label (``f:lid.2``). An element no
construction names -- of an imported file, or of a shape the model took from
outside its run -- is named by its geometric token, twelve hexadecimal characters
(``f:9d2e8c1b5a70``, ``_internal/geometric_tokens.py``). A part label stands in
for an occurrence token (``#base``, ``#base/f:dial.make_box1.6``); a bare element
name (``#f:dial.make_box1.6``) resolves in a document with one leaf. Mates (``m1``)
are not part of the structured grammar. Both parsers assert the cases of
``cadRefs.parity.json``; to both, a name is an opaque string the grammar admits.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Mapping


# `<file>#<selectors>`, where the file half is optional. The prefix is the file's path relative
# to the root of the viewer the ref was copied from, or its absolute path, and it exists so a ref
# stays meaningful when it is pasted into a prompt that spans several files. It lives LEFT of the
# '#' on purpose: the selector grammar owns everything to the right, so a prefix can never collide
# with a label, its ':' qualifiers, or the '/' between an occurrence and its element.
# JSON-quoted paths mirror the JS parser, including literal filename delimiters.
QUOTED_PATH_PATTERN = r'"(?:[^"\\\r\n]|\\.)*"'
QUOTED_PREFIX_RE = re.compile(rf"^({QUOTED_PATH_PATTERN})#")
OCCURRENCE_TOKEN_PATTERN = r"o:[0-9a-f]{12}"
# A name segment: a function, an operation and its count, or a label (``kernel_trace.identifier``).
_SEGMENT = r"[A-Za-z_][A-Za-z0-9_]*"
_NUMBER = r"[1-9][0-9]*"
_HEX12 = r"[0-9a-f]{12}"
# A feature: ``label`` or ``function.operationN``. Segments start with a letter and numbers
# with a digit, so the two never read as one another.
FEATURE_NAME_PATTERN = rf"{_SEGMENT}(?:\.{_SEGMENT})?"
# An atom: the n-th element of its kind a feature made, or a derivation folded to the feature
# of its last step and a digest of it (the component lineage's table spells it).
MADE_ATOM_PATTERN = rf"{FEATURE_NAME_PATTERN}\.{_NUMBER}"
FOLDED_ATOM_PATTERN = rf"{FEATURE_NAME_PATTERN}~{_HEX12}"
ATOM_PATTERN = rf"{FEATURE_NAME_PATTERN}(?:\.{_NUMBER}|~{_HEX12})"
# A step: what a feature made of the element before it -- modified (M), generated from an
# element of a kind (G<k>), both (MG<k>), the n-th unnamed sub-element of a parent (U[n]),
# named from lower elements (L) -- with further sources or lower elements, or the n-th of
# a component's elements otherwise named alike (D<n>). A number after M, G<k> or MG<k> is
# the n-th of a result's elements the same step made from the same sources.
STEP_PATTERN = (rf";(?:(?:(?:M|MG[fev]|G[fev]|U)(?:{_NUMBER})?|L):{FEATURE_NAME_PATTERN}(?:\+[fev]:{ATOM_PATTERN})*"
                rf"|D{_NUMBER})")
DERIVATION_PATTERN = rf"{ATOM_PATTERN}(?:{STEP_PATTERN})*"
# An element's name: its kind and its derivation, or its geometric token.
ELEMENT_NAME_PATTERN = rf"[fev]:(?:{_HEX12}|{DERIVATION_PATTERN})"
FEATURE_NAME_RE = re.compile(rf"^{FEATURE_NAME_PATTERN}$")
FOLDED_ATOM_RE = re.compile(rf"^{FOLDED_ATOM_PATTERN}$")
DERIVATION_RE = re.compile(rf"^{DERIVATION_PATTERN}$")
_STEP_RE = re.compile(rf"^(M|MG|G|U|L|D)([fev])?({_NUMBER})?(?::({FEATURE_NAME_PATTERN})((?:\+[fev]:{ATOM_PATTERN})*))?$")
OCCURRENCE_SELECTOR_RE = re.compile(rf"^({OCCURRENCE_TOKEN_PATTERN})$")
OCCURRENCE_ELEMENT_SELECTOR_RE = re.compile(rf"^({OCCURRENCE_TOKEN_PATTERN})/({ELEMENT_NAME_PATTERN})$")
ELEMENT_SELECTOR_RE = re.compile(rf"^({ELEMENT_NAME_PATTERN})$")
# Texts of one form, one per line, each admitted whole (``joined_lines``): element names of
# a kind, folded atoms, derivations of at least one step (what a lineage's table spells).
_LINES = "(?:{0})(?:\n(?:{0}))*"
ELEMENT_NAME_LINES_RE = {letter: re.compile(_LINES.format(rf"{letter}:(?:{_HEX12}|{DERIVATION_PATTERN})"))
                         for letter in "fev"}
FOLDED_ATOM_LINES_RE = re.compile(_LINES.format(FOLDED_ATOM_PATTERN))
STEPPED_DERIVATION_LINES_RE = re.compile(_LINES.format(rf"{ATOM_PATTERN}(?:{STEP_PATTERN})+"))

# A build123d part label used in place of an occurrence token. Deliberately narrow: it must not
# start with a digit, and it excludes "/" because that separates an occurrence from its element.
# Real labels in the repo are alphanumerics plus "_" and ":" (mounting_eye:lower,
# piston_rod:chrome), so this covers them. A token matches it too, which is why the token forms
# are tried first.
LABEL_PATTERN = r"[A-Za-z_][A-Za-z0-9_:]*"
LABEL_SELECTOR_RE = re.compile(rf"^({LABEL_PATTERN})$")
LABEL_ELEMENT_SELECTOR_RE = re.compile(rf"^({LABEL_PATTERN})/({ELEMENT_NAME_PATTERN})$")
# Mates are not part of the structured grammar -- consumers handle them -- and "m1" would
# otherwise be swallowed by LABEL_SELECTOR_RE.
MATE_SELECTOR_RE = re.compile(r"^m\d+$", re.IGNORECASE)

_TYPE_FOR_KIND = {"f": "face", "e": "edge", "v": "vertex"}
_KIND_FOR_TYPE = {"face": "f", "edge": "e", "vertex": "v"}


@dataclass(frozen=True)
class ParsedSelector:
    selector_type: str
    occurrence_id: str
    # The element's name (``f:…``, ``e:…``, ``v:…``) for a face, edge or vertex selector; "".
    element: str
    canonical: str
    # Set when the occurrence was named by label rather than by token. Resolution against a
    # loaded artifact turns this into an occurrence_id; the parser cannot, because it has no
    # index to look in.
    label: str = ""


def is_element_name(value: object, kind: str | None = None) -> bool:
    """Whether ``value`` is an element name -- of ``kind`` (``"face"``, ``"edge"`` or
    ``"vertex"``) when given."""
    if not isinstance(value, str) or ELEMENT_SELECTOR_RE.match(value) is None:
        return False
    return kind is None or _TYPE_FOR_KIND[value[0]] == kind


def is_feature_name(value: object) -> bool:
    """Whether ``value`` is a feature's name: a label, or ``function.operationN``."""
    return isinstance(value, str) and FEATURE_NAME_RE.match(value) is not None


@dataclass(frozen=True)
class Step:
    """One step of a derivation: ``op`` (``M``, ``G``, ``MG``, ``U``, ``L`` or ``D``), the
    source kind of a ``G``/``MG`` (``f``, ``e``, ``v``; else ""), the number of a ``U`` or a
    ``D`` (0 where none is written), the feature that took the step ("" for ``D``) and its
    further sources or lower elements, ``(kind, atom)``."""

    op: str
    kind: str = ""
    number: int = 0
    feature: str = ""
    extras: tuple[tuple[str, str], ...] = ()


def is_folded_atom(value: object) -> bool:
    """Whether ``value`` is a folded atom (``feature~`` and twelve hex digits)."""
    return isinstance(value, str) and FOLDED_ATOM_RE.match(value) is not None


def atom_feature(atom: str) -> str:
    """The feature an atom names: the one that made the element, or the last step's of a
    folded derivation (``dial.make_box1.6`` and ``dial.cut3~5d1e0c2f9a3b`` -> ``dial.make_box1``,
    ``dial.cut3``)."""
    return atom.split("~", 1)[0] if "~" in atom else atom.rsplit(".", 1)[0]


def split_derivation(text: str) -> tuple[str, tuple[Step, ...]]:
    """``(atom, steps)`` of a derivation -- an element name without its kind. ValueError
    for text the grammar does not admit."""
    if DERIVATION_RE.match(text) is None:
        raise ValueError(f"not a derivation: {text!r}")
    atom, *written = text.split(";")
    steps = []
    for step in written:
        match = _STEP_RE.match(step)
        if match is None:  # the derivation pattern admitted it, so this cannot happen
            raise ValueError(f"not a step: {step!r}")
        op, kind, number, feature, extras = match.groups()
        steps.append(Step(op, kind or "", int(number or 0), feature or "",
                          tuple((extra[0], extra[2:]) for extra in extras.split("+")[1:]) if extras else ()))
    return atom, tuple(steps)


def joined_lines(pattern: re.Pattern[str], texts: list[object]) -> str | None:
    """``texts`` joined one per line when every one is a string the grammar admits whole
    (``pattern`` one of the ``*_LINES_RE``, which take every line in one match), else
    None: a lineage's thousands of names checked by one call, not one each."""
    if not texts:
        return ""
    try:
        joined = "\n".join(texts)  # type: ignore[arg-type]
    except TypeError:
        return None
    if joined.count("\n") != len(texts) - 1 or pattern.fullmatch(joined) is None:
        return None
    return joined


def folded_atoms(text: str) -> list[str]:
    """The folded atoms of a derivation the grammar admits, or of names one per line -- the
    atom each starts from, a further source's -- read off each '~', which only a folded
    atom holds: the feature before it, back to the ':' (a name's kind's, at the latest) or
    the start, and the digest after it."""
    pieces = text.split("~")
    return [f"{piece.rsplit(':', 1)[-1]}~{after[:12]}" for piece, after in zip(pieces, pieces[1:])]


def derivation_features(text: str) -> list[str]:
    """The features a derivation names -- its atom's, each step's and each extra atom's --
    in order, each once."""
    atom, steps = split_derivation(text)
    found = [atom_feature(atom)]
    for step in steps:
        found.extend([step.feature] if step.feature else [])
        found.extend(atom_feature(extra) for _kind, extra in step.extras)
    return list(dict.fromkeys(found))


def expanded_derivation(text: str, table: Mapping[str, str]) -> tuple[str, tuple[Step, ...]]:
    """``(made atom, steps)`` of a derivation with every folded atom it starts from spelled
    out through ``table`` (a component lineage's): the element's whole making, from the
    element some feature made to the last step. ValueError where the table lacks an atom."""
    steps: tuple[Step, ...] = ()
    seen: set[str] = set()
    while True:
        atom, more = split_derivation(text)
        steps = more + steps
        if not is_folded_atom(atom):
            return atom, steps
        if atom in seen or atom not in table:
            raise ValueError(f"the lineage does not spell {atom!r}")
        seen.add(atom)
        text = table[atom]


def made_and_modified(element: str, table: Mapping[str, str]) -> tuple[str | None, str | None]:
    """The feature that made the element a name names -- the last step's that made it
    anew (generated it, named it from a parent or from its lower elements), else the one
    that made the element it was made from -- and the feature that last modified it since,
    or None; ``(None, None)`` for a geometric token."""
    derivation = element[2:]
    if DERIVATION_RE.match(derivation) is None:
        return None, None
    atom, steps = expanded_derivation(derivation, table)
    made, modified = atom_feature(atom), None
    for step in steps:
        if step.op == "M":
            modified = step.feature
        elif step.op != "D":
            made, modified = step.feature, None
    return made, modified


def element_kind(element: str) -> str:
    """``"face"``, ``"edge"`` or ``"vertex"`` for an element name."""
    return _TYPE_FOR_KIND[element[0]]


def element_ref(occurrence_id: str, element: str) -> str:
    """The one composition rule: ``<occurrence token>/<element name>``."""
    return f"{occurrence_id}/{element}"


def split_element_ref(selector: object) -> tuple[str, str] | None:
    """``(owner, element)`` of an element ref -- ``o:…/f:…``, or ``label/f:…`` -- with any
    leading ``#`` dropped; None for anything else. Mirrors ``splitElementRef`` in the JS
    grammar: the one place a ref is taken apart, as :func:`element_ref` is the one place it
    is put together."""
    text = str(selector or "").strip()
    text = text[1:] if text.startswith("#") else text
    slash = text.find("/")
    if slash <= 0:
        return None
    owner, element = text[:slash], text[slash + 1:]
    return (owner, element) if ELEMENT_SELECTOR_RE.match(element) else None


def is_native_selector(value: object) -> bool:
    """Whether ``value`` is a ref in the grammar itself (an occurrence token, an
    occurrence/element pair, a bare element name or a mate), as opposed to a label or
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
        occurrence_id, element = occurrence_element.group(1), occurrence_element.group(2)
        return ParsedSelector(
            selector_type=element_kind(element),
            occurrence_id=occurrence_id,
            element=element,
            canonical=element_ref(occurrence_id, element),
        )

    occurrence_match = OCCURRENCE_SELECTOR_RE.match(selector)
    if occurrence_match:
        return ParsedSelector(
            selector_type="occurrence",
            occurrence_id=occurrence_match.group(1),
            element="",
            canonical=occurrence_match.group(1),
        )

    element_match = ELEMENT_SELECTOR_RE.match(selector)
    if element_match:
        element = element_match.group(1)
        return ParsedSelector(
            selector_type=element_kind(element),
            occurrence_id="",
            element=element,
            canonical=element,
        )

    # Label forms are tried only after every token and name form has had its chance, so
    # neither can be read as a label. Mates are excluded explicitly: they look like labels.
    if not MATE_SELECTOR_RE.match(selector):
        label_element = LABEL_ELEMENT_SELECTOR_RE.match(selector)
        if label_element:
            label, element = label_element.group(1), label_element.group(2)
            return ParsedSelector(
                selector_type=element_kind(element),
                occurrence_id="",
                element=element,
                canonical=element_ref(label, element),
                label=label,
            )

        label_match = LABEL_SELECTOR_RE.match(selector)
        if label_match:
            label = label_match.group(1)
            return ParsedSelector(
                selector_type="label",
                occurrence_id="",
                element="",
                canonical=label,
                label=label,
            )

    return ParsedSelector(
        selector_type="opaque",
        occurrence_id="",
        element="",
        canonical=selector,
    )
