# Changelog

版本号对应 `pyproject.toml` 里的 `version`。工具数量指 `tools/list` 返回的数量（Windows + GUI 可用时）。

## 0.11.1 — 2026-10-23

**打包修复：GUI 驱动进 wheel，工具面按可用性注册。**

- 修：0.11.0 的 wheel 只有 24 个文件、完全没有 `tools/gui`，`pip install` 后 10 个 GUI 工具必然报"找不到脚本"。
- GUI 驱动搬进包内：`robei_mcp/gui_agent/{ui,driver,job,launcher,paths}.py`。
- 控制文件（job / args / lock）从安装目录移到 `workspace/gui_agent/`。
- `tools/gui/` 下四个同名文件变 shim，已注册的计划任务和历史探针不用改。
- GUI 工具改由 `mcp.add_tool` 按 `gui.available()` 注册：不可用时**不出现在 `tools/list`**，而不是调用了才报错。
- 新增 `ROBEI_MCP_GUI=0`，Windows 上也能只留 28 个文件级工具。
- 装成 wheel 时 workspace 默认落在 `%LOCALAPPDATA%\robei-mcp\workspace`（不再默认 `site-packages\workspace`）。
- 新增 `tests/stage21_packaging.py`（13 项，不需要桌面）与 `tools/gui/probe_{desktop,input}.py`。
- 修：`stage20` 的 monkeypatch 打在 shim 上导致 3 项单测失败；`robei_ui` shim 丢私有名导致 `_wait_for` 不可见。

## 0.11.0 — 2026-10-23

**精简工具面：47 → 38。**

移除 9 个"只有本机/课堂场景才用得上"的工具，每个都有替代路径：

| 移除 | 替代 |
|---|---|
| `report_lab` | `simulate_project` + `read_waveform` + `render_waveform` |
| `robei_gui_experiment` | 组合 `open_project` → `open_model` → `compile` |
| `robei_gui_run` | `simulate_project`（同一套 iverilog/vvp，无需桌面） |
| `robei_gui_wave` / `robei_gui_wave_load` | `render_waveform`（打开 Wave 会让 UIA 树塌掉） |
| `robei_gui_code_read` / `robei_gui_code_write` | `read_robei_model` / `create_robei_model` |
| `robei_gui_refresh` | `open_project` 已自动重载 |
| `robei_gui_health` | `robei_gui_status(probe=true)` |

- 配置：`robei_home` 与 `iverilog`/`vvp`/`yosys` 改为自动发现（PATH、`%ProgramFiles%\Robei`、`<盘符>:\Robei`、msys64），不再假定 `E:\Robei`。
- `robei.toml.example` 改为全占位符、整份可选。
- `runtime_status` 增加 `robei_home` 与 `config.discovered`。

## 0.10.0

**GUI 可靠性。**

- Output 等待改为「标记 + 静止」双条件，不再靠固定 sleep。
- `compile_all` 遇到 `unknown_state` 会重试。
- 窗口焦点获取失败时重试。

## 0.9.0

**FPGA 约束闭环（后半段）。**

- 发现 Robei 在 **Compile** 阶段自己导出 `build/<name>.xdc`；新增抓取并修复该文件的能力
  （原文件 `IOSTANDARD` 是空值，nextpnr/Vivado 会报 `expected four arguments to 'set_property'`）。
- `export_robei_xdc(also_build=true)` 把修复后的版本写回 `build/`。

## 0.8.0

**FPGA 前端 + 工程级综合（47 个工具）。**

- `robei_gui_save_project`（综合前必须保存）、`robei_gui_synthesis`（等 `Synthesis complete`）。
- 记录后端边界：综合只产出 library-only 网表，Route/Package 因器件库与 prjxray 缺失走不到底。

## 0.7.0

**引脚约束（45 个工具）。**

- `create_robei_const` / `read_robei_const` / `export_robei_xdc`。
- `.const` 与 `.model`/`.test` 共用 `<Module>` 根，因此复用同一个解析器。

## 0.6.0

**P1（42 个工具）。**

- `import_verilog_canvas`（结构级 Verilog → 画布）、`check_connectivity`（ERC）、
  `truth_table`（穷举真值表）、`expect_waveform`（波形断言）、`waveform_stats`（跳变/时钟统计）。

## 0.5.0

**协议与输入加固。**

- 工具 annotations、稳定错误码（`code` / `hint` / `stage`）、进度上报与取消。
- XML 防护（拒绝 `<!ENTITY>` 与 DOCTYPE 内部子集）、`robei.toml` 配置层。

## 0.4.0（未单独发版）

- 画布级 `.model` 支持（37 个工具）：解析与生成 instances/wires，Robei 能编译、能仿真。

## 0.3.0

- `report_lab`（0.11 已移除）、`compare_waveforms`、操作日志、GUI 互斥锁、`pyproject.toml` + pytest。
- 跨平台配置与 `portable_core` CI（ubuntu + iverilog）。

## 0.2.0（未单独发版）

- `simulate_project`（34 个工具）：无头跑 Robei 的 `test/*.test`。
- `robei_doctor` + `tools/setup.py`；解析 `.const`。

## 0.1.0

- 第一版可用：`runtime_status` / `lint_verilog` / `simulate` / `read_waveform` /
  `read_robei_model` / `export_robei_verilog` / `generate_testbench`，以及 Session-1 GUI 桥。
