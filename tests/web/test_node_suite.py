"""Run the Node test suite for the pure web modules (``tests/web/*.test.js``) under pytest."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_node_suites():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found")
    # Node 24 does not expand a bare directory argument, so pass a glob.
    proc = subprocess.run(
        [node, "--test", "tests/web/*.test.js"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr)
    assert proc.returncode == 0, "node --test failed; output printed above"
