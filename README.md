# Robei MCP Server

Windows 本地 MCP 服务器，把 Robei EDA 自带的 Icarus Verilog、vvp 和 Yosys（再加可选的 Verilator）封装成 MCP 工具，并提供只读的 Robei `.model` / `.proj` 解析、Verilog 导出、testbench 生成和纯 Python VCD 读取/绘图。

## 交付状态

已验证（阶段 2/3）：

- MCP stdio 协议、4 位计数器 lint/simulate/waveform 闭环
- `iverilog -Wall` 警告提取、Yosys `read_verilog + synth -top` 可综合性检查
- 语法错误返回给 MCP 调用方
- 1 秒超时能终止 `vvp`，无残留 `vvp.exe`
- Robei `.model` 只读解析，`mux.model` 前后 SHA256 不变
- Mac 通过 `ssh my-win` 使用远端 stdio；stdout 只有 JSON-RPC，stderr 为空
- Claude Code 实际调用 `mcp__robei_remote__runtime_status`

已验证（阶段 4，`tests/stage4_cases.py` 全绿）：

- Verilator `5.036` 已安装并接入 lint 路径（`--lint-only -Wall -Wno-fatal`）
- `warning_width_unused.v` 能报出 `unused_signal`（Yosys `opt_clean` 前后网表 diff）
- `read_robei_project` 解析 `.proj` + 四类目录 + 每个 `.model`，未知节点原样保留；`.proj` SHA256 前后一致
- `export_robei_verilog`：`mux.model` 导出 `mux_len1_stage4.v` 并自动 lint 通过，源 `.model` SHA256 不变
- `generate_testbench`：`counter4` 生成 TB 后 `simulate` 跑通（`SIM start` / `SIM end: 1120000` ps）
- `find_events`：`count == 4'b1111` 返回 `t=265000` 等 6 次事件；非法条件被拒绝（不使用 `eval`）
- `render_waveform`：生成 25,991 字节 PNG
- 路径逃逸（`` `include ``、`$fopen`、`$readmemh` 指向工作区外）在编译前被拒绝；工作区内路径正常放行
- 覆盖写拒绝：export / testbench / PNG 同名第二次调用返回 `FileExistsError`

仅可见 / 待定：

- Robei GUI 打开和编译：当前 SSH 在 Session 0，交互桌面在 Session 1，无法从 SSH 看到/点击 GUI；已准备 `workspace\robei_counter4_project` 和手动步骤，待人工验证。
- Mac Codex 配置已写入 `~/.codex/config.toml`，`codex mcp list` 可见；但当前长期运行的 Codex app-server 没有重新挂载新工具，需要重启 Codex 会话/应用后再验证。

## 目录

```text
C:\dev\robei-mcp\
  .venv\                         Python 3.12.10，仅安装 mcp 及其依赖
  .gitignore
  .mcp.json                      Windows Claude Code 项目配置
  README.md
  requirements.txt               mcp==2.3.0, matplotlib
  run-mcp.cmd                    Windows stdio 包装脚本
  examples\
    codex-windows.toml
    codex-mac-remote.toml
    claude-windows.mcp.json
    claude-mac-remote.mcp.json
  server.py                      MCP stdio 入口
  robei_mcp\
    config.py
    model.py                     .model 解析 + 通用节点树 + Verilog 导出
    security.py                  路径策略 + simulate 前静态路径扫描
    server_app.py                MCP 工具注册
    tools.py                     工具实现
    vcd.py                       VCD 解析、事件查询、PNG 渲染
    verilog.py                   端口解析 + testbench 生成
  tests\
    stage2_smoke.py              正常路径冒烟
    stage3_cases.py              警告、语法错误、超时、残留进程
    stage4_cases.py              Verilator/未用信号、工程解析、导出、TB、波形、路径逃逸
  workspace\
    counter4\
      counter4.v                 4 位计数器
      counter4_tb.v              testbench
    cases\
      warning_width_unused.v     越界位选 + 未用信号
      warning_portbind.v         悬空输入
      syntax_error.v             语法错误
      timeout_tb.v               长仿真超时
      escape_include.v           路径逃逸负向用例
      escape_fopen.v             路径逃逸负向用例
      escape_readmemh.v          路径逃逸负向用例
      inside_paths.v             路径逃逸正向对照
    testbench\                    generate_testbench 输出目录
    robei_samples\                层次化样例（_synthetic 为容错自测样例）
    robei_counter4_project\      Robei 手动闭环测试工程
