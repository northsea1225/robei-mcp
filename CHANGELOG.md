# Changelog

版本号对应 `pyproject.toml` 里的 `version`。工具数量指 `tools/list` 返回的数量（Windows + GUI 可用时）。

## 1.3.0 — Unreleased（草稿）

**GUI 不再依赖鼠标、工程可以嵌套、产物落在工程里。** 工具面仍是 49 个（文件级 38 + GUI 11），
`ROBEI_MCP_GUI=0` 时 38 个——本版只加参数，不加工具。

### M0 取证：为什么放弃「文件对话框」这条路

Session-1 探针在真实 Robei 上逐个试 UIA 模式（Qt6，窗口类 `Qt680QWindowIcon`）：

| 目标 | 暴露的模式 | 实测 |
|---|---|---|
| 菜单栏项 | invoke / value / expand_collapse | `Invoke()` 无效果；`ExpandCollapse.Expand()` 才能打开菜单 |
| 菜单项（popup） | invoke | `Invoke()` 可用，且立即返回 |
| 原生打开对话框 | — | Invoke `Open` 后 Qt 的 UIA provider 全局阻塞（同进程、新进程都 90 s 超时）→ 只能重启 IDE |
| Files 树 TreeItem | invoke / selection_item / expand_collapse | 三种都不改展开状态（前后都是 0），必须点一下 + RIGHT |
| 命令行 | — | `Robei.exe "<proj>"` 直接打开工程（无对话框）；`Robei.exe "<model>"` 独立打开模型；已有实例时新开一个窗口 |

### P0-1 `open_project` 改成 argv 重启，菜单改走 UIA

- `robei_gui_open_project`：关掉**自己启动的** Robei → `Robei.exe "<proj>"` → 按 pid attach →
  校验 Files 树根 == `.proj` 的 stem，返回 `restarted=true` / `pid` / `argv`。全程不碰鼠标、
  键盘和文件对话框（锁屏也能开工程；实测一次重启 1–3 s）。
- `main_window()` 改成按 pid 绑定；只有拿不到 pid 的只读调用（`status`）才用旧的
  "最大且带菜单栏的窗口"启发式。
- 只能关自己的实例：pid 记在 `workspace/gui_agent/robei_pid.json`，`robei_gui_close` 由
  `taskkill /IM Robei.exe` 改成 `taskkill /PID <记录>`。动手前先核对这个 pid 的**进程名**和
  **记录里的 exe 路径**都还是 `Robei.exe`：pid 已退出、被回收给别的程序、或者是一次手工启动的
  IDE，都当作"没有自己启动的实例"（`closed:false`）并删掉过期记录。有外来实例时返回
  `code=gui_foreign_instance`，不会 kill。
- **未保存状态探测不到**（编辑后窗口标题和模型标签都不带 `*`），所以文档写明
  "打开工程会丢画布上未保存的修改"；`discard_unsaved` 参数保留，等真能探测到时再生效。
- `robei_gui_open_model` 仍走树 + 双击（Qt 树对 UIA 无反应）；桌面不可用时返回
  `code=gui_needs_active_desktop`，不再抛 `RuntimeError`。打开成功的判定改成轮询活动标签
  （编译后 Robei 切页更慢，固定 sleep 会假失败）。
- `compile` / `save_project` / `synthesis` 用 `ExpandCollapse.Expand()` + 条目 `Invoke`，
  返回新增 `menu = {"via": "uia" | "mouse", "uia_error"}`；鼠标路径保留为 fallback，
  `_mouse` 的 active-desktop 重试逻辑没动。

### P0-2 嵌套工程名

`create_robei_project("lab2/exp1")` → 目录 `workspace/lab2/exp1/`，`.proj` 文件名、XML 根标签
和 `name` 属性只用最后一段 `exp1`。每段都要求 `[A-Za-z_][A-Za-z0-9_]*`；`..`、绝对路径、
反斜杠、`a//b`、`a/b/` 全部 `invalid_argument`；解析完再做一次 workspace 越界检查。

### P1

