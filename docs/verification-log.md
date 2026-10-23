# 验证日志

按时间顺序记录每个阶段「实际跑通了什么」。这里只写**做过并留下证据**的事，不是使用文档。

阅读提示：

- 想装起来用，看 [README](../README.md)；想看每个工具的用法，看 [tool-reference](tool-reference.md)。
- 本文件里的阶段编号（stage2…stage21）对应 `tests/` 下的同名脚本，可以直接重跑。
- 0.11 移除了 9 个工具（`report_lab`、`robei_gui_experiment`、`robei_gui_run`、`robei_gui_wave`、
  `robei_gui_wave_load`、`robei_gui_code_read/write`、`robei_gui_refresh`、`robei_gui_health`），
  早期小节里提到它们的地方属于历史记录，替代关系见 [CHANGELOG](../CHANGELOG.md)。

## 交付状态

### 0.11：精简工具面（P0）

47 → 38 个工具。移除的都是「只有本机 / 只有课堂场景才用得上」的部分：

| 移除 | 原因 | 替代 |
| --- | --- | --- |
| `report_lab` | 唯一中文模板，绑死中国数电实验报告 | `simulate_project` + `read_waveform` + `render_waveform` |
| `robei_gui_experiment` | 纯编排，与独立工具重复 | 按需组合 `open_project` → `open_model` → `compile` |
| `robei_gui_run` | 仅对 `Type="test"` 有效 | `simulate_project`（同一套 iverilog/vvp，无需桌面） |
| `robei_gui_wave` / `robei_gui_wave_load` | 打开 Wave dock 会让 UIA 树塌掉（`widgets 133 → 0`） | `render_waveform` / `read_waveform` |
| `robei_gui_code_read` / `robei_gui_code_write` | 与文件级读写重复，且绕过备份与 XML 校验 | `read_robei_model` / `create_robei_model` |
| `robei_gui_refresh` | `open_project` 已自动重载 | — |
| `robei_gui_health` | 并入 `robei_gui_status(probe=True)` | `robei_gui_status` |

打包也修了：GUI 驱动（`ui.py` / `driver.py` / `job.py` / `launcher.py`）搬进
`robei_mcp/gui_agent/`，wheel 现在带得上（0.11.0 的 wheel 只有 24 个文件、完全没有
`tools/gui`，`pip install` 后 10 个 GUI 工具必然报"找不到脚本"）。控制文件（job / args / lock）
改放到 `workspace/gui_agent/`，不再写进安装目录；装成 wheel 时 workspace 默认落在
`%LOCALAPPDATA%\robei-mcp\workspace`。`ROBEI_MCP_GUI=0` 可以在 Windows 上强制关掉 GUI 面，
此时 `tools/list` 只有 28 个工具。

配置也改了：`robei_home` 和四个工具路径现在自动发现（`PATH`、`%ProgramFiles%\Robei`、
`<盘符>:\Robei`、`C:\msys64\{ucrt64,mingw64}\bin`），不再假定 `E:\Robei`。
`runtime_status().config.sources` 会报告每个键是 `env` / `toml` / `discovered` / `default` 哪来的。

下面「已验证（阶段 N）」各块是历史记录，其中提到的已移除工具以本节为准。

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
- GUI 加互斥锁 `workspace/gui_agent/agent.lock`：第二个并发调用立刻返回
  `another GUI job is still running`，不再互相覆盖 job 文件后一起超时
- 工程化：`pyproject.toml`（0.3.0，`requires-python>=3.10`，可选 `dev` = pytest），
  `tests/test_portable.py` 用 pytest 参数化跑 5 个子系统

已验证（阶段 16，`tests/stage16_canvas.py`，12/12 PASS，37 个工具）：

- 真实画布 schema 来自 GitHub 公开工程（`wwkkww1983/uart`，38 个 `.model`：183 个
  `<Wire>`、81 个嵌套 `<Module>`）——仓里没有 LICENSE，因此**只取文件格式，不取任何代码**
