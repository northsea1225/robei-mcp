# Robei MCP Server

> An MCP server that turns Robei EDA into AI-callable tools: Verilog lint, simulation,
> waveform analysis, project XML, and optional GUI automation. Docs are in Chinese.

把 Robei EDA 变成 AI 能直接调用的工具集：Verilog 检查、仿真、波形、工程文件读写，
以及（可选）驱动 Robei 本体 GUI。基于 MCP（Model Context Protocol）stdio 传输，
可在 Codex / Claude Code 里直接用自然语言完成「写代码 → 检查 → 仿真 → 看波形」。

- **38 个工具**，其中 28 个不需要 Robei、不需要桌面：只要有 `iverilog`/`vvp`/`yosys` 就能跑。
- **不碰安装目录**：`E:\Robei` 全程只读，所有产物写在 `workspace/`。
- **可回滚**：每次运行独立目录，默认不覆盖已有文件，改模型前先备份。
- **不执行任意 shell**：固定工具白名单 + `shell=False` + 路径白名单。

## 快速开始

### 1. 前置条件

| 需要 | 说明 |
|---|---|
| Python 3.10+ | 开发机用 3.12.10 |
| Robei EDA | 自带 `iverilog` / `vvp` / `yosys`；也可用独立安装的 Icarus |
| （可选）Verilator | `--lint-only` 那一路，缺了会自动跳过 |
| （可选）Windows 交互桌面 | 只有 10 个 `robei_gui_*` 工具需要 |

### 2. 安装

