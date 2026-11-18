import subprocess
import sys
from pathlib import Path


def kx(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "kidneyloop.cli", *args], cwd=cwd, capture_output=True, text=True, timeout=600
    )
