"""Pytest config: put reverie/backend_server on sys.path for all tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "reverie" / "backend_server"))
