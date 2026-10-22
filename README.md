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

已验证（阶段 9/10，Robei 原生闭环，`tests/stage9_native_run.py` + `tests/stage10_counter_native.py`）：

- 纯 Python 生成的 `test/<name>.test`（`Type="test"`、`Timescale="1ns/1ns"`、Verilog 写在 `Code` 属性里）被 Robei 接受，不需要 GUI 的 New 对话框
- `sync_robei_verilog` 把每个 `.model` 导出成 `<工程>/verilog/<model>.v`；Robei 用 `iverilog -o build/<test> build/filelist.vf -y <工程>/verilog` 编译，子模块因此能被解析
- `robei_gui_experiment` 一次跑完「打开工程 → 打开 test 模型 → Compile → Run」，返回 Robei 自己的输出和 `build/<test>.vcd`
- 与门实验：`t=40 a=1 b=1 y=1`、`$finish called at 50 (1ns)`；VCD 由 `read_waveform` 读到 6 个信号（`y/a/b` 与 `dut.y/a/b`），`find_events(y == 1'b1)` 命中 `t=30`
- 4 位计数器：`t=25 count=0001 … t=165 count=1111 … t=255 count=1000`，`$finish called at 260`；`find_events(count == 4'b1111)` 命中 `t=155`，PNG 落在 `workspace/build/waveforms/`
- 注册型输出端口必须写成 `datatype="reg"`（否则导出 `output wire` 后报 `count is not a valid l-value`），这条是实测出来的
- `read_robei_project` 现在同时解析 `model/*.model` 与 `test/*.test`（含 `code`、`timescale`）

已验证（阶段 12，`tests/stage12_ops.py`，17/17 PASS，全部走 stdio MCP）：

- `list_projects`：一次列出 8 个工程及其 models/tests/verilog/VCD
- `robei_gui_health`：正常时 `widgets=133`、`menu_items=9`；打开 Wave dock 后
  `widgets=0` 并返回原因
- `robei_gui_experiment(heal=True)`：检测到 UIA 静默会自动重启 Robei 再跑，实测
  `heal: "restarting Robei because the UIA bridge is silent"`，随后 Compile+Run 正常
- `robei_gui_experiment(plot=True)`：一次调用返回 `steps.compile/run` + VCD + PNG
- `robei_gui_compile_all`：`two_level` 两个模型 2/2 通过，层次化 `top_two` 靠
  `iverilog -y <工程>/verilog` 找到 `and2`

已验证（阶段 13，`tests/stage13_doctor.py`，18/18 PASS）：

- `robei_doctor` 一项一项报告 python / venv / mcp / matplotlib / workspace 可写 /
  Robei / iverilog / vvp / yosys / verilator / GUI 任务 / 环境变量覆盖
- `deep=true` 会真的编译并运行一个两行 testbench，输出 `DOCTOR_OK` 才算通过
- 负向：把 `ROBEI_HOME` 指到不存在的目录，doctor 返回 `ok=false`、失败项
  `['robei_exe','iverilog','vvp','yosys']`，并给出 `ROBEI_HOME` 的修复提示
- `tools\setup.py --check` 退出码 0；`--apply` 重复执行全部 `skip`
- `.const` 现在会被解析：真机 `File→New→const` 生成的文件是
  `<Module Type="const" Vendor="AMD" Device="" Family="" Code="" .../>`，
  与 `.model`/`.test` 同一个 `<Module>` 根，因此复用同一个解析器

已验证（阶段 14，`tests/stage14_headless.py`，9/9 PASS）：

- `simulate_project` 不需要 IDE：Robei 关掉的情况下，用同一份 `.test` 的 Code +
  每个 `.model` 导出出的 Verilog，自己编译跑出 26 行 `t=… count=…`
- 期望序列是**按 testbench 语义独立推导**的（10 ns 周期、t>12 释放复位、4 位回绕），
  与真实输出逐行相等；`find_events(count == 4'b1111)` 同样是 `t=155`，和 IDE 内跑的一致
