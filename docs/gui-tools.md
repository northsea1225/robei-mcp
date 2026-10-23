# Robei GUI 工具（robei_gui_*）

MCP 直接驱动 Robei 本体。底层走 Robei 自编入的 **UIA provider**（详见 `L1-uia-bridge.md`），
不修改程序、不注入 DLL、不需要 `QT_ACCESSIBILITY`。

## 前置条件

1. Windows 上有**已登录且未锁屏**的交互桌面（Session 1）。
2. 装 GUI 依赖（`pywinauto`、`Pillow`、`pywin32`）：

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -e ".[gui]"
   ```

   三个包缺任何一个，`gui.available()` 就是 false，10 个 `robei_gui_*` 工具**不会出现在
   `tools/list`** 里（不是调用时才报错）；`tools\setup.py --apply` 也会在 Windows 上补装。
   `robei_doctor` 的 `gui_deps` 一项会逐个报缺什么。

3. 注册辅助任务（只需一次）：

```powershell
python tools\gui\register_task.py            # 创建/刷新 RobeiMCP_GuiAgent
python tools\gui\register_task.py --remove   # 不再需要时删除
```

`register_task.py` 用当前用户名注册 `/IT`（仅交互式）任务，动作是
`C:\dev\robei-mcp\.venv\Scripts\pythonw.exe C:\dev\robei-mcp\robei_mcp\gui_agent\launcher.py`。

## 传输方式

| 场景 | mode | 行为 |
| --- | --- | --- |
| MCP 服务器跑在交互桌面 | `direct` | 在同一进程里直接调 `robei_mcp.gui_agent.ui` |
| MCP 服务器跑在 Session 0（SSH / 服务） | `queued` | 写 `workspace/gui_agent/agent-job.json` → `schtasks /Run RobeiMCP_GuiAgent` → 轮询 `workspace/gui_shots/last.json` 匹配 `job_id` |

`robei_gui_status` 返回里的 `bridge` 字段会给出 `server_session` / `console_session` / `task_state`，
可直接判断当前是哪种模式。单个任务串行执行，默认超时 90 秒（上限 180 秒）。

## 工具

| 工具 | 作用 | 关键返回 |
| --- | --- | --- |
| `robei_gui_status(output_lines=20)` | 是否在运行、当前工程、打开的模型页签、Output 尾部 | `running` `project` `tree` `models` `output_tail` `bridge` |
| `robei_gui_launch(wait_s=30)` | 启动 Robei（默认 `D:\robei-lab\robei\bin\Robei.exe`，可用 `ROBEI_GUI_EXE` 覆盖） | `pid` `title` `already_running` |
| `robei_gui_open_project(proj_path, timeout_s=90)` | File→Open，键入绝对路径并回车，随后用 Files 树根节点校验 | `project` `tree` `dialog` |
| `robei_gui_open_model(name, timeout_s=90)` | 展开 Files 树（Qt 忽略 UIA 展开模式，改用 `点击 + →`）后双击模型 | `models` `tree` `active` |
| `robei_gui_compile(timeout_s=90)` | Build→Compile，解析 `Output` 增量 | `generated` `errors` `warnings` `missing_modules` `check_warnings` |
| `robei_gui_output(lines=80)` | Output 面板末 N 行 | `text` `lines_total` |
| `robei_gui_close()` | `taskkill /F /IM Robei.exe` | `stdout` |

路径校验：`robei_gui_open_project` 复用 `security.resolve_project_path`，
只允许 `workspace`、`ROBEI_MODEL_ROOTS`（默认 `C:\ProgramData\Robei`）和 `ROBEI_HOME\project` 之下的 `.proj`。

`robei_gui_open_model` 的查找顺序：`model/` → `test/` → `const/` → `verilog/`，只展开
真正还是目录节点、且名字没有扩展名的项，最多重试 10 轮（Qt 展开是异步的，一次点击
未必立刻出子节点）；树的读取上限 400 项。`ok` 以 `active` 是否等于目标为准——
激活失败但模型名出现在 `models` 里，仍然算失败，并回退报告树的当前状态。

## 实测

`tests/stage5_gui_cases.py` 用真实 stdio MCP 客户端串起 10 项断言：

```
PASS  tool_registration        PASS  robei_gui_launch      PASS  robei_gui_open_model
PASS  status_before_launch     PASS  robei_gui_status      PASS  robei_gui_compile
PASS  robei_gui_open_project   PASS  robei_gui_output      PASS  robei_gui_close
PASS  status_after_close
10/10 passed
```

编译 `and2.model` 的真实返回：

```json
{"ok": true, "compiled": true, "check_warnings": 0, "error_count": 0,
 "generated": ["C:/dev/robei-mcp/workspace/robei_samples/two_level/build/and2.v"]}
