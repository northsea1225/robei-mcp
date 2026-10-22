"""Author a canvas-style Robei model (nested <Module> instances + <Wire> nets) and open it."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(r"C:\dev\robei-mcp\workspace\canvas_probe")

LEAF = """<!DOCTYPE Robei>
<Module Category="Current" Class="module" Code="assign y = a &amp; b;" Color="#d3d3d3" Comment="2-input AND" Height="1080" Name="and2" Parameters="" Parent="0" Type="model" Width="1600" X="0" Y="0">
 <Port Color="#faebd7" Connect="" Datasize="1" Datatype="wire" Description="" Height="30" Inout="input" Name="a" Parent="and2" Side="left" Width="30" X="-0.01875" Y="0.163889"/>
 <Port Color="#faebd7" Connect="" Datasize="1" Datatype="wire" Description="" Height="30" Inout="input" Name="b" Parent="and2" Side="left" Width="30" X="-0.01875" Y="0.453704"/>
 <Port Color="#f0ffff" Connect="" Datasize="1" Datatype="wire" Description="" Height="30" Inout="output" Name="y" Parent="and2" Side="right" Width="30" X="0.98125" Y="0.566667"/>
</Module>
"""

def inst(name: str, x: int, y: int, wires: dict[str, str]) -> str:
    ports = []
    for i, (pname, side, py, inout) in enumerate(
        [("a", "left", "0.1875", "input"), ("b", "left", "0.4375", "input"), ("y", "right", "0.26875", "output")]
    ):
        px = "-0.125" if side == "left" else "0.875"
        ports.append(
            f'  <Port Connect="{wires.get(pname, "")}" Name="{pname}" Inout="{inout}" Function="" Width="20" '
            f'Side="{side}" X="{px}" Y="{py}" Datatype="wire" Parent="{name}" Height="20" Color="#faebd7" Datasize="1"/>'
        )
    body = "\n".join(ports)
    return (
        f' <Module Name="{name}" Type="model" Width="160" Class="and2" Comment="" X="{x}" Parameters="" Y="{y}" '
        f'Parent="top_canvas" Height="160" Color="#d3d3d3" Include="" File="Current/and2.model" Code="">\n'
        f"{body}\n </Module>"
    )

TOP_PORTS = """  <Port Name="a" Inout="input" Function="" Width="20" Side="left" X="-0.0222222" Y="0.181667" Datatype="wire" Parent="top_canvas" Height="20" Color="#faebd7" Datasize="1"/>
  <Port Name="b" Inout="input" Function="" Width="20" Side="left" X="-0.0222222" Y="0.381667" Datatype="wire" Parent="top_canvas" Height="20" Color="#00ffff" Datasize="1"/>
  <Port Name="c" Inout="input" Function="" Width="20" Side="left" X="-0.0222222" Y="0.581667" Datatype="wire" Parent="top_canvas" Height="20" Color="#7fffd4" Datasize="1"/>
  <Port Name="y" Inout="output" Function="" Width="20" Side="right" X="0.977778" Y="0.231667" Datatype="wire" Parent="top_canvas" Height="20" Color="#f0ffff" Datasize="1"/>"""

WIRES = [
    ("w_a", "top_canvas>a", "top_canvas#u1>a"),
    ("w_b", "top_canvas>b", "top_canvas#u1>b"),
    ("t1", "top_canvas#u1>y", "top_canvas#u2>a"),
    ("w_c", "top_canvas>c", "top_canvas#u2>b"),
    ("w_y", "top_canvas#u2>y", "top_canvas>y"),
]

CANVAS = (
    "<!DOCTYPE Robei>\n"
    '<Module Name="top_canvas" Type="module" Width="900" Class="module" Comment="" X="0" Parameters="" Y="0" '
    'Parent="0" Height="600" Color="#d3d3d3" Include="" File="Current/top_canvas.model" Code="">\n'
    + inst("u1", 200, 120, {})
    + "\n"
    + inst("u2", 520, 120, {})
    + "\n"
    + TOP_PORTS
    + "\n"
    + "\n".join(
        f' <Wire Name="{n}" To="{to}" From="{frm}" Datatype="wire" Parent="top_canvas" Datasize="1"/>'
        for n, frm, to in WIRES
    )
    + "\n</Module>\n"
)

PROJ = """<canvas_probe name="canvas_probe" parent="0" type="proj">
 <model name="model" parent="canvas_probe" type="folder">
  <and2.model name="and2.model" parent="model" type="model"/>
  <top_canvas.model name="top_canvas.model" parent="model" type="model"/>
 </model>
 <test name="test" parent="canvas_probe" type="folder"/>
 <const name="const" parent="canvas_probe" type="folder"/>
 <verilog name="verilog" parent="canvas_probe" type="folder"/>
</canvas_probe>
"""

if __name__ == "__main__":
    for sub in ("model", "test", "const", "verilog"):
        (ROOT / sub).mkdir(parents=True, exist_ok=True)
    (ROOT / "model" / "and2.model").write_text(LEAF, encoding="utf-8", newline="\n")
    (ROOT / "model" / "top_canvas.model").write_text(CANVAS, encoding="utf-8", newline="\n")
    (ROOT / "canvas_probe.proj").write_text(PROJ, encoding="utf-8", newline="\n")
    print(CANVAS)
