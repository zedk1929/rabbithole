#!/usr/bin/env python3
"""
Rabbithole App Entrypoint.
Enables running the application directly from the repository root via:
    python3 run.py
or:
    uv run run.py
"""
import sys
from pathlib import Path

# Add src/ to Python module search path
src_path = Path(__file__).resolve().parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from rabbithole_app.server import run

if __name__ == "__main__":
    run()
