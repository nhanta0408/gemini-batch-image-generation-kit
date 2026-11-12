#!/usr/bin/env python3
"""Entry point. From a workspace: ./gbig <command>. From the tool folder: ./gbig init <folder>."""
import sys
from pathlib import Path

if sys.version_info < (3, 9):
    sys.exit("STOP: Python 3.9 or newer is needed (macOS: xcode-select --install).")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.cli import main  # noqa: E402

main()
