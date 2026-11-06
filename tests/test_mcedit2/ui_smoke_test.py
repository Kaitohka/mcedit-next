"""
    Run scripts/smoke_ui.py, which needs a display and opens windows, so it is opt-in.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.skipif("MCEDIT_GUI_TESTS" not in os.environ, reason="set MCEDIT_GUI_TESTS to open windows")


def test_ui_smoke_with_stub_assets():
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "smoke_ui.py"), "--stub-assets"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=600)
    assert result.returncode == 0, result.stdout[-4000:]