- **产物落工程目录**：`simulate_project` 的 tb/deps/`sim-*` 运行目录/VCD 都落在
  `<工程>/build/`；`render_waveform` 默认写 `<工程>/build/waveforms/`（`out_dir` 可覆盖，
  越界照拒），不在工程里时才回退到 `workspace/build/waveforms/`。
  实测 Robei 的 Compile 会删掉 `<工程>/build` 下的**顶层文件**，但**保留子目录**——所以
  `build/<run dir>/`、`build/waveforms/` 这种目录形态是安全的，别再往 build 根目录散写文件。
- **inline PNG**：`render_waveform` / `simulate_project(render=true)` / `robei_gui_screenshot`
  新增 `inline=false`；为 true 时额外返回 `ImageContent`（`mimeType=image/png`，5 MiB 上限，
  超限返回 `inline.code=image_too_large` 且照常给 `path`）。默认行为与 1.2.2 一致
  （文本 JSON + `structured_content` 都在）。
- **`skeleton`**：`create_robei_project(..., skeleton=false)` 或 `models=[]` 不建空白模型，
  `.proj` 里 `<model .../>` 自闭合、不留空行。
- **`read_run_log` 收紧**：只认 `workspace/logs/**`、`workspace/build/<真实戳记 run dir>/**`、
  `<工程>/build/**`（该工程目录里必须有 `*.proj`）；其余 `path_escape_log`。
  一个只是碰巧叫 `build` 的目录不再放行。
- README 快速开始补一句：基础 wheel 38 个文件级工具，装 `[gui]` 且在 Windows 才多出 11 个。

### 测试

`pytest` 178 passed、`portable_core` 16/16、stage2–21 全过；
`open_project` 与 `compile` 在"鼠标/键盘一调用就抛异常"的桩下通过。详见
`docs/verification-log.md` 的 1.3 段。

## 1.2.2 — 2026-10-24

**把「用 MCP 做数电实验」这条路上踩到的坑补掉。** 工具面 49 个（文件级 38 + GUI 11）；
`ROBEI_MCP_GUI=0` 时 38 个。

### P0：失败的 FPGA 段不再变成 `code=internal`

`fpga.py` 里调用的 `FpgaStepFailed.from_record` 以前**根本不存在**：任何一段返回 rc≠0，
都会在抛异常那一刻抛成 `AttributeError`，上层只能兜成 `code=internal` + 一段看不懂的
`hint`。现在实现了 `from_record`，失败段返回：

- 顶层 `code=fpga_step_failed`、`stage`（`synth` / `route` / `package.frames` /
  `package.bit`）、`argv`（脱敏后的真实命令行）、`returncode`、`log_tail`、
  `log_path`、`run_dir`；完整记录仍在 `record` 里。
- 每一步的完整 stdout+stderr 落到 `run_dir/<stage>.log`，一次调用共用一个 `run_dir`
  （`workspace/build/fpga-<时间戳>-<随机>/`），`summary.json` 是整次调用的记录。

实测（实验六的工程，原样交给工具）：

```text
build_bitstream(proj_path="exp6_counter/exp6_counter.proj",
                part="xc7a50tfgg484-1", const_name="dec_counter")
→ code=fpga_step_failed  stage=route  returncode=3
  log_path=workspace/build/fpga-20261024-203823-2550804b/route.log
  log_tail: Error: Unable to constrain IO 'cnt[0]', device does not have a pin named 'M14'
            terminate called after throwing an instance of 'log_execution_error_exception'
```

（`M14` 是实验指导书那张板的引脚，`xc7a50tfgg484` 上并不存在——所以这条报错是真实约束，
不是工具链问题。把 `cnt` 改成 `A20/A19/A13/A18`、`clk/rst/en` 改成 `A1/A14/A15`、
`co` 改成 `A16` 之后同一份 RTL 出 `.bit`：2,192,170 B，
`bit_payload_sha256=bcd969f3b531ba4c7266ebeba35e9c3dc1e9c1c19e572fea287b8e0d5adc6e85`
（`bit_payload_offset=206`），Roplace 报 `Max frequency 539.08 MHz`，
`utilization = LUT 4 / FF 4 / CARRY 1 / IO 8`。）

### 新增 `read_run_log`

