"""Compatibility launcher for ``uv run python main.py``.

All application logic lives in ``tutor_agent.main``.
``app`` is re-exported so ``uvicorn main:app`` remains valid.
"""

from __future__ import annotations

from tutor_agent.main import app, run

__all__ = ["app", "run"]

if __name__ == "__main__":
    run()