```

## 安装

已使用系统 Python 3.12.10 创建虚拟环境：

```powershell
C:\Users\15635\AppData\Local\Programs\Python\Python312\python.exe -m venv C:\dev\robei-mcp\.venv
C:\dev\robei-mcp\.venv\Scripts\python.exe -m pip install -r C:\dev\robei-mcp\requirements.txt
```

`requirements.txt`：

```text
mcp==2.3.0
```

MCP 2.x 的官方 Python SDK 使用 `mcp.server.mcpserver.MCPServer`；2.x 已把 `FastMCP` 改名。

## 启动

Windows 本地：

```powershell
C:\dev\robei-mcp\.venv\Scripts\python.exe C:\dev\robei-mcp\server.py
```

或使用包装脚本：

```powershell
C:\dev\robei-mcp\run-mcp.cmd
```

`run-mcp.cmd` 内容：

```bat
@echo off
setlocal
chcp 65001 >nul
set "ROOT=%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
"%ROOT%.venv\Scripts\python.exe" -u "%ROOT%server.py"
```

## 配置

可通过环境变量覆盖：

| 变量 | 默认值 |
|---|---|
| `ROBEI_MCP_WORKSPACE` | `C:\dev\robei-mcp\workspace` |
| `ROBEI_HOME` | `E:\Robei` |
| `ROBEI_EXE` | `E:\Robei\bin\Robei.exe` |
| `IVERILOG_EXE` | `E:\Robei\bin\iverilog.exe` |
| `VVP_EXE` | `E:\Robei\bin\vvp.exe` |
| `YOSYS_EXE` | `E:\Robei\bin\yosys.exe` |
| `ROBEI_MODEL_ROOTS` | `C:\ProgramData\Robei` 和工作区，多个路径用 `;` 分隔 |

### Windows Codex

`C:\Users\15635\.codex\config.toml`：

```toml
[mcp_servers.robei]
command = 'C:\dev\robei-mcp\.venv\Scripts\python.exe'
args = ['C:\dev\robei-mcp\server.py']
startup_timeout_sec = 20
tool_timeout_sec = 120
```

### Windows Claude Code

项目 `.mcp.json`：

```json
{
  "mcpServers": {
    "robei": {
      "command": "C:\\dev\\robei-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\dev\\robei-mcp\\server.py"]
    }
  }
}
```

或命令行：

```powershell
claude mcp add --scope user robei -- "C:\dev\robei-mcp\.venv\Scripts\python.exe" "C:\dev\robei-mcp\server.py"
```

### Mac 通过 SSH 使用远端 stdio

远端默认 shell 是 MSYS bash。直接启动 Python 和包装脚本都已验证：

```bash
ssh my-win 'C:/dev/robei-mcp/.venv/Scripts/python.exe C:/dev/robei-mcp/server.py'
ssh my-win 'MSYS_NO_PATHCONV=1 cmd.exe /d /c C:/dev/robei-mcp/run-mcp.cmd'
```

Mac Codex `~/.codex/config.toml`：

```toml
[mcp_servers.robei_remote]
command = "ssh"
args = ["my-win", "C:/dev/robei-mcp/.venv/Scripts/python.exe C:/dev/robei-mcp/server.py"]
startup_timeout_sec = 30
tool_timeout_sec = 120
```

更稳妥的包装脚本版本：

```toml
[mcp_servers.robei_remote]
command = "ssh"
args = ["my-win", "MSYS_NO_PATHCONV=1 /c/Windows/System32/cmd.exe /d /c C:/dev/robei-mcp/run-mcp.cmd"]
startup_timeout_sec = 30
tool_timeout_sec = 120
```

Mac Claude Code `.mcp.json`：

```json
{
  "mcpServers": {
    "robei_remote": {
      "command": "ssh",
      "args": [
        "my-win",
        "MSYS_NO_PATHCONV=1 /c/Windows/System32/cmd.exe /d /c C:/dev/robei-mcp/run-mcp.cmd"
      ]
    }
  }
}
```

Claude Code 命令行：

```bash
claude mcp add --scope user robei_remote -- ssh my-win "MSYS_NO_PATHCONV=1 /c/Windows/System32/cmd.exe /d /c C:/dev/robei-mcp/run-mcp.cmd"
```

已实际用 Claude Code（`claude-haiku-5-5`）通过上述 `.mcp.json` 调用：

```bash
claude -p --model claude-haiku-5-5 \
  --mcp-config examples/claude-mac-remote.mcp.json --strict-mcp-config \
  --allowedTools mcp__robei_remote__runtime_status --permission-mode dontAsk \
  "Call mcp__robei_remote__runtime_status once"
