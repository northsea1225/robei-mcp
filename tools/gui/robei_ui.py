"""Drive the Robei IDE through its own compiled-in UIA bridge.

Runs inside the interactive Windows session (Session 1). Every entry point
returns a plain dict; no file in the Robei installation is written by us.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

PROCESS = "Robei.exe"
MENU_BAR_ID = "QApplication.Romain.QMenuBar"
TOOLBAR_ID = "QApplication.Romain.QToolBar"
OUTPUT_ID = "QApplication.Romain.Output.TextEdit"
FILE_TREE_ID = "QApplication.Romain.FileManager.FileTree"
MODEL_TAB_ID = "QApplication.Romain.Tab.qt_tabwidget_tabbar"
GRAPH_CODE_MARK = "Design.qt_tabwidget_tabbar"
DIALOG_CLASS = "#32770"
FILENAME_COMBO_ID = "1148"


# --------------------------------------------------------------------------
# window plumbing
# --------------------------------------------------------------------------
def _exe_name(pid: int) -> str:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)
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


def windows() -> list[dict]:
    import win32gui
    import win32process

    rows: list[dict] = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if _exe_name(pid).lower() != PROCESS.lower():
            return True
        rows.append(
            {
                "hwnd": hwnd,
                "pid": pid,
                "title": title,
                "class": win32gui.GetClassName(hwnd),
                "rect": list(win32gui.GetWindowRect(hwnd)),
            }
        )
        return True

    win32gui.EnumWindows(cb, None)
    return rows


def _rect(obj) -> list[int]:
    try:
        return [int(obj.left), int(obj.top), int(obj.right), int(obj.bottom)]
    except AttributeError:
        return [int(v) for v in obj]


def _center(rect: list[int]) -> tuple[int, int]:
    return ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)


def attach(hwnd: int):
    from pywinauto import Application

    return Application(backend="uia").connect(handle=hwnd).window(handle=hwnd)


def main_window():
    """Return (wrapper, win_info) for the Robei main window, or (None, None).

    Modal dialogs share the ``Qt640QWindowIcon`` class, so pick the biggest window
    that actually owns a menu bar instead of the first Qt window.
    """
    candidates = [w for w in windows() if w["class"].startswith("Qt")]
    candidates.sort(key=lambda w: -((w["rect"][2] - w["rect"][0]) * (w["rect"][3] - w["rect"][1])))
    for win in candidates:
        try:
            dlg = attach(win["hwnd"])
            if dlg.child_window(auto_id="QApplication.Romain.QMenuBar").exists(timeout=0.3):
                return dlg, win
        except Exception:
            continue
    return None, None


def popups() -> list[dict]:
    out = []
    for win in windows():
        if "Popup" not in win["class"]:
            continue
        try:
            p = attach(win["hwnd"])
            out.append(
                {
                    "items": [
                        {
                            "type": c.element_info.control_type,
                            "name": (c.element_info.name or ""),
                            "rect": _rect(c.element_info.rectangle),
                        }
                        for c in p.descendants()[:80]
                    ]
                }
            )
        except Exception:
            continue
    return out


# --------------------------------------------------------------------------
# small readers
# --------------------------------------------------------------------------
def read_output(dlg) -> str:
    try:
        return dlg.child_window(auto_id=OUTPUT_ID, control_type="Edit").window_text()
    except Exception as exc:
        return f"ERR {type(exc).__name__}: {exc}"


def _tail(text: str, lines: int) -> str:
    parts = text.splitlines()
    return "\n".join(parts[-max(1, lines) :])


def tree_items(dlg) -> list[str]:
    for ctrl in dlg.descendants(control_type="Tree"):
        try:
            if ctrl.element_info.class_name != "FileTree":
                continue
            return [i.window_text() for i in ctrl.descendants(control_type="TreeItem")[:80]]
        except Exception:
            continue
    return []


def tab_groups(dlg) -> list[dict]:
    groups = []
    for ctrl in dlg.descendants(control_type="Tab"):
        try:
            groups.append(
                {
                    "auto_id": ctrl.element_info.automation_id,
                    "items": [t.window_text() for t in ctrl.children(control_type="TabItem")],
                }
            )
        except Exception:
            continue
    return groups


def model_tabs(dlg) -> list[str]:
    out: list[str] = []
    for g in tab_groups(dlg):
        if g["auto_id"] == MODEL_TAB_ID:
            out.extend(g["items"])
    return out


def active_model(dlg) -> str | None:
    """Name of the selected model tab, when Qt exposes SelectionItemPattern."""
    for ctrl in dlg.descendants(control_type="Tab"):
        try:
            if ctrl.element_info.automation_id != MODEL_TAB_ID:
                continue
            for item in ctrl.children(control_type="TabItem"):
                try:
                    if item.iface_selection_item.CurrentIsSelected:
                        return item.window_text()
                except Exception:
                    continue
        except Exception:
            continue
    return None


def menu_headers(dlg) -> dict:
    mb = dlg.child_window(auto_id=MENU_BAR_ID)
    return {
        i.element_info.name: _rect(i.element_info.rectangle)
        for i in mb.descendants(control_type="MenuItem")
    }


# --------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------
def click_menu(dlg, title: str, item: str, attempts: int = 3) -> dict:
    """Open a top menu and click one of its entries. Returns a small report."""
    from pywinauto import keyboard, mouse

    headers = menu_headers(dlg)
    if title not in headers:
        return {"ok": False, "error": f"menu {title!r} not found", "menus": list(headers)}
    tries = []
    for attempt in range(attempts):
        try:
            dlg.set_focus()
        except Exception:
            pass
        keyboard.send_keys("{ESC}")
        time.sleep(0.4)
        mouse.click(button="left", coords=_center(headers[title]))
        time.sleep(1.2)
        found = [p for p in popups() if any(i["name"] == item for i in p["items"])]
        tries.append(
            {"attempt": attempt, "items": [i["name"] for p in popups() for i in p["items"] if i["type"] == "MenuItem"]}
        )
        for popup in found:
            for entry in popup["items"]:
                if entry["name"] == item and entry["rect"]:
                    mouse.click(button="left", coords=_center(entry["rect"]))
                    time.sleep(1.2)
                    return {"ok": True, "menu": title, "item": item, "tries": len(tries)}
        keyboard.send_keys("{ESC}")
        time.sleep(0.3)
    return {"ok": False, "error": f"{item!r} not found under {title!r}", "tries": tries}


def _wait_dialog(timeout_s: float = 8.0) -> dict | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for win in windows():
            if win["class"] == DIALOG_CLASS:
                return win
        time.sleep(0.2)
    return None


def _open_dialog_controls(win: dict) -> list[dict]:
    d = attach(win["hwnd"])
    rows = []
    for c in d.descendants():
        i = c.element_info
        rows.append(
            {
                "type": i.control_type,
                "name": (i.name or ""),
                "auto_id": (i.automation_id or ""),
                "class": (i.class_name or ""),
                "rect": _rect(i.rectangle),
                "ctrl": c,
            }
        )
    return rows


def select_file_in_dialog(path: str) -> dict:
    """Type an absolute path into the native Open dialog and confirm it."""
    from pywinauto import keyboard, mouse

    dialog = _wait_dialog()
    if dialog is None:
        return {"ok": False, "error": "file dialog did not appear"}
    controls = _open_dialog_controls(dialog)
    combo = next((c for c in controls if c["type"] == "ComboBox" and c["auto_id"] == FILENAME_COMBO_ID), None)
    if combo is None:
        combo = next((c for c in controls if c["type"] == "ComboBox"), None)
    if combo is None:
        keyboard.send_keys("{ESC}")
        return {"ok": False, "error": "filename field not found"}
    mouse.click(button="left", coords=_center(combo["rect"]))
    time.sleep(0.3)
    keyboard.send_keys("^a")
    keyboard.send_keys(path, with_spaces=True, pause=0.005)
    time.sleep(0.6)
    keyboard.send_keys("{ENTER}")
    deadline = time.time() + 20.0
    while time.time() < deadline:
        if not any(w["class"] == DIALOG_CLASS for w in windows()):
            return {"ok": True, "typed": path}
        time.sleep(0.3)
    button = next((c for c in controls if c["type"] == "Button" and re.match(r"^(打开|Open)", c["name"] or "")), None)
    if button is not None:
        mouse.click(button="left", coords=_center(button["rect"]))
        deadline = time.time() + 20.0
        while time.time() < deadline:
            if not any(w["class"] == DIALOG_CLASS for w in windows()):
                return {"ok": True, "typed": path, "confirmed": "button"}
            time.sleep(0.3)
    keyboard.send_keys("{ESC}")
    return {"ok": False, "error": "file dialog did not close"}


# --------------------------------------------------------------------------
# public operations
# --------------------------------------------------------------------------
def status(output_lines: int = 20) -> dict:
    dlg, win = main_window()
    if dlg is None:
        running = [w for w in windows()]
        return {
            "ok": running == [] or True,
            "running": bool(running),
            "windows": running,
            "hint": "Robei is not running; call robei_gui_launch",
        }
    text = read_output(dlg)
    return {
        "ok": True,
        "running": True,
        "pid": win["pid"],
        "title": win["title"],
        "project": next((t for t in tree_items(dlg)), None),
        "tree": tree_items(dlg),
        "models": model_tabs(dlg),
        "active": active_model(dlg),
        "tabs": tab_groups(dlg),
        "output_tail": _tail(text, output_lines),
        "output_lines_total": len(text.splitlines()),
    }


def _open_project_once(target: Path) -> dict:
    dlg, win = main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei is not running"}
    report: dict = {"ok": False, "path": str(target)}
    report["menu"] = click_menu(dlg, "File", "Open")
    if not report["menu"].get("ok"):
        report["error"] = "could not open the File menu"
        return report
    report["dialog"] = select_file_in_dialog(str(target))
    if not report["dialog"].get("ok"):
        report["error"] = "file dialog handling failed"
        return report
    time.sleep(2.0)
    tree = tree_items(dlg)
    report["tree"] = tree
    report["models"] = model_tabs(dlg)
    report["project"] = tree[0] if tree else None
    expected = target.stem.lower()
    report["ok"] = bool(tree) and str(report["project"]).lower() == expected
    if not report["ok"]:
        report["error"] = f"loaded project root is {report['project']!r}, expected {target.stem!r}"
    report["output_tail"] = _tail(read_output(dlg), 10)
    return report


def open_project(path: str, attempts: int = 2) -> dict:
    """Open a .proj; retries once because a freshly started IDE may swallow the first try."""
    target = Path(path)
    if not target.is_file():
        return {"ok": False, "error": f"project not found: {path}"}
    tries = []
    for attempt in range(max(1, attempts)):
        result = _open_project_once(target)
        tries.append({k: result.get(k) for k in ("ok", "project", "error")})
        if result.get("ok"):
            result["attempts"] = len(tries)
            return result
        time.sleep(2.0)
    last = result
    last["attempts"] = len(tries)
    last["tries"] = tries
    return last


def compile_model(timeout_s: float = 25.0) -> dict:
    dlg, win = main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei is not running"}
    models = model_tabs(dlg)
    if not models:
        return {
            "ok": False,
            "error": "no model is open in the canvas; call robei_gui_open_model first",
            "models": models,
        }
    before = read_output(dlg)
    click = click_menu(dlg, "Build", "Compile")
    if not click.get("ok"):
        return {"ok": False, "error": "could not reach Build>Compile", "detail": click}
    deadline = time.time() + timeout_s
    after = before
    while time.time() < deadline:
        after = read_output(dlg)
        if after != before and "Compile done" in after[len(before):]:
            break
        time.sleep(0.4)
    delta = after[len(before):] if after.startswith(before) else after
    lines = [line for line in delta.splitlines() if line.strip()]
    errors = [line for line in lines if ": error:" in line]
    warnings = [line for line in lines if ": warning:" in line]
    generated = re.findall(r"Generating (.+?)\.\.\.", delta)
    done = "Compile done" in delta
    missing: list[str] = []
    if "*** These modules were missing:" in delta:
        block = delta.split("*** These modules were missing:", 1)[1]
        missing = [line.strip() for line in block.splitlines() if "referenced" in line]
    check = re.search(r"Check complete with (\d+) warning", delta)
    return {
        "ok": done and not errors,
        "compiled": done,
        "model": models,
        "check_warnings": int(check.group(1)) if check else None,
        "error_count": len(errors),
        "errors": errors[:20],
        "warnings": warnings[:20],
        "generated": generated,
        "missing_modules": missing,
        "delta": delta.strip()[:4000],
        "output_tail": _tail(after, 20),
    }


def run_simulation(timeout_s: float = 25.0) -> dict:
    """Build > Run: Robei compiles and simulates the active model."""
    dlg, win = main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei is not running"}
    models = model_tabs(dlg)
    before = read_output(dlg)
    click = click_menu(dlg, "Build", "Run")
    if not click.get("ok"):
        return {"ok": False, "error": "could not reach Build>Run", "detail": click}
    deadline = time.time() + timeout_s
    after = before
    while time.time() < deadline:
        after = read_output(dlg)
        delta = after[len(before):] if after.startswith(before) else after
        if "not test" in delta or "waveform" in delta or "error" in delta.lower():
            break
        time.sleep(0.5)
    delta = (after[len(before):] if after.startswith(before) else after).strip()
    needs_test = "is not test" in delta
    return {
        "ok": not needs_test,
        "models": models,
        "active": active_model(dlg),
        "delta": delta[:2000],
        "hint": (
            "Robei only produces a waveform when the active model Type is 'test'; "
            "this model is a normal module"
        )
        if needs_test
        else None,
        "windows": [{"title": w["title"], "class": w["class"]} for w in windows()],
    }


def open_wave(timeout_s: float = 15.0) -> dict:
    """Build > Wave: open Robei's wave viewer for the last run."""
    dlg, win = main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei is not running"}
    before = read_output(dlg)
    click = click_menu(dlg, "Build", "Wave")
    if not click.get("ok"):
        return {"ok": False, "error": "could not reach Build>Wave", "detail": click}
    deadline = time.time() + timeout_s
    after = before
    while time.time() < deadline:
        after = read_output(dlg)
        delta = after[len(before):] if after.startswith(before) else after
        if delta.strip():
            break
        time.sleep(0.5)
    delta = (after[len(before):] if after.startswith(before) else after).strip()
    return {
        "ok": not delta.strip(),
        "delta": delta[:1000],
        "tabs": tab_groups(dlg),
        "screens": [w for w in windows() if w["class"] != "Qt680QWindowIcon"],
    }