只读工具：打开 `fpga_step_failed` / `internal` 返回里的 `log_path`，或者任何落在
workspace 下 `logs/`、`build/` 目录里的文件。`tail` 默认 200 行，`max_bytes` 默认
500,000（超长只留末尾并返回 `truncated=true` 与 `note`）。所有 hint 里原来那句
「see workspace/logs」改成直接给出能喂给它的 `path`。越界不再是通用的 `path_escape`，
而是新的 `code=path_escape_log`，hint 写明 `logs/` / `build/` 规则。

### 其余修复

- **`steps` 改成枚举**：`build_bitstream` 的 `steps` 在 schema 里就是
  `enum ["synth","route","package"]`，写错值在 MCP 层被拒。`synthesize_project`
  本来就没有 `steps` 参数（综合只有一段），tool-reference 里已注明。
- **`route` 段自动补 xdc**：`build/<const>.xdc` 不存在时用 `.const` 现场导出
  （等价于 `export_robei_xdc(also_build=true)`），返回 `xdc_generated=true` 并记一条
  warning；`.const` 本身不存在是 `invalid_argument`，hint 说明先 `create_robei_const`。
  （1.2.2 之前这条路径直接端到端失败，因为 IDE 打开工程会把 `.const` 的
  `Family`/`Device` 清空，`.xdc` 也跟着不见。）
- **`$dumpvars` 只看 testbench 作用域**：自动注入的变成
  `$dumpvars(0, <tb>);`，iverilog 不再报 `Package ($unit) is not dumpable`。
- **`import_library_model` 接受 `proj_path`**：和 `read_robei_project` 等工具同名参数
  对齐；两个都不给时 hint 直接写出正确参数名和示例值，不再返回千篇一律的
  「check the argument names」。

### 测试

`pytest` 146 passed（1.2.1 是 138）；新增 `test_route_failure_reports_code_and_stage`、
`test_route_regenerates_a_missing_xdc`、`test_missing_const_is_invalid_argument`、
`test_read_run_log_returns_the_tail`、`test_read_run_log_caps_long_files`、
`test_read_run_log_rejects_paths_outside_log_roots`、`test_simulate_project_scopes_dumpvars`。

## 1.2.1 — 2026-10-24

**修掉 1.2.0 的四个实测问题。** 工具面 48 个（文件级 37 + GUI 11）；`ROBEI_MCP_GUI=0` 时 37 个。

- **默认超时会被自己截断**：`build_bitstream` 默认 `timeout_s` 600 → **120**，
  `synthesize_project` 300 → **120**（都等于 `ROBEI_MAX_TIMEOUT_S`）。以前不传参数每次都是
  `clamped:true` 加一条 warning，现在默认调用是 `clamped:false`、没有截断提示；只有显式要
  更长时间才会被截断并在 `timeout` / `warnings` 里说明。
- **键名拼写**：`tool_paths()` 的键名多打了一个 l，已改成 `roplace`；`list_fpga_devices`
  的返回、tool-reference 与 pytest 同步更新，`robei_mcp/`、`tests/`、`docs/tool-reference.md`
  里已无旧拼写（本段是历史说明）。
- **不兼容**：`list_fpga_devices` / `tool_paths` 的键 `ropllace` → `roplace`（1.2.0 的拼写
  错误，**未保留旧键**），调用方升级后要按新键取值。
- **`.bit` 说法更正**：xc7frames2bit 会在开头写 ASCII 字段——`a` = `.frame` 的**绝对路径**、
  `b` = 器件、`c` = 日期、`d` = 时间——之后才是同步字 `0xAA995566` 和帧数据。因此
  **同样构建的 `.bit` 大小随路径长度变化**（实测同一工程：xc7a50tfgg484-1 2,192,172 B、
  xc7a100tfgg484-1 3,825,949 B），**逐字节差异只出现在头部**（路径字段与 `d` 时间戳，
  偏移随路径浮动，不要写死 145–148）。比对请用 `fasm` / `frame`。
