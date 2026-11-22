#!/usr/bin/env python3
"""All tests, offscreen, temp config folder, no real claude, no network. Works on Linux, macOS and Windows.
Run: python tests/run.py [unittest args]   (tests/run.sh does the same for bash)"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "QT_SCREEN_SCALE_FACTORS", "SESSION_MANAGER")}
env["QT_QPA_PLATFORM"] = "offscreen"
rc = 0
for name in ("test_counter", "test_mdhtml", "test_window", "test_features"):
    print("==", name, flush=True)
    rc |= subprocess.call([sys.executable, "-I", str(ROOT / "tests" / f"{name}.py"), *sys.argv[1:]], cwd=ROOT, env=env) != 0
sys.exit(rc)
