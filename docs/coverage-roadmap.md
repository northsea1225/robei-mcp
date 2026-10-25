# Robei 能力覆盖与完善路线

> 目的：回答"Robei 本体还有什么功能没被 MCP 覆盖、下一步补哪里"。
> 证据来自三处：本仓库源码、`D:\robei-lab\robei`（Robei 安装目录）、
> `C:\ProgramData\Robei`（器件库/后端数据），以及 2026-10-24 在 Windows 实机上做的只读探查。
> 文中所有"未验证"都明确标注；不要把它们当成已通过。

## 1. 一句话结论

38 个工具把**文件级读写 + 无头验证**这一层做得相当完整：`.model/.proj/.test/.const` 的解析与生成、
Verilog 导出、iverilog/vvp/yosys/verilator 三路检查、VCD 全套分析、约束与 XDC。

没覆盖的是三大块：

1. **硬件后端**：Synthesis → Route → Package → Download/Burn。四个后端可执行文件、1.1 GB 的
   chipdb 与 prjxray 数据库、53 个预编译 spiOverJtag bitstream 全都在机器上，但 MCP 一个工具都没接。
2. **库模型（ToolBox）与画布增量编辑**：Robei 自带 24 个模型（andgate/counter/dff/ram/mux/...），
   MCP 既不能列出、也不能拖进工程；已有画布只能整份重建，不能加一个实例、改一根线、删一个端口。
3. **交付型验证**：没有资源利用率、没有批量回归、没有"测试报告"产物，GUI 截图能力写好了却没暴露。

## 2. 覆盖矩阵

| 功能域 | Robei 本体 | 现有工具 | 覆盖度 |
| --- | --- | --- | --- |
| 工程/模型 XML | 新建、打开、保存、画布编辑 | `create_robei_project/model/test/canvas_model`、`read_robei_*`、`set_robei_port`、`import_verilog_canvas`、`sync_robei_verilog` | 部分（只能整份生成，不能增量改） |
| 模型库（ToolBox） | 24 个内置模型，拖拽实例化 | 无 | **缺失** |
| 编译（当前模型） | Build→Compile | `robei_gui_compile`、`robei_gui_compile_all`、`export/sync_robei_verilog` | 完整（GUI 与被动手写两条路） |
| 仿真 | Build→Run（当前 test） | `simulate`、`simulate_project` | 完整（而且不依赖桌面） |
| 波形 | Build→Wave（UIA 会塌） | `read_waveform`、`find_events`、`compare_waveforms`、`waveform_stats`、`expect_waveform`、`render_waveform` | 完整（刻意绕开 GUI） |
| 综合 | Build→Synthesis（产 `<name>.json` + `.v`） | `robei_gui_synthesis`、`lint_verilog` 里的 yosys synth | 部分（有综合，无结构化资源报告） |
| 布局布线 | Build→Route（`Roplace.exe`） | 无 | **缺失** |
| 打包比特流 | Build→Package（`fasm2frames` + `xc7frames2bit`） | 无 | **缺失** |
| 下载/烧写 | Build→Download/Burn（`openFPGALoader` + spiOverJtag） | 无 | **缺失** |
| 引脚约束 | `.const` + Build 生成 `.xdc` | `create_robei_const`、`read_robei_const`、`export_robei_xdc` | 完整（器件字段只是写进去，没有校验） |
| 器件/板卡选择 | Setting→JTAG Configure、`.const` 的 Vendor/Device/Family | 只能手工传 `device/family` 字符串 | 部分（无器件目录、无板卡档案） |
| 测试台 | `test/*.test`（Robei 包一层 module） | `create_robei_test`、`simulate_project`、`generate_testbench` | 完整 |
| ERC/连通性 | 画布上人工看 | `check_connectivity` | 教学级 |
| 诊断 | Help/About | `runtime_status`、`robei_doctor` | 完整 |
| 报告 | 无（靠人工截图） | 无（0.11 删掉了 `report_lab`） | **缺失** |

## 3. 缺口明细

### 3.1 硬件后端（最大、也最值钱）

**机器上实际有什么**（2026-10-24 实查）：

