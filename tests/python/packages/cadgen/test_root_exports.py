"""What `from cadgen import ...` offers, and what it costs.

Two properties that are easy to break together. Every name in ``__all__`` must actually
resolve -- the lazy ``__getattr__`` dispatches on hand-written name sets, so adding a name
to ``__all__`` and forgetting the branch advertises an AttributeError. And importing the
package must stay cheap: the whole point of the lazy dispatch is that `import cadgen`
does not pay for OCP, which costs seconds and is useless to a caller who only wanted
`__version__`.
"""

import subprocess
import sys
import unittest

import cadgen

HEAVY = ("OCP", "build123d")


class RootExports(unittest.TestCase):
    def test_every_advertised_name_resolves(self):
        unresolvable = [name for name in cadgen.__all__ if not hasattr(cadgen, name)]
        self.assertEqual([], unresolvable, "in __all__ but not handled by __getattr__")

    def test_the_root_is_the_engines_surface_and_its_verbs(self):
        """A model imports what it needs from ``cadgen`` itself: the engine's names, each the very
        object ``cadgen.engine`` exports, and its verbs as functions."""
        import cadgen.engine
        from cadgen.engine import api

        self.assertEqual(set(cadgen.engine.__all__) - set(cadgen.__all__), set(), "an engine name the root lacks")
        for name in cadgen.engine.__all__:
            with self.subTest(name=name):
                self.assertIs(getattr(cadgen, name), getattr(cadgen.engine, name))
        for verb in ("export", "snapshot", "serve", "inspect", "status", "stop", "daemon"):
            with self.subTest(verb=verb):
                self.assertIs(getattr(cadgen, verb), getattr(api, verb))
        self.assertEqual(set(cadgen.__all__) - set(cadgen.engine.__all__),
                         {"__version__", "export", "snapshot", "serve", "inspect", "status", "stop", "daemon",
                          "build123d", "srgb"})

    def test_a_name_the_root_never_had_is_pythons_own_error(self):
        # No backwards compatibility: a retired name is no name, and says nothing about history.
        # ``build`` among them: a model builds by a decorated function's call, never cadgen.build (law 12).
        for name in ("build", "read_scene", "AssemblyHelper", "label_shape", "revolute", "compound_from_instances"):
            with self.subTest(name=name), self.assertRaises(AttributeError) as caught:
                getattr(cadgen, name)
            self.assertEqual(str(caught.exception), f"module 'cadgen' has no attribute {name!r}")

    def test_importing_cadgen_does_not_import_the_cad_stack(self):
        """Run in a subprocess: this one almost certainly has OCP loaded already."""
        code = (
            "import sys, cadgen;"
            f"print('HEAVY:' + ','.join(m for m in {HEAVY!r} if m in sys.modules))"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        marked = [ln for ln in proc.stdout.splitlines() if ln.startswith("HEAVY:")]
        self.assertEqual(["HEAVY:"], marked, "importing cadgen pulled in the CAD stack")

    def test_model_body_helpers_do_not_import_the_cad_stack(self):
        """`from cadgen import flatten` sits in a model's MODULE BODY, so the module behind it
        must defer the kernel the way `cadgen.build123d` does -- or every such model pays the
        ~2.5s import in the process that only asks the daemon to build it."""
        code = (
            "import sys, cadgen.flatten;"
            "from cadgen import flatten;"
            f"print('HEAVY:' + ','.join(m for m in {HEAVY!r} if m in sys.modules))"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        marked = [ln for ln in proc.stdout.splitlines() if ln.startswith("HEAVY:")]
        self.assertEqual(["HEAVY:"], marked, "cadgen.flatten imported the kernel at module scope")


if __name__ == "__main__":
    unittest.main()
