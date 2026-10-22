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
            if dlg.child_window(auto_id="QApplication.Romain.QMenuBar").exists(timeout=1.5):
                return dlg, win
        except Exception:
            continue
    # Opening the Wave dock makes Robei's accessibility tree very large; the menu
    # bar probe can then time out even though the window is the real main window.
    for win in candidates:
        if not win["title"].strip() or "Popup" in win["class"]:
            continue
        try:
            return attach(win["hwnd"]), win
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


def uia_health() -> dict:
    """Is Robei's accessibility bridge actually serving a widget tree?

    After the Wave dock is created the Qt accessibility bridge sometimes goes
    silent: the window still exists but ``descendants()`` returns nothing. That
    state cannot be repaired from outside, only by restarting the IDE.
    """
    alive = any(w["class"].startswith("Qt") for w in windows())
    dlg, win = main_window()
    if dlg is None:
        return {
            "ok": False,
            "running": alive,
            "main_window": False,
            "reason": "no main window with a menu bar",
        }
    try:
        widgets = len(dlg.descendants())
    except Exception:
        widgets = 0
    try:
        menu_items = len(dlg.descendants(control_type="MenuItem"))
    except Exception:
        menu_items = 0
    healthy = widgets > 0 and menu_items > 0
    return {
        "ok": healthy,
        "running": True,
        "main_window": True,
        "pid": win.get("pid"),
        "widgets": widgets,
        "menu_items": menu_items,
        "reason": None if healthy else "UIA bridge returned no controls; Robei needs a restart",
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


def compile_all(compile_timeout: float = 45.0, models: list[str] | None = None) -> dict:
    """Compile every .model in the open project, one at a time.

    Robei only compiles the model that owns the active canvas tab, so a
    project-wide check has to open each model and press Compile.
    """
    dlg, win = main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei is not running"}
    names = [n for n in (models or []) if n.lower().endswith(".model")]
    if not names:
        # a freshly opened project keeps its folders collapsed
        for _ in range(4):
            tree = _file_tree(dlg)
            names = [e["text"] for e in _tree_entries(tree)] if tree is not None else []
            names = [n for n in names if n.lower().endswith(".model")]
            if names:
                break
            rows = _tree_entries(tree) if tree is not None else []
            folders = [r for r in rows if r["state"] != 1]
            if not folders:
                break
            for folder in folders[:4]:
                try:
                    _expand_tree_item(folder["item"])
                except Exception:
                    continue
            time.sleep(0.6)
            dlg, win = main_window()
            if dlg is None:
                break
    if not names:
        return {
            "ok": False,
            "error": "no .model entries in the Files tree",
            "tree": [e["text"] for e in _tree_entries(_file_tree(dlg))] if dlg else [],
        }
    results = []
    for name in names:
        opened = open_model(name)
        if not opened.get("ok"):
            results.append({"model": name, "ok": False, "error": opened.get("error")})
            continue
        compiled = compile_model(compile_timeout)
        results.append(
            {
                "model": name,
                "ok": bool(compiled.get("compiled")) and not compiled.get("errors"),
                "check_warnings": compiled.get("check_warnings"),
                "warnings": compiled.get("warnings", [])[:5],
                "errors": compiled.get("errors", [])[:5],
                "missing_modules": compiled.get("missing_modules", [])[:5],
                "generated": compiled.get("generated", []),
            }
        )
    passed = sum(1 for row in results if row.get("ok"))
    return {
        "ok": passed == len(results),
        "project": next(iter(tree_items(dlg)), None),
        "compiled": passed,
        "total": len(results),
        "results": results,
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


def _wave_tree(dlg):
    for ctrl in dlg.descendants(control_type="Tree"):
        try:
            if (ctrl.element_info.automation_id or "").endswith("WaveTree"):
                return ctrl
        except Exception:
            continue
    return None


def _wave_rows(dlg) -> list[dict]:
    tree = _wave_tree(dlg)
    if tree is None:
        return []
    rows = []
    for item in tree.descendants(control_type="TreeItem")[:80]:
        try:
            text = item.window_text()
        except Exception:
            continue
        try:
            state = int(item.get_expand_state())
        except Exception:
            state = None
        try:
            rect = _rect(item.element_info.rectangle)
        except Exception:
            rect = [0, 0, 0, 0]
        rows.append({"text": text, "state": state, "rect": rect, "item": item})
    return rows


def _signal_table_rows(dlg) -> list[str]:
    for ctrl in dlg.descendants():
        try:
            if not (ctrl.element_info.automation_id or "").endswith("SignalTable"):
                continue
            texts = [c.window_text() for c in ctrl.children(control_type="DataItem")]
            return [t for t in texts if t]
        except Exception:
            continue
    return []


def wave_load(signals: list[str] | None = None, max_signals: int = 12) -> dict:
    """Open the Wave dock, expand the VCD tree and click signals into the viewer.

    Robei fills ``SignalTree.WaveTree`` from the last ``build/<model>.vcd``; each
    single click on a signal row adds it to ``WaveViewer.SignalTable``.
    """
    from pywinauto import mouse

    def attach_retry(tries: int = 6, delay: float = 1.0):
        for _ in range(tries):
            dlg, win = main_window()
            if dlg is not None:
                return dlg, win
            time.sleep(delay)
        return None, None

    dlg, win = attach_retry()
    if dlg is None:
        return {"ok": False, "error": "Robei is not running"}
    steps: dict = {}
    steps["wave_menu"] = open_wave()
    time.sleep(1.0)
    if _wave_tree(dlg) is None:
        steps["waveview_menu"] = click_menu(dlg, "Window", "Waveview")
        time.sleep(1.0)
    if _wave_tree(dlg) is None:
        dlg, win = attach_retry()
        if dlg is not None and _wave_tree(dlg) is None:
            steps["waveview_retry"] = click_menu(dlg, "Window", "Waveview")
            time.sleep(1.0)

    tried: set[str] = set()
    for _ in range(4):
        dlg, win = attach_retry()
        if dlg is None:
            return {"ok": False, "error": "Robei window lost", "steps": steps}
        rows = _wave_rows(dlg)
        if not rows:
            break
        todo = [r for r in rows if r["state"] != 1 and r["text"] not in tried]
        if not todo:
            break
        if not todo:
            break
        for row in todo[:6]:
            try:
                mouse.double_click(button="left", coords=_center(row["rect"]))
                tried.add(row["text"])
                time.sleep(0.5)
            except Exception:
                continue
    rows = _wave_rows(dlg)

    wanted = [s.split(".")[-1].lower() for s in signals] if signals else None
    clicked: list[str] = []
    for _ in range(max_signals):
        dlg, win = attach_retry()
        if dlg is None:
            break
        rows = _wave_rows(dlg)
        leaves = [r for r in rows if r["state"] != 1 and (wanted is None or r["text"].lower() in wanted)]
        leaf = next((r for r in leaves if r["text"].lower() not in [c.lower() for c in clicked]), None)
        if leaf is None:
            break
        try:
            mouse.click(button="left", coords=_center(leaf["rect"]))
            clicked.append(leaf["text"])
            time.sleep(0.4)
        except Exception:
            break
    dlg, win = attach_retry()
    if dlg is None:
        return {"ok": False, "error": "Robei window lost", "steps": steps}
    time.sleep(1.0)
    table = _signal_table_rows(dlg)
    tree_names = [r["text"] for r in _wave_rows(dlg)]
    return {
        "ok": bool(clicked) or bool(table),
        "steps": steps,
        "tree": tree_names,
        "clicked": clicked,
        "signal_table": table,
        "rendered": bool(table),
        "hint": None
        if table
        else "Robei's wave viewer lists the VCD signals but did not add them to the canvas; "
        "use read_waveform/render_waveform for numeric/PNG output",
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


def _wait_exit(timeout_s: float = 15.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not any(w["class"].startswith("Qt") for w in windows()):
            return True
        time.sleep(0.5)
    return False


def experiment(
    exe: str,
    proj_path: str,
    test_name: str,
    restart: bool = False,
    open_wave_after: bool = False,
    compile_timeout: float = 45.0,
    run_timeout: float = 45.0,
    signals: list[str] | None = None,
    heal: bool = True,
) -> dict:
    """Whole native loop: (re)start Robei -> open project -> open test model ->
    Compile -> Run -> Wave. Returns one report with every step's raw output.

    ``heal`` restarts a Robei whose UIA bridge went silent (see ``uia_health``)
    instead of failing with "Robei did not start" on a live process.
    """
    steps: dict = {}
    if heal and not restart:
        health = uia_health()
        steps["health"] = health
        if health.get("running") and not health.get("ok"):
            steps["heal"] = "restarting Robei because the UIA bridge is silent"
            restart = True
    if restart:
        steps["close"] = close()
        steps["closed"] = _wait_exit()
        time.sleep(1.5)
    dlg, win = main_window()
    if dlg is None:
        steps["launch"] = launch(exe, 40.0)
        dlg, win = main_window()
        if dlg is None:
            return {"ok": False, "error": "Robei did not start", "steps": steps}

    opened = open_project(proj_path)
    steps["open_project"] = {k: opened.get(k) for k in ("ok", "project", "attempts", "error")}
    if not opened.get("ok"):
        return {"ok": False, "error": "project did not open", "steps": steps}

    # A freshly opened project leaves the Files tree collapsed and the UIA cache
    # stale for a moment; refresh + re-attach before looking for the test entry.
    time.sleep(1.5)
    steps["refresh"] = refresh()
    dlg, win = main_window()
    if dlg is None:
        return {"ok": False, "error": "Robei window disappeared after refresh", "steps": steps}

    model = open_model(test_name)
    steps["open_model"] = {k: model.get(k) for k in ("ok", "opened", "active", "models", "error")}
    if not model.get("ok"):
        time.sleep(1.5)
        steps["refresh"] = refresh()
        dlg, win = main_window()
        model = open_model(test_name)
        steps["open_model_retry"] = {
            k: model.get(k) for k in ("ok", "opened", "active", "models", "error")
        }
    if not model.get("ok"):
        return {"ok": False, "error": "test model did not open", "steps": steps}

    compiled = compile_model(compile_timeout)
    if not compiled.get("compiled") and not compiled.get("errors"):
        # right after a restart the Output panel sometimes swallows the log;
        # no errors + no "Compile done" means the log was missed, not that the build failed
        steps["compile_retry"] = "no compile log captured; retrying once"
        time.sleep(2.0)
        compiled = compile_model(compile_timeout)
    steps["compile"] = {
        k: compiled.get(k)
        for k in ("ok", "compiled", "check_warnings", "error_count", "errors", "warnings", "generated", "missing_modules", "delta")
    }
    if not compiled.get("compiled"):
        return {"ok": False, "error": "compile failed", "steps": steps}

    ran = run_simulation(run_timeout)
    steps["run"] = {k: ran.get(k) for k in ("ok", "active", "delta", "hint")}

    build = Path(proj_path).parent / "build"
    vcds = sorted(p.name for p in build.glob("*.vcd")) if build.is_dir() else []
    result = {
        "ok": bool(compiled.get("ok")) and bool(ran.get("ok")) and bool(vcds),
        "project": str(proj_path),
        "test": test_name,
        "steps": steps,
        "build_dir": str(build),
        "build_files": sorted(p.name for p in build.glob("*")) if build.is_dir() else [],
        "vcd": [str(build / name) for name in vcds],
    }
    if open_wave_after and vcds:
        result["wave"] = wave_load(signals=signals)
        result["windows"] = [w["title"] for w in windows()]
        result["tabs"] = tab_groups(dlg)
    return result
