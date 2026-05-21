"""Pytest shared fixtures and configuration for Fuxi tool tests."""

import os
import sys
import shutil
import pytest

# ---------------------------------------------------------------------------
# sys.path setup: add python/src so that "from tools import registry" works
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..'))
SRC_DIR = os.path.join(PROJECT_ROOT, 'python', 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


@pytest.fixture(scope="session")
def project_root():
    """Return the absolute project root (fuxi_v0.2.5 directory)."""
    return PROJECT_ROOT


@pytest.fixture
def tmp_test_dir(project_root):
    """Create a temporary directory under PROJECT_ROOT/tmp_test_data/.

    This directory lives inside ``_BASE_DIR`` so that ``_validate_path()``
    in ``file_tools.py`` accepts paths pointing into it.  Cleaned up after
    each test.
    """
    path = os.path.join(project_root, 'tmp_test_data')
    os.makedirs(path, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)
