"""``python -m cadgen.engine``: the ``cadgen xbf`` commands."""

import sys

from cadgen.engine.cli import main

sys.exit(main(prog="python -m cadgen.engine"))
