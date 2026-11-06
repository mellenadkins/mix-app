"""Pytest configuration for the Mix Revision Agent test suite.

The package lives under ``src/`` (a src layout), so this file adds it to
``sys.path`` so that ``python -m pytest tests/`` works straight from the
repository root without requiring ``PYTHONPATH=src`` to be set manually.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
