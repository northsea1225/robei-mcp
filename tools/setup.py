"""Bootstrap and self-check for robei-mcp.

    python tools/setup.py                 # check only (read-only, default)
    python tools/setup.py --apply         # create .venv, install deps, register the GUI task
    python tools/setup.py --check --deep  # also compile+run a two-line testbench
    python tools/setup.py --json          # machine-readable output

``--apply`` only creates things that are missing and never touches ``E:\\Robei``.
Every step it takes is printed so it can be undone by hand.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
REGISTER = ROOT / "tools" / "gui" / "register_task.py"
TASK = "RobeiMCP_GuiAgent"


def _run(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)


def _has_module(python: Path, module: str) -> bool:
    return _run([str(python), "-c", f"import {module}"]).returncode == 0


def _task_exists() -> bool:
    return _run(["schtasks", "/Query", "/TN", TASK], timeout=30).returncode == 0


def apply_steps() -> list[dict]:
    steps: list[dict] = []

    if not VENV_PY.is_file():
        proc = _run([sys.executable, "-m", "venv", str(ROOT / ".venv")], timeout=300)
        steps.append({"step": "create_venv", "ran": True, "ok": proc.returncode == 0,
                      "detail": (proc.stdout + proc.stderr).strip()[-300:]})
    else:
        steps.append({"step": "create_venv", "ran": False, "ok": True, "detail": str(VENV_PY)})

    if VENV_PY.is_file() and not _has_module(VENV_PY, "mcp"):
        proc = _run([str(VENV_PY), "-m", "pip", "install", "-q", "-r", "requirements.txt"], timeout=600)
        steps.append({"step": "pip_install", "ran": True, "ok": proc.returncode == 0,
                      "detail": (proc.stdout + proc.stderr).strip()[-300:]})
    else:
        steps.append({"step": "pip_install", "ran": False, "ok": True, "detail": "mcp already importable"})

    if VENV_PY.is_file() and not _has_module(VENV_PY, "matplotlib"):
        proc = _run([str(VENV_PY), "-m", "pip", "install", "-q", "matplotlib"], timeout=600)
        steps.append({"step": "pip_install_matplotlib", "ran": True, "ok": proc.returncode == 0,
                      "detail": (proc.stdout + proc.stderr).strip()[-300:]})
    else:
        steps.append({"step": "pip_install_matplotlib", "ran": False, "ok": True,
                      "detail": "matplotlib already importable"})

    if _task_exists():
        steps.append({"step": "register_task", "ran": False, "ok": True, "detail": f"{TASK} already registered"})
    elif VENV_PY.is_file():
        proc = _run([str(VENV_PY), str(REGISTER)], timeout=120)
        steps.append({"step": "register_task", "ran": True, "ok": proc.returncode == 0,
                      "detail": (proc.stdout + proc.stderr).strip()[-300:]})
    else:
        steps.append({"step": "register_task", "ran": False, "ok": False,
                      "detail": "no .venv yet; re-run --apply"})
    return steps


def doctor(deep: bool) -> dict:
    python = VENV_PY if VENV_PY.is_file() else Path(sys.executable)
    code = (
        "import json;from robei_mcp import doctor;"
        f"print(json.dumps(doctor.run(deep={deep}), ensure_ascii=False))"
    )
    proc = _run([str(python), "-c", code], timeout=180)
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout).strip()[-600:]}
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "error": f"cannot parse doctor output: {proc.stdout[-300:]}"}


def print_report(report: dict) -> None:
    if "checks" not in report:
        print("doctor failed:", report.get("error"))
        return
    width = max(len(c["name"]) for c in report["checks"]) + 2
    print(f"\n{'check'.ljust(width)}ok    detail")
    print("-" * (width + 40))
    for c in report["checks"]:
        mark = "yes" if c["ok"] else ("no*" if not c["required"] else "NO ")
        detail = c["detail"]
        if isinstance(detail, (dict, list)):
            detail = json.dumps(detail, ensure_ascii=False)
        print(f"{c['name'].ljust(width)}{mark}  {str(detail)[:110]}")
        if not c["ok"] and c.get("hint"):
            print(f"{' ' * width}      hint: {c['hint']}")
    print()
    if report.get("ok"):
        print("core checks passed" + (f" (warnings: {', '.join(report['warnings'])})" if report.get("warnings") else ""))
    else:
        print("core checks failed:", ", ".join(report.get("failures", [])))


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    deep = "--deep" in argv
    as_json = "--json" in argv

    steps = apply_steps() if apply else []
    report = doctor(deep)

    if as_json:
        print(json.dumps({"applied": steps, "doctor": report}, ensure_ascii=False, indent=1))
    else:
        if steps:
            print("\n--- bootstrap ---")
            for s in steps:
                state = "done" if s["ran"] else "skip"
                print(f"{s['step']:<24}{state:<6}{'ok' if s['ok'] else 'FAIL'}  {s['detail'][:90]}")
        print_report(report)
        if not apply:
            print("(this was a read-only check; run with --apply to create what is missing)")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
