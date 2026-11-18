import shutil
from pathlib import Path

import pytest

from kx_runner import kx

QA_PAIRS = 60
QA_ALTRUISTS = 4


@pytest.fixture(scope="session")
def built_store(tmp_path_factory) -> Path:
    """A fresh 60-pair store built and matched through the real CLI."""
    root = tmp_path_factory.mktemp("qa")
    store = root / "store"
    built = kx("build", "--store", str(store), "--pairs", str(QA_PAIRS), "--altruists", str(QA_ALTRUISTS), cwd=root)
    assert built.returncode == 0, built.stderr
    matched = kx("match", "--store", str(store), cwd=root)
    assert matched.returncode == 0, matched.stderr
    return store


@pytest.fixture
def store_copy(built_store, tmp_path) -> Path:
    target = tmp_path / "store"
    shutil.copytree(built_store, target)
    return target