```

Claude Code 的 `system/init` 事件显示 `mcp_servers: [{"name":"robei_remote","status":"connected"}]`，并列出 5 个 `mcp__robei_remote__*` 工具；实际 `tool_use` 后返回 `ok=true`、Robei `5.0.0.0`、iverilog/vvp `12.0`。

上述 SSH stdio 的 `initialize` 和 `tools/list` 都只输出 JSON；stdout 无 banner，stderr 为空。直接启动 Python、`cmd.exe /d /c run-mcp.cmd`、直接执行 `run-mcp.cmd` 三种方式都已验证。

注意：Codex app-server 是长期进程，新增 MCP server 后当前会话可能不会热加载；需要重启 Codex 会话/应用。`codex mcp list` 只证明配置存在，不等价于工具已经在模型侧挂载。

## MCP 工具

### `runtime_status`

返回：

- `mcp_version`
- Python 版本和路径
- 工作区、模型根目录
- Robei、iverilog、vvp、yosys、verilator 的路径和存在性
- `verilator_available`（可执行文件 + `VERILATOR_ROOT` 都在位）
- `tools.verilator.version`（`5.036`）

Robei 版本从 PE 文件版本资源读取。Yosys 这个厂商构建的 `-V` 输出 `Synthesis complete`，因此 `version` 为 `null`，但路径和综合检查可用。

### `lint_verilog`

```json
{"files": ["counter4/counter4.v"], "top": "counter4", "timeout_s": 30}
```

分别执行三路检查：

1. `iverilog -Wall -g2012 ...`（语法 + 警告，包含 `warning_count` / `warnings`）
2. `yosys -p "read_verilog ...; synth -top counter4"`（可综合性）
3. `verilator_bin --lint-only -Wall -Wno-fatal --top-module ...`（`verilator` 段）

另外执行一次 `yosys_check`：`read_verilog → hierarchy -check → proc → check → write_json → opt_clean → write_json`，
对比前后网表，**被 `opt_clean` 删掉且不是端口** 的 net 名就是未用信号，写入 `unused_signals`（含模块名和 `src` 位置）。

`ok` 需要 iverilog、Yosys synth、Verilator 三路都通过。Verilator 不存在时该段返回
`skipped: true` + `skip_reason`，不会让工具失败。`-Wno-fatal` 让告警不改变退出码，
告警从输出里的 `%Warning-CODE:` 解析。

样例（`cases/warning_width_unused.v`）：

```json
{"ok": true,
 "verilator": {"warning_count": 1, "warnings": [{"code": "Warning-SELRANGE"}]},
 "unused_signals": [{"module": "warning_width_unused", "name": "unused_signal",
                     "src": "...warning_width_unused.v:5.10-5.23"}]}
```

### `simulate`

```json
{"files": ["counter4/counter4.v", "counter4/counter4_tb.v"], "top": "counter4_tb", "timeout_s": 30}
```

编译前先做静态路径扫描（见「安全边界」）；被拒绝时返回 `stage: "security_scan"`，
不会启动 `iverilog`。通过后执行 `iverilog` 编译和 `vvp` 运行，
返回 `$display`、VCD 绝对/相对路径。超时会终止子进程并返回 `simulation.timeout=true`。

### `read_waveform`

```json
{"vcd_path": "build/sim-.../counter4.vcd", "signals": ["counter4_tb.dut.count"], "time": 45}
```

不传 `signals` 时列信号；传 `signals` 时按原始 VCD 时间刻度取值。返回值含 `timescale`、`max_time`、`effective_time`、二进制 `value` 和可解析时的 `value_int`。

### `read_robei_model`

```json
{"path": "C:\\ProgramData\\Robei\\mux.model"}
```

只读解析 `.model` 的模块名、类型、参数、端口、`parameter_list` 和 `Code`。
非 `Port` 的子节点（例如 `Instance`）以通用节点树放进 `other_children`，不猜测含义。不写回。

### `read_robei_project`

```json
{"proj_path": "robei_samples/_synthetic/sample.proj"}
```

只读解析 `.proj`：

- `folders`：`model` / `test` / `const` / `verilog` 四个目录的文件清单（名字、大小、SHA256）
- `models`：`model/` 下每个 `.model` 的完整解析结果
- `unknown_nodes` / `node_tree`：**未知节点原样保留**（tag / attributes / text / children），不推断结构
- `sha256_before` / `sha256_after` / `unchanged`：证明没有改写

允许根目录：`workspace`、`C:\ProgramData\Robei`、`E:\Robei\project`（只读）。

### `export_robei_verilog`

```json
{"model_path": "C:\\ProgramData\\Robei\\mux.model",
 "out_name": "mux_len1.v",
 "parameter_overrides": {"LEN": 1}}
