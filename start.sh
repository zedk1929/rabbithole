#!/usr/bin/env bash
# Startup script for Rabbithole
cd "$(dirname "$0")"

if command -v uv >/dev/null 2>&1; then
    uv run run.py
elif [ -f ".venv/bin/python" ]; then
    .venv/bin/python run.py
else
    PYTHONPATH=src python3 run.py
fi
