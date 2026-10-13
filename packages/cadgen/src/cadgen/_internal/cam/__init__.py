"""The CAM engine behind :mod:`cadgen.cam`: job parsing, features, toolpaths, posts and outputs.

Stdlib only at import, in every module a job is parsed with (``job``,
``values``, ``operations``): OCP, build123d, shapely and numpy are imported
inside the functions that need them.
"""