- `render=true` 同时产出 PNG；未知 test 名会返回可用列表

已验证（阶段 15，可移植性 + CI）：

- `config.py` 不再硬依赖 `ctypes.wintypes`（非 Windows 条件导入），工具路径按平台探测
  （Windows 走 `<ROBEI_HOME>\bin\*.exe`，POSIX 走 `/usr/bin`、`/usr/local/bin`、
  `/opt/homebrew/bin`、`/opt/local/bin`）
- macOS 上 `git clone` 后 `from robei_mcp import tools` 成功，
  `runtime_status()` 优雅返回 `ok=false` + 各工具 `exists=false`（不再 ImportError）
- `tests/portable_core.py`：15/15 PASS（Windows，真实 iverilog/vvp/yosys），覆盖
  simulate / VCD / generate_testbench / model / project / simulate_project / lint
- macOS（Homebrew iverilog 13.0，Python 3.14）：同一份 `portable_core.py` 跑出
  **14 passed, 1 skipped**（skipped = 本机没装 yosys）
- `.github/workflows/ci.yml`：ubuntu-latest + `apt install iverilog yosys` 跑同一份
  `portable_core.py`（不需要 Robei、不需要 GUI、不需要 Windows）——首次运行就抓到两个
  真实问题，修完后 **CI 绿**（run `39747769496`, commit `d54439d`）
- 注意：需要 **Python 3.10+**（`Path.write_text(newline=...)` 是 3.10 才有的参数；
  macOS 自带的 3.9 会在 `generate_testbench` 处报 TypeError）

已验证（阶段 15，`tests/stage15_report.py`，15/15 PASS，36 个工具）：

- `report_lab`：无 IDE 跑一次实验 → `workspace/reports/<工程>-<test>-<时间>.md`，
  含编译结果、`$display` 原文、信号表、每信号前 12 次跳变、内嵌波形 PNG、5 项自动检查
- `compare_waveforms`：两份 VCD 逐信号比对；同源两份 0 差异，改动一个值后精确报出
  `t=155 golden=1111 dut=1110`（只报这一个信号）
- `workspace/logs/mcp-YYYYMMDD.log`：每次子进程 / 每次 GUI 往返一行 JSON
  （`tool` / `rc` / `ms` / `timeout` / `ok`），出问题能复盘
- GUI 加互斥锁 `tools/gui/agent.lock`：第二个并发调用立刻返回
  `another GUI job is still running`，不再互相覆盖 job 文件后一起超时
- 工程化：`pyproject.toml`（0.3.0，`requires-python>=3.10`，可选 `dev` = pytest），
  `tests/test_portable.py` 用 pytest 参数化跑 5 个子系统

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
  pyproject.toml                 0.3.0，requires-python>=3.10，可选 dev=pytest
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
    oplog.py                     workspace/logs 下的操作日志
    vcd.py                       VCD 解析、事件查询、PNG 渲染
    verilog.py                   端口解析 + testbench 生成
    doctor.py                    环境自检（python/venv/包/路径/任务/深度仿真）
  tools\
    setup.py                     一键 bootstrap + 自检（--check / --apply）
    gui\                          Session 1 侧 UIA 驱动与 schtasks 桥
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

一条命令搞定（只补缺的，不改 `E:\Robei`）：

```powershell
# 只检查，不动磁盘
python C:\dev\robei-mcp\tools\setup.py --check

# 缺什么建什么：.venv、mcp/matplotlib、Session-1 助手任务
python C:\dev\robei-mcp\tools\setup.py --apply
```

`--apply` 的每一步都会打印（`done` / `skip` + 结果），重复执行是幂等的。
想连"真的能编译并跑一个 tb"一起验证，加 `--deep`。

手工步骤（等价）：

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

### `simulate_project`

```json
{"proj_path": "counter4_native/counter4_native.proj",
 "test_name": "tb_counter4.test", "render": true}
```

