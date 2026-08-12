"""Suite-wide isolation for backend tests.

This file is loaded before test modules are collected, so application config
can never bind to the repository's development database by accident.
"""

import os
import sys
import tempfile
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_TEST_DATA = tempfile.TemporaryDirectory(prefix="subtitle-factory-pytest-")
os.environ["SUBTITLE_FACTORY_DATA_DIR"] = _TEST_DATA.name


def pytest_sessionstart(session):
    del session
    from app.utils.config import DATA_DIR

    if Path(DATA_DIR).resolve() != Path(_TEST_DATA.name).resolve():
        raise RuntimeError("后端测试拒绝使用非隔离数据目录")


def pytest_sessionfinish(session, exitstatus):
    del session, exitstatus
    _TEST_DATA.cleanup()