- 画布格式：`<Module Type="module">` 里放**嵌套** `<Module Type="model" Class="<被引用模型名>"
  File="Current/<模型>.model">` 表示摆好的实例，`<Wire Name From To>` 表示连线，
  端点语法 `<owner>#<instance>><port>`（实例）/ `<owner>><port>`（顶层端口）；
  Robei 里**没有** `<Instance>` 标签
- `create_robei_canvas_model` 直接写这种 XML（不点画布）→ Robei 打开工程并 Compile，
  自己生成 `//---Module instantiation---` + `and2 u1( .a(a), .b(b), .y(t1) );`
- headless 仿真验证连线电气正确：`y = a&b&c`（两个 `and2` 串联），`$strobe` 真值表 8 行全对
- `read_robei_model` / `read_robei_project` 现在解析 `instances` / `wires` / `netlist`
  （`nets` + `dangling_instance_ports`），`is_canvas` 标识画布级模型

已验证（阶段 17，协议与输入加固，`tests/stage17_hardening.py`，28/28 PASS）：

- 37 个工具全部带 MCP annotations：`read_waveform` 是 `readOnlyHint=true`、
  `robei_gui_close` 是 `destructiveHint=true`、`robei_gui_*` 是 `openWorldHint=true`
- 失败统一带稳定 `code` + `hint`：`not_found` / `already_exists` / `invalid_argument` /
  `unsafe_xml` / `path_rejected` / `cancelled` / `timeout` / `compile_failed` /
  `run_failed` / `internal`，客户端不必再猜错误文本
- XML 防护：`<!ENTITY>` 或带内部子集的 `<!DOCTYPE ... [ ... ]>` 直接拒（`code=unsafe_xml`），
  Robei 自己的裸 `<!DOCTYPE Robei>` 与真实 `mux.model` 照常解析
- 客户端取消：`simulate` 跑到一半取消 → 2 秒内 `vvp.exe` 消失、服务端仍能继续响应
- 进度上报：一次 `simulate` 发出 5 条 `notifications/progress`（started → iverilog → vvp → done）
- 路径逃逸与"文件不存在"现在能区分：越界文件报 `invalid_argument`，缺文件报 `not_found`
- `robei.toml` 生效，`runtime_status().config.sources` 逐键报告 `env` / `toml` / `default`
- 65 个一次性 GUI 探针曾集中到 `tools/gui/archive/`，1.0.0 时随仓库整理移除（结论保留在 `docs/gui-tools.md`）

已验证（阶段 18，P1 第一批，`tests/stage18_p1.py`，29/29 PASS，42 个工具）：

- **Verilog → 画布**：`import_verilog_canvas` 把结构化的 `.v` 变成 Robei 画布 `.model`（具名与**位置**连接都支持）
  （嵌套 `<Module>` + `<Wire>`），Robei 打开工程编译后自己产出
  `//---Module instantiation--- and2 u1( .a(a), .b(b), .y(t1) );`，headless 仿真 `y = a&b&c` 全对
- **连接性检查**：`check_connectivity` 报多驱动 / 未驱动网络 / 位宽不匹配 / 未知模块 / 未连接端口，
  坏样例精确命中 `multi_driver:1, undriven_net:2`
- **真值表**：`truth_table` 穷举输入跑仿真，与门 4 行全对；`max_inputs` 超限直接拒绝
- **波形断言**：`expect_waveform` 支持 `at` 取点和 `from`+`to` 保持窗口，负例能准确报 `failed=1`；
  信号名有歧义（`count` 同时命中 `counter4_tb.count` 和 `counter4_tb.dut.count`）只让这一条失败，不炸整个调用
- **波形统计**：`waveform_stats` 识别时钟周期 `10 ns`（`100 MHz`）、单比特 level coverage `1.0`
- **testbench 升级**：`generate_testbench(full_combo=true)` 自动穷举输入组合（4 行 `combo` 输出）

已验证（阶段 19，引脚约束 + FPGA 前端流程，`tests/stage19_constraints.py`，25/25 PASS，47 个工具）：

