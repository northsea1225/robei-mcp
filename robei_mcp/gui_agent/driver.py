"""Small GUI driver that runs inside the interactive Windows session.

Every invocation is a separate process started by a scheduled task with /IT,
so all coordinates are physical screen pixels of the logged-in desktop.

Usage examples:
    python -m robei_mcp.gui_agent.driver shot label
    python -m robei_mcp.gui_agent.driver windows
    python -m robei_mcp.gui_agent.driver uia "Robei" 60
    python -m robei_mcp.gui_agent.driver focus "Robei"
    python -m robei_mcp.gui_agent.driver click 100 200
    python -m robei_mcp.gui_agent.driver dclick 100 200
    python -m robei_mcp.gui_agent.driver keys "^n"
    python -m robei_mcp.gui_agent.driver type "hello"
    python -m robei_mcp.gui_agent.driver start "E:\\Robei\\bin\\Robei.exe"
    python -m robei_mcp.gui_agent.driver kill <pid|Robei.exe>   (owned pid only)
    python -m robei_mcp.gui_agent.driver wait 2000

``probe*`` commands import a same-named module from ``tools/gui`` (dev only).
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .paths import SHOTS_DIR as OUT_DIR

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def vmetrics() -> dict:
    user32 = ctypes.windll.user32
    return {
        "virtual_x": user32.GetSystemMetrics(76),
        "virtual_y": user32.GetSystemMetrics(77),
        "virtual_w": user32.GetSystemMetrics(78),
        "virtual_h": user32.GetSystemMetrics(79),
    }


def session_id() -> int | None:
    value = ctypes.c_ulong()
    ok = ctypes.windll.kernel32.ProcessIdToSessionId(
        ctypes.windll.kernel32.GetCurrentProcessId(), ctypes.byref(value)
    )
    return int(value.value) if ok else None


def exe_name(pid: int) -> str:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = ctypes.c_ulong(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


def rect_tuple(rect) -> list[int]:
    if rect is None:
        return []
    try:
        return [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]
    except AttributeError:
        return list(rect)


def list_windows() -> list[dict]:
    import win32gui
    import win32process

    result: list[dict] = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title.strip():
            return True
        try:
            rect = list(win32gui.GetWindowRect(hwnd))
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            cls = win32gui.GetClassName(hwnd)
        except Exception:
            return True
        result.append(
            {
                "hwnd": hwnd,
                "title": title,
                "class": cls,
                "rect": rect,
                "pid": pid,
                "exe": exe_name(pid),
            }
        )
        return True

    win32gui.EnumWindows(callback, None)
    return result


def shot(label: str) -> dict:
    from PIL import ImageGrab, ImageStat

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = OUT_DIR / f"{label}-{stamp}.png"
    image = ImageGrab.grab(all_screens=True)
    image.save(target)
    stat = ImageStat.Stat(image.convert("RGB"))
    luma = image.convert("L").getextrema()
    return {
        "path": str(target),
        "size_bytes": target.stat().st_size,
        "width": image.width,
        "height": image.height,
        "mean_rgb": [round(v, 2) for v in stat.mean],
        "black_screen": luma[1] < 8,
        **vmetrics(),
    }


def find_hwnds(substr: str) -> list[int]:
    import win32gui

    hits: list[int] = []

    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if substr.lower() in title.lower():
                hits.append(hwnd)
        return True

    win32gui.EnumWindows(callback, None)
    return hits


def attach(hwnd: int):
    from pywinauto import Application

    return Application(backend="uia").connect(handle=hwnd).window(handle=hwnd)


def uia_dump(title_substr: str, limit: int = 120) -> dict:
    handles = find_hwnds(title_substr)
    if not handles:
        return {"ok": False, "error": f"no window matching {title_substr}"}
    win = attach(handles[0])
    items: list[dict] = []
    try:
        descendants = win.descendants()
    except Exception as exc:
        return {"ok": False, "error": f"descendants failed: {exc}", "title": win.window_text()}
    for ctrl in descendants[:limit]:
        try:
            info = ctrl.element_info
            rect = info.rectangle
            items.append(
                {
                    "type": info.control_type,
                    "name": (info.name or "")[:80],
                    "auto_id": (info.automation_id or "")[:60],
                    "class": (info.class_name or "")[:60],
                    "rect": rect_tuple(rect),
                    "enabled": bool(info.is_enabled),
                }
            )
        except Exception:
            continue
    return {
        "ok": True,
        "title": win.window_text(),
        "rect": rect_tuple(win.rectangle()),
        "count": len(items),
        "truncated": len(descendants) > limit,
        "items": items,
    }


def focus(title_substr: str) -> dict:
    handles = find_hwnds(title_substr)
    if not handles:
        return {"ok": False, "error": f"no window matching {title_substr}"}
    win = attach(handles[0])
    win.set_focus()
    time.sleep(0.3)
    return {
        "ok": True,
        "title": win.window_text(),
        "hwnd": handles[0],
        "rect": rect_tuple(win.rectangle()),
    }


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "windows"
    started = time.time()
    payload: dict
    if cmd == "shot":
        payload = shot(argv[2] if len(argv) > 2 else "shot")
    elif cmd.startswith("probe"):
        import importlib

        # Probes are dev-only scripts that still live in tools/gui.
        dev_dir = Path(__file__).resolve().parents[2] / "tools" / "gui"
        if dev_dir.is_dir() and str(dev_dir) not in sys.path:
            sys.path.insert(0, str(dev_dir))
        payload = importlib.import_module(cmd).run()
    elif cmd == "job":
        from . import job

        payload = job.run()
    elif cmd == "fg":
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        payload = {
            "ok": True,
            "hwnd": hwnd,
            "title": win32gui.GetWindowText(hwnd),
            "class": win32gui.GetClassName(hwnd),
            "pid": pid,
            "exe": exe_name(pid),
        }
    elif cmd == "windows":
        payload = {"ok": True, "session_id": session_id(), "windows": list_windows()}
    elif cmd == "uia_pid":
        from pywinauto import Application

        target_exe = argv[2].lower()
        rows = []
        for win in list_windows():
            if win["exe"].lower() != target_exe:
                continue
            row = {"hwnd": win["hwnd"], "title": win["title"], "class": win["class"], "count": None}
            try:
                app = Application(backend="uia").connect(handle=win["hwnd"])
                dlg = app.window(handle=win["hwnd"])
                row["count"] = len(dlg.descendants())
                row["control_type"] = dlg.element_info.control_type
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)
        payload = {"ok": True, "windows": rows}
    elif cmd == "uia":
        payload = uia_dump(argv[2] if len(argv) > 2 else "Robei", int(argv[3]) if len(argv) > 3 else 120)
    elif cmd == "focus":
        payload = focus(argv[2] if len(argv) > 2 else "Robei")
    elif cmd in {"click", "dclick"}:
        from pywinauto import mouse

        x, y = int(argv[2]), int(argv[3])
        mouse.click(button="left", coords=(x, y), double=(cmd == "dclick"))
        time.sleep(0.4)
        payload = {"ok": True, "clicked": [x, y], "double": cmd == "dclick"}
    elif cmd == "rmenu":
        from pywinauto import mouse

        x, y, dx, dy = int(argv[2]), int(argv[3]), int(argv[4]), int(argv[5])
        label = argv[6] if len(argv) > 6 else "rmenu"
        mouse.right_click(coords=(x, y))
        time.sleep(1.0)
        mouse.click(button="left", coords=(x + dx, y + dy))
        time.sleep(2.0)
        payload = shot(label)
        payload["menu_click"] = [x + dx, y + dy]
    elif cmd == "dclick2":
        from pywinauto import mouse

        x, y = int(argv[2]), int(argv[3])
        label = argv[4] if len(argv) > 4 else "dclick2"
        mouse.click(button="left", coords=(x, y))
        time.sleep(0.12)
        mouse.click(button="left", coords=(x, y))
        time.sleep(1.5)
        payload = shot(label)
        payload["double_clicked"] = [x, y]
    elif cmd == "rclick":
        from pywinauto import mouse

        x, y = int(argv[2]), int(argv[3])
        mouse.right_click(coords=(x, y))
        time.sleep(0.8)
        payload = {"ok": True, "right_clicked": [x, y]}
    elif cmd == "rclick_shot":
        from pywinauto import mouse

        x, y = int(argv[2]), int(argv[3])
        label = argv[4] if len(argv) > 4 else "rclick"
        mouse.right_click(coords=(x, y))
        time.sleep(1.2)
        payload = shot(label)
        payload["right_clicked"] = [x, y]
    elif cmd == "click_shot":
        from pywinauto import mouse

        x, y = int(argv[2]), int(argv[3])
        label = argv[4] if len(argv) > 4 else "click"
        mouse.click(button="left", coords=(x, y))
        time.sleep(0.8)
        payload = shot(label)
        payload["clicked"] = [x, y]
    elif cmd == "keys_shot":
        from pywinauto import keyboard

        keyseq = argv[2]
        label = argv[3] if len(argv) > 3 else "keys"
        keyboard.send_keys(keyseq, pause=0.05)
        time.sleep(0.8)
        payload = shot(label)
        payload["keys"] = keyseq
    elif cmd == "paste":
        import win32clipboard
        from pywinauto import keyboard

        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(argv[2], win32clipboard.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
        keyboard.send_keys("^v", pause=0.05)
        time.sleep(0.3)
        payload = {"ok": True, "pasted": argv[2]}
    elif cmd == "keys":
        from pywinauto import keyboard

        keyboard.send_keys(argv[2], pause=0.05)
        time.sleep(0.2)
        payload = {"ok": True, "keys": argv[2]}
    elif cmd == "type":
        from pywinauto import keyboard

        keyboard.send_keys(argv[2], with_spaces=True, pause=0.01)
        time.sleep(0.2)
        payload = {"ok": True, "typed_chars": len(argv[2])}
    elif cmd == "startenv":
        # startenv VAR=VAL [VAR2=VAL2] <exe> [args...]
        pairs = []
        rest = argv[2:]
        while rest and "=" in rest[0] and not rest[0].lower().endswith(".exe"):
            pairs.append(rest.pop(0))
        target = rest[0]
        args = rest[1:]
        env = os.environ.copy()
        for pair in pairs:
            key, _, value = pair.partition("=")
            env[key] = value
        proc = subprocess.Popen([target, *args], cwd=str(Path(target).parent), env=env)
        time.sleep(1.0)
        payload = {"ok": True, "pid": proc.pid, "exe": target, "env": pairs}
    elif cmd == "start":
        target = argv[2]
        args = argv[3:]
        proc = subprocess.Popen([target, *args], cwd=str(Path(target).parent))
        time.sleep(1.0)
        payload = {"ok": True, "pid": proc.pid, "exe": target, "args": args}
    elif cmd == "restore":
        import win32gui

        handles = find_hwnds(argv[2])
        if not handles:
            payload = {"ok": False, "error": f"no window matching {argv[2]}"}
        else:
            win32gui.ShowWindow(handles[0], 9)  # SW_RESTORE
            payload = {"ok": True, "hwnd": handles[0]}
    elif cmd == "minimize":
        import win32gui

        handles = find_hwnds(argv[2])
        if not handles:
            payload = {"ok": False, "error": f"no window matching {argv[2]}"}
        else:
            win32gui.ShowWindow(handles[0], 6)  # SW_MINIMIZE
            payload = {"ok": True, "hwnd": handles[0]}
    elif cmd == "kill":
        # Only the IDE this bridge launched may be killed.  ``taskkill /F /IM
        # Robei.exe`` would also take out a hand-started (or someone else's)
        # instance, so a cleanup request by image name is refused instead.
        from .ui import owned_pid

        requested = argv[2].strip()
        pid = owned_pid()
        if pid is None:
            payload = {
                "ok": False,
                "error": (
                    "no Robei instance recorded by this bridge; refusing to kill by image "
                    f"name ({requested!r}) - start the IDE with robei_gui_launch so the "
                    "bridge owns the pid"
                ),
            }
        elif requested.isdigit() and int(requested) != pid:
            payload = {
                "ok": False,
                "error": f"pid {requested} is not the instance this bridge started (owned pid {pid})",
            }
        else:
            proc = subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)], capture_output=True, text=True
            )
            payload = {
                "ok": proc.returncode == 0,
                "pid": pid,
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
            }
    elif cmd == "menus":
        from pywinauto import mouse

        targets = [
            ("file", 20),
            ("edit", 56),
            ("tool", 91),
            ("setting", 141),
            ("build", 229),
            ("view", 262),
            ("window", 317),
            ("help", 362),
        ]
        y = int(argv[2]) if len(argv) > 2 else 52
        from pywinauto import keyboard

        shots = []
        for name, x in targets:
            keyboard.send_keys("{ESC}", pause=0.05)
            time.sleep(0.4)
            mouse.click(button="left", coords=(x, y))
            time.sleep(1.6)
            info = shot(f"menu-{name}")
            shots.append({"menu": name, "path": info["path"]})
        payload = {"ok": True, "shots": shots}
    elif cmd == "menu_one":
        from pywinauto import keyboard, mouse

        name = argv[2]
        x, y = int(argv[3]), int(argv[4]) if len(argv) > 4 else 52
        keyboard.send_keys("{ESC}", pause=0.05)
        time.sleep(0.6)
        mouse.click(button="left", coords=(x, y))
        time.sleep(1.5)
        info = shot(f"menuone-{name}")
        payload = {"ok": True, "menu": name, "path": info["path"]}
    elif cmd == "wait":
        time.sleep(int(argv[2]) / 1000)
        payload = {"ok": True, "waited_ms": int(argv[2])}
    else:
        payload = {"ok": False, "error": f"unknown command: {cmd}"}

    payload["command"] = cmd
    payload["duration_ms"] = round((time.time() - started) * 1000)
    # Console redirection uses a legacy code page; always persist UTF-8 JSON.
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "last.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps({"command": cmd, "ok": payload.get("ok"), "result_file": str(OUT_DIR / "last.json")}))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
