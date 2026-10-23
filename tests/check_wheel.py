"""Assert that a built wheel really contains the optional GUI driver.

0.11.0 shipped a wheel with 24 files and no tools/gui, so every `robei_gui_*`
tool broke after `pip install`. This guard is cheap and catches that class of
regression on every CI run.

Usage:  python tests/check_wheel.py [dist/]
"""
from __future__ import annotations

import glob
import sys
import zipfile
from pathlib import Path

REQUIRED = [
    "robei_mcp/__init__.py",
    "robei_mcp/server_app.py",
    "robei_mcp/gui.py",
    "robei_mcp/gui_agent/__init__.py",
    "robei_mcp/gui_agent/paths.py",
    "robei_mcp/gui_agent/ui.py",
    "robei_mcp/gui_agent/driver.py",
    "robei_mcp/gui_agent/job.py",
    "robei_mcp/gui_agent/launcher.py",
]
FORBIDDEN = ["tools/gui/", ".venv/", "workspace/build/"]


def main(argv: list[str]) -> int:
    where = Path(argv[1] if len(argv) > 1 else "dist")
    wheels = sorted(glob.glob(str(where / "*.whl")))
    if not wheels:
        print(f"no wheel under {where}", file=sys.stderr)
        return 2
    wheel = wheels[-1]
    names = zipfile.ZipFile(wheel).namelist()
    missing = [name for name in REQUIRED if name not in names]
    leaked = [name for name in names if any(bad in name for bad in FORBIDDEN)]

    print(f"wheel: {Path(wheel).name}")
    print(f"files: {len(names)}")
    for name in REQUIRED:
        print(f"  {'ok     ' if name in names else 'MISSING'} {name}")
    if leaked:
        print("  leaked:", leaked[:5])

    ok = not missing and not leaked
    print("RESULT:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
