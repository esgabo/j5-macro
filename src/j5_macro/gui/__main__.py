"""``python -m j5_macro.gui [SCRIPT_OR_DIR]``: the same as ``j5 gui``."""

import sys

from j5_macro.cli.main import main

if __name__ == "__main__":
    sys.exit(main(["gui", *sys.argv[1:]]))