- **新增 `bit_payload_sha256`（+ `bit_payload_offset`）**：从同步字 `0xAA995566` 起算的
  哈希。`examples/fpga_demo` 连跑两次：整文件 sha256 不同
  （`e8f9aef7…57c685` vs `50275fe7…d04ebd`），`bit_payload_sha256` 相同
  （`46d75f4c…c44b547`，`bit_payload_offset` 203；工程目录换长一点，偏移也跟着变）。
- **`json` 也不是稳定判据**：同一环境连跑两次字节一致，但换一份 yosys 安装（二进制 md5
  相同）会写出字节数不同的 json（9,382,162 B vs 9,594,583 B），拓扑与 fasm 相同。
- verification-log 里 1.2.0 那段的“.bit 大小恒定 / 偏移 145–148”已按实测改写。

## 1.2.0 — 2026-10-24

**FPGA 后端打通到 .bit。** 工具面 48 个（文件级 37 + GUI 11）；`ROBEI_MCP_GUI=0` 时 37 个。

### M0 取证：Robei 自己跑的三段命令

用进程命令行轮询抓到的真实 argv（Robei 5.0，Build > Synthesis / Route / Package）：

```text
Synthesis  yosys -p "read_verilog <build>/<top>.v; synth_xilinx -flatten -arch xc7
                   -abc9 -nodsp; write_json -noscopeinfo <build>/<const>.json"

Route      Roplace --chipdb <db>/chipdb/<part>.bin --xdc <build>/<const>.xdc
                   --json <build>/<const>.json --write <build>/<const>_routed.json
                   --fasm <build>/<const>.fasm

Package    fasm2frames --db-root <db>/<family>/ --part <part>
                       <build>/<const>.fasm <build>/<const>.frame
           xc7frames2bit --part_file <db>/<family>/<part>/part.yaml
                          --part_name <part> --frm_file <frame>
                          --output_file <build>/<const>.bit
```

- `<db>` 是 `C:\ProgramData\Robei\xilinx`；中间产物顺序是 `.json` → `_routed.json` +
  `.fasm` → `.frame` → `.bit`。
- 器件库是 5 个 `chipdb/<part>.bin` 对 6 个 `<family>/<part>/part.yaml`：
  `xc7a100tcsg324-1` 只有 part.yaml、没有 chipdb（Robei 的器件列表里也没有它），
  所以它固定是 `device_unsupported`。
- Route/Package 认的是 `.const` 根节点的 `Family` / `Device`：两者为空时 Roplace 会去找
  `chipdb/-1.bin` 并失败（这也是原来 GUI 上 Package 一直失败的原因）。
- 附带一条实测：`E:\Robei\bin` 与 `D:\robei-lab\robei\bin` 里的 yosys / Roplace /
  fasm2frames / xc7frames2bit 是同一份二进制（md5 一致），用同一个 json 跑出的 `.fasm`
  也逐字节一致，所以工具链换成哪一份都不影响结果。

### M1 器件与综合

- 新增 `list_fpga_devices(part?)`：返回每个 part 的 `chipdb`、`part.yaml`、`family`、
  `db_root`，以及 `buildable`（chipdb 与 part.yaml 都在）和不可用原因。
- 新增 `synthesize_project(proj_path, top?, part?, timeout_s=300)`：按上面那条
  yosys 脚本综合，写 `build/<const>.json`，再用 `tee -o ... stat` 解析出
  LUT / FF / CARRY / BRAM36 / BRAM18 / DSP / IO 并写一份 `workspace/build/fpga-*/summary.json`。
  统计走文件而不是 stdout，是因为工具层会裁剪超长输出，直接把 cell 表切掉了。

### M2 生成 bitstream

- 新增 `build_bitstream(proj_path, part, const_name?, steps=['synth','route','package'], timeout_s=600)`：
  按 `synth` → `route` → `package` 顺序执行，每一步返回 `stage` / `returncode` /
  `duration_s` / `log_tail` / 产物路径；第一个失败就停在那一步，返回
  `code=fpga_step_failed` 和该 `stage`（`package` 细分成 `package.frames` 与 `package.bit`）。
- 没有 chipdb → `device_unsupported`；端口位没绑引脚 → `unconstrained_io` 并列出缺的
  `port` / `missing_bits`；三件套二进制缺失 → `fpga_step_failed`。
