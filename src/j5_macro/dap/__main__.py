"""``python -m j5_macro.dap``: the DAP server on stdio."""

from j5_macro.dap.server import main

if __name__ == "__main__":
    raise SystemExit(main())