- `.const` 的真实 schema 来自网络（`wwkkww1983/uart` 的 `prac_04_led/light_constrain.const`）：
  `Type="constrain"` 画布 + 嵌套 `<Module Type="model">` + 每根 FPGA 引脚一个 `<Port>` +
  `<Wire Name="<bit>">` 把引脚接到实例端口
- `create_robei_const` 生成这种文件并登记进 `.proj` 的 `<const>` 目录；
  **Robei 真的能打开它**（Files 树出现 `light_constrain.const`，双击后画布显示
  `G15/P15/W13/T16 → light_constrain_inst → M14/M15/G14/D18`，截图
  `workspace/gui_shots/const_canvas-20261023-083541.png`）
- `read_robei_const` 返回端口 → 位 → 引脚映射，SHA256 前后一致（只读）
- `export_robei_xdc` 生成的 `constrain/light_constrain.xdc` 与网络工程里 Robei 自己生成的
  那份**逐行相同**（18 行 PACKAGE_PIN + IOSTANDARD）
- 负向：引脚数不匹配 / 端口不存在 / 引脚名非法 / 重复创建，分别返回
  `invalid_argument` 与 `already_exists`
- **Robei 自己就会导出 xdc，入口是 Compile 而不是 Synthesis**：constrain 模型保存后按
  Build→Compile，Robei 写出 `build/<name>.v` + `build/<name>.xdc`（引脚逐行与
  `export_robei_xdc` 一致）；Compile 的完成标记是 `Finished to generate constrain file!`
  （不是 `Compile done`，这也是之前误判“没有产物”的原因）
- **Robei 自己的 FPGA 前端跑通了**：`robei_gui_save_project`（File→Save）+ `robei_gui_synthesis`
  （Build→Synthesis）在生成的 `.const` 上跑完 yosys Xilinx 流程，输出 `Synthesis complete`
  并写出 `build/<name>.json`。三个前提都是实测踩出来并已修进工具的：根节点必须是
  `Type="const"` + `Vendor/Device/Family`（`Type="constrain"` 会被拒绝为
  `is not constrain file`）、工程里要有 `build/` 目录（否则最后一步
  `Can't open output file .../build/<name>.json`）

已验证（阶段 20，GUI 可靠性，`tests/stage20_gui_robustness.py`，14/14 PASS）：

- **完成标记 + 输出静止 双条件**：`_wait_for` 只在"看到 `Compile done` / `Synthesis complete` /
  `Finished to generate constrain file` **并且** 输出连续 800ms 不变"时才返回，避免标记先到、
  错误行后到的竞态；`ERROR:` 之类的停止标记会立即返回
- **`unknown_state` 自动重试一次**：既没有标记、也没有错误行的情况（stage16 真遇到过）
  现在会重打一次 Compile，并在结果里标 `retried: true`
- **`compile_all` 每个模型都带 `state` / `marker` / `output_tail`**，不再是空的
  `{"ok": false, "error": null}`
- **输入注入失败自动补救**：所有鼠标点击走 `_mouse()`，遇到
  `There is no active desktop required for moving mouse cursor` 先 `SetForegroundWindow`
  再重试一次（今天 open_project 就是被这个卡住的）
- 标记字符串集中在 `COMPILE_MARKERS` / `CONSTRAIN_MARKERS` / `SYNTH_MARKERS`，
  Robei 换版本改文案只需要动一处

仅可见 / 待定：

- Robei GUI 打开和编译：当前 SSH 在 Session 0，交互桌面在 Session 1，无法从 SSH 看到/点击 GUI；已准备 `workspace\robei_counter4_project` 和手动步骤，待人工验证。
- Mac Codex 配置已写入 `~/.codex/config.toml`，`codex mcp list` 可见；但当前长期运行的 Codex app-server 没有重新挂载新工具，需要重启 Codex 会话/应用后再验证。


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

`stage16_canvas.py` 覆盖（12/12 PASS，画布级 model，全程真 Robei + 真仿真）：

