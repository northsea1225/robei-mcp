# Robei GUI 工具（robei_gui_*）

MCP 直接驱动 Robei 本体。底层走 Robei 自编入的 **UIA provider**（详见 `L1-uia-bridge.md`），
不修改程序、不注入 DLL、不需要 `QT_ACCESSIBILITY`。

## 前置条件

1. Windows 上有**已登录且未锁屏**的交互桌面（Session 1）。
2. 注册辅助任务（只需一次）：

```powershell
python tools\gui\register_task.py            # 创建/刷新 RobeiMCP_GuiAgent
python tools\gui\register_task.py --remove   # 不再需要时删除
```

`register_task.py` 用当前用户名注册 `/IT`（仅交互式）任务，动作是
`C:\dev\robei-mcp\.venv\Scripts\pythonw.exe C:\dev\robei-mcp\tools\gui\agent_launch.py`。

## 传输方式

| 场景 | mode | 行为 |
| --- | --- | --- |
| MCP 服务器跑在交互桌面 | `direct` | 在同一进程里直接调 `tools/gui/robei_ui.py` |
| MCP 服务器跑在 Session 0（SSH / 服务） | `queued` | 写 `tools/gui/agent-job.json` → `schtasks /Run RobeiMCP_GuiAgent` → 轮询 `workspace/gui_shots/last.json` 匹配 `job_id` |

`robei_gui_status` 返回里的 `bridge` 字段会给出 `server_session` / `console_session` / `task_state`，
可直接判断当前是哪种模式。单个任务串行执行，默认超时 90 秒（上限 180 秒）。

## 工具

| 工具 | 作用 | 关键返回 |
| --- | --- | --- |
| `robei_gui_status(output_lines=20)` | 是否在运行、当前工程、打开的模型页签、Output 尾部 | `running` `project` `tree` `models` `output_tail` `bridge` |
| `robei_gui_launch(wait_s=30)` | 启动 Robei（默认 `D:\robei-lab\robei\bin\Robei.exe`，可用 `ROBEI_GUI_EXE` 覆盖） | `pid` `title` `already_running` |
| `robei_gui_open_project(proj_path, timeout_s=90)` | File→Open，键入绝对路径并回车，随后用 Files 树根节点校验 | `project` `tree` `dialog` |
| `robei_gui_open_model(name, timeout_s=90)` | 展开 Files 树（Qt 忽略 UIA 展开模式，改用 `点击 + →`）后双击模型 | `models` `tree` |
| `robei_gui_compile(timeout_s=90)` | Build→Compile，解析 `Output` 增量 | `generated` `errors` `warnings` `missing_modules` `check_warnings` |
| `robei_gui_output(lines=80)` | Output 面板末 N 行 | `text` `lines_total` |
| `robei_gui_code_read()` | 切到画布 `Code` 页签，读当前模型的 Verilog 片段 | `model` `text` `length` |
| `robei_gui_code_write(code, save=True)` | 写入 `Code` 页签并用 Ctrl+S 保存 | `changed` `written` `saved` `save_log` |
| `robei_gui_close()` | `taskkill /F /IM Robei.exe` | `stdout` |

路径校验：`robei_gui_open_project` 复用 `security.resolve_project_path`，
只允许 `workspace`、`ROBEI_MODEL_ROOTS`（默认 `C:\ProgramData\Robei`）和 `ROBEI_HOME\project` 之下的 `.proj`。

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

## Code 页签读写

模型画布底部有 `Graph | Code` 两个页签。`Code` 页签里的 `CodeEditor` 控件通过 UIA 暴露整段文本
（auto_id 以 `...Design.qt_tabwidget_stackedwidget.CodeEditor` 结尾），所以读和写都不用模拟键盘：

- 读：`window_text()` 直接拿到 `.model` 里 `Code` 字段的原文；
- 写：`ValuePattern.SetValue(text)` 一次替换全文，再 `Ctrl+S`，Robei 在 Output 打印
  `|>File: .../and2.model saved.`，`save_log` 就是这条增量。

实测（`tests/stage6_gui_code.py`，11/11 PASS）：

```
read1    'assign y = a & b;'
write    written=true saved=true  log='|>File: ...and2.model saved.'
read2    'assign y = a & b;  // stage6 probe'
restore  saved=true
read3    'assign y = a & b;'    ← 原文已还原
compile  ok  generated=[".../build/and2.v"]
```

写的是**模型的 `Code` 字段**（模块内部的 Verilog 片段），不是整份 `.v`；端口、参数仍在 `.model` 的 `<Port>` 节点里。

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

实测过 GUI 那条路（`probe19/20`）：点工具栏 `Port` 后在画布边缘点击确实会生成一个端口节点，但

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

也就是说「端口（`set_robei_port`）+ 逻辑（`robei_gui_code_write`）+ 编译验证（`robei_gui_compile`）」
已经能纯脚本造出一个 Robei 能编译的模型。