**无 GUI、无桌面**地把一个 Robei 实验跑起来：读 `test/<name>.test` 的 `Code`，
包成 `` `timescale … module <name>(); … endmodule ``（并补 `$dumpfile`/`$dumpvars`），
把 `model/*.model` 全部导出到 `build/headless/<工程>-<test>/deps/`，再交给
`iverilog` + `vvp` 跑。

- 输出结构与 `simulate` 相同（`compile` / `simulation` / `display_output` / `vcd_paths`），
  另外多个 `headless` 段记录生成物路径
- `render=true` 顺带把 VCD 画成 PNG
- 适用场景：桌面锁屏、SSH 里没开 Session 1、或者 UIA 桥已经塌了

### `read_waveform`

```json
{"vcd_path": "build/sim-.../counter4.vcd", "signals": ["counter4_tb.dut.count"], "time": 45}
```

不传 `signals` 时列信号；传 `signals` 时按原始 VCD 时间刻度取值。返回值含 `timescale`、`max_time`、`effective_time`、二进制 `value` 和可解析时的 `value_int`。

### `report_lab`

```json
{"proj_path": "counter4_native/counter4_native.proj", "test_name": "tb_counter4.test",
 "title": "4 位计数器"}
```

用 `simulate_project` 跑一次（不需要 Robei、不需要桌面），把结果写成一份可以直接贴进
实验报告的 Markdown：编译结论、`$display` 原文、信号表（位宽/类型/跳变次数）、
每个信号前 N 次跳变、波形 PNG（相对路径嵌入）、以及 5 项自动检查
（`$finish`、产生 VCD、无 X/Z、有 `$display`、全部通过）。

输出在 `workspace/reports/`，同名文件不覆盖。

### `compare_waveforms`

```json
{"golden_vcd": "build/sim-.../tb.vcd", "dut_vcd": "build/golden/tb.vcd"}
```

两份 VCD 逐信号比对，比较点在**两边跳变时间的并集**上，所以"一边完全没跳"也能被抓到。
返回每个信号的跳变次数、差异条数、首个差异（时间/两侧值），以及总和
`ok / mismatch_total / differing_signals`。

### `read_robei_model`

```json
{"path": "C:\\ProgramData\\Robei\\mux.model"}
```

只读解析 `.model` 的模块名、类型、参数、端口、`parameter_list` 和 `Code`。
非 `Port` 的子节点（例如 `Instance`）以通用节点树放进 `other_children`，不猜测含义。不写回。

### `robei_doctor`

```json
{"deep": false}
```

自检整条链路，每项返回 `{name, ok, required, detail, hint}`，`ok` 只由
`required` 项决定（Verilator、GUI 任务、matplotlib 是可选件）。

`deep=true` 时会在 `workspace/build/doctor/` 编译并运行一个两行 testbench，
只有拿到 `DOCTOR_OK` 才算通过——用来区分"路径对"和"真的能跑"。

### `list_projects`

```json
{"limit": 200}
```

列出 workspace 里所有 `.proj`（含递归子目录）：工程名、`model/` 下的模型、`test/` 下的
test 模型、`verilog/` 下的依赖、`build/` 里已有的 VCD，以及 `.proj` 里声明的条目数。
只读，用来回答「我现在有哪些工程、哪个已经跑过」。

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

### `sync_robei_verilog`

```json
{"proj_path": "counter4_native/counter4_native.proj", "overwrite": true}
```

把工程 `model/` 下每个 `.model` 的 `Parameters + Port + Code` 导成
`<工程>/verilog/<model>.v`（模块名与文件名一致）。这是 Robei 仿真能解析被实例化
子模块的前提——它的编译命令只把当前 test 模型写进 `filelist.vf`，其余模块靠
`-y <工程>/verilog` 目录查找。

- 只写工程自己的 `verilog/` 目录（工程必须位于 workspace 内）
- 返回每个文件的 `sha256`、大小与出错列表

### `create_robei_test`

```json
{"proj_path": "counter4_native/counter4_native.proj",
 "name": "tb_counter4",
 "code": "reg clk; reg rst_n; wire [3:0] count;\ncounter4 dut (...);\n..."}
```

生成 `test/<name>.test` 并登记进 `.proj` 的 `<test>` 目录。

- `code` 只写 **module body**：Robei 自己补 `module <name>();`、`endmodule` 和
  `$dumpfile("./build/<name>.vcd"); $dumpvars;`
- 默认 `sync_deps=true`，顺带刷新 `verilog/` 依赖库
- 目标已存在时 `FileExistsError`（`overwrite=true` 才覆盖，并留 `.bak-<时间戳>`）
- 登记是幂等的：返回 `registered_in_project`（布尔）与 `registration`（`created` / `exists`）

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

## Robei 原生仿真闭环

不用 GUI 也能让 Robei 自己编译、自己跑 vvp、自己出 VCD，全流程只碰 workspace：

```text
create_robei_project(name)                    建 model/ test/ const/ verilog/ + .proj
create_robei_model(..., datatype="reg")       写 .model（端口 + Code）
create_robei_test(proj, name, code)           写 .test（Type="test"）+ 自动导出 verilog/
robei_gui_experiment(proj, "tb_x.test")       File→Open → 双击 test → Compile → Run
read_waveform / find_events / render_waveform 读 build/<test>.vcd
```

几个实测出来的硬约束：

1. **扩展名与 Type 必须配对**：测试模型是 `test/*.test` 且 `Type="test"`；写成
   `.model` 或 `Type="model"` 时 Run 会回 `is not test, so there is no waveform generated`。
2. **Compile 会清空 `build/`**：每次 Compile 重新生成 `filelist.vf` 与可执行文件，
   所以「先编 DUT 再编 TB」会把 DUT 产物冲掉；正确做法是把子模块放进
   `<工程>/verilog/`，让 `-y` 去解析。
3. **Run 不会隐式编译**：只点 Run 会执行上一次留下的可执行文件，缺失时报
   `build/<name>: Unable to open input file`。顺序必须是 Compile → Run。
4. **注册型输出要 `datatype="reg"`**：`output wire` 上做非阻塞赋值会报
   `count is not a valid l-value`。
5. `.proj` 是登记表：手工放进去的文件必须同时写进 `.proj` 对应目录块，否则
   Files 树里看不到，`robei_gui_open_model` 会返回 not found。

## 持续集成

```text
.github/workflows/ci.yml
  portable (ubuntu-latest)
    apt install iverilog yosys
    pip install -r requirements.txt
    python tests/portable_core.py
```

`tests/portable_core.py` 是"无 Robei / 无 GUI / 跨平台"的子集：simulate、VCD 解析、
`generate_testbench` 往返、`.model`/`.proj` 只读解析、`simulate_project`、lint。
每一步是独立 step（`simulate` / `testbench` / `robei-xml` / `headless` / `lint`），
所以失败时 annotation 直接点名是哪一组，不用翻日志。

第一版 CI 就是靠这个定位到两个问题的：
1. `portable_core` 断言了 `runtime_status()["ok"]`，而它要求 Robei 本体存在——
   CI 里没有 Robei，必然失败；改成断言 iverilog/vvp 存在。
2. Ubuntu 与 macOS 都缺少 Windows 上的 `D:\robei-lab` 沙盒路径，`config.py` 里
   `ctypes.wintypes` 的顶层 import 直接让整包无法导入；改成条件导入 + 按平台探测 PATH。

Robei 相关的一切（`robei_gui_*`、`robei_doctor` 的 GUI 任务检查、stage5–13）只在
Windows 交互桌面上跑，不进 CI。

跨平台注意：Linux/macOS 只支持核心工具链（iverilog/vvp/yosys/verilator），
`robei_gui_*` 会返回 `GUI tools require Windows`。

## 日志与并发

- **操作日志**：`workspace/logs/mcp-YYYYMMDD.log`，一行一个 JSON。子进程记录
  `tool / rc / ms / timeout / cwd / stderr 尾部`，GUI 记录 `op / transport / ok / ms / error`。
  日志写入失败会静默跳过，绝不影响工具返回。
- **GUI 互斥**：所有 `robei_gui_*` 共用一份 `agent-job.json`，因此 `gui.call()`
  先用 `O_CREAT|O_EXCL` 抢 `tools/gui/agent.lock`；抢不到就立刻返回
  `another GUI job is still running`（超过 `MAX_TIMEOUT_S` 的陈旧锁会被自动清掉）。
  这是"失败得快"，不是排队——目前没有任务队列。
- 两者都在 `.gitignore` 里（`workspace/logs/`、`workspace/reports/`、
  `tools/gui/agent.lock`）。

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

`stage11_mcp_native.py` 覆盖（全部走真实 stdio MCP 客户端，14/14 PASS）：

1. 29 个工具注册，含 `sync_robei_verilog` / `create_robei_test` / `robei_gui_experiment` / `robei_gui_wave_load`
2. `sync_robei_verilog` → `counter4_native/verilog/counter4.v`
3. `read_robei_project` 读到 `.test`（`type=test`、`timescale=1ns/1ns`），`.proj` SHA 不变
4. `create_robei_test` 建新 test 并登记（第二次调用返回 `registration=exists`）
5. `robei_gui_experiment` 在 Robei 里 Compile+Run，输出 `t=45 count=0011`
6. `read_waveform` / `find_events` 读 Robei 生成的 VCD（`count==4'b0011` → `t=35`）

`stage9_native_run.py` / `stage10_counter_native.py` 覆盖（Robei 原生闭环）：

1. 用 `create_robei_project` + `create_robei_model` + `create_robei_test` 造工程
2. `robei_gui_experiment(restart=True)` 在 Robei 里 Compile + Run，返回原始输出
3. `read_waveform` 列信号、按时间取值；`find_events` 命中条件
4. `render_waveform` 出 PNG
5. `read_robei_project` 的 `sha256_before == sha256_after`

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
- 不写回 `.model` / `.proj` 以外的 Robei 状态；`E:\Robei` 全程只读。
- **打开 Wave dock 会让 Robei 的 UIA 树塌掉**（`descendants()` 变 0，新进程也读不到），
  只能重启 IDE 恢复。`robei_gui_health` 能识别这种状态，`robei_gui_experiment`
  默认 `heal=true`，会自动重启后再跑；`wave_load` 仍是显式可选项。
- Robei 的 Wave 面板确实能载入我们生成的 VCD（实测截图里 `SignalTree` 出现
  `Waves → tb_and_gate → y/a/b/dut`，点 `y` 后波形画布出现绿色轨迹），但自动把
  信号批量加入画布不稳定，属于 **待定**。数值/图形证据请用 `read_waveform` /
  `find_events` / `render_waveform`。
- Robei 的 Wave dock 打开后 `main_window()` 的菜单栏探测会超时，已加"最大 Qt 窗口"
  兜底，但兜底窗口的控件树可能为空——所以判断健康度要看 `robei_gui_health`，
  而不是"窗口在不在"。
- 层次化 `.model` 的实例/连线 schema 未用真实 Robei 工程验证：当前只做通用节点树保留，
  不解释 `Instance`/`netlist` 的语义；等 Robei 里画出的两层样例补齐后再定。
- 核心工具在 Linux/macOS 上可用（只要有 iverilog/vvp），但 `robei_gui_*` 与 Robei
  版本读取是 Windows 专属；CI 只覆盖核心部分。
- **画布编辑无法自动化**：右键 `Add model`、ToolBox 列表、按坐标拖拽三条路都试过，
  模型文件都不变（细节见 `docs/gui-tools.md` 末节）。要层次化设计只能写 `Code` 字段，
  这条已验证可编译。
- `read_robei_project` 现在解析 `.model` / `.test` / `.const` 三类，但只到属性层
  （名称、Type、Code、端口、Vendor/Device/Family）；画布上的 `Instance`/连线语义仍未解释。
- 没有创建 `.const` 的工具：真机生成的空 const 只有 `<Module Type="const">`，
  引脚/时序约束的写法需要真实样例才能实现（同类问题见层次化 Instance）。
- `generate_testbench` 是骨架生成器，不理解设计语义（例如不会自动产生激励序列或断言）。
- 超时后可能留下部分 VCD；这是被终止仿真的预期副产物。
- Codex 长驻 app-server 需要重启才能热加载新 MCP server。
## GUI 工具（robei_gui_*）

MCP 现在可以直接驱动 Robei 本体（打开工程、打开模型、编译、读日志、读写 Code 页签里的 Verilog，并用 set_robei_port 造端口），底层走 Robei 自带的 UIA 桥，不改程序、不注入 DLL。

前提：Windows 上有一个已登录且未锁屏的交互桌面，并注册一次辅助任务：

```powershell
python tools\gui\register_task.py
```

服务器跑在 SSH（Session 0）时会自动把请求排给该任务；在桌面直接运行时就地执行。完整说明与已知限制见 `docs/gui-tools.md`，桥的存在性与探测过程见 `docs/L1-uia-bridge.md`。

Robei 原生闭环已接入：`create_robei_test` + `sync_robei_verilog` + `robei_gui_experiment`
（Compile → Run → VCD），普通模块 Run 会被 Robei 拒绝（`is not test`），波形数值走
`read_waveform` / `find_events` / `render_waveform`。`robei_gui_wave_load` 可打开 Robei
自带的 Wave 面板，但它会破坏 UIA 树，见「已知限制」。

工程脚手架：`create_robei_project` 建目录与 .proj，`create_robei_model` 生成带端口+Code 的 .model 并登记进工程。

运维类补充：

- `robei_gui_health`：返回 `widgets` / `menu_items` / `pid`，判断 UIA 桥是否还活着
- `robei_gui_compile_all`：把工程里每个 `.model` 依次打开并 Compile，逐模型报告
  `warnings` / `errors` / `missing_modules`（层次化工程先跑 `sync_robei_verilog`）
- `robei_gui_experiment(plot=True)`：跑完自动把 VCD 画成 PNG 放到
  `workspace/build/waveforms/`，一次调用拿到 输出 + 波形 + 信号列表

回归：`tests\stage5_gui_cases.py`（10 项）、`tests\stage6_gui_code.py`（11 项）、`tests\stage7_model_ports.py`（10 项）、`tests\stage8_create_flow.py`（10 项）、`tests\stage9_native_run.py`、`tests\stage10_counter_native.py`、`tests\stage11_mcp_native.py`（14 项）、`tests\stage12_ops.py`（17 项）、`tests\stage13_doctor.py`（18 项）、`tests\stage14_headless.py`（9 项），都走真实 stdio MCP 客户端（stage14 连 IDE 都不需要）；另有 `tests/stage15_report.py`（15 项：报告、VCD 比对、日志、锁）；跨平台的
`tests/portable_core.py`（16 项）与 `tests/test_portable.py`（pytest，5 个子系统，CI 跑的就是它）。全部 PASS。

## 从 SSH 会话推送仓库

SSH 跑在 Session 0，`git push` 会失败：GCM 的凭据在交互会话的 Windows
Credential Manager 里，Session 0 读写不到（`Unable to persist credentials with the
'wincredman' credential store`）。

办法是把 push 交给交互会话（和 GUI 工具共用同一个 schtasks 桥）：

```powershell
# 在 Windows 交互桌面上
C:\dev\robei-mcp\.venv\Scripts\python.exe C:\dev\robei-mcp\tools\gui\probe_push.py

# 或从 SSH 触发
echo probe_push > C:\dev\robei-mcp\tools\gui\agent-args.txt
schtasks /Run /TN RobeiMCP_GuiAgent
```

脚本设置 `GIT_TERMINAL_PROMPT=0`、`GCM_INTERACTIVE=never`，避免卡在登录窗口，
并对返回文本里的 token 做脱敏。

回归补充：`tests\stage9_native_run.py`、`tests\stage10_counter_native.py`（Robei
原生闭环）、`tests\stage11_mcp_native.py`（29 个工具的 stdio 端到端，14/14 PASS）。
