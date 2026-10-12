"""``python -m j5_macro.engine``: run the engine over stdio (JSON lines)."""

import sys

from j5_macro.engine.app import main

sys.exit(main())