| 位置 | 内容 |
| --- | --- |
| `D:\robei-lab\robei\bin` | `Roplace.exe`（nextpnr-xilinx P&R）、`fasm2frames.exe`、`xc7frames2bit.exe`、`openFPGALoader.exe`、`yosys-smtbmc.exe` |
| `C:\ProgramData\Robei\xilinx`（1.1 GB） | `chipdb/` 5 个 part：`xc7a50tfgg484-1`、`xc7a100tfgg484-1`、`xc7a200tfbg484-1`、`xc7z020clg400-1`、`xc7z020clg484-1`；`artix7/` 180 MB、`zynq7/` 76 MB 的 prjxray 掩码/segbit/timing 数据 |
| `C:\ProgramData\Robei\spiOverJtag` | 53 个 `spiOverJtag_<part>.bit[.gz]`（含 `xc7a35tcsg324`、`xc7a100tcsg324` 等教学板）＋每种封装的 `constr_*.xdc` |

也就是说，**"Robei 只能到综合"这个旧结论是错的**：数据齐全，缺的是 MCP 侧的封装。

**我实际试着跑了一遍**（只读工程 + 临时目录 `workspace/_qa_runs/route_probe`）：

```text
Roplace.exe --chipdb .../xc7a100tfgg484-1.bin --json build/qa_light_const.json \
             --fasm out.fasm --top qa_light --xdc constrain/qa_light_const.xdc
→ Error: Unable to constrain IO 'led[0]', device does not have a pin named 'M14'

（去掉 PACKAGE_PIN，只留 IOSTANDARD）
→ Error: No Bel named 'OPAD_X0Y14/IOB33/INBUF_EN' located for this chip

（换成 yosys synth_xilinx 产出的 json，同样在 IO 放置处失败）
→ 同一个 OPAD 错误
```

三次结论：

1. **封装不匹配是硬伤**：工程里用的 `M14/P15/...` 是 csg324 教学板引脚，而随附 chipdb 只有 fgg484
   与 clg400/clg484；csg324 没有 chipdb，所以这些引脚在 `xc7a100tfgg484-1` 上不存在。
2. **只给 IOSTANDARD 不给 PACKAGE_PIN 也不行**：`Roplace` 自动放 IO 会撞上 `OPAD_.../IOB33/INBUF_EN`
   这个 bel 命名问题（版本/数据不匹配的典型症状）。
3. **给 fgg484 的合法引脚能过一部分**：`A10~A16` 被接受，`A17`/`B11` 被拒；同一组引脚换个约束顺序
   又会在 `A11` 报"不存在"——说明这颗 chipdb 与这个 `Roplace` 组合对 IO 约束很敏感，
   **必须复刻 Robei GUI 自己的调用参数**（它在 GUI 里能跑到 Package，说明参数组合是存在的）。

**另外**：`create_robei_const` 的 `device/family` 默认是空串（`server_app.py:452`），
workspace 里所有 `.const` 实查都是 `Device="" Family=""`；这正是 GUI Route 报
`has no Device/Family configured` 的原因。器件不是"可选装饰"，是 Route 的前置条件。

**建议新增工具**（都放在无桌面路径，GUI 只作后备）：

| 工具 | 作用 | 关键返回 |
| --- | --- | --- |
| `list_fpga_devices` | 扫 `chipdb/*.bin` + `mapping/parts.yaml`，列出可用 part 与封装 | `parts[]`、`families[]`、`missing_packages[]` |
| `build_bitstream(proj_path, const_name, part, top, stages=["synth","route","package"])` | 一条链跑完，每段独立返回 | `stages{synth,route,package}`、`fasm/frames/bit` 路径、`log_tail`、`failed_stage` |
| `program_fpga(bit_path, mode="sram"|"flash", cable=None, board=None, confirm=True)` | 包 `openFPGALoader` | `detected[]`、`written`、`verify` |
| `scan_jtag` | `openFPGALoader --scan-usb` / `--list-boards` | `probes[]`、`boards[]` |

验收标准（可写成脚本）：`xc7a100tfgg484-1` 上跑通一个 4 位反相器 → `.fasm` → `.frames` → `.bit`，
字节数稳定；`bit` 的头部魔数正确；`scan_jtag` 在没插板子时返回空列表而不是报错。

### 3.2 库模型（ToolBox）

`C:\ProgramData\Robei\*.model` 有 24 个模型：
`addsub andgate bin2gray clock compare constant counter dff dffce dual_port_ram jk_trigger leftshift
merge mod_n_counter mux notgate orgate por rightshift ring_counter selectbits shift_register
single_port_ram xorgate`。

现状：