```

把 `Parameters` + `Port` + `Code` 拼成完整 `module ... endmodule`，写入
`workspace/build/export/<name>.v`，随后自动跑 `lint_verilog`。

- 只读输入，只写工作区；目标已存在则 `FileExistsError`
- `parameter_overrides` 用于修正 `mux.model` 的 `LEN = 0`（直接导出会得到非法的 `[-1:0]`）
- 返回 `model_sha256_before/after`，证明 `.model` 未被改动

### `generate_testbench`

```json
{"verilog_path": "counter4/counter4.v", "top": "counter4",
 "out_path": "testbench/counter4_stage4_tb.v"}
```

解析端口后生成骨架：时钟（`clk`/`clock`/`*_clk`）、复位（`rst`/`rst_n`/`reset_n` 等，
`_n` 结尾判定低有效）、`$dumpfile`/`$dumpvars`、`$display`、实例化、TODO 注释。

- 默认写到 `workspace/testbench/<top>_tb.v`，已存在则拒绝覆盖
- 生成模块名固定为 `<top>_tb`
- 其余输入先置 0，复位释放后把第一个非时钟/复位输入拉高，并留 TODO 提示

### `find_events`

```json
{"vcd_path": "build/sim-.../counter4_tb.vcd",
 "signal": "counter4_tb.dut.count", "condition": "== 4'b1111", "limit": 3}
```

条件语法只有 `== != > >= < <=` 加字面量（`4'b1111`、`8'hFF`、`15`），
**不使用 `eval`**；非法条件抛 `ConditionError`。返回每次「进入条件」的时刻。

### `render_waveform`

```json
{"vcd_path": "build/sim-.../counter4_tb.vcd",
 "signals": ["counter4_tb.clk", "counter4_tb.dut.count"],
 "out_name": "counter4.png"}
```

用 venv 内的 matplotlib（Agg 后端）逐信号阶梯图，写入
`workspace/build/waveforms/`，返回路径和字节数。已存在则拒绝覆盖；`x`/`z` 记为 NaN。

## 安全边界

- 固定工具白名单，`shell=False`。
- 源码路径必须在工作区。
- 模型读取路径必须在允许的模型根目录。
- 每次运行创建独立 `build/<kind>-<timestamp>-<uuid>`，不覆盖旧文件。
- 子进程默认 30 秒、最大 120 秒超时。
- 子进程 `TMP`/`TEMP` 指向运行目录。
- 输出有长度上限。
- `simulate` 编译前静态扫描 `` `include `` 与 `$fopen` / `$readmemh` / `$readmemb` /
  `$writememh` / `$writememb` / `$dumpfile`：字面量路径解析后必须在工作区内，否则
  直接拒绝（`stage: "security_scan"`），且不启动编译器。
- 相对路径按源文件目录解析；`..\..\..` 这类逃逸会被拒绝。
- 非字面量（变量/宏拼接）无法静态判定，列在 `security.rejected` 之外的
  `unresolved` 里提示，不做猜测性放行判断。
- 扫描前会去掉注释（保留字符串字面量），避免注释里的示例路径误报。
- 仍未做的是：仿真过程中的运行时文件系统隔离（例如 `$system` 调用）。

## 测试

```powershell
C:\dev\robei-mcp\.venv\Scripts\python.exe C:\dev\robei-mcp\tests\stage2_smoke.py
C:\dev\robei-mcp\.venv\Scripts\python.exe C:\dev\robei-mcp\tests\stage3_cases.py
C:\dev\robei-mcp\.venv\Scripts\python.exe C:\dev\robei-mcp\tests\stage4_cases.py
```

`stage4_cases.py` 覆盖（全部通过）：

