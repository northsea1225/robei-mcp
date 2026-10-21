# Robei MCP Server

Windows 本地 MCP 服务器，把 Robei EDA 自带的 Icarus Verilog、vvp 和 Yosys 封装成 MCP 工具，并提供只读的 Robei `.model` 解析和纯 Python VCD 读取。

## 交付状态

已验证：

- MCP stdio 协议、5 个工具、4 位计数器 lint/simulate/waveform 闭环
- `iverilog -Wall` 警告提取、Yosys `read_verilog + synth -top` 可综合性检查
- 语法错误返回给 MCP 调用方
- 1 秒超时能终止 `vvp`，无残留 `vvp.exe`
- Robei `.model` 只读解析，`mux.model` 前后 SHA256 不变
- Mac 通过 `ssh my-win` 使用远端 stdio；stdout 只有 JSON-RPC，stderr 为空
- Claude Code 实际调用 `mcp__robei_remote__runtime_status`

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
  requirements.txt               mcp==2.3.0
  run-mcp.cmd                    Windows stdio 包装脚本
  examples\
    codex-windows.toml
    codex-mac-remote.toml
    claude-windows.mcp.json
    claude-mac-remote.mcp.json
  server.py                      MCP stdio 入口
  robei_mcp\
    config.py
    model.py
    security.py
    server_app.py
    tools.py
    vcd.py
  tests\
    stage2_smoke.py              5 个工具的正常路径
    stage3_cases.py              警告、语法错误、超时、残留进程
  workspace\
    counter4\
      counter4.v                 4 位计数器
      counter4_tb.v              testbench
    cases\
      warning_width_unused.v     越界位选 + 未用信号
      warning_portbind.v         悬空输入
      syntax_error.v             语法错误
      timeout_tb.v               长仿真超时
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
- Robei、iverilog、vvp、yosys 的路径和存在性

Robei 版本从 PE 文件版本资源读取。Yosys 这个厂商构建的 `-V` 输出 `Synthesis complete`，因此 `version` 为 `null`，但路径和综合检查可用。

### `lint_verilog`

```json
{"files": ["counter4/counter4.v"], "top": "counter4", "timeout_s": 30}
```

分别执行：

1. `iverilog -Wall -g2012 ...`
2. `yosys -p "read_verilog ...; synth -top counter4"`

返回 `iverilog` 和 `yosys` 两段结果；`ok` 只有两者都成功才为 `true`。`iverilog` 结果包含 `warning_count` 和 `warnings`；Yosys 结果包含 `summary` 和截断后的 stdout。

### `simulate`

```json
{"files": ["counter4/counter4.v", "counter4/counter4_tb.v"], "top": "counter4_tb", "timeout_s": 30}
```

执行 `iverilog` 编译和 `vvp` 运行，返回 `$display`、VCD 绝对/相对路径。超时会终止子进程并返回 `simulation.timeout=true`。

### `read_waveform`

```json
{"vcd_path": "build/sim-.../counter4.vcd", "signals": ["counter4_tb.dut.count"], "time": 45}
```

不传 `signals` 时列信号；传 `signals` 时按原始 VCD 时间刻度取值。返回值含 `timescale`、`max_time`、`effective_time`、二进制 `value` 和可解析时的 `value_int`。

### `read_robei_model`

```json
{"path": "C:\\ProgramData\\Robei\\mux.model"}
```

只读解析 `.model` 的模块名、类型、参数、端口和 `Code`。不写回。

## 安全边界

- 固定工具白名单，`shell=False`。
- 源码路径必须在工作区。
- 模型读取路径必须在允许的模型根目录。
- 每次运行创建独立 `build/<kind>-<timestamp>-<uuid>`，不覆盖旧文件。
- 子进程默认 30 秒、最大 120 秒超时。
- 子进程 `TMP`/`TEMP` 指向运行目录。
- 输出有长度上限。
- 测试平台中的 Verilog 仍被视为本地可信代码；`include` 或 `$fopen` 理论上可越过工作区，这是当前 MVP 的边界。

## 测试

```powershell
C:\dev\robei-mcp\.venv\Scripts\python.exe C:\dev\robei-mcp\tests\stage2_smoke.py
C:\dev\robei-mcp\.venv\Scripts\python.exe C:\dev\robei-mcp\tests\stage3_cases.py
```

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

- 没有 Verilator，lint 只使用 `iverilog -Wall`。
- Icarus 12.0 `-Wall` 不报告未使用信号；警告样例中的实际警告是越界位选和悬空端口。
- Yosys 厂商构建不返回可靠版本号。
- Yosys 只做可综合性粗检，不替代完整时序/面积验证。
- 没有写回 `.model` / `.proj` 的能力。
- 没有 Robei GUI 自动化。
- 层次化 `.model`、`.test`、`.const` 的完整 schema 未验证。
- 超时后可能留下部分 VCD；这是被终止仿真的预期副产物。
- Codex 长驻 app-server 需要重启才能热加载新 MCP server。