- `list_projects` 只扫 workspace（`tools.py:1010`），库模型不可见；
- `create_robei_canvas_model` 只认工程自己的 `model/`（`tools.py:1632`），
  库模型无法实例化；
- `read_robei_model` 能读（`model_roots` 含 `C:\ProgramData\Robei`），但需要调用方知道确切文件名。

这就是"Robei 教学流"最核心的一步——从 ToolBox 拖一个 `counter`/`dff` 到画布——目前完全做不到。

建议：

- `list_library_models(query=None)`：返回名字、参数（如 `andgate` 的 `LEN`）、端口、说明；
- `import_library_model(proj_path, name, as_name=None, overwrite=False)`：把库 `.model` 复制进工程
  `model/` 并登记进 `.proj`（Robei 拖拽本质上也是这么做）；
- 之后 `create_robei_canvas_model` 就能自然引用它，无需改现有语义。

风险：库模型是 Robei 安装目录里的数据，只读复制到用户 workspace 没问题，但不要写回安装目录。

### 3.3 画布与模型的增量编辑

现有能力只有"整份生成"：`create_robei_canvas_model`（新建）、`import_verilog_canvas`（Verilog→画布）、
`set_robei_port`（加/改一个端口）。缺的是日常改图动作：

| 缺的动作 | 影响 |
| --- | --- |
| `add_instance` / `remove_instance` | 加一个子模块、删一个实例，只能整份重建 |
| `connect` / `disconnect` / `rename_net` | 改线必须重生成，容易丢其它连线 |
| `move_instance`（X/Y） | 画布布局无法调整 |
| `delete_port` / `rename_port` | 端口只能加不能删；改名会牵连 `Wire` 端点 |
| `set_robei_code` | 改一行 Code 要 `create_robei_model(overwrite=True)` 并重给全部端口 |
| `set_parameter` | 参数只能读、导出时覆盖，不能写回 `.model` |
| `delete/rename/duplicate model` | 工程结构只能加不能减 |

实现建议：都在 XML 层做（已有 `xmlsafe` + `_backup_file` + 写前 `ET.fromstring` 校验），
并且**每次改完复用 `parse_robei_model` 的 `netlist` 做悬空端点检查**，把 `dangling_instance_ports`
直接放进返回；`delete_port` 要顺带删掉引用它的 `Wire` 或明确拒绝。

### 3.4 工程级操作

- 没有批量验证：`lint`/`simulate`/`truth_table` 都是单模型；教学工程有 10 个模型 + 5 个 test，
  目前只能调用方自己循环（每次都要传路径、拼参数）。
- 没有 `run_all_tests(proj_path)`：跑完所有 `test/*.test` 并汇总 PASS/FAIL/超时。
- 没有工程打包/导出（zip），也没有"从模板新建"（带标准 `model/test/const/verilog` + 板卡引脚）。
- 没有 model/proj 级 diff（改完想确认只动了预期字段）。

### 3.5 验证与报告

- **资源利用率**：`lint_verilog` 只把 yosys 输出里的几行文本塞进 `summary`
  （`tools.py:329`），没有 LUT/FF/BRAM/DSP 的结构化数字，也没有和器件容量对比。
  `element_counts.csv`、`parts.yaml` 已经在机器上，做 `utilization` 是纯解析工作。
- **时序**：`xilinx/*/timings/*.sdf` 在，`Roplace` 也支持时序驱动；但没有任何 STA/Fmax 工具，
  也没有声明"不做时序签核"之外的承诺。
- **批量回归/覆盖率**：`waveform_stats` 能给翻转和 0/1 覆盖，但没有"跑 N 个 testbench + 汇总覆盖率"。
- **报告产物**：0.11 删掉了 `report_lab`（课程模板），但没有替代品。缺一个
  `build_report(proj_path, test, ...)`：真值表 + 断言结果 + 波形 PNG + 资源数字 → Markdown/HTML。
- **形式验证**：`yosys-smtbmc.exe` 在，但机器上没有 yices/z3/boolector，`smtbmc` 跑不起来；
  要做必须先声明外部依赖。

### 3.6 GUI 面

已实现但没暴露的（`robei_mcp/gui_agent/ui.py`）：
`run_simulation`（:684）、`open_wave`（:719）、`wave_load`（:789）、`refresh`（:879）；
截图能力在 `gui_agent/driver.py:120`（`shot`），也没有对应 MCP 工具。