def refresh() -> dict:
    """Build > Refresh: make Robei reload models/projects that changed on disk."""
    dlg, win = main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei is not running"}
    before = read_output(dlg)
    click = click_menu(dlg, "Build", "Refresh")
    time.sleep(1.5)
    after = read_output(dlg)
    return {
        "ok": bool(click.get("ok")),
        "detail": click,
        "tree": tree_items(dlg),
        "models": model_tabs(dlg),
        "output_tail": _tail(after, 6),
    }


def output(lines: int = 80) -> dict:
    dlg, win = main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei is not running"}
    text = read_output(dlg)
    return {"ok": True, "lines_total": len(text.splitlines()), "text": _tail(text, lines)}


CODE_EDITOR_CLASS = "CodeEditor"


def _design_tab_item(dlg, label: str):
    for tab in dlg.descendants(control_type="Tab"):
        try:
            if "Design.qt_tabwidget_tabbar" not in (tab.element_info.automation_id or ""):
                continue
            for item in tab.children(control_type="TabItem"):
                if item.window_text().strip().lower() == label.lower():
                    return item
        except Exception:
            continue
    return None


def _code_editor(dlg, activate: bool = True):
    """Switch to the canvas 'Code' tab and return its CodeEditor element."""
    from pywinauto import mouse

    if activate:
        tab = _design_tab_item(dlg, "code")
        if tab is None:
            return None
        mouse.click(button="left", coords=_center(_rect(tab.element_info.rectangle)))
        time.sleep(1.0)
    for ctrl in dlg.descendants():
        try:
            if ctrl.element_info.class_name == CODE_EDITOR_CLASS:
                return ctrl
        except Exception:
            continue
    return None


