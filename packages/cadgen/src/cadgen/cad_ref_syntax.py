from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass


# `<file>#<selectors>`, where the file half is optional. The prefix is the file's path relative
# to the root of the viewer the ref was copied from, or its absolute path, and it exists so a ref
# stays meaningful when it is pasted into a prompt that spans several files. It lives LEFT of the
# '#' on purpose: the selector grammar owns everything to the right, so a prefix can never collide
# with a label, its ':' qualifiers, or an entity's '.'.
# JSON-quoted paths mirror the JS parser, including literal filename delimiters.
QUOTED_PATH_PATTERN = r'"(?:[^"\\\r\n]|\\.)*"'
QUOTED_PREFIX_RE = re.compile(rf"^({QUOTED_PATH_PATTERN})#")
OCCURRENCE_SELECTOR_RE = re.compile(r"^o((?:\d+)(?:\.\d+)*)$")
OCCURRENCE_ENTITY_SELECTOR_RE = re.compile(r"^o((?:\d+)(?:\.\d+)*)\.([sfev])(\d+)$")
ENTITY_SELECTOR_RE = re.compile(r"^([sfev])(\d+)$")

# A build123d part label used in place of an occurrence id. Deliberately narrow: it must not
# start with a digit, and it excludes "." because that is the entity separator. Real labels in
# the repo are alphanumerics plus "_" and ":" (mounting_eye:lower, piston_rod:chrome), so this
# covers them while leaving the numeric grammar's territory untouched. Widening later is
# backwards compatible; narrowing would not be.
LABEL_PATTERN = r"[A-Za-z_][A-Za-z0-9_:]*"
LABEL_SELECTOR_RE = re.compile(rf"^({LABEL_PATTERN})$")
LABEL_ENTITY_SELECTOR_RE = re.compile(rf"^({LABEL_PATTERN})\.([sfev])(\d+)$")
# Mates are not part of the structured grammar -- consumers handle them -- and "m1" would
# otherwise be swallowed by LABEL_SELECTOR_RE.
MATE_SELECTOR_RE = re.compile(r"^m\d+$", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedSelector:
    selector_type: str
    occurrence_id: str
    ordinal: int | None
    canonical: str
    # Set when the occurrence was named by label rather than by numeric id. Resolution against
    # a loaded artifact turns this into an occurrence_id; the parser cannot, because it has no
    # index to look in.
    label: str = ""


def _selector_type_for_kind(kind: str) -> str:
    if kind == "s":
        return "shape"
    if kind == "f":
        return "face"
    if kind == "e":
        return "edge"
    return "vertex"


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

    Copied refs may carry a file prefix (``STEP/plate.step#o1.2``) so they stay meaningful in a
    prompt spanning several files: the file's path relative to the viewer's root, or its absolute
    path. It has to name ``document_path``, the file the command was given, as a path does
    (:func:`ref_prefix_names`). A CLI only ever inspects that file, so a prefix naming a DIFFERENT
    file has to be an error: silently ignoring it would inspect the wrong file and report a
    confident answer about geometry the user never asked about.

    CLIs do not resolve prefixes to paths -- the agent does that, then passes the file and the
    ref separately. See ``skills/cad/references/inspection-and-validation.md``.
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
    # anywhere, splicing "o1#f2" into the label "o1f2"; the JS parser strips /^#/
    # and leaves the rest opaque, and the two are one language.
    selector = str(raw_selector or "").strip()
    if selector.startswith("#"):
        selector = selector[1:]
    if not selector:
        return None

    occurrence_entity_match = OCCURRENCE_ENTITY_SELECTOR_RE.match(selector)
    if occurrence_entity_match:
        occurrence_id = f"o{occurrence_entity_match.group(1)}"
        kind = str(occurrence_entity_match.group(2))
        ordinal = int(occurrence_entity_match.group(3))
        return ParsedSelector(
            selector_type=_selector_type_for_kind(kind),
            occurrence_id=occurrence_id,
            ordinal=ordinal,
            canonical=f"{occurrence_id}.{kind}{ordinal}",
        )

    occurrence_match = OCCURRENCE_SELECTOR_RE.match(selector)
    if occurrence_match:
        occurrence_id = f"o{occurrence_match.group(1)}"
        return ParsedSelector(
            selector_type="occurrence",
            occurrence_id=occurrence_id,
            ordinal=None,
            canonical=occurrence_id,
        )

    entity_match = ENTITY_SELECTOR_RE.match(selector)
    if entity_match:
        kind = str(entity_match.group(1))
        ordinal = int(entity_match.group(2))
        return ParsedSelector(
            selector_type=_selector_type_for_kind(kind),
            occurrence_id="",
            ordinal=ordinal,
            canonical=f"{kind}{ordinal}",
        )

    # Label forms are tried only after every numeric form has had its chance, so an existing
    # ref can never change meaning. Mates are excluded explicitly: they look like labels.
    if not MATE_SELECTOR_RE.match(selector):
        label_entity_match = LABEL_ENTITY_SELECTOR_RE.match(selector)
        if label_entity_match:
            label = str(label_entity_match.group(1))
            kind = str(label_entity_match.group(2))
            ordinal = int(label_entity_match.group(3))
            return ParsedSelector(
                selector_type=_selector_type_for_kind(kind),
                occurrence_id="",
                ordinal=ordinal,
                canonical=f"{label}.{kind}{ordinal}",
                label=label,
            )

        label_match = LABEL_SELECTOR_RE.match(selector)
        if label_match:
            label = str(label_match.group(1))
            return ParsedSelector(
                selector_type="label",
                occurrence_id="",
                ordinal=None,
                canonical=label,
                label=label,
            )

    return ParsedSelector(
        selector_type="opaque",
        occurrence_id="",
        ordinal=None,
        canonical=selector,
    )
