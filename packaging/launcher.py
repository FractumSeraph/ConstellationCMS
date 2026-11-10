"""What the installed ConstellationCMS program runs (PyInstaller's entry point)."""

import multiprocessing
import sys

from constellation.desktop import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
