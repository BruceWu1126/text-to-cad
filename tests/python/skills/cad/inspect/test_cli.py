"""Retired inspect entry points fail immediately with migration guidance."""
import contextlib
import io
import subprocess
import sys
import unittest
from unittest import mock


class RemovedInspectTests(unittest.TestCase):
    def test_retired_imports_name_the_replacement(self):
        for statement in (
            "from cadgen import load_step_scene",
            "from cadgen.step import inspect",
            "from cadgen.step_scene import scene_occurrence_shape",
        ):
            with self.subTest(statement=statement), self.assertRaisesRegex(ImportError, "read_scene"):
                exec(statement, {})

    def test_retired_diagnostic_modules_fail_without_loading_the_kernel(self):
        for module, replacement in (
            ("cadgen.interference", "overlap_volume"),
            ("cadgen.validity", "topology_errors"),
        ):
            with self.subTest(module=module):
                code = f'''
import importlib, sys
class NoKernel:
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {{'OCP', 'build123d'}}:
            raise AssertionError('retired diagnostic imported kernel')
sys.meta_path.insert(0, NoKernel())
try:
    importlib.import_module({module!r})
except ImportError as error:
    assert 'removed' in str(error), str(error)
    assert 'cadgen.geometry' in str(error), str(error)
    assert {replacement!r} in str(error), str(error)
else:
    raise AssertionError('retired diagnostic import succeeded')
'''
                result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_python_api_is_removed_and_help_does_not_advertise_it(self):
        import cadgen
        from cadgen import step
        with self.assertRaisesRegex(ImportError, "read_scene"):
            step.inspect
        with self.assertRaisesRegex(ImportError, "read_scene"):
            cadgen.load_step_scene