- Roplace 的 Fmax 只作为参考（`fmax_reference`）：设计里没有时钟时它只打印
  `Warning: No clocks found in design`，此时该字段为 null。**本工具不做时序签核。**

### M3 小项

- 新增 `run_all_tests(proj_path, timeout_s=60, render=false)`：跑工程里全部 `test/*.test`，
  汇总 `total` / `passed` / `failed`、每个用例的 rc、耗时、VCD 路径和失败尾部；
  一个用例失败不影响其它用例。
- `edit_robei_canvas` 新增 `add_port` / `remove_port`：删端口会把接在它上面的 wire 一起删掉
  （`wires_dropped` 记录条数），返回里多了 `ports`。
- `lint_verilog` 的 Verilator 一路在 ≥ 5.0 时自动加 `--timing`，`#5 clk = ~clk` 这类时钟模型
  现在 lint 通过；若版本说支持但实际被拒，会回退并不带 flag 重跑，同时在
  `verilator.timing_warning` 里写明原因。
- 修正 CHANGELOG 里 `tests/test_1_1.py` 的用例数：25 → 27（实际是 27 个）。

## 1.1.0 — 2026-10-24

**元件库可以直接用了，画布也能增量改。** 工具面 44 个（文件级 33 + GUI 11）；
`ROBEI_MCP_GUI=0` 时 33 个。

### 元件库（M1）

- 新增 `list_library_models(filter?, include_lint=true, limit=200)`：只读扫描 Robei 自带的
  ToolBox（默认 `C:\ProgramData\Robei`，`ROBEI_MCP_LIBRARY` 可覆盖），给出每个模型的
  `name` / `module_name` / `ports` / `parameters` 和**真的** `iverilog -g2012` 编译结果。
  24 个模型里 `constant.model`（模块名 `register`）与 `mod_n_counter.model`
  （模块名 `ring_counter`）文件名与模块名不一致，用 `name_matches_module=false` 标出来。
- 新增 `import_library_model(name, project, as_name?, overwrite=false, dry_run=false)`：
  复制进 `<project>/model/`、备份 `.proj` 并登记、再 lint 副本。重名 → `already_exists`；
  文件名和模块名冲突时拒绝并提示用 `as_name`（给了 `as_name` 就同时改文件名和根节点 `Name`）。

### 画布增量编辑（M2）

- 新增 `edit_robei_canvas(model, ops[], dry_run=false)`：`add_instance` / `remove_instance` /
  `connect` / `disconnect` / `rename_wire` 一次调用内**原子执行**——先在内存里跑完全部校验，
  任何一条失败都不落盘。返回 `diff`、`netlist` 和 `verilog_preview`。
- 位宽按 Robei 的 `Datasize` 语义算（`4` 是 4 位、`3:0` 是 4 位、`LEN-1:0` 未解析时要求两边
  字面量一致）：不一致返回新的错误码 `width_mismatch`，消息里带两边位宽。
- `parse_robei_model` 顺带读出旧式 `<Instance Module="..."/>` 的 `Module` 字段
  （`instances[].module`），这种实例没有 `<Port>` 子节点时端口从子模型补齐。
- 新增 `set_robei_code(model, code, dry_run=false)`：只改行为级模型，结构级模型返回
  `not_behavioral`。
- 新增 `set_robei_parameter(model, name, value, dry_run=false)`：改一个根级参数并**重算
  `width_info`**（给出代入后的表达式与解析出的位宽），保留其它参数的注释；名字不存在返回
  `unknown_parameter`。
- `create_robei_canvas_model` 与 `edit_robei_canvas` 现在共用同一个 `_render_canvas()`，
  写出的画布格式只剩一处定义。
- `verilog.robei_width()` 统一了"`Datasize` 是宽度还是范围"这个判断。

### 测试（M3）

- 给此前 pytest 里没有引用的工具补了测试（`tests/test_tool_coverage.py`，32 项）：
  `render_waveform`（真出 PNG，校验 PNG 头与不可覆盖）、`robei_doctor`（浅/深两次）、
  `create_robei_test`、`import_verilog_canvas`、`check_connectivity`（悬空网络 + 未知模块 +
  完整设计）、`expect_waveform`（`at` 与 `from`+`to` 两种断言）、`waveform_stats`、
  `create_robei_const` / `read_robei_const` / `export_robei_xdc` 三个工具层封装。
