# tests: unported

`joint_playground/joint_playground.urdf`: an authored URDF of primitives, no model, and
no doc names it, so the port to `cadgen.engine` left it out (`../README.md`, "Not
ported"). It is kept as it was. The engine's snapshot draws it as it is:

```bash
python -m cadgen.engine snapshot models/tests/unported/joint_playground/joint_playground.urdf out.png
```
