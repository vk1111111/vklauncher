#!/usr/bin/env python3
import sys

if sys.version_info < (3, 10):
    print("vklauncher requires Python 3.10 or newer.")
    sys.exit(1)

from launcher.tui import run

if __name__ == "__main__":
    run()
