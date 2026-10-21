"""PROTOTYPE -- not used by any verb. A pure-Python OCCT renderer of an engine document, built
to compare with ``cadgen xbf snapshot`` (Chromium) flag by flag and in time.

Nothing in ``snapshot.py``, ``serve.py`` or ``mesh.py`` imports this package. It draws from the
engine's own meshes (the XBM buffers ``mesh.py`` writes and the daemon serves), never a mesh of
OCCT's own: ``shapes.py`` turns a part's buffer into triangulation-only faces and polygon edges
in one kernel read; ``attach.py`` is the alternative measured against it (a pool returns the
meshed B-rep, its triangulations attached to the document's own faces). ``occt.py`` is the scene
and the CLI (``python -m cadgen.engine.render.occt``); ``context.py`` the headless GL context.
"""
