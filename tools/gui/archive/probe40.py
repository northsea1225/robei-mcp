from __future__ import annotations
import json, time
from pathlib import Path


def run() -> dict:
    import robei_ui
    main, _ = robei_ui.main_window()
    if main is None:
        return {"ok": False, "error": "Robei not running"}
    out: dict = {"ok": True}
    out["tree_items_fn"] = robei_ui.tree_items(main)
    tree = robei_ui._file_tree(main)
    out["tree_found"] = tree is not None
    if tree is not None:
        entries = robei_ui._tree_entries(tree)
        out["entries"] = [{"text": e["text"], "state": e["state"]} for e in entries]
    out["open"] = robei_ui.open_model("tb_and_gate.test")
    out["open"]["tree"] = [e["text"] for e in robei_ui._tree_entries(robei_ui._file_tree(main))]
    out["tabs"] = robei_ui.tab_groups(main)
    out["active"] = robei_ui.active_model(main)
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
