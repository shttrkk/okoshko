"""Explicit development toolchain pins must survive pip-compile's unsafe filter."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("package", ["pip", "setuptools"])
def test_explicit_unsafe_toolchain_pin_is_emitted_with_hashes(package):
    source = (ROOT / "requirements-dev.in").read_text()
    declared = re.search(rf"(?m)^{package}==([^\s]+)$", source)
    assert declared is not None, f"Expected explicit development toolchain pin: {package}"
    lock = (ROOT / "requirements-dev.lock").read_text()
    emitted = re.search(rf"(?ms)^{package}==([^\s\\]+)(.*?)(?=^[A-Za-z0-9_.-]+==|\Z)", lock)
    assert emitted is not None, f"Regenerate with --allow-unsafe: missing {package}"
    assert emitted[1] == declared[1]
    assert re.search(r"--hash=sha256:[a-f0-9]{64}\b", emitted[2]), f"Missing public artifact hash: {package}"