- GUI 工具用 mock 的 `gui.call` 锁住 op/参数契约（status/launch/open_project/open_model/
  compile/output/save_project/synthesis/close/compile_all/screenshot/health），并覆盖
  `transport()=="unsupported"` 时返回 "GUI tools require Windows" 的分支。
- `errors.py` 的映射表逐条测（15 条），并断言 `code_for` 能产出的每个 code 都在 `HINTS` 里。
- `tests/test_1_1.py`（27 项）覆盖 M1/M2 的行为，含从零搭 4 位链路后真跑 iverilog/vvp
  断言 `Y==A`、失败批次不落盘、`dry_run` 不写文件。

### 里程碑实测（Windows，`workspace/verify_1_1_a`，跑完即删）

- **画布**：`edit_robei_canvas` 从零搭出 `a→u1→mid→u2→y` 的 4 位链路（u1/u2 均为 `buf4`），
  预览里是 `wire [3:0] mid;` + `buf4 u1(` / `buf4 u2(`；用 `iverilog`/`vvp` 实跑，
  `A=1011 Y=1011 MATCH`、`A=0110 Y=0110 MATCH`。
- **Robei 自己的 Compile**：工程在 Robei 里打开后，`andgate`（导入的库模型）和 `chain`
  （编辑过的画布）各 Compile 一次，日志都是 `Check complete with 0 warnings.` +
  `Compile done.`；Robei 生成的 `build/chain.v` 与我们的导出一致
  ——`wire [3:0] mid;`、`buf4 u1(.a(a), .y(mid));`、`buf4 u2(.a(mid), .y(y));`。
- **截图**（3200×2000，留在 `workspace/gui_shots/`，该目录 gitignore）：
  `1-1-a-andgate-20261024-155633.png` 1,032,217 字节、
  `1-1-a-chain-20261024-155642.png` 1,046,331 字节（第二张能看到工程树里的
  `andgate/buf4/chain` 与画布上的 u1/u2）。

## 1.0.4 — 2026-10-24

**`Parameters` 里的 `//` 注释不再吃掉它后面的参数。** 老解析器先按 `;` 切分再剥注释，于是
`START = 0; //a` 的注释把下一行的 `DUTY = 50` 一起卷进注释，只留下第一个参数。Robei 自带的
24 个 ToolBox 模型里有 10 个是多参数，全部因此导出失败（iverilog 报 `Unable to bind ... DUTY`）。
工具面不变（39 个 = 文件级 28 + GUI 11）。

### 参数解析（B1）

- **先剥注释，再按 `;` 切分**：逐行处理，`//` 到行尾算注释（字符串里的 `//` 不算），
  注释挂回它所在行的参数（同一行多个参数时归最后一个）；注释里带 `;` 也不再切断参数。
- **`;` 只在字符串外才算分隔符**：`P = "a//b";` 这类值不再被误切。
- **根级 `Parameters` 解析失败也报 warning**：`parse_robei_model` 新增 `parameter_bad`，
  `model_to_verilog_with_warnings` 把它变成 `root Parameters: cannot parse chunk …`，
  `export_robei_verilog` / `sync_robei_verilog` 的 `warnings` 里都能看到（以前只有实例参数会报）。
  坏掉的 chunk 不生成 `parameter`，能解析的照常生成。
- **注释不再吞掉参数之间的逗号**：带注释的根级参数输出
  `parameter START = 0,  // initial_state`；以前逗号落在注释后面，iverilog 直接语法错误。
- 实例参数覆盖走同一个函数，`"W = 4; // four"` 仍然生成 `#(.W(4))`。

### 测试

- 新增单测：多参数带行尾注释、注释里含 `;`、整块无注释、根级坏参数报 warning、
  实例参数带注释仍出 `#(`、带注释的两参数逗号位置。
