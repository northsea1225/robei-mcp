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


# expat honours a BOM or the declared encoding, so the guard must look at the text the
# parser will see, not only at the UTF-8 reading of the bytes (a UTF-16 document hides
# "<!ENTITY" from a UTF-8 decode). Every reading below is checked; garbage readings of
# a plain UTF-8 file cannot contain the markers, so there is no false positive.
_READINGS = ("utf-8", "latin-1", "utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be")


def check_bytes(data: bytes, source: str = "<bytes>") -> None:
    """Run :func:`check_text` over every encoding the XML parser could apply."""
    for encoding in _READINGS:
        check_text(data.decode(encoding, errors="replace"), source)


def parse_bytes(data: bytes, source: str = "<bytes>") -> ET.Element:
    if len(data) > MAX_BYTES:
        raise UnsafeXml(f"{source}: XML is larger than {MAX_BYTES} bytes")
    check_bytes(data, source)
    return ET.fromstring(data)


def fromstring(text: str | bytes, source: str = "<string>") -> ET.Element:
    """``ET.fromstring`` for text this package built or edited, behind the same guard."""
    if isinstance(text, bytes):
        check_bytes(text, source)
    else:
        check_text(text, source)
    return ET.fromstring(text)


def load(path: Path) -> ET.Element:
    """Parse ``path`` after the DTD/entity guard; returns the root element."""
    return parse_bytes(Path(path).read_bytes(), str(path))
