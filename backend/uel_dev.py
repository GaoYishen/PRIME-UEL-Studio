"""Standalone entry for the bundled copied-case development backend."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uel_lab.devflow.cli import standalone_main

if __name__ == "__main__":
    raise SystemExit(standalone_main())