def code_read() -> dict:
    """Return the Verilog snippet stored in the active model's Code tab."""
    dlg, win = main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei is not running"}
    model = active_model(dlg)
    if model is None:
        return {"ok": False, "error": "no active model tab"}
    editor = _code_editor(dlg)
    if editor is None:
        return {"ok": False, "error": "Code tab not available for this model", "model": model}
    text = editor.window_text()
    return {"ok": True, "model": model, "length": len(text), "text": text}


def code_write(code: str, save: bool = True) -> dict:
    """Replace the Code tab text of the active model, then save with Ctrl+S."""
    from pywinauto import keyboard, mouse

    dlg, win = main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei is not running"}
    model = active_model(dlg)
    if model is None:
        return {"ok": False, "error": "no active model tab"}
    editor = _code_editor(dlg)
    if editor is None:
        return {"ok": False, "error": "Code tab not available for this model", "model": model}
    before = editor.window_text()
    if before == code:
        return {
            "ok": True,
            "model": model,
            "changed": False,
            "saved": False,
            "note": "editor already contains this text; nothing written",
        }
    method = "value_pattern"
    try:
        editor.iface_value.SetValue(code)
    except Exception as exc:
        method = f"type_keys (SetValue failed: {type(exc).__name__})"
        mouse.click(button="left", coords=_center(_rect(editor.element_info.rectangle)))
        time.sleep(0.2)
        keyboard.send_keys("^a")
        keyboard.send_keys(code, with_spaces=True, pause=0.005)
    time.sleep(0.8)
    after = editor.window_text()
    result = {
        "model": model,
        "changed": True,
        "method": method,
        "before": before,
        "after": after,
        "written": after == code,
    }
    if not result["written"]:
        result.update({"ok": False, "error": "editor text does not match the requested code"})
        return result
    if save:
        out_before = read_output(dlg)
        keyboard.send_keys("^s")
        deadline = time.time() + 8.0
        log = ""
        while time.time() < deadline:
            out = read_output(dlg)
            if out != out_before:
                log = out[len(out_before):] if out.startswith(out_before) else out
                break
            time.sleep(0.3)
        result["saved"] = "saved" in log.lower()
        result["save_log"] = log.strip()
    result["ok"] = True
    return result


