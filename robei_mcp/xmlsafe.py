"""Read Robei XML without letting the document define entities.

Robei itself writes ``<!DOCTYPE Robei>`` and never declares an entity, so a
document that declares one is not a Robei file. ``xml.etree`` expands internal
entities, which turns a hostile ``.model`` (we now fetch schemas from public
repositories) into a denial-of-service vector: billion laughs or quadratic
blowup can exhaust memory before parsing fails.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

MAX_BYTES = 5_000_000


class UnsafeXml(ValueError):
    """Raised when an XML document declares a DTD or an entity."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.stage = "xml_guard"


def check_text(text: str, source: str = "<string>") -> None:
    lowered = text.lower()
    # Robei writes a bare ``<!DOCTYPE Robei>``; that is fine. What is never fine
    # is declaring an entity, or smuggling one in via a DOCTYPE internal subset.
    entity = lowered.find("<!entity")
    if entity >= 0:
        raise UnsafeXml(
            f"{source}: ENTITY declarations are rejected (offset {entity}); "
            "Robei files never declare entities"
        )
    doctype = lowered.find("<!doctype")
    if doctype >= 0:
        end = lowered.find(">", doctype)
        if end == -1 or "[" in lowered[doctype:end]:
            raise UnsafeXml(
                f"{source}: a DOCTYPE with an internal subset is rejected (offset {doctype})"
            )


def parse_bytes(data: bytes, source: str = "<bytes>") -> ET.Element:
    if len(data) > MAX_BYTES:
        raise UnsafeXml(f"{source}: XML is larger than {MAX_BYTES} bytes")
    check_text(data.decode("utf-8", errors="replace"), source)
    return ET.fromstring(data)


def load(path: Path) -> ET.Element:
    """Parse ``path`` after the DTD/entity guard; returns the root element."""
    return parse_bytes(Path(path).read_bytes(), str(path))