- 新增实机回归 `test_toolbox_models_export_and_compile`：把 `C:\ProgramData\Robei` 下的
  24 个 ToolBox 模型（只读）复制到临时 workspace，逐个导出并用 `iverilog -g2012` 编译。
  修复前 14/24 通过（10 个失败全是 B1），修复后 24/24 通过。

## 1.0.3 — 2026-10-24

**层次化导出不再丢位宽；路径错误码、工程列表、约束字段与 GUI 截图补齐。**
工具面 39 个（文件级 28 + GUI 11）。

### 层次化导出（P0）

- **内部 wire 保留位宽**：画布上的 `<Wire Datasize="4">` 以前导出成 `wire mid;`，
  iverilog 报端口位宽不匹配、仿真得到 0。现在按 Datasize 生成 `wire [3:0] mid;`
  （Datasize 为 1/空时不加范围），同一条线取最宽的声明。
- **实例参数覆盖（experimental）**：嵌套 `<Module Parameters>` 非空、且与子模型
  `Parameters` 的默认值不同时，生成 `child #(.W(4)) u1(...)`；等于默认值则不输出。
  **还没有 Robei GUI 保存的样本，格式按根节点的 `NAME = value;` 推断**——安装目录 + 仓库
  共 67 个 `.model` 里 `<Module Parameters>` 全是空串，没有可对照的实机样本。
- `Parameters` 写坏/无法解析时**不输出任何 `#(...)`**，并把这些情况作为 `warnings`
  返回（`export_robei_verilog` / `sync_robei_verilog`），不静默跳过。
  `<Wire Datasize>` 字段本身在样本里是确认存在的。

### 路径与接口

- 新增错误码 **`path_escape`**（`errors.py`），所有 `resolve_*` 越界都抛它，
  hint 明确说明路径必须留在 workspace 内
  （`the path must stay inside the workspace: ...`）。`invalid_argument` 不再兜底路径问题。
- 配置里的 model roots 在 `_default_model_roots` / `_pick_roots` 去重，
  错误信息里的 allowed roots 不再重复列出 workspace。
- `list_projects` 默认跳过 `workspace/_qa_runs` 与 `workspace/qa_*`，新增 `include_qa=true`。
- 单独传 `"workspace"`（无斜杠，`must_exist=False`）解析为 workspace 根，不再套成
  `workspace\workspace`。
- `module m; endmodule` / `module m(); endmodule`：`parse_module_ports` 现在返回 `[]`
  （合法零端口模块），与 `parse_module` 一致；模块不存在仍然报错。

### 约束与 GUI

- `.const` 引脚上的 `Drive` / `Pull` / `Voltage` 映射进 XDC：
  `Drive` → `set_property DRIVE <值>`，`Pull`（up/down 及常见变体）→ `PULLUP/PULLDOWN TRUE`；
  `Voltage` 只返回 `voltage_hints`，不猜 IOSTANDARD。空字段不产生行。
  `read_robei_const` 新增 `pin_options`。
- 新增 **`robei_gui_screenshot`**：抓取交互桌面到 `workspace/gui_shots/<label>-<时间戳>.png`，
  走同一条 Session-1 桥。
- `create_robei_canvas_model` 的实例支持 `parameters` 字符串（写入嵌套 `<Module Parameters>`），
  wire 支持 `datasize`。

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
- **路径前缀规则写死**：开头的 `workspace/`（大小写不敏感，`/` 与 `\` 都算）**一律去掉**再解析，
  所以新建路径也不会落到 `workspace/workspace/...`；去掉前缀后仍做逃逸检查
  （`workspace/../x` 直接拒绝）。如果工作区里真有 `workspace/` 子目录，且根目录下同名路径也存在，
  返回 `ambiguous path`，不静默选一个。`resolve_workspace_path` / `resolve_model_path` /
  `resolve_project_path` 用的是同一套规则。
- **一行写的参数化模块头**：`module m #(parameter WIDTH=4)(input [WIDTH-1:0] a, …)` 以前报
  `no ports parsed`，现在能正常解析（端口位宽保留为 `WIDTH-1:0`）。把参数默认值代入位宽仍不支持，
  `truth_table` / `generate_testbench` 会明确报错。
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
