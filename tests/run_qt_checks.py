"""Run isolated offline GUI checks; requires Windows desktop + PySide6."""
import os
from pathlib import Path
import runpy
import sys
import tempfile
root = Path(__file__).resolve().parents[1]
output = Path(os.environ.get("HUD_TEST_OUTPUT", str(Path(tempfile.gettempdir()) / "ai-usage-hud-tests")))
output.mkdir(parents=True, exist_ok=True)
os.environ.update(HUD_QT_TEST="1", HUD_QT_CONFIG=str(output / "config.json"),
                  HUD_QT_TEST_SCRIPT=str(root / "tests" / "qt_checks.py"), HUD_TEST_OUTPUT=str(output))
sys.path.insert(0, str(root))
runpy.run_path(str(root / "ai-hud-qt.py"), run_name="__main__")
