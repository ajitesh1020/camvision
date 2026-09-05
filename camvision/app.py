"""CamVision entry point.

Launched standalone (``python3 -m camvision.app``) or, on the machine, by
LinuxCNC's ``[APPLICATIONS] APP`` so the window opens beside the AXIS GUI. The
config path defaults to a ``config.json`` next to the active LinuxCNC config
(``CAMVISION_CONFIG`` overrides it), so calibration and offsets persist with the
machine config.
"""

from __future__ import annotations

import logging
import os
import sys


def default_config_path() -> str:
    env = os.environ.get("CAMVISION_CONFIG")
    if env:
        return env
    base = os.environ.get("CONFIG_DIR") or os.getcwd()
    return os.path.join(base, "config.json")


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    argv = list(sys.argv if argv is None else argv)
    config_path = argv[1] if len(argv) > 1 else default_config_path()

    from PyQt5.QtWidgets import QApplication  # imported here so --help etc. need no Qt

    from .ui.main_window import MainWindow

    app = QApplication(argv)
    window = MainWindow(config_path)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