1. 37 个工具注册，含 `create_robei_canvas_model`
2. 生成「两个 `and2` 实例 + 5 条 `<Wire>`」的画布模型，解析回 `u1` / `u2` / `t1`
3. 连线语义正确：`t1` 是 `u1.y → u2.a`，没有悬空实例端口
4. Robei 打开工程、Compile 通过，输出 `.a(a) .b(b) .y(t1)` 形式的实例化
5. headless 仿真 `$strobe` 真值表 8 行全部满足 `y = a&b&c`

`stage17_hardening.py` 覆盖（28/28 PASS）：

1. 37 个工具都带 annotations；只读 / 破坏性 / open-world 各抽查一个
2. 错误码：缺文件 → `not_found`、重复创建 → `already_exists`、越界路径 → `invalid_argument`
3. XML 防护：实体炸弹 → `unsafe_xml`；裸 `<!DOCTYPE Robei>` 与真实 `mux.model` 仍能解析
4. 画布负向：重复实例名 / 未知实例端口 / 未知顶层端口 / 模型缺失 / 属性注入
5. 进度：`simulate` 期间收到 ≥4 条 progress 通知
6. 取消：先确认 `vvp.exe` 在跑，取消后消失，服务端仍存活
7. `robei.toml`：`ROBEI_MCP_CONFIG` 指向临时文件后 limits 生效且 `sources` 变成 `toml`

`stage18_p1.py` 覆盖（29/29 PASS，需要交互桌面跑 Robei 编译）：

1. 5 个 P1 工具注册，`check_connectivity` 标成只读
2. `truth_table(and2)` 4 行全对；`max_inputs=1` 被拒
3. ERC：干净设计 `ok=true`；坏样例报 `multi_driver=1`、`undriven_net=2`
4. `import_verilog_canvas` 生成 2 个实例 + `t1` 连线，无悬空端口；位置连接版本同样解析正确
5. 导入后的画布在 Robei 里编译成功，产物含 `and2 u1( .a(a), .b(b), .y(t1) )`；headless 仿真 `y=a&b&c`
6. `expect_waveform` 正例通过、反例 `failed=1`、保持窗口通过
7. `waveform_stats` 认出 10 ns / 100 MHz 时钟，coverage 1.0
8. `generate_testbench(full_combo=true)` 生成的 TB 打印 4 行 combo

`stage19_constraints.py` 覆盖（18/18 PASS）：

1. 3 个约束工具注册，`read_robei_const` 标成只读
2. `create_robei_const` 写入并登记；`read_robei_const` 的位→引脚映射与输入一致、SHA256 不变
3. `export_robei_xdc` 输出与网络工程里 Robei 生成的 xdc **逐行相同**
4. 负向：引脚数不符 / 未知端口 / 非法引脚名 / 重复创建
5. Robei 打开工程并双击 `light_constrain.const`，画布 tab 变成 `light_constrain`

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


## 从 SSH 会话推送仓库

SSH 跑在 Session 0，`git push` 会失败：GCM 的凭据在交互会话的 Windows
Credential Manager 里，Session 0 读写不到（`Unable to persist credentials with the
'wincredman' credential store`）。

办法是把 push 交给交互会话（和 GUI 工具共用同一个 schtasks 桥）：

```powershell
# 在 Windows 交互桌面上
C:\dev\robei-mcp\.venv\Scripts\python.exe C:\dev\robei-mcp\tools\gui\probe_push.py

# 或从 SSH 触发
echo probe_push > C:\dev\robei-mcp\workspace\gui_agent\agent-args.txt
schtasks /Run /TN RobeiMCP_GuiAgent
```

脚本设置 `GIT_TERMINAL_PROMPT=0`、`GCM_INTERACTIVE=never`，避免卡在登录窗口，
并对返回文本里的 token 做脱敏。

回归补充：`tests\stage9_native_run.py`、`tests\stage10_counter_native.py`（Robei
原生闭环）、`tests\stage11_mcp_native.py`（29 个工具的 stdio 端到端，14/14 PASS）。
