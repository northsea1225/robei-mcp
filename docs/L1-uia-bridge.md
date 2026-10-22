# L1 结论：Robei 自带的 UIA 桥可以在**不改程序**的前提下驱动

日期：2026-10-22　样本：`D:\robei-lab\robei\bin\Robei.exe`（`E:\Robei` 的副本，sha256 与原版一致）
运行方式：登录会话 Session 1，经计划任务 `RobeiMCP_GuiAgent` 用 `pythonw.exe` 拉起 agent

## 1. 桥是否存在

| 探测 | 结果 |
| --- | --- |
| `WM_GETOBJECT / OBJID_WINDOW(0)` | 0 |
| `WM_GETOBJECT / OBJID_CLIENT(-4)` | 0（没有 MSAA/IAccessible 路径） |
| `WM_GETOBJECT / OBJID_NATIVEOM(-16)` | 0 |
| `WM_GETOBJECT / OBJID_UIA(-25)` | 0（同步调用返回 0，但 UIA 客户端照样能枚举） |
| pywinauto `backend="uia"` `descendants()` | **119 个控件** |

结论：程序里编进去的是 **UIA 原生 provider**（`QWindowsUiaAccessibility` + `UiaReturnRawElementProvider`），
不是 MSAA 桥。UIA 客户端（UIAutomationCore）能正常取到控件树，`SendMessage(WM_GETOBJECT)` 返回 0 不代表不可用。

## 2. 环境变量

`QT_ACCESSIBILITY=1` / `QT_ACCESSIBILITY_ENABLED=1` **不是必需的**：
用不带任何 Qt 环境变量的全新进程启动（`agent start D:\robei-lab\robei\bin\Robei.exe`），
第一次 `descendants()` 就返回 119 个控件。

## 3. 能读到什么

- 菜单栏 8 项：`File Edit Tool Setting Build View Window Help`
- 工具栏 23 个 `QToolButton`（UIA 里表现为 `CheckBox`）：
  `New Open Save | Copy Paste Cut Delete | Select Port Wire Refresh | Compile Run Wave |
   Synthesis Route Package Download Burn | Codeview Zoomin Zoomout Zoomfull`
- 8 个菜单的完整子项（通过点开菜单后枚举 `Qt680QWindowPopupDropShadowSaveBits` 弹窗）：
  - File: `New Open | Save Saveas | Quit`
  - Edit: `Copy Paste Cut Delete | Find`
  - Tool: `Select Port Wire | Check AutoConnect`
  - Setting: `JTAG Configure`
  - Build: `Refresh Compile Run Wave | Synthesis Route Package Download Burn`
  - View: `| Codeview Zoomin Zoomout Zoomfull`
  - Window: `Files Output RoAI Waveview | Search Toolbox Property`
  - Help: `Help Register About`
- Dock：`Files`(FileTree) `ToolBox` `Property` `Find`
  `Search` `Output` `Wave`(WaveForm + SignalTable) `RoAI`
- `ToolBox` 里的模块库可读：`addsub` `andgate` `bin2gray` …（带英文说明）
- 模型画布自带 `Graph / Code` 两个页签 —— 这就是“CodeView”，不必再找 `View→Codeview`
- **Output 日志可读**（`window_text()` / `get_value()` / `ValuePattern` 都返回全文）

## 4. 已跑通的真实驱动链（已验证）

`File→Open` → 原生文件对话框（class `#32770`，103 个控件，文件名 ComboBox `auto_id=1148`，按钮“打开”）
→ 在文件名框输入 `C:\dev\robei-mcp\workspace\robei_samples\two_level\two_level.proj` → `ENTER`
→ 工程真的打开了（FileTree 显示 `two_level / model / test / const / verilog`）
→ `Build→Compile` → Output 追加：

```
|>File: D:/robei-lab/robei/project/test1/model/and0_mode.model saved.
|>File: D:/robei-lab/robei/project/test1/model/and0.model saved.
|>Check complete with 0 warnings.
|>Generating C:/dev/robei-mcp/workspace/robei_samples/two_level/build/and0.v...
|>Compile done.
```

同时落盘：`build/and0.v`（91 字节空模块）、`build/and0`、`build/filelist.vf`、`model/and0.model`。
三个文件随后已删除，`workspace/robei_samples/two_level` 回到 L1 之前的状态。

注意：Compile 编译的是**当前打开的 model**。当时画布上没有打开任何 model，Robei 自动新建了空模型 `and0`，
所以产物是空模块；要编译 `top_two` 得先在 FileTree 里双击打开它。

## 5. 坑（写代码时要处理）

1. `SendMessageTimeoutW(..., OBJID_UIA/-16)` 之后再在同一进程里查 UIA 树会得到 0 个控件；
   同一进程另起一次查询（不先发 -25/-16）又正常。没必要发这两个消息就别发。
2. `UIAElementInfo` 没有 `is_enabled` 属性（这个 pywinauto 版本），要用控件对象的 `is_enabled()`。
3. `FileTree` 的 `auto_id` 在树里有 6 个同名元素，`child_window()` 会抛
   `ElementAmbiguousError`，要用 `descendants()` 再自己筛。
4. 工具栏按钮 `control_type` 是 `CheckBox` 不是 `Button`。
5. 文件名 ComboBox 的 `name` 是标签“文件名(N):”，不是内容；`set_edit_text` 在
   `ComboBoxWrapper` 上不存在，要点击后 `^a` + 键入。
6. 菜单弹窗要重新枚举窗口列表（`Qt680QWindowPopupDropShadowSaveBits`），它不在主窗口子树里；
   点击前先 `set_focus()`，否则偶发点空（已加 3 次重试）。
7. 原生文件对话框里 `ESC` 会直接取消；自动补全下拉（`ComboLBox`）会挡住按钮，用 `ENTER` 提交。

## 6. 仍未验证

- `Code` 页签里能不能读/写 Verilog 文本（下一步）
- FileTree 双击打开 model → Compile `top_two` → 产物是否两层展开
- `Run` / `Wave` 的窗口（仿真与波形）能否通过 UIA 读
- `Register` 对话框：按约定不点，遇到就停
