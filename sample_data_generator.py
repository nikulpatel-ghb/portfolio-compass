"""Convenience launcher for the standalone Sample Data Generator."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys


def _use_project_environment() -> None:
    """Re-launch with the project venv when the shell selected another Python."""

    venv_python = Path(__file__).resolve().parent / ".venv" / "Scripts" / "python.exe"
    current_python = Path(sys.executable).resolve()
    if venv_python.exists() and current_python != venv_python and importlib.util.find_spec("yfinance") is None:
        os.execv(str(venv_python), [str(venv_python), __file__, *sys.argv[1:]])


if __name__ == "__main__":
    _use_project_environment()
    from portfolio_compass.sample_data_generator import main

    main()
