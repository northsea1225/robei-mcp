# 参与开发

## 环境

- Windows（GUI 部分需要交互桌面）或 Linux/macOS（只有核心工具），Python 3.10+。
- 工具链：Robei 自带的 `iverilog`/`vvp`/`yosys`，或独立安装的 Icarus。
- `python -m venv .venv && .venv/Scripts/pip install -r requirements.txt pytest`

## 跑测试

`tests/` 下每个脚本对应一个功能域，能单独跑，退出码非 0 即失败：

| 脚本 | 需要 Robei | 需要桌面 |
|---|---|---|
| `portable_core.py` / `pytest -q` | 否（有 iverilog 即可） | 否 |
| `stage2`/`stage3`/`stage4`/`stage13`/`stage14`/`stage17`/`stage18` | 否 | 否 |
| `stage21_packaging.py` | 否 | 否 |
| `stage5`/`stage7`/`stage9`/`stage10`/`stage11`/`stage12`/`stage16`/`stage19`/`stage20` | 是 | 部分（编译、打开工程） |

GUI 测试前先注册 Session-1 助手任务：

```powershell
python tools\gui\register_task.py
```

## 测试不写夹具

`workspace/` 下被跟踪的工程（`counter4_native/`、`robei_samples/`、`counter4/`、`cases/`）是**只读夹具**：
测试只许读，需要可变工程就自己在 `workspace/<name>_scratch/` 里建（已在 `.gitignore` 里）。

判断标准很简单：**跑完任何 stage，`git status` 都应该是干净的**。脏了就是测试在改夹具，按 bug 处理。

## 代码约定

- 所有对外能力走 `robei_mcp/server_app.py` 里的 `@mcp.tool`（GUI 工具走 `GUI_TOOLS` 清单），
  实现放 `robei_mcp/tools.py` / `p1.py` / `constraints.py` / `gui_agent/`。
- **不执行任意命令**：子进程用固定 argv 列表、`shell=False`；不要加"传一个命令字符串"的工具。
- **路径必须过白名单**：源码走 `security.resolve_source_files`，模型走 `resolve_model_path`，
  VCD 走 `resolve_vcd_path`。新增工具也要接进同一套检查。
- **错误要可分类**：抛 `errors.py` 里的异常（或让 `_error()` 包一层），返回 `code`/`hint`/`stage`，
  不要只回一句字符串。
- **不写 Robei 安装目录**：`E:\Robei` 与 `C:\ProgramData\Robei` 只读；产物一律进 `workspace/`。
- **默认不覆盖**：新文件同名时抛 `FileExistsError`，覆盖必须显式 `overwrite=True` 并先备份。

## 提交

- 一个提交一件事；提交信息写"为什么"，不是"改了什么"。
- 改了版本号就同步 `pyproject.toml`、`robei_mcp/__init__.py`、`CHANGELOG.md` 三处。
- 提交前至少跑 `pytest -q` + 受影响的 stage 脚本。

## 报问题

带上：`runtime_status` 的输出、`robei_doctor` 的结果、出错工具的完整返回（含 `code`/`stage`）、
以及能不能在 Robei GUI 里手工复现。**不要贴许可证内容、令牌或账号信息。**
