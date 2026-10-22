from pathlib import Path
from PIL import Image

base = Path(r"C:\dev\robei-mcp\workspace\gui_shots")
keep = [
    "plain-start-20261022-082623.png",
    "l1-filemenu-20261022-083230.png",
    "l1-dlg-20261022-084139.png",
    "l1-state-20261022-085254.png",
]
for name in keep:
    src = base / name
    if not src.exists():
        print("missing", name)
        continue
    im = Image.open(src).convert("RGB")
    im.thumbnail((1600, 1600))
    dst = src.with_suffix(".jpg")
    im.save(dst, "JPEG", quality=72, optimize=True)
    print("kept", dst.name, dst.stat().st_size // 1024, "KB")
for p in base.glob("*.png"):
    if p.name in keep:
        continue
    p.unlink()
print("remaining files:", len(list(base.iterdir())))