没有实现的 GUI 动作：Route / Package / Download / Burn / Setting→JTAG Configure。
其中 Route/Package/Download/Burn 建议走 CLI（见 3.1），不要加 GUI 依赖；
JTAG Configure 建议不碰 GUI，改为自己的 `device/board/cable` 参数。

一个便宜且有用的补充：`robei_gui_screenshot(label)`——把 `driver.shot` 接出来，
让 agent 能"看一眼画布/Output"，排查 GUI 问题时不用靠猜。

### 3.7 小项

- VCD → CSV / 指定时间窗切片导出（现在只能读 JSON）。
- 多 VCD 合并（同一 testbench 分段跑）。
- 信号别名/分组（`counter4_tb.dut.count` 太长）。
- `.test` 的独立读写工具（现在借 `read_robei_model`，能用但不直观）。
- `robei.toml` 的读/校验/生成（现在只能手写）。

## 4. 路线图

### P0（不依赖新数据，1–2 天量级）

1. `list_fpga_devices` + `create_robei_const` 的 device/family 校验（错误信息直接给可用 part 列表）。
2. `list_library_models` + `import_library_model`。
3. `set_robei_code`（只改 Code，保留端口/参数/画布）。
4. `add_instance` / `remove_instance` / `connect` / `disconnect`（XML 层 + 悬空检查）。
5. `run_all_tests(proj_path)`（复用 `simulate_project`，汇总 PASS/FAIL/超时/耗时）。
6. `synthesis_utilization`（`synth_xilinx` + `stat` 解析，按 part 容量算百分比）。

### P1（需要实机反复对齐）

7. `build_bitstream`：synth → Roplace → fasm2frames → xc7frames2bit，逐段返回日志；
   先把 `xc7a100tfgg484-1` 这一个 part 打通，再扩。
8. `scan_jtag` + `program_fpga`（默认只读扫描；写操作必须显式 `confirm=true`）。
9. `robei_gui_screenshot`。
10. `build_report`（Markdown/HTML）。

### P2（按需）

11. `move_instance` / `rename_port` / `delete_port` / `delete_model` / `duplicate_model`。
12. 工程 zip 导入导出、模板工程。
13. 覆盖率汇总、形式验证（需外部 solver）。

### 1.6 backlog（1.5 审阅时确认，本版不做）

14. **根级参数的「先建后补」**：端口宽度引用未声明的参数时，`create_robei_model` 现在直接报错，
    这是有意的严格行为（1.5 明确不加 `allow_undeclared_width` 开关）。缺的是「改建根级
    `Parameters` 并重算端口位宽」这个能力；现在只能带 `overwrite=true` 一次性重建：

    ```text
    create_robei_model(latep, width="LEN")            -> invalid_argument
        hint: declare it first (parameters={"LEN": 4}) or use a literal width such as width=4
    create_robei_model(latep, parameters={"LEN": 4}, overwrite=true)
                                                     -> ok, parameters_text="LEN = 4;"
    回读：Parameters="LEN = 4;"、a Datasize="LEN-1:0"、Code 未变
    ```

    要补的是 `set_robei_parameters(model, {...})`（改根参数 → 重算端口位宽 → 保住 Code/画布），
    这样不用重建模型。

## 5. 明确做不到 / 不建议做

- **csg324 教学板的 Route**：随附 chipdb 没有这个封装；除非用户自备 chipdb，否则无法本地布线。
- **任意器件的 Vivado 级时序/功耗签核**：机器上没有 Vivado，`timings/*.sdf` 只是 nextpnr 用的延迟表。
- **GUI Wave 面板**：打开会让 UIA 树塌掉（历史结论），继续用 VCD 工具链。
- **在 CI 里烧板子**：需要真实 USB/JTAG，默认只提供 dry-run 与人工确认。

## 6. 风险与前置条件

- 后端工具全部来自 Robei 安装目录，**不能改、不能写**；输出一律落 workspace。
- `Roplace`/`fasm2frames`/`xc7frames2bit` 的 CLI 参数没有官方文档，只能从二进制和 Robei 行为反推；
  必须为每个 part 留一份"最小可复现命令 + 预期输出"的回归脚本。
- `openFPGALoader` 能识别板卡，但**写 flash 是不可逆操作**，工具层要默认 SRAM + 二次确认。
- 库模型复制进工程会改变 `.proj`，必须走备份与"已存在则拒绝/需 overwrite"的既有约定。
