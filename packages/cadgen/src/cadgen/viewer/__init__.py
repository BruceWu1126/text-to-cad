"""The engine's CAD Viewer HTTP application and its short-lived launcher.

``cadgen viewer`` ensures the installation's engine and opens a page session.
The built React client ships in ``cadgen/_runtime/viewer``. Every file is named
by its absolute path; page sessions have independent starting folders.

No HTTP request thread imports OCP or build123d. Document reads, topology,
poses and meshes run in the engine's worker processes. The application holds
plain data and dispatches document routes in process, without a relay.
"""