```

## 模型的 Verilog 存在哪里

模型画布底部的 `Code` 页签对应 `.model` 里的 `Code` 属性（模块内部的 Verilog 片段，
不是整份 `.v`；端口和参数在 `<Port>` 节点里）。

读写这一层**不要走 GUI**：UIA 的 `CodeEditor` 能读写，但受窗口焦点和页签状态影响，
而且写回绕过了备份与 XML 校验。用文件级工具即可：

- 读：`read_robei_model(path)` → `code`
- 写：`create_robei_model(..., code=..., overwrite=True)` 或 `set_robei_port(...)`
- 导出成可编译的 `.v`：`export_robei_verilog(...)`

## 已知限制

1. **Compile 只编译当前打开的 model**。没有打开模型时不会编译工程，工具会直接拒绝并提示先 `robei_gui_open_model`。
2. **层次化 model 的 elaboration 仍会报错**：`top_two`（实例化 `and2`）编译时 Robei 只把 `top_two.v` 放进 `filelist.vf`，
   `iverilog` 去 `C:\ProgramData\Robei\lib\AND2.v` 找库并撞上重复声明：

   ```
   top_two.v:12: error: Unknown module type: and2
   C:/ProgramData/Robei/lib/AND2.v:23: error: 'AND2' has already been declared in this scope.
   *** These modules were missing:
   and2 referenced 2 times.
   ```

   这不是本项目的 bug，是 Robei 自己的库解析行为；工具的 `missing_modules`/`errors` 会如实回传。
3. Robei 编译时会往**安装副本**目录（`<install>\project\test1\model\`）写模板模型，属其自身行为；`E:\Robei` 只读使用时需注意。
4. `robei_gui_compile` 的 `model` 是**所有打开的页签**；当前激活的那个看 `active`（来自 TabItem 的 SelectionItemPattern）。
5. 同一时刻只允许一个 GUI 任务；并发调用会被计划任务串行化，可能触发超时。
6. `robei_gui_close` 是强制结束进程，未保存的模型编辑会丢失。
7. **Files 树只列 `.proj` 里声明过的模型**。`_synthetic/sample.proj` 没有 `<file>` 节点，
   所以磁盘上的 `model/top.model` 在树上不出现，`robei_gui_open_model` 也找不到——这不是展开失败。
8. 切换工程不会关闭旧页签，`models` 里可能残留上一个工程的模型名；以 `active` 为准。

## 端口：为什么不用 GUI 点，而用 `set_robei_port`

实测过 GUI 那条路（当时的探针 probe19/probe20，1.0.0 起已从仓库移除）：点工具栏 `Port` 后在画布边缘点击确实会生成一个端口节点，但

- 名字固定是 `p1`、方向默认、`Side` 由落点决定（点到右边缘却生成了 `Side="top"`）；
- 想改名字/方向/位宽要再选中该端口，让 `Property` 表格出现对应行，再进单元格编辑——
  表格子项在 UIA 里只有文本，没有可写的 Value 模式，实测枚举不到稳定句柄。

所以改成**直接写 `.model` 的 `<Port>` 节点**，再用 Robei 编译来验证它认不认：

```json
{"ok": true, "model": "blank", "port": {"name": "a", "direction": "input", "width": 1,
 "datatype": "wire", "side": "left"}, "replaced": false,
 "backup": "...\\blank.model.bak-20261022-103500", "sha256_before": "...", "sha256_after": "..."}
```

规则：
- 只允许改 `workspace` 下的 `.model`（`C:\ProgramData\Robei` 里的模型是只读的）；
- 写前先备份成 `<name>.bak-<时间戳>`，写完用 `ElementTree` 验一遍 XML；
- 同名端口已存在就替换（`replaced: true`），否则插到 `<Module>` 之后；
- `direction ∈ {input,output,inout}`、`datatype ∈ {wire,reg}`、`1 ≤ width ≤ 1024`、`side ∈ {left,right,top,bottom}`。

闭环实测（`tests/stage7_model_ports.py`，10/10 PASS）：三行 `set_robei_port` 造出 `a/clk/y`，
Robei 打开该工程编译后生成的 `blank.v`：

```verilog
module blank(
output wire y,
input wire clk,
input wire a
);
  //----Code starts here: integrated by Robei-----
  assign y = a;
endmodule    //blank
```

也就是说「端口（`set_robei_port`）+ 逻辑（`create_robei_model` 的 `code`）+ 编译验证（`robei_gui_compile`）」
已经能纯脚本造出一个 Robei 能编译的模型。

## 从零造工程：`create_robei_project` / `create_robei_model`

配合 `set_robei_port`，现在已经不需要手写任何 XML：

| 工具 | 作用 |
| --- | --- |
| `create_robei_project(name, models=[])` | 在 workspace 下建 `<name>/`：`model/test/const/verilog` 四个目录 + `<name>.proj`（可顺带建空模型） |
| `create_robei_model(proj_path, name, ports, code, comment, overwrite=False)` | 生成带端口和 `Code` 的 `.model`，并自动登记进 `.proj` 的 `<model>` 目录（文本插入，保留原格式） |

安全约束：只能建在 `workspace` 内；目录已存在直接 `FileExistsError`；模型已存在需显式 `overwrite=True`（旧文件备份成 `.bak-<时间戳>`）。

闭环实测（`tests/stage8_create_flow.py`，10/10 PASS）：

```
create_robei_project("mcp_sandbox", models=[])        → mcp_sandbox.proj
create_robei_model(..., "half_adder", ports=a/b/s/c,
                   code="assign s = a ^ b;\nassign c = a & b;")  → registered_in_project=True
