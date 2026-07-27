import subprocess
import sys
from pathlib import Path

import pytest

HELLO_PY = Path(__file__).resolve().parent.parent / "hello.py"


def test_package_importable():
    import thermaltrend
    assert thermaltrend.__name__ == "thermaltrend"


@pytest.mark.parametrize(
    "submodule",
    [
        "thermaltrend.core.engine",
        "thermaltrend.core.strategy",
        "thermaltrend.feed",
        "thermaltrend.analytics.compare",
        "thermaltrend.analytics.metrics",
    ],
)
def test_submodules_importable(submodule):
    __import__(submodule)


def test_hello_prints_output():
    result = subprocess.run(
        [sys.executable, str(HELLO_PY)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Hello, Thermaltrend!" in result.stdout


def test_hello_no_stderr():
    result = subprocess.run(
        [sys.executable, str(HELLO_PY)],
        capture_output=True,
        text=True,
    )
    assert result.stderr == ""
