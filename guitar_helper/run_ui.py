"""CLI entrypoint: launch the PySide6 correction/calibration UI.

Usage:
    python -m guitar_helper.run_ui [--mock] [--db ...]

Requires loopMIDI running with the configured port (unless --mock).
"""
from __future__ import annotations

import argparse
import sys

from guitar_helper.config import add_config_args, config_from_args
from guitar_helper.ui.app import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Guitar Helper correction/calibration UI.")
    parser.add_argument("--mock", action="store_true", help="Use a mock MIDI port (no loopMIDI)")
    add_config_args(parser)
    args = parser.parse_args()
    cfg = config_from_args(args)
    cfg.ensure_dirs()
    cfg.ensure_user_resources()
    sys.exit(run(cfg, mock=args.mock))


if __name__ == "__main__":
    main()
