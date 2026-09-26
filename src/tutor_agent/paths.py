"""Repository resource path helpers.

Resolves paths relative to the repository root so modules under ``src/tutor_agent``
do not hard-code parent-directory counts when loading prompts, evals, or data.
"""

from __future__ import annotations

from pathlib import Path

# src/tutor_agent/paths.py -> parents[0]=tutor_agent, [1]=src, [2]=repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]


def repo_root() -> Path:
    """Return the tutor-agent repository root."""
    return _REPO_ROOT


def prompts_dir() -> Path:
    """Version-controlled prompt registry and approved prompt files."""
    return _REPO_ROOT / "prompts"


def evals_dir() -> Path:
    """Offline evaluation fixtures and schemas."""
    return _REPO_ROOT / "evals"


def data_dir() -> Path:
    """Local runtime data directory (typically gitignored)."""
    return _REPO_ROOT / "data"


def frontend_dir() -> Path:
    """Frontend package directory."""
    return _REPO_ROOT / "frontend"


def scripts_dir() -> Path:
    """Operational and submission scripts."""
    return _REPO_ROOT / "scripts"


def docs_dir() -> Path:
    """Project documentation."""
    return _REPO_ROOT / "docs"
