# Changelog

版本号对应 `pyproject.toml` 里的 `version`。工具数量指 `tools/list` 返回的数量（Windows + GUI 可用时）。

## 1.0.2 — 2026-10-24

**一轮完整的 QA 回归：38 个工具逐个打，修正 18 处行为。** 工具面不变（仍是 38 个），
变的是"错了会报错"和"能跑通"的边界。本地 22 项回归测试 + Windows 实机 38 项端到端用例全过
（其中 3 项是 1.0.1 遗留的真 bug：`[4-1:0]` 位宽、`runtime_status` 版本字段、include 越界用例）。

### 解析与生成（会算错数的那一类）

- **ANSI 端口简写**：`input wire [3:0] a, b, c` 以前只认第一个名字，`b`/`c` 丢失；
  现在按 ANSI 规则继承方向/类型/位宽。
- **一行写完的 non-ANSI 头**：`module m(a,y); input a; output y;` 以前整段抓不到端口；
  现在按 `;`/换行边界解析，并限制在 `endmodule` 之前（多模块文件不再串端口）。
- **同一行多个实例**：`and2 u1(...); and2 u2(...);` 以前只取第一个，`import_verilog_canvas`
  出来的画布缺一半实例。
- **`[4-1:0]` 位宽**：Robei 的 `Datasize="4"` 以前导出成 `[4-1:0]`，自家 `width_of` 只当 1 位，
  于是 4 位输入的 `truth_table` 只出 2 行。现在导出直接折成 `[3:0]`，`width_of` 也能算
  `4-1`/`3+1`/`2*4-1` 这类纯数字表达式（`ast` 白名单，不用 `eval`）。
- **混合位宽真值表**：`[1:0] a` + `[3:0] b` 以前按"端口个数"移位，枚举顺序错乱；
  现在按位打包，最后一个端口占最低位。

### 波形

- `compare_waveforms` 的 `signals=` 分支以前直接 `NameError`（漏导入 `match_signals`）。
- 比对从"只报差异"改成**结构性差异也不放过**：缺信号、无共同信号、位宽不一致、
  `max_time` 不一致、timescale 不可解析，全部 `ok=false` 并写进 `reasons`；timescale 不同
  时按 fs 归一到共同时间轴再比。
- `parse_vcd` 只认真有 `$var` + `$enddefinitions` 的文件，空文件/随便一段文本报
  `invalid_argument`，不再返回"0 个信号"的假成功。
- `render_waveform` 补上最后一个值的保持段（以前曲线到最后一个跳变就断），默认文件名加
  `uuid8`，matplotlib 渲染串行化。

### 安全

- 静态路径扫描**递归跟 `` `include ``**（最多 8 层、visited 去重），被包含文件里的越界
  字面量同样拦下。
- 新增严格模式 `ROBEI_MCP_STRICT_PATHS`（默认开）：`$readmemh(PATH)`、`$fopen(name)`
  这类无法静态判定的路径也计入拒绝，`simulate` 返回 `stage="security_scan"` 且不编译。
  设 `0` 回到旧行为（只记 `unresolved`）。`runtime_status` 里能看到当前档位。

### 约束与 XDC

- `create_robei_const` 拒绝同一引脚被两个端口/两位占用（`G15` 给 `a[0]` 又给 `a[1]` 这种
  以前会安静写进 `.const`，到 Route 才炸）。
- `export_robei_xdc` 默认输出**标准 Tcl** `[get_ports {a[0]}]`；要喂回 Robei 自带 Route
  解析器时传 `quote_bus=false`，回到 `[get_ports a[0]]`。返回值多了 `tcl_quoting`。

### 备份与并发

- 备份名带微秒 + 序号，同一秒内连改两次不再互相覆盖（`BACKUP-UNIQUE` 用例守着）。
- `simulate_project`、`truth_table`、`render_waveform` 每次都进独立运行目录，同一工程/
  同一 top 并行跑不串文件。
- MCP 进度通知钳制成单调不减，结束时固定 `total/total`（以前会从 `2/2` 退回 `1/1`）。

### GUI 与安装

- GUI 依赖改成显式探测（`pywinauto`/`Pillow`/`pywin32` 缺一个就不注册那 10 个工具），
  并新增 `gui` extra：`pip install "robei-mcp[gui]"`；`tools/setup.py --apply` 会补装。
- `robei_gui_open_model` 在 10 模型工程里找 `const`：目录节点判定改成"无扩展名且未展开"，
  最多 10 轮展开，搜索顺序 `model/` → `test/` → `const/` → `verilog/`，树读取上限 400；
  `ok` 以 `active` 为准。
- **console entry 修好**：wheel 的 `robei-mcp = robei_mcp.__main__:main` 以前指向不存在的
  `main`，装完命令行直接 `ImportError`。`tests/check_wheel.py` 现在校验 entry_points、
  LICENSE 与 `__main__.py`。

### 非 ASCII 路径

- Windows 上含中文的路径先转 8.3 短路径再交给 `iverilog`/`vvp`/`yosys`/`verilator`；
  转不出来返回 `unsupported_path` 并说明原因，而不是让工具链吐乱码。

### 测试

- 新增 `tests/test_regressions.py`（22 项，本地与 Windows 全过），覆盖上面每一条。
- QA 侧的 `verify_1_0_2.py` 实机脚本（真实 stdio MCP + 真实 Robei GUI，38 个用例）连同
  `results.json` 留在 `test-results/qa_20261023122612/` 与 Windows 的
  `workspace/_qa_runs/qa_20261023122612/verify_1_0_2/`，可照原样重跑。

### 1.0.2 复审修复（2026-10-24 第二轮）

外部复审在真实 Windows 环境跑完 1.0.2 后提出的问题，逐条修掉：

- **`compare_waveforms` 的 `max_time` 现在按 fs 比较**：以前拿原始 tick 直接比，
  1ns 的 `#20` 与 1ps 的 `#20000` 明明等长却判 `ok:false`；返回里加了
  `max_time_fs: {golden, dut}`，diff 文案也带 fs 数值。
