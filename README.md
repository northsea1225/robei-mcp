# Robei MCP Server

[![CI](https://github.com/northsea1225/robei-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/northsea1225/robei-mcp/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/northsea1225/robei-mcp)](https://github.com/northsea1225/robei-mcp/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![MCP](https://img.shields.io/badge/MCP-stdio-informational)

**中文** | [English](#english)

让 AI 助手（Codex、Claude Code 等任何 MCP 客户端）直接操作 [Robei EDA](http://www.robei.com/) 工程：
读写 `.model` / `.proj`、Verilog 检查与仿真、自动生成语义测试台、波形分析、实验报告，
以及综合 → 布线 → 打包 `.bit` 的 FPGA 流程（下载只生成 dry-run 计划，不碰真机）。

> **54 个工具**：42 个文件级工具（只需 `iverilog` / `vvp` / `yosys`，Linux / macOS / Windows 均可）
> + 12 个可选 GUI 工具（仅 Windows，需 `[gui]` 额外依赖和 Robei 本体）。

## 30 秒快速开始

```bash
# 1. 安装：克隆源码（或从 Releases 下载 wheel 后 pip install 该文件）
git clone https://github.com/northsea1225/robei-mcp && cd robei-mcp
pip install -e .                    # 仅 Windows 且要 GUI 工具时用 pip install -e ".[gui]"

# 2. 准备工具链：iverilog、vvp、yosys 在 PATH 中即可（Robei 自带的也行）

# 3. 在 MCP 客户端里登记（以 Claude Code 为例）
claude mcp add --scope user robei -- python -m robei_mcp   # 需在上面的 Python 环境里执行

# 4. 自检：让助手调用 runtime_status / robei_doctor，看到 ok=true 即可
```

Codex 配置、Windows 路径、通过 SSH 使用远端 stdio 等完整配置见 [安装与客户端配置](#安装与客户端配置)。
试一试（路径相对 `workspace/`，不要再写 `workspace/` 前缀）：

```text
lint_verilog(["counter4/counter4.v"], top="counter4")
simulate(["counter4/counter4.v", "counter4/counter4_tb.v"], top="counter4_tb")
read_waveform(<vcd_path>, signals=["counter4_tb.dut.count"], time=45)
```

## 能力一览

| 能力 | 代表工具 | 需要 Robei | 需要桌面 |
|---|---|:-:|:-:|
| Verilog 检查 / 仿真 / 综合资源 | `lint_verilog` `simulate` `simulate_project` `run_all_tests` `synthesize_project` | 否 | 否 |
| 波形分析 | `read_waveform` `compare_waveforms` `find_events` `expect_waveform` `render_waveform` | 否 | 否 |
| 工程 / 模型读写（`.proj` `.model`） | `read_robei_project` `create_robei_project` `edit_robei_canvas` `set_robei_code` `set_robei_parameter` | 否 | 否 |
| 语义测试台（表达式白名单，不用 `eval`） | `create_testbench` `generate_testbench` `truth_table` | 否 | 否 |
| 实验报告（Markdown + evidence.jsonl） | `build_report` | 否 | 否 |
| FPGA（综合 / 布线 / 打包 / Download dry-run） | `list_fpga_devices` `build_bitstream` `create_robei_const` `export_robei_xdc` | 否（需 FPGA 工具链） | 否 |
| Robei GUI 自动化（可选） | `robei_gui_open_model` `robei_gui_synthesis` `robei_gui_screenshot` `gui_doctor` | 是 | 是（Windows） |

完整参数、返回值与示例：[docs/tool-reference.md](docs/tool-reference.md)。

## 安全边界

| 保证 | 说明 |
|---|---|
| 不执行任意 shell | 只调用固定白名单的 `iverilog` / `vvp` / `yosys` 等程序，`shell=False`，无 `eval`/`exec` |
| 不改 Robei 安装目录、不写注册表 | 安装目录只读；所有产物写在 `workspace/` |
| 子进程受控 | 子进程有超时；XML 解析拒绝 DOCTYPE/ENTITY |
| 路径白名单 | 读写路径必须落在 workspace / 允许的模型根内；仿真前对 `$fopen` `$readmemh` `` `include `` 等做静态路径扫描，相对路径按仿真运行目录（真实 cwd）解析 |
| 不接触真机 | `build_bitstream` 的 Download 恒为 dry-run，`dry_run=false` 无条件拒绝 |
| 可回滚 | 每次运行独立目录；默认不覆盖已有文件；改模型前先备份 |

> 语义自检失败时进程返回码仍为 0，请只看返回值里的 `ok` / `run.code`。

> **升级注意（1.0.2 起）**：`simulate` 默认开启**严格路径扫描**。像
> `$readmemh(PATH, mem)` 这种无法静态判定路径的写法，以前能跑，现在会直接返回
> `stage="security_scan"` 并拒绝编译。确实需要动态路径时设
> `ROBEI_MCP_STRICT_PATHS=0`（或在 `robei.toml` 里写 `[security] strict_path_scan = false`）。

## 版本记录

当前版本 **1.12.0**，完整变更与各版本说明见 [CHANGELOG.md](CHANGELOG.md)。

<a name="english"></a>
## English

An MCP (stdio) server that lets AI assistants drive **Robei EDA** projects: read/write `.model`/`.proj`,
lint & simulate Verilog (Icarus), generate semantic testbenches, analyse VCD waveforms, build lab reports,
and run the yosys → place/route → bitstream flow (Download is dry-run only; no hardware is touched).
54 tools — 42 portable file-level tools (need only `iverilog`/`vvp`/`yosys`) + 12 optional Windows GUI tools.
The detailed docs are in Chinese; tool names and JSON fields are in English.

## 安装与客户端配置

### 1. 前置条件

| 需要 | 说明 |
|---|---|
| Python 3.10+ | 开发与 CI 使用 3.12 |
| `iverilog` / `vvp` / `yosys` | Robei 自带；也可用独立安装的 Icarus + yosys（Linux/macOS 用包管理器装即可） |
| Robei EDA | 仅 Robei 相关能力（FPGA 器件库、GUI 工具）需要 |
| （可选）Verilator | `--lint-only` 那一路，缺了会自动跳过 |
| （可选）Windows 交互桌面 | 只有 12 个 GUI 工具需要 |

### 2. 安装

```powershell
git clone https://github.com/northsea1225/robei-mcp
cd robei-mcp
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

或者用自带引导脚本（只补缺的，幂等，可先 `--check` 只看不动）：

```powershell
python tools\setup.py --check
python tools\setup.py --apply
```

依赖只有两个：`mcp==2.3.0`、`matplotlib`（画波形 PNG 用）。

想用 12 个 GUI 工具，再装可选依赖（仅 Windows 需要）：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[gui]"     # 源码目录
.\.venv\Scripts\python.exe -m pip install "robei-mcp[gui]" # 已发布包
```

`[gui]` 会带上 `pywinauto`、`Pillow`、`pywin32`。**没装这三个包时 GUI 工具不会出现在
`tools/list` 里**，而不是调用时才报错；`tools\setup.py --apply` 也会在 Windows 上补装它们。

一句话：基础 wheel 只有 **42 个文件级工具**；装上 `[gui]`（且只在 Windows 上）才会多出
12 个 GUI 工具，合计 **54 个**。

### 3. 自检

```powershell
.\.venv\Scripts\python.exe -c "from robei_mcp import tools; import json; print(json.dumps(tools.runtime_status(), indent=1))"
```

看 `ok: true` 和 `tools.*.exists` 全为 `true` 就齐了。缺什么，`robei_doctor` 会逐项给修复提示。

### 4. 接到客户端

**Windows 本地 Codex**（`~/.codex/config.toml`；`<repo>` 换成你的克隆目录）：

```toml
[mcp_servers.robei]
command = '<repo>\.venv\Scripts\python.exe'
args = ['<repo>\server.py']
startup_timeout_sec = 20
tool_timeout_sec = 120
```

**Windows 本地 Claude Code**：

```powershell
claude mcp add --scope user robei -- "<repo>\.venv\Scripts\python.exe" "<repo>\server.py"
```

**通过 SSH 使用远端 stdio**（例如 Mac 连 Windows；不需要在 Windows 上开端口，`<windows-host>` 为 ssh 主机别名）：

```toml
[mcp_servers.robei_remote]
command = "ssh"
args = ["<windows-host>", "MSYS_NO_PATHCONV=1 /c/Windows/System32/cmd.exe /d /c <repo>/run-mcp.cmd"]
startup_timeout_sec = 30
tool_timeout_sec = 120
```

`run-mcp.cmd` 切 UTF-8、`cd` 到仓库，由轻量等待进程以 `-m robei_mcp.server` 启动服务并记录开始/退出。
等待进程不自动重启服务，生命周期日志只写文件，stdout 仍只留给 JSON-RPC。

> MCP 客户端通常是长驻进程，改完配置要重启会话/应用；`codex mcp list` 之类的命令只说明配置存在。

### 5. 三十秒验证

在客户端里依次调用：

```text
runtime_status                 → ok=true，四个工具的 exists 都是 true
lint_verilog(["counter4/counter4.v"], top="counter4")
simulate(["counter4/counter4.v", "counter4/counter4_tb.v"], top="counter4_tb")
read_waveform(<上一步的 vcd_path>, signals=["counter4_tb.dut.count"], time=45)
```

路径一律**相对 workspace**（`counter4/counter4.v`）。写成 `workspace/counter4/counter4.v`
也认，不会因此多套一层目录。

## 配置

两层：`robei.toml`（把 `robei.toml.example` 复制到仓库根）和环境变量。
**优先级：环境变量 > robei.toml > 自动发现 > 内置默认值**。

`robei_home`、`iverilog`、`vvp`、`yosys` 默认**自动发现**（`PATH`、`%ProgramFiles%\Robei`、
`<盘符>:\Robei`、`C:\msys64\ucrt64\bin`），所以大多数人不用写配置。`runtime_status().config.sources`
会逐项告诉你每个键来自哪一层（`env` / `toml` / `discovered` / `default`）。

常改的几个：

| 变量 | 作用 |
|---|---|
| `ROBEI_MCP_WORKSPACE` | 产物目录，默认 `<repo>/workspace`；装成 wheel 时默认 `%LOCALAPPDATA%\robei-mcp\workspace` |
| `ROBEI_HOME` | Robei 安装根目录 |
| `IVERILOG_EXE` / `VVP_EXE` / `YOSYS_EXE` | 单独指定工具链 |
| `VERILATOR_EXE` / `VERILATOR_ROOT_DIR` | Verilator 那一路 |
| `ROBEI_MCP_GUI` | `0` 强制关闭 GUI 面（工具从 `tools/list` 消失，只剩 42 个） |
| `ROBEI_MCP_STRICT_PATHS` | 默认 `1`：源码扫描遇到无法静态判定的路径（`$readmemh(PATH)`）直接拒绝；设 `0` 退回只记录 `unresolved` |
| `ROBEI_DEFAULT_TIMEOUT_S` / `ROBEI_MAX_TIMEOUT_S` | 子进程超时，默认 30 / 120 秒；大器件跑 Roplace 时把上限调大 |
| `ROBEI_FPGA_DB` | 器件库根目录，默认 `%ProgramData%\Robei\xilinx`（里面要有 `chipdb/` 与 `<family>/<part>/part.yaml`） |
| `ROBEI_FPGA_BIN` | `Roplace.exe` / `fasm2frames.exe` / `xc7frames2bit.exe` 所在目录，默认 `%ROBEI_HOME%\bin` |

`robei.toml` 里对应的一节：

```toml
[security]
strict_path_scan = true
```

## 工具一览

完整参数与真实返回示例见 [docs/tool-reference.md](docs/tool-reference.md)。

**诊断（3）** — `runtime_status`（路径/版本/配置来源）、`robei_doctor`（全栈自检，`deep=true` 会真跑一个 tb）、`gui_doctor`（Session-1 桌面前置检查）

**检查与仿真（5）** — `lint_verilog`（iverilog `-Wall` + Verilator + Yosys 三路，Verilator ≥5.0 自动带 `--timing`）、`simulate`（编译+跑 tb）、`simulate_project`（无头跑 Robei 的 `test/*.test`）、`run_all_tests`（一次跑完工程里的 test 并汇总）、`read_run_log`（只读打开 `build/`、`logs/` 下的运行日志，默认尾部 200 行、上限 500 KB）

**波形（6）** — `read_waveform`（列信号/按时刻取值）、`find_events`（`count == 4'b1111` 首次出现）、`compare_waveforms`（两份 VCD 逐信号比对，任何结构性差异都算不通过）、`waveform_stats`（跳变数/翻转率/时钟周期估计）、`expect_waveform`（断言取值）、`render_waveform`（画 PNG）

**工程与模型（11）** — `read_robei_model`、`list_projects`、`read_robei_project`（含画布 instances/wires）、`create_robei_project`、`create_robei_model`、`set_robei_port`、`export_robei_verilog`、`sync_robei_verilog`、`create_robei_test`、`create_robei_canvas_model`（画布级 XML）、`import_verilog_canvas`（Verilog → 画布）

**辅助（4）** — `generate_testbench`（旧的时钟/复位/VCD 骨架）、`create_testbench`（组合/ timed 语义 TB）、`check_connectivity`（ERC）、`truth_table`（组合逻辑穷举真值表，`max_inputs` 按**输入总位数**计）

**引脚约束（3）** — `create_robei_const`（拒绝同一引脚被两个端口/两位重复占用）、`read_robei_const`、`export_robei_xdc`（默认标准 Tcl 写法 `[get_ports {a[0]}]`，`quote_bus=false` 回到 Robei legacy 写法）

可跑的示例工程见 `examples/fpga_demo/`（4 位 `light` + fgg484 引脚 `.const`，`build_bitstream` 开箱即用）。

**FPGA（3）** — `list_fpga_devices`（器件表：有没有 chipdb、有没有 part.yaml）、`synthesize_project`（yosys `synth_xilinx` + LUT/FF/BRAM/DSP/IO 用量）、`build_bitstream`（Roplace → fasm2frames → xc7frames2bit，直出 `.bit`）

**GUI（12，需 Windows 桌面）** — `gui_doctor`、`robei_gui_status`（`probe=true` 顺带查 UIA 桥是否还活着）、`launch`、`open_project`、`open_model`、`compile`、`output`、`save_project`、`synthesis`、`compile_all`、`screenshot`、`close`

## GUI 部分的前提

`robei_gui_*` 要走 Windows 的**交互桌面**：SSH 登录的会话（Session 0）碰不到桌面，
所以这些工具通过一个计划任务 `RobeiMCP_GuiAgent` 把请求投递到登录用户所在的 Session 1。

```powershell
python tools\gui\register_task.py            # 创建/刷新任务
python tools\gui\register_task.py --remove   # 删除
```

三个要知道的事实：

1. **必须有人登录在桌面上**。机器停在登录界面（`LogonUI.exe` 在跑、会话状态 `Connected`）时，
   Windows 会拒绝合成鼠标输入，所有点击类工具都会报
   `There is no active desktop required for moving mouse cursor!`。
2. **GUI 工具是可选面**：非 Windows 或驱动缺失时它们根本不会出现在 `tools/list`，
   不会"调用了才报错"。`ROBEI_MCP_GUI=0` 可以手动关掉。
3. 细节、限制和历史坑见 [docs/gui-tools.md](docs/gui-tools.md)。

## 安全边界（详细）

- 固定工具白名单，`shell=False`，不执行任意命令。
- 源码路径必须在 `workspace/` 内；相对路径（`$fopen`、`$readmemh`、`$dump*`、`` `include `` 里的相对写法）按**仿真运行目录**这一真实 cwd 解析，越出 workspace/运行目录同样拒绝；模型读取只允许 `model_roots` 里的目录（默认 Robei 的 `%ProgramData%\Robei` + 工作区）。
- 每次运行独立目录 `build/<kind>-<timestamp>-<uuid>`，不覆盖旧文件；改模型前自动备份，
  备份名带微秒+序号，**同一秒内的两次修改不会互相覆盖**。
- `simulate` 编译前静态扫描 `` `include `` / `$fopen` / `$readmemh` / `$writememh` / `$dumpfile`：
  字面量路径逃出工作区直接拒绝，且不启动编译器；`include` 会**递归解析**（最多 8 层，防环），
  被包含文件里的越界字面量同样拦下。
- 严格模式（默认 `strict_path_scan=true`）下，无法静态判定的路径参数（`$readmemh(PATH)`、
  `$fopen(name)`）也计入 `security.rejected`，`simulate` 返回 `stage="security_scan"` 且不编译；
  确实需要动态路径时显式设 `ROBEI_MCP_STRICT_PATHS=0`，此时它们只进 `unresolved`。
- 这条静态扫描是**工具链输入检查**，不是 OS 沙箱：它约束的是"我的编译器去读哪个文件"，
  不限制同机其他进程。
- 子进程有超时（默认 30s，上限 120s）、`TMP/TEMP` 指向运行目录；XML 解析拒绝 DOCTYPE/ENTITY。
- Robei 安装目录只读；不写系统设置、不写注册表。

## 已知限制（摘要）

- **打开 Robei 的 Wave 面板会让它的 UIA 树塌掉**（控件数变 0，新进程也读不到），只能重启 IDE。
  所以波形一律走 `read_waveform` / `render_waveform`，不用 Robei 的 Wave。
- GUI 只做到「打开、编译、读 Output、综合、保存」；**在已有画布上拖拽/改连线做不到**
  （右键菜单、ToolBox 拖拽、坐标点击三条路都验证失败），但可以用 `create_robei_canvas_model`
  直接生成画布级 XML。
- FPGA 后端可以**不开 IDE 直接出 `.bit`**（`synthesize_project` → `build_bitstream`），但
  **GUI 侧的 Route/Package 无法复现**：IDE 打开工程会把 `.const` 根节点的 `Family` / `Device`
  清空，Route 只报 `has no Family/Device configured` 后去找 `chipdb/-1.bin`。同一份 json 用
  同一套二进制跑出的 `.fasm` 与 IDE 侧逐字节一致（md5 相同），所以差异只在器件配置的来源，
  不在工具链。
- Verilator 5.036 对"声明了但既不用也不驱动"的 net 不报 `UNUSEDSIGNAL`，这一项由 Yosys
  `opt_clean` 前后网表 diff 兜底；Icarus 12.0 的 `-Wall` 不报未使用信号。
- Yosys 只做可综合性粗检，不做时序/面积签核；ERC 是教学级静态检查（不分析组合环）。
- `truth_table` 默认穷举上限 8 个**输入位**，可显式设为 1..12；更宽输入只接受有限抽样（合计输入最多 64 位，最多 4096 行）。
  参数未解析仍拒绝，不猜位宽；`generate_testbench` 仍只生成骨架，不理解设计语义。
- 非 ASCII 路径（`C:\项目\验证\a.v`）在 Windows 上会先转成 8.3 短路径再交给
  `iverilog`/`vvp`/`yosys`/`verilator`；如果该盘关了 8.3 生成且路径又真的不可表示，
  会返回 `unsupported_path` 而不是让工具链报一堆乱码。
- 核心工具在 Linux/macOS 上可用（有 iverilog 即可），GUI 与 Robei 版本读取是 Windows 专属。

完整清单（含每条限制的原因和实测证据）见 [docs/verification-log.md](docs/verification-log.md)。

## 常见问题

**看到 `Transport closed`，先查什么？**

远端（SSH）使用时先在本机运行 `bash tools/mcp_health.sh`：通过 ssh 只读显示最近 20 条生命周期事件、
`CIM` 读取进程身份（ExecutablePath + CommandLine），最后给出 `alive` / `stale_pid` / `exited:<原因>` / `never_started`。
日志为 `%LOCALAPPDATA%\robei-mcp\lifecycle.log`（JSON Lines，UTC 时间），超过 1 MB 轮转，
只保留 `.1` 一份；`.lock` 是写入互斥锁文件。`runtime_status.lifecycle` 返回日志路径、最近 5 条、
本进程 PID 和上一个进程的已观测退出信息。强杀不会运行 Python 的 `atexit`；若连等待进程也被杀，
正常退出和 SSH 断开都统一记为 `stdin_closed`，health 显示“客户端断开”；若外层 cmd 与 Python 同时被强杀而没有 `exit`，health 显示“未观察到终止”。不能从缺失记录推断确切退出时间或操作者。不会自动重启。

**改了代码，为什么工具还是旧行为？**

常驻的 MCP 服务是客户端启动会话时拉起来的，**不会自动重载**：改完 `robei_mcp/*.py`
要重开 Codex（或重启这个服务）才会生效。不重开也能确认自己用的是哪份代码——调
`runtime_status` 看 `git_commit`（当前源码目录的 HEAD）和 `stale`：`stale=true` 表示
源码比进程新，这时每次工具调用的返回里都会多一条 `warnings` 提醒你重启。

**为什么 GUI 工具在嵌套的 stdio 服务里报 `OSError: Invalid argument`（Errno 22）？**

GUI 工具必须落在登录用户的交互桌面上，正式路径是
「stdio 服务 → 计划任务 `RobeiMCP_GuiAgent` → Session 1」，返回里的 `transport` 是 `"schtasks"`。
如果自己再套一层 stdio 服务（比如调试脚本里用 `mcp.ClientSession` 启动 `server.py` 之后再调
GUI 工具），UIA 拿不到桌面就会报 `Errno 22`。**不要在嵌套 stdio 服务里驱动 GUI 工具**：
要么用正常配置的 MCP 客户端直连，要么在 Session 1 里跑。

任务侧的注册与删除见上面「GUI 部分的前提」。

## 文档

| 文件 | 内容 |
|---|---|
| [docs/tool-reference.md](docs/tool-reference.md) | 54 个工具的完整参数、返回值、真实示例 |
| [docs/gui-tools.md](docs/gui-tools.md) | GUI 工具、Session-1 桥、坐标/菜单/画布的历史坑 |
| [docs/L1-uia-bridge.md](docs/L1-uia-bridge.md) | Robei 内建 UIA 桥的层级与控件 id |
| [docs/verification-log.md](docs/verification-log.md) | 每个阶段「实际跑通了什么」的证据日志 |
| [docs/coverage-roadmap.md](docs/coverage-roadmap.md) | Robei 能力覆盖矩阵、缺口清单与补齐路线（含未接的 Route/Package/Download） |
| [docs/repo-layout.md](docs/repo-layout.md) | 仓库结构 |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 参与开发、测试约定 |
| [SECURITY.md](SECURITY.md) | 安全问题报告方式 |

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q            # 全量（Windows + Robei 环境）
.\.venv\Scripts\python.exe -m pytest tests\test_regressions.py -q   # 1.0.2 的解析/波形/安全回归
.\.venv\Scripts\python.exe tests\stage3_cases.py   # 核心：警告/语法错误/超时/残留进程
.\.venv\Scripts\python.exe tests\stage21_packaging.py   # 打包与 GUI 可选面
```

各 stage 脚本对应一个功能域，能单独跑，需要 GUI 的会自己启动/关闭 Robei。pytest 标记说明见 `pyproject.toml`。

## License

[MIT](LICENSE)