```powershell
git clone <REPO_URL> C:\dev\robei-mcp
cd C:\dev\robei-mcp
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

或者用自带引导脚本（只补缺的，幂等，可先 `--check` 只看不动）：

```powershell
python C:\dev\robei-mcp\tools\setup.py --check
python C:\dev\robei-mcp\tools\setup.py --apply
```

依赖只有两个：`mcp==2.3.0`、`matplotlib`（画波形 PNG 用）。

### 3. 自检

```powershell
.\.venv\Scripts\python.exe -c "from robei_mcp import tools; import json; print(json.dumps(tools.runtime_status(), indent=1))"
```

看 `ok: true` 和 `tools.*.exists` 全为 `true` 就齐了。缺什么，`robei_doctor` 会逐项给修复提示。

### 4. 接到客户端

**Windows 本地 Codex**（`~/.codex/config.toml`）：

```toml
[mcp_servers.robei]
command = 'C:\dev\robei-mcp\.venv\Scripts\python.exe'
args = ['C:\dev\robei-mcp\server.py']
startup_timeout_sec = 20
tool_timeout_sec = 120
```

**Windows 本地 Claude Code**：

```powershell
claude mcp add --scope user robei -- "C:\dev\robei-mcp\.venv\Scripts\python.exe" "C:\dev\robei-mcp\server.py"
```

**Mac 通过 SSH 用远端 stdio**（不需要在 Windows 上开端口）：

```toml
[mcp_servers.robei_remote]
command = "ssh"
args = ["my-win", "MSYS_NO_PATHCONV=1 /c/Windows/System32/cmd.exe /d /c C:/dev/robei-mcp/run-mcp.cmd"]
startup_timeout_sec = 30
tool_timeout_sec = 120
```

`run-mcp.cmd` 只做三件事：切 UTF-8、`cd` 到仓库、用 venv 的 python 起 `server.py`。
已验证 stdout 只有 JSON-RPC，没有 banner 污染。

> Codex 的 app-server 是长驻进程，改完配置要重启会话/应用；`codex mcp list` 只说明配置存在。

### 5. 三十秒验证

在客户端里依次调用：

```text
runtime_status                 → ok=true，四个工具的 exists 都是 true
lint_verilog(["workspace/counter4/counter4.v"], top="counter4")
simulate(["workspace/counter4/counter4.v", "workspace/counter4/counter4_tb.v"], top="counter4_tb")
read_waveform(<上一步的 vcd_path>, signals=["counter4_tb.dut.count"], time=45)
```

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
| `ROBEI_MCP_GUI` | `0` 强制关闭 GUI 面（工具从 `tools/list` 消失，只剩 28 个） |
| `ROBEI_DEFAULT_TIMEOUT_S` / `ROBEI_MAX_TIMEOUT_S` | 子进程超时，默认 30 / 120 秒 |

## 工具一览

完整参数与真实返回示例见 [docs/tool-reference.md](docs/tool-reference.md)。

**诊断（2）** — `runtime_status`（路径/版本/配置来源）、`robei_doctor`（全栈自检，`deep=true` 会真跑一个 tb）

**检查与仿真（3）** — `lint_verilog`（iverilog `-Wall` + Verilator + Yosys 三路）、`simulate`（编译+跑 tb）、`simulate_project`（无头跑 Robei 的 `test/*.test`）

**波形（6）** — `read_waveform`（列信号/按时刻取值）、`find_events`（`count == 4'b1111` 首次出现）、`compare_waveforms`（两份 VCD 逐信号找第一处分歧）、`waveform_stats`（跳变数/翻转率/时钟周期估计）、`expect_waveform`（断言取值）、`render_waveform`（画 PNG）

**工程与模型（11）** — `read_robei_model`、`list_projects`、`read_robei_project`（含画布 instances/wires）、`create_robei_project`、`create_robei_model`、`set_robei_port`、`export_robei_verilog`、`sync_robei_verilog`、`create_robei_test`、`create_robei_canvas_model`（画布级 XML）、`import_verilog_canvas`（Verilog → 画布）

**辅助（3）** — `generate_testbench`（时钟/复位/VCD 骨架）、`check_connectivity`（ERC）、`truth_table`（组合逻辑穷举真值表）

**引脚约束（3）** — `create_robei_const`、`read_robei_const`、`export_robei_xdc`

**GUI（10，需 Windows 桌面）** — `robei_gui_status`（`probe=true` 顺带查 UIA 桥是否还活着）、`launch`、`open_project`、`open_model`、`compile`、`output`、`save_project`、`synthesis`、`compile_all`、`close`

## GUI 部分的前提

`robei_gui_*` 要走 Windows 的**交互桌面**：SSH 登录的会话（Session 0）碰不到桌面，
所以这些工具通过一个计划任务 `RobeiMCP_GuiAgent` 把请求投递到登录用户所在的 Session 1。

```powershell
python C:\dev\robei-mcp\tools\gui\register_task.py            # 创建/刷新任务
python C:\dev\robei-mcp\tools\gui\register_task.py --remove   # 删除
```

三个要知道的事实：

1. **必须有人登录在桌面上**。机器停在登录界面（`LogonUI.exe` 在跑、会话状态 `Connected`）时，
   Windows 会拒绝合成鼠标输入，所有点击类工具都会报
   `There is no active desktop required for moving mouse cursor!`。
2. **GUI 工具是可选面**：非 Windows 或驱动缺失时它们根本不会出现在 `tools/list`，
   不会"调用了才报错"。`ROBEI_MCP_GUI=0` 可以手动关掉。
3. 细节、限制和历史坑见 [docs/gui-tools.md](docs/gui-tools.md)。

## 安全边界

- 固定工具白名单，`shell=False`，不执行任意命令。
- 源码路径必须在 `workspace/` 内；模型读取只允许 `model_roots` 里的目录（默认 `C:\ProgramData\Robei` + 工作区）。
- 每次运行独立目录 `build/<kind>-<timestamp>-<uuid>`，不覆盖旧文件；改模型前自动备份。
- `simulate` 编译前静态扫描 `` `include `` / `$fopen` / `$readmemh` / `$writememh` / `$dumpfile`：
  字面量路径逃出工作区直接拒绝，且不启动编译器；非字面量列进 `unresolved`，不做猜测性放行。
- 子进程有超时（默认 30s，上限 120s）、输出有长度上限、`TMP/TEMP` 指向运行目录。
- `E:\Robei` 只读；不写系统设置、不写注册表。

## 已知限制（摘要）

- **打开 Robei 的 Wave 面板会让它的 UIA 树塌掉**（控件数变 0，新进程也读不到），只能重启 IDE。
  所以波形一律走 `read_waveform` / `render_waveform`，不用 Robei 的 Wave。
- GUI 只做到「打开、编译、读 Output、综合、保存」；**在已有画布上拖拽/改连线做不到**
  （右键菜单、ToolBox 拖拽、坐标点击三条路都验证失败），但可以用 `create_robei_canvas_model`
  直接生成画布级 XML。
- `Build→Route` / `Build→Package` 在这台机器上走不到底（器件库缺对应引脚、prjxray 目录为空），
  FPGA 后端只到「生成约束 + 综合出网表」。
- Verilator 5.036 对"声明了但既不用也不驱动"的 net 不报 `UNUSEDSIGNAL`，这一项由 Yosys
  `opt_clean` 前后网表 diff 兜底；Icarus 12.0 的 `-Wall` 不报未使用信号。
- Yosys 只做可综合性粗检，不做时序/面积签核；ERC 是教学级静态检查（不分析组合环）。
- `truth_table` 上限 8 个输入；`generate_testbench` 只生成骨架，不理解设计语义。
- 核心工具在 Linux/macOS 上可用（有 iverilog 即可），GUI 与 Robei 版本读取是 Windows 专属。

完整清单（含每条限制的原因和实测证据）见 [docs/verification-log.md](docs/verification-log.md)。

## 文档

| 文件 | 内容 |
|---|---|
| [docs/tool-reference.md](docs/tool-reference.md) | 38 个工具的完整参数、返回值、真实示例 |
| [docs/gui-tools.md](docs/gui-tools.md) | GUI 工具、Session-1 桥、坐标/菜单/画布的历史坑 |
| [docs/L1-uia-bridge.md](docs/L1-uia-bridge.md) | Robei 内建 UIA 桥的层级与控件 id |
| [docs/verification-log.md](docs/verification-log.md) | 每个阶段「实际跑通了什么」的证据日志 |
| [docs/repo-layout.md](docs/repo-layout.md) | 仓库结构 |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更 |

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q            # 单元测试（不需要 Robei）
.\.venv\Scripts\python.exe tests\stage3_cases.py   # 核心：警告/语法错误/超时/残留进程
.\.venv\Scripts\python.exe tests\stage21_packaging.py   # 打包与 GUI 可选面
```

各 stage 脚本对应一个功能域，能单独跑，需要 GUI 的会自己启动/关闭 Robei。

## License

[MIT](LICENSE)
