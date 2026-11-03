"""
Rabbithole — Non-predatory, curiosity-driven media recommendation engine.
"""
from rabbithole_app.server import run, app

def main() -> None:
    run()

__all__ = ["run", "app", "main"]

