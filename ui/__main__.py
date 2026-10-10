"""``python -m ui`` — start the operator shell.

Deliberately does **not** call :func:`lib.env.load_env`. This process holds no credentials: every
command it runs is a subprocess, and each of those loads ``.env`` in its own ``__main__`` block,
which is the arrangement ``tests/lib/test_env.py`` enforces. Loading it here would put production
secrets into a long-lived desktop process for no benefit, and would arm the staging-guard
variables inside it.

It does enter the data folder (:mod:`lib.data_dir`), which holds no secret: the shell calls ``lib``
in-process too, and those calls resolve ``Path("output") / …`` against the working directory.
"""

from __future__ import annotations

import argparse

from lib.data_dir import enter_data_dir
from ui.app import main


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ui", description="The GS1 Digital Link operator shell.")
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Serve in a browser tab instead of a native window (still loopback-only)",
    )
    return parser.parse_args()


if __name__ in {"__main__", "__mp_main__"}:  # NiceGUI re-imports under this name on some platforms
    enter_data_dir()
    main(native=not _parse_args().browser)
