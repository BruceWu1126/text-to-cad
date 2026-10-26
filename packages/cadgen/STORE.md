# Document identity and storage

Identity is namespace plus name. In 0.8 the namespace is the script's canonical folder;
nothing detects a project, searches configuration files or asks version control.
A model's name is its identity within its namespace, the function's name by default,
`name=` gives it another.

Every id uses the unchanged formula `uuid5(NAMESPACE, canonical(scope) + "::" + name)`.
An unnamed model uses its script path and function name, exactly as before. An explicitly
named model uses its script's folder and that name, independent of the declaring file.
Two explicit names within one folder are refused at declaration with both `file:line`
locations. The same explicit name in different folders is allowed; unnamed functions
with the same name in different files are separate documents. A shape handed over uses
its calling script (or its working folder) and its required name; a file uses its path
and no name. There are no global named identities.

## Records and cache

The registry points, and never holds geometry. In the state folder,
`documents/<id>/document.json` has this shape (optional fields marked `?`):

```
schema, id, kind, root, home, entry?, namespace, name, fingerprint?, module?, line?,
relocated?, revision?, built?, created?, facts?, program?, cache?, opened?, pinned?, preview?
```

`kind` is `model`, `shape` or `file`. `root` is the absolute folder and `namespace` equals
it. `home` is the script or opened file relative to root; `entry` is the function to
rebuild, while `name` is its explicit name or that function's name. A model's fingerprint
is SHA-256 of its script's bytes at each run. Its Python module qualifier is `module`;
an explicit declaration's `line` gives collision diagnostics. Files and shapes have no
script fingerprint. Creation time, open history, pins, facts and the preview survive a
model's runs. Thumbnail PNGs live under `recents/thumbnails` in the state folder.

The cache folder holds `documents/<id>/document.xbf`, the last document's exact bytes.
The record's `cache: {revision, bytes}` describes it. This file is regenerable; the model's
code remains the source of truth. A declared/exported XBF is the person's file, never
the engine's cache. Folder writes land atomically through the registry; cache bytes and
a new record land before an old identity is removed.

## Re-attachment on a run

Before the worker checks the cache of an unrecorded id, the daemon looks for records
with the same name, entry function and exact script fingerprint whose script no longer
exists. One match is adopted. Several choose the most recently built (the id breaks a
time tie), and the choice is logged. No match creates a new document. A copy whose
original still exists never re-attaches; renaming an unnamed function creates a new
document. A named model moved between files of its folder keeps its id.

Adoption re-points the record and keeps its history, thumbnail and cache bytes under the
new id. `relocated: {script, module}` remembers the original cache's source so paths and
symbol qualifiers can be projected for comparison, without rewriting the XBF. Code,
modules, input contents and toolchain checks still decide whether the document is warm.
A fresh build clears this projection. Nothing reads or runs a script outside a run,
except the one-time fingerprint fill below.

## Identity migration at engine start

Before accepting requests, an independent pass fills missing namespace, name and
fingerprint fields in existing records. It also moves records identified by the earlier
`uuid5(NAMESPACE, "key::" + key)` formula to their folder/name ids, with their cache files.
A shape formerly given a key moves to its calling-scope/name id instead. This pass reads
JSON and, only to fill missing fingerprints, script bytes; it imports no kernel, parses
no XBF and rewrites no XBF. It is separate from any build-closure migration.

The pass is idempotent: a second pass changes no file. Missing scripts keep a null
fingerprint until a run supplies one. Unreadable/malformed records and destination
conflicts are skipped, never deleted, and listed in the single log line:
`identity migration: filled N, moved N, skipped N`, followed by each skipped path/reason.