def _file_tree(dlg):
    for ctrl in dlg.descendants(control_type="Tree"):
        try:
            if ctrl.element_info.class_name == "FileTree":
                return ctrl
        except Exception:
            continue
    return None


def _tree_state(item) -> int | None:
    try:
        return int(item.get_expand_state())
    except Exception:
        return None


def _expand_tree_item(item) -> int | None:
    """Expand a folder. Qt's tree ignores the UIA ExpandCollapse pattern here,
    but a click plus the RIGHT arrow works."""
    from pywinauto import keyboard, mouse

    rect = _rect(item.element_info.rectangle)
    mouse.click(button="left", coords=_center(rect))
    time.sleep(0.3)
    state = _tree_state(item)
    if state == 0:
        keyboard.send_keys("{RIGHT}")
        time.sleep(0.6)
        state = _tree_state(item)
    if state == 0:
        mouse.click(button="left", coords=(rect[0] + 6, _center(rect)[1]))
        time.sleep(0.6)
        state = _tree_state(item)
    return state


def _tree_entries(tree) -> list[dict]:
    rows = []
    for item in tree.descendants(control_type="TreeItem")[:80]:
        try:
            rows.append({"text": item.window_text(), "state": _tree_state(item), "item": item})
        except Exception:
            continue
    return rows