1. 10 个工具注册 + `runtime_status` 报告 Verilator `5.036`
2. `warning_width_unused.v` 报出 `unused_signal`，Verilator 报 `Warning-SELRANGE`
3. `counter4.v` 三路 lint 干净；`syntax_error.v` 的错误文本返回
4. `read_robei_project` 解析合成样例（2 个 model、未知 `netlist`/`Instance` 节点）+ 真实 `.proj` SHA 不变
5. `export_robei_verilog` 导出并 lint 通过；重复导出被拒绝
6. `generate_testbench` 生成 TB → `simulate` 跑通；重复生成被拒绝
7. `find_events` 命中 15；注入式条件被拒绝
8. `render_waveform` 出 PNG；重复渲染被拒绝
9. 三类路径逃逸被拒 + 工作区内路径放行
10. 1 秒超时后残留 `vvp.exe = 0`

`stage3_cases.py` 覆盖：

1. 正常 4 位计数器 lint/simulate/read_waveform
2. `warning_width_unused.v`：越界位选警告；未用信号存在，但 Icarus 12.0 `-Wall` 不报 unused
3. `warning_portbind.v`：悬空输入端口警告
4. `syntax_error.v`：iverilog 和 Yosys 的错误文本返回
5. `timeout_tb.v`：1 秒超时，检查无残留 `vvp.exe`

## Robei GUI 手动闭环

测试工程：

```text
C:\dev\robei-mcp\workspace\robei_counter4_project\
  counter4_project.proj
  verilog\counter4.v
  MANUAL_STEPS.md
```

当前 SSH 进程在 Session 0，交互桌面 Explorer 在 Session 1，无法从 SSH 会话看到或点击 Robei GUI。因此状态是 **待手动验证**。

手动步骤：

1. 在交互桌面打开 `E:\Robei\bin\Robei.exe`。
2. `File -> Open` 打开 `counter4_project.proj`。
3. 新建 `counter4` 模型（输入 `clk`、`rst_n`、`en`，输出 `count[3:0]`），把 `verilog\counter4.v` 粘贴到 Code 视图，或使用 Robei 的 Verilog 打开/导入入口。
4. `Build -> Compile`，记录输出。
5. 不修改 `E:\Robei\project`，不写回 Robei 安装目录。

## 已知限制

- Verilator 是 MSYS2 系统包（`C:\msys64\ucrt64`），不在 `C:\dev` 内；撤销需
  `pacman -R mingw-w64-ucrt-x86_64-verilator`。
- Verilator 的 perl 包装脚本 `verilator` 在本机缺 `Pod::Usage`，因此直接调用
  `verilator_bin.exe` 并设置 `VERILATOR_ROOT=C:\msys64\ucrt64\share\verilator`；
  可用 `VERILATOR_EXE` / `VERILATOR_ROOT_DIR` 覆盖。
- Verilator 5.036 对「声明了但既不驱动也不使用」的 net 不报 `UNUSEDSIGNAL`；
  未用信号由 Yosys `opt_clean` 前后网表 diff 兜底识别（这是设计选择，不是缺陷）。
- Icarus 12.0 `-Wall` 不报告未使用信号。
- Yosys 厂商构建不返回可靠版本号。
- Yosys 只做可综合性粗检，不替代完整时序/面积验证。
- 没有写回 `.model` / `.proj` 的能力。
- 没有 Robei GUI 自动化。
- 层次化 `.model` 的实例/连线 schema 未用真实 Robei 工程验证：当前只做通用节点树保留，
  不解释 `Instance`/`netlist` 的语义；等 Robei 里画出的两层样例补齐后再定。
- `.test`、`.const` 目录只列文件，不解析内容。
- `generate_testbench` 是骨架生成器，不理解设计语义（例如不会自动产生激励序列或断言）。
- 超时后可能留下部分 VCD；这是被终止仿真的预期副产物。
- Codex 长驻 app-server 需要重启才能热加载新 MCP server。
## GUI 工具（robei_gui_*）

MCP 现在可以直接驱动 Robei 本体（打开工程、打开模型、编译、读输出），底层走 Robei 自带的 UIA 桥，不改程序、不注入 DLL。

前提：Windows 上有一个已登录且未锁屏的交互桌面，并注册一次辅助任务：

```powershell
python tools\gui\register_task.py
```

服务器跑在 SSH（Session 0）时会自动把请求排给该任务；在桌面直接运行时就地执行。完整说明与已知限制见 `docs/gui-tools.md`，桥的存在性与探测过程见 `docs/L1-uia-bridge.md`。

回归：`tests\stage5_gui_cases.py`（真实 stdio MCP 客户端，10 项断言，全部 PASS）。
