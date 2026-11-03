<div align="center">

# Robei MCP Server

**让 AI 助手直接操作 [Robei EDA](http://www.robei.com/) 工程：Verilog 检查与仿真、语义测试台、波形分析、实验报告，以及 yosys → 比特流的 FPGA 流程。**

[![CI](https://github.com/northsea1225/robei-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/northsea1225/robei-mcp/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/northsea1225/robei-mcp)](https://github.com/northsea1225/robei-mcp/releases)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![MCP](https://img.shields.io/badge/MCP-stdio-informational)

[快速开始](#快速开始) · [工具一览](#工具一览) · [配置](#配置) · [安全边界](#安全边界) · [常见问题](#常见问题与排错) · [文档](#文档) · [English](#english)

</div>

---

## 目录

- [为什么用它](#为什么用它)
- [快速开始](#快速开始)
- [安装详解](#安装详解)
- [工具一览](#工具一览)
- [配置](#配置)
- [GUI 部分的前提](#gui-部分的前提)
- [安全边界](#安全边界)
- [已知限制](#已知限制)
- [常见问题与排错](#常见问题与排错)
- [文档](#文档)
- [开发与测试](#开发与测试)
- [参与贡献](#参与贡献)
- [许可证](#许可证)
- [English](#english)

## 为什么用它

Codex、Claude Code 等任何 MCP 客户端都可以通过 stdio 连上这个服务，把「写 Verilog → 仿真 → 看波形 → 写报告 → 出比特流」交给 AI 助手一步步做，每一步都有结构化的返回值和证据可查。

- **54 个工具**：42 个文件级工具（只需 `iverilog` / `vvp` / `yosys`，Linux / macOS / Windows 均可）+ 12 个可选 GUI 工具（仅 Windows，需 `[gui]` 额外依赖和 Robei 本体）。
- **读写 Robei 工程**：`.proj` / `.model` / `.const` 的读取、创建、改端口、改参数、改画布、导出 Verilog，改之前先备份。
- **检查与仿真**：iverilog `-Wall` + Verilator + Yosys 三路 lint；`simulate` 编译并运行测试台，返回 VCD 路径。
- **语义测试台**：`create_testbench` 用表达式白名单生成组合 / 时序测试台，不用 `eval`；`truth_table` 穷举真值表。
- **波形分析**：按时刻取值、找事件、逐信号比对两份 VCD、断言、统计、渲染 PNG、导出时间窗。
- **实验报告**：`build_report` 生成只含事实的 Markdown 报告和逐行可定位的 `evidence.jsonl`。
- **FPGA 流程**：`synthesize_project`（资源用量）→ `build_bitstream`（Roplace → fasm2frames → xc7frames2bit，直出 `.bit`）。Download 恒为 dry-run，**不碰真机**。
- **默认安全**：固定工具白名单、`shell=False`、路径限定在 `workspace/`，详见[安全边界](#安全边界)。

## 快速开始

三步：装好 → 接到客户端 → 调用第一个工具。

### 1. 安装

```bash
git clone https://github.com/northsea1225/robei-mcp && cd robei-mcp
pip install -e .                 # 仅 Windows 且要 GUI 工具时：pip install -e ".[gui]"
```

也可以从 [Releases](https://github.com/northsea1225/robei-mcp/releases) 下载 wheel，再 `pip install` 该文件。

工具链：`iverilog`、`vvp`、`yosys` 在 `PATH` 中即可（Robei 自带的也行；Linux / macOS 用包管理器装独立的 Icarus + yosys）。Windows 的虚拟环境、`tools\setup.py` 引导脚本等见[安装详解](#安装详解)。

### 2. 接到 MCP 客户端

**Codex**（`~/.codex/config.toml`；`python` 要换成装了本包的那个解释器）：

```toml
[mcp_servers.robei]
command = "python"
args = ["-m", "robei_mcp"]
startup_timeout_sec = 20
tool_timeout_sec = 120
```

**Claude Code**：

```bash
claude mcp add --scope user robei -- python -m robei_mcp   # 在装了本包的 Python 环境里执行
```

装包后也有控制台脚本 `robei-mcp`，与 `python -m robei_mcp` 等价。Windows 本地、通过 SSH 的远端 stdio 等写法见[接到客户端](#4-接到客户端)；`examples/` 下有现成的配置模板。

> MCP 客户端通常是长驻进程，改完配置要重启会话 / 应用。

### 3. 第一次调用

在客户端里让助手依次调用（路径相对 `workspace/`，不要再写 `workspace/` 前缀）：

```text
runtime_status                          → ok=true，看各工具链的路径与版本
lint_verilog(["counter4/counter4.v"], top="counter4")
simulate(["counter4/counter4.v", "counter4/counter4_tb.v"], top="counter4_tb")
read_waveform(<simulate 返回的 vcd_rel_paths[0]>, signals=["counter4_tb.dut.count"], time=45)
```

`read_waveform` 在 t=45 时返回 `counter4_tb.dut.count` 的值 `0100`（即 4）。缺什么工具，`robei_doctor` 会逐项给出修复提示。

## 安装详解

### 前置条件

| 需要 | 说明 |
|---|---|
| Python 3.10+ | 开发与 CI 使用 3.12 |
| `iverilog` / `vvp` / `yosys` | Robei 自带；也可用独立安装的 Icarus + yosys（Linux/macOS 用包管理器装即可） |
| Robei EDA | 仅 Robei 相关能力（FPGA 器件库、GUI 工具）需要 |
| （可选）Verilator | `--lint-only` 那一路，缺了会自动跳过 |
| （可选）Windows 交互桌面 | 只有 12 个 GUI 工具需要 |

### 1. 从源码安装（Windows 示例）

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

### 2. 自检

```powershell
.\.venv\Scripts\python.exe -c "from robei_mcp import tools; import json; print(json.dumps(tools.runtime_status(), indent=1))"
```

看 `ok: true` 和 `tools.*.exists` 全为 `true` 就齐了。缺什么，`robei_doctor` 会逐项给修复提示。

### 3. 路径约定

文件参数一律**相对 workspace**（`counter4/counter4.v`）。写成 `workspace/counter4/counter4.v`
也认，不会因此多套一层目录。

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

## 工具一览

按类别分组，共 **54 个**（文件级 42 + GUI 12）。完整参数与真实返回示例见 [docs/tool-reference.md](docs/tool-reference.md)。

| 类别 | 数量 | 工具 | 需要 Robei | 需要桌面 |
|---|:-:|---|:-:|:-:|
| 诊断 | 2 | `runtime_status` `robei_doctor` | 否 | 否 |
| 检查与仿真 | 6 | `lint_verilog` `simulate` `simulate_project` `run_all_tests` `read_run_log` `check_connectivity` | 否 | 否 |
| 波形分析 | 7 | `read_waveform` `find_events` `compare_waveforms` `waveform_stats` `expect_waveform` `render_waveform` `export_vcd_window` | 否 | 否 |
| 工程与模型 | 16 | `list_projects` `read_robei_project` `read_robei_model` `create_robei_project` `create_robei_model` `set_robei_port` `set_robei_code` `set_robei_parameter` `edit_robei_canvas` `create_robei_canvas_model` `import_verilog_canvas` `manage_robei_model` `export_robei_verilog` `sync_robei_verilog` `list_library_models` `import_library_model` | 否 | 否 |
| 测试台与报告 | 5 | `generate_testbench` `create_testbench` `create_robei_test` `truth_table` `build_report` | 否 | 否 |
| 引脚约束 | 3 | `create_robei_const` `read_robei_const` `export_robei_xdc` | 否 | 否 |
| FPGA | 3 | `list_fpga_devices` `synthesize_project` `build_bitstream` | 否（需 FPGA 工具链） | 否 |
| GUI 自动化（可选） | 12 | `gui_doctor` `robei_gui_status` `robei_gui_launch` `robei_gui_open_project` `robei_gui_open_model` `robei_gui_compile` `robei_gui_output` `robei_gui_save_project` `robei_gui_synthesis` `robei_gui_close` `robei_gui_compile_all` `robei_gui_screenshot` | 是 | 是（Windows） |

<details>
<summary><b>诊断（2）与检查、仿真（6）</b></summary>

| 工具 | 作用 |
|---|---|
| `runtime_status` | 路径 / 版本 / 配置来源 / 源码是否过期（`stale`、`git_commit`）/ 生命周期日志 |
| `robei_doctor` | 全栈自检；`deep=true` 会真跑一个测试台 |
| `lint_verilog` | iverilog `-Wall` + Verilator + Yosys 三路（Verilator ≥5.0 自动带 `--timing`） |
| `simulate` | 编译并运行测试台，返回输出和 VCD 路径 |
| `simulate_project` | 无头运行 Robei 工程里的 `test/*.test`（不开 IDE、不要桌面） |
| `run_all_tests` | 一次跑完工程里选中的 test 并汇总 |
| `read_run_log` | 只读打开 `build/`、`logs/` 下的运行日志，默认尾部 200 行、上限 500 KB |
| `check_connectivity` | 静态 ERC：多驱动、未驱动网、位宽不匹配、未知模块、悬空端口 |

</details>

<details>
<summary><b>波形分析（7）</b></summary>

| 工具 | 作用 |
|---|---|
| `read_waveform` | 列信号 / 按时刻取值 |
| `find_events` | 找信号进入某条件的时刻，如 `count == 4'b1111` 首次出现 |
| `compare_waveforms` | 两份 VCD 逐信号比对，任何结构性差异都算不通过 |
| `waveform_stats` | 跳变数、电平覆盖、X/Z 标记、时钟周期 / 频率估计 |
| `expect_waveform` | 断言取值（某时刻值，或某区间内保持） |
| `render_waveform` | 画 PNG |
| `export_vcd_window` | 把一个有界时间窗导出为 CSV 或 Markdown |

</details>

<details>
<summary><b>工程与模型（16）</b></summary>

| 工具 | 作用 |
|---|---|
| `list_projects` | 列出工作区里所有 Robei 工程及其模型、测试、Verilog、VCD |
| `read_robei_project` | 只读 `.proj`（含画布 instances / wires） |
| `read_robei_model` | 读 `.model`：元数据、端口、参数、Verilog 代码 |
| `create_robei_project` | 在工作区创建工程骨架 |
| `create_robei_model` | 创建带参数、端口、代码的 `.model` 并注册 |
| `set_robei_port` | 增加 / 更新一个端口（备份 + XML 校验） |
| `set_robei_code` | 替换行为级模型的 Code（含画布连线的模型会被拒绝） |
| `set_robei_parameter` | 改一个根参数的默认值并重算受影响的端口位宽 |
| `edit_robei_canvas` | 原子地编辑画布模型（含 `move_instance`、`rename_port`），支持 `dry_run` |
| `create_robei_canvas_model` | 直接生成画布级 XML（实例 + 连线） |
| `import_verilog_canvas` | Verilog → 画布：结构化源码导入，行为级源码返回建议 |
| `manage_robei_model` | 重命名 / 删除 / 复制模型，带引用检查和回滚 |
| `export_robei_verilog` | 由 `.model` 导出完整模块并 lint |
| `sync_robei_verilog` | 把工程所有模型导出到 `<proj>/verilog/<model>.v` |
| `list_library_models` | 只读列出已安装的 Robei ToolBox 模型（含 iverilog lint） |
| `import_library_model` | 把 ToolBox 模型复制进工程并注册（先备份、再 lint） |

</details>

<details>
<summary><b>测试台与报告（5）、引脚约束（3）、FPGA（3）</b></summary>

| 工具 | 作用 |
|---|---|
| `generate_testbench` | 旧的时钟 / 复位 / VCD 骨架；`full_combo` 枚举全部输入组合（只生成骨架，不理解设计语义） |
| `create_testbench` | 组合 / 时序语义测试台，期望值走表达式白名单 |
| `create_robei_test` | 创建 `test/<name>.test` 并注册到 `.proj` |
| `truth_table` | 组合逻辑穷举真值表（`max_inputs` 按**输入总位数**计）；更宽输入只接受有限抽样 |
| `build_report` | 只含事实的 Markdown 实验报告 + `evidence.jsonl` |
| `create_robei_const` | 写引脚约束（拒绝同一引脚被两个端口 / 两位重复占用） |
| `read_robei_const` | 读 `.const`：端口 → 位 → FPGA 引脚映射 |
| `export_robei_xdc` | `.const` → `<proj>/constrain/<name>.xdc`（默认标准 Tcl 写法 `[get_ports {a[0]}]`，`quote_bus=false` 回到 Robei legacy 写法） |
| `list_fpga_devices` | 器件表：有没有 chipdb、有没有 part.yaml、能不能出 `.bit` |
| `synthesize_project` | yosys `synth_xilinx` + LUT / FF / BRAM / DSP / IO 用量 |
| `build_bitstream` | Roplace → fasm2frames → xc7frames2bit，直出 `.bit`；Download 只生成 dry-run 计划 |

可跑的示例工程见 `examples/fpga_demo/`（4 位 `light` + fgg484 引脚 `.const`，`build_bitstream` 开箱即用）。

</details>

<details>
<summary><b>GUI 自动化（12，需 Windows 桌面）</b></summary>

| 工具 | 作用 |
|---|---|
| `gui_doctor` | Session-1 桌面前置检查（只读）；`allow_battery=true` 允许在电量 ≥20% 的电池上跑 GUI 阶段，默认 `false` |
| `robei_gui_status` | Robei 是否在运行、当前工程、已打开模型、Output 尾部；`probe=true` 顺带查 UIA 桥是否还活着 |
| `robei_gui_launch` | 启动 Robei |
| `robei_gui_open_project` / `robei_gui_open_model` | 打开工程 / 模型 |
| `robei_gui_compile` / `robei_gui_compile_all` | 编译当前模型 / 批量编译 |
| `robei_gui_output` | 读 Output 面板 |
| `robei_gui_save_project` | 保存工程 |
| `robei_gui_synthesis` | 触发综合 |
| `robei_gui_screenshot` | 截图 |
| `robei_gui_close` | 关闭 Robei |

前提与限制见[GUI 部分的前提](#gui-部分的前提)和 [docs/gui-tools.md](docs/gui-tools.md)。

</details>

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
| `ROBEI_MCP_CONFIG` | 指向另一份 `robei.toml`（不放在仓库根时用） |
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

## 安全边界

| 保证 | 说明 |
|---|---|
| 不执行任意 shell | 只调用固定白名单的 `iverilog` / `vvp` / `yosys` 等程序，`shell=False`，无 `eval`/`exec` |
| 不改 Robei 安装目录、不写注册表 | 安装目录只读；所有产物写在 `workspace/` |
| 子进程受控 | 超时杀整个进程树；输出有长度上限（超出标记 `output_truncated`）；XML 解析拒绝 DOCTYPE/ENTITY（含 UTF-16/32 编码） |
| 路径白名单 | 读写路径必须落在 workspace / 允许的模型根内；仿真前对 `$fopen` `$readmemh` `` `include `` 等做静态路径扫描，相对路径按仿真运行目录（真实 cwd）解析 |
| 不接触真机 | `build_bitstream` 的 Download 恒为 dry-run，`dry_run=false` 无条件拒绝 |
| 可回滚 | 每次运行独立目录；默认不覆盖已有文件；改模型前先备份 |

> 语义自检失败时进程返回码仍为 0，请只看返回值里的 `ok` / `run.code`。

> **升级注意（1.0.2 起）**：`simulate` 默认开启**严格路径扫描**。像
> `$readmemh(PATH, mem)` 这种无法静态判定路径的写法，以前能跑，现在会直接返回
> `stage="security_scan"` 并拒绝编译。确实需要动态路径时设
> `ROBEI_MCP_STRICT_PATHS=0`（或在 `robei.toml` 里写 `[security] strict_path_scan = false`）。

<details>
<summary><b>安全边界（详细）</b></summary>

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
- 子进程有超时（默认 30s，上限 120s）、输出有长度上限（超出截断并标记 `output_truncated`）、超时会杀掉整个进程树、`TMP/TEMP` 指向运行目录；XML 解析拒绝 DOCTYPE/ENTITY（含 UTF-16/32 编码声明）。
- Robei 安装目录只读；不写系统设置、不写注册表。

</details>

发现安全问题请按 [SECURITY.md](SECURITY.md) 私下报告，不要在公开 Issue 里贴可利用的细节。

## 已知限制

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

## 常见问题与排错

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
| [CHANGELOG.md](CHANGELOG.md) | 版本变更（当前版本 **1.12.0**） |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 参与开发、测试约定 |
| [SECURITY.md](SECURITY.md) | 安全问题报告方式 |

## 开发与测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q            # 全量（Windows + Robei 环境）
.\.venv\Scripts\python.exe -m pytest -q -m "not needs_robei and not needs_windows"   # CI 子集：只需 iverilog + yosys
.\.venv\Scripts\python.exe -m pytest tests\test_regressions.py -q   # 1.0.2 的解析/波形/安全回归
.\.venv\Scripts\python.exe tests\stage3_cases.py   # 核心：警告/语法错误/超时/残留进程
.\.venv\Scripts\python.exe tests\stage21_packaging.py   # 打包与 GUI 可选面
```

各 stage 脚本对应一个功能域，能单独跑，需要 GUI 的会自己启动/关闭 Robei。pytest 标记说明见 `pyproject.toml`。

## 参与贡献

欢迎 Issue 和 PR：先读 [CONTRIBUTING.md](CONTRIBUTING.md)（环境、测试约定、夹具只读规则），提交时会用到仓库里的 Issue / PR 模板。

## 许可证

[MIT](LICENSE)

<a name="english"></a>
## English

An MCP (stdio) server that lets AI assistants drive **Robei EDA** projects: read/write `.model`/`.proj`,
lint & simulate Verilog (Icarus), generate semantic testbenches, analyse VCD waveforms, build lab reports,
and run the yosys → place/route → bitstream flow (Download is dry-run only; no hardware is touched).
54 tools — 42 portable file-level tools (need only `iverilog`/`vvp`/`yosys`) + 12 optional Windows GUI tools.
The detailed docs are in Chinese; tool names and JSON fields are in English.

**Quick start**

```bash
git clone https://github.com/northsea1225/robei-mcp && cd robei-mcp
pip install -e .                       # add the [gui] extra on Windows for the 12 GUI tools
claude mcp add --scope user robei -- python -m robei_mcp
```

For Codex add this to `~/.codex/config.toml`:

```toml
[mcp_servers.robei]
command = "python"
args = ["-m", "robei_mcp"]
```

Put `iverilog`, `vvp` and `yosys` on `PATH`, then call `runtime_status` and `robei_doctor` to verify the setup.
See [docs/tool-reference.md](docs/tool-reference.md) for every tool, [SECURITY.md](SECURITY.md) for reporting
vulnerabilities, [CONTRIBUTING.md](CONTRIBUTING.md) to contribute, and the [MIT License](LICENSE).