def open_model(name: str) -> dict:
    """Expand the Files tree and double-click a model entry."""
    from pywinauto import mouse

    dlg, win = main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei is not running"}
    tree = _file_tree(dlg)
    if tree is None:
        return {"ok": False, "error": "Files tree not found"}

    wanted = name.lower()
    target = None
    expanded: list[str] = []
    for _ in range(5):
        entries = _tree_entries(tree)
        target = next((e["item"] for e in entries if e["text"].lower() == wanted), None)
        if target is not None:
            break
        collapsed = [
            e
            for e in entries
            if e["state"] != 1 and e["text"] not in expanded
        ]
        if not collapsed:
            break
        current = collapsed[0]
        expanded.append(current["text"])
        _expand_tree_item(current["item"])

    if target is None:
        return {
            "ok": False,
            "error": (
                f"model {name!r} not found in Files tree "
                "(Robei lists only models declared in the .proj manifest)"
            ),
            "tree": [e["text"] for e in _tree_entries(tree)],
        }
    rect = _rect(target.element_info.rectangle)
    mouse.double_click(button="left", coords=_center(rect))
    time.sleep(2.5)
    models = model_tabs(dlg)
    return {
        "ok": wanted.rsplit(".", 1)[0] in [m.lower() for m in models],
        "opened": name,
        "models": models,
        "active": active_model(dlg),
        "tree": [e["text"] for e in _tree_entries(tree)],
    }


def launch(exe: str, wait_s: float = 20.0) -> dict:
    import subprocess

    if any(w["class"].startswith("Qt") for w in windows()):
        return {"ok": True, "already_running": True, "windows": windows()}
    proc = subprocess.Popen([exe], cwd=str(Path(exe).parent))
    deadline = time.time() + wait_s
    while time.time() < deadline:
        time.sleep(1.0)
        if main_window()[0] is not None:
            return {"ok": True, "pid": proc.pid, "title": main_window()[1]["title"]}
    return {"ok": False, "error": "Robei window did not appear", "pid": proc.pid}


def close() -> dict:
    import subprocess

    proc = subprocess.run(["taskkill", "/F", "/IM", PROCESS], capture_output=True, text=True)
    return {"ok": proc.returncode == 0, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}
