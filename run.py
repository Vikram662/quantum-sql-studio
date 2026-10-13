"""Launcher used for development (python run.py) and as the PyInstaller entry script."""
import sys

from quantumsql.app import main

if __name__ == "__main__":
    sys.exit(main())
