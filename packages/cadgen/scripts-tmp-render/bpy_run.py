"""Run blender_render.py through the `bpy` wheel instead of the Blender app: same script, same args.
usage: python bpy_run.py SHOT.json ENGINE OUT_DIR [options]"""
import runpy, sys
from pathlib import Path
sys.argv = [sys.argv[0], "--"] + sys.argv[1:]
runpy.run_path(str(Path(__file__).resolve().parent / "blender_render.py"), run_name="__main__")
