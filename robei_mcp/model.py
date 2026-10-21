from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def _attr_ci(element: ET.Element, name: str) -> str | None:
    target = name.lower()
    for key, value in element.attrib.items():
        if key.lower() == target:
            return value
    return None


def parse_robei_model(path: Path) -> dict[str, Any]:
    tree = ET.parse(path)
    root = tree.getroot()
    ports: list[dict[str, Any]] = []
    for child in root:
        if child.tag.lower() != "port":
            continue
        ports.append(
            {
                "name": _attr_ci(child, "Name") or "",
                "inout": _attr_ci(child, "Inout") or "",
                "datatype": _attr_ci(child, "Datatype") or "",
                "datasize": _attr_ci(child, "Datasize") or "",
                "connect": _attr_ci(child, "Connect") or "",
                "description": _attr_ci(child, "Description") or "",
                "parent": _attr_ci(child, "Parent") or "",
            }
        )

    parameters = _attr_ci(root, "Parameters") or ""
    code = _attr_ci(root, "Code") or ""
    return {
        "ok": True,
        "path": str(path),
        "root_tag": root.tag,
        "name": _attr_ci(root, "Name") or root.tag,
        "type": _attr_ci(root, "Type") or "",
        "class": _attr_ci(root, "Class") or "",
        "category": _attr_ci(root, "Category") or "",
        "comment": _attr_ci(root, "Comment") or "",
        "parameters": parameters,
        "parameter_lines": parameters.splitlines(),
        "ports": ports,
        "code": code,
        "code_lines": code.splitlines(),
        "size_bytes": path.stat().st_size,
    }