- **`signals=[...]` 点名了两边都不存在的信号**：以前 `match_signals` 抛 `KeyError`，
  现在返回 `ok:false` + `unknown_signals` + 原因；歧义信号单独进 `ambiguous_signals`。
- **新增 `allow_extra_signals`**（默认 `false`）：golden 对比"另一套层次结构"时只比共有信号，
  单边多出的内部信号记进 `missing_*` 但不判失败。
- **安全拒绝时顶层字段补齐**：`simulate` 被拒时顶层也有 `strict` / `rejected` /
  `unresolved` / `scanned_files`，不再只有 `security.*`；成功时 `rejected` 为空数组。
- **路径不再多套一层**：`workspace/proj/a.v` 与 `proj/a.v` 等价，
  `resolve_workspace_path` / `resolve_model_path` / `resolve_project_path` 都接受两种写法，
  新建路径也一样（不会出现 `workspace/workspace/...`）。
- **`const_eval` 改用 Verilog 整数语义**：`-1/2 == 0`（向零截断）、`-1%2 == -1`（余数随被除数），
  之前是 Python 的 floor 语义。
- **参数化位宽不再静默当 1 位**：`width_info("LEN-1:0")` 返回 `resolved:false`，
  `truth_table` / `generate_testbench` 直接报错并要求先导出具体位宽，而不是生成 2 行假真值表。
- **`tcl_quoting` 统一为 `"legacy"`**（文档一直这么写，代码之前返回 `"robei"`）。
- **`open_model` 的去重键**从每次都变的 `id(wrapper)` 改成 `(文本, 行矩形)`，展开循环真的会跳过已展开的目录。
- `tests/stage19_constraints.py` 的期望更新为新的标准 Tcl 写法，并新增"Robei 自己仍写 legacy 形式"
  的对照断言（25 → 26 项）。

## 1.0.1 — 2026-10-23

**测试不再改仓库里的夹具。**

跑完 `stage7` / `stage9` / `stage10` 之后 `git status` 会脏几条，因为这几个脚本直接在被跟踪的样例工程里
建模型、加端口、重建工程。1.0.1 把它们全部挪到临时工程：

| 脚本 | 以前写这里 | 现在写这里 |
|---|---|---|
| `stage7_model_ports` | `robei_samples/port_probe/model/blank.model` | `workspace/port_scratch/`（自己用 `create_robei_project` + `create_robei_model` 建） |
| `stage9_native_run` | `exp3_write/` | `workspace/and_gate_scratch/` |
| `stage10/11/12` | `counter4_native/` | `workspace/counter4_scratch/` |

- `workspace/exp3_write/` 从仓库移除（`robei_samples/exp1_and` 已是同类样例）。
- `counter4_native/`、`robei_samples/`、`counter4/`、`cases/` 现在是**只读夹具**：
  `portable_core`（CI）和 `stage14` 仍然读 `counter4_native`。
- `CONTRIBUTING.md` 写入判据：跑完任何 stage，`git status` 必须干净，脏了按 bug 处理。
- `.gitignore` 增加 `workspace/{port,and_gate,counter4}_scratch/`。

## 1.0.0 — 2026-10-23

第一个稳定版。工具面固定为 **38 个**，文档分成「用户指南 / 工具参考 / 验证日志 / 变更记录」四层，
打包与可选 GUI 面都有测试守着。

**这一版做了什么**

- 仓库整理：删掉 84 个开发期文件（71 个一次性 GUI 探针 `tools/gui/archive/`、
  4 个被 `agent.py` 取代的截图/画布脚本、`workspace/part2_exp12/` 课程实验残留），
  跟踪文件 209 → 125。
- 新增 `.gitattributes`（仓库内统一 LF，`.cmd` 保持 CRLF）。
- 新增 `CONTRIBUTING.md`：环境、哪些测试需要 Robei/桌面、五条硬约定（不走 shell、路径白名单、
  错误可分类、不写安装目录、默认不覆盖）。
- CI 增加 `wheel` 作业：构建 wheel 后用 `tests/check_wheel.py` 断言
  `robei_mcp/gui_agent/*` 五个模块与 LICENSE 都在包里、且没有把 `tools/gui/`、`.venv/`、
  `workspace/build/` 打进去 —— 0.11.0 那个"装完 GUI 全废"的回归从此会被 CI 拦住。

**1.0 的边界（先说清楚，避免误解）**

- 核心能力（检查/仿真/波形/工程 XML/约束）跨平台；`robei_gui_*` 是 Windows + 交互桌面专属，可关闭。
- GUI 只到「打开、编译、读 Output、综合、保存」；已有画布上的拖拽/改连线做不到，但可以生成画布级 XML。
- FPGA 后端只到「生成约束 + 综合出网表」，Route/Package 在本机环境走不到底。

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