robei_gui_open_project / open_model / compile          → build/half_adder.v
simulate + read_waveform                                → 真值表 4/4 正确
  t=10000 a=0 b=0 s=0 c=0
  t=20000 a=0 b=1 s=1 c=0
  t=30000 a=1 b=0 s=1 c=0
  t=40000 a=1 b=1 s=0 c=1
```

## IDE 只负责「打开 + 编译」，仿真走无头路径

`robei_gui_run` / `robei_gui_wave` / `robei_gui_wave_load` / `robei_gui_experiment`
在 0.11 已移除。原因有两条，都是实测结论：

- Robei 的 Run 只对 `Type="test"` 的当前模型有效，普通模型直接报
  `and_gate is not test, so there is no waveform generated`；
- 打开 Wave dock 会让 Robei 的 UIA 树整体塌掉（`widgets 133 → 0` 且不再恢复），
  自动化写入的信号无法再被验证。

现在 GUI 只承担「IDE 自己才能做的事」：打开工程、编译、读 Output、综合、保存。
仿真统一走 `simulate_project`（用 Robei 自带的 iverilog/vvp，无需桌面），
波形统一走 `read_waveform` / `find_events` / `render_waveform`。
如果确实需要 Robei 自己的 Run/Wave，人工在 IDE 里点即可——它不影响 MCP 返回的结果。

## 画布：点不动，但可以写（2026-10-22 / 阶段 16 更新）

目标：用 UIA 在画布上放两个实例并连线，拿到 `<Instance>` / 连线的真实 XML schema
（`two_level` 里的实例目前是写在 `Code` 字段里的，不是画布拖出来的）。

三条路都失败，记录在此以免重复劳动：

1. **右键 `model` 文件夹 → `Add model`**：确实弹出了原生 "Select File" 对话框，
   把 `and2.model` 的绝对路径打进去后对话框正常关闭，但 `top_two.model`
   字节数（1073）和内容都不变，没有出现 `<Instance>` 子节点。
2. **ToolBox 列表**：`QApplication.Romain.ToolBox.QToolBox` 里只有一个 `QList`，
   UIA 完全不暴露 `ListItem`（`children()` 为空），拿不到条目名和坐标。
3. **按估算坐标从 ToolBox 拖到画布**（`mouse.press/move/release` 分 10 步）：
   拖拽动作本身返回成功，模型文件仍然不变。

4. **找 `code2graph`（"Code to Graph"）**：exe 里确实有 `code2graph` / `Code to Graph`
   字符串，但在 UI 上找不到入口——画布右键只有 `Parameters` / `Datasheet`，
   模型标签页右键为空，`Edit` 菜单只有 Copy/Paste/Cut/Delete/Find。
5. **工具栏**：主窗口上那排图标根本不暴露给 UIA（`descendants()` 里只有 `MenuBar`，
   没有任何 `QToolButton`），所以无法按名字点到"Code to Graph"。
6. **搜索面板**：`Search for Models` 能搜到 `and2` / `top_two`，但结果项坐标是负的
   （`-933,-752`，不在任何屏幕上），右键菜单因此抓不到。

结论：这个版本的 Robei 没有可用的**画布编辑**自动化入口（6 条路都试过）。

**但画布文件格式可以直接写。** 阶段 16 从 GitHub 公开工程
`wwkkww1983/uart`（38 个 `.model`，183 个 `<Wire>`、81 个嵌套 `<Module>`；
仓库无 LICENSE，因此只取格式、不取代码）拿到真实 schema，并在本机双向验证：

```xml
<Module Name="top" Type="module" ... Code="">
  <Module Name="u1" Type="model" Class="and2" File="Current/and2.model" X="148" Y="66" ...>
    <Port Name="a" Inout="input" Datasize="1" .../>
    <Port Name="b" Inout="input" Datasize="1" .../>
    <Port Name="y" Inout="output" Datasize="1" .../>
  </Module>
  <Port Name="a" Inout="input" .../>       <!-- 顶层端口，与实例同级 -->
  <Wire Name="t1" To="top#u2>a" From="top#u1>y" Datatype="wire" Datasize="1"/>
</Module>
```

要点：

- **不存在 `<Instance>` 标签**，实例就是嵌套 `<Module Type="model">`；`Class` 写被引用模型名
- 端点语法 `<owner>#<instance>><port>`（实例）/ `<owner>><port>`（顶层端口）
- 顶层端口连线的表达式必须用**端口名**，不能用 `Wire.Name`，否则导出后输出悬空

`create_robei_canvas_model` 生成的就是这段 XML；Robei 打开工程 Compile 后自己产出
`and2 u1( .a(a), .b(b), .y(t1) );`，headless 仿真真值表 `y = a&b&c` 全对
（`tests/stage16_canvas.py`，12/12 PASS）。仍然做不到的是在已有画布上拖动 / 删除连线。
