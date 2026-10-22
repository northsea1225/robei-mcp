# 归档：一次性 GUI 探针

这些 `probe*.py` 是 2026-10-22 前后为了摸清 Robei 的 UIA 树、菜单、New 对话框、
画布与 Wave 面板写的一次性脚本（共 65 个）。结论已经整理进
`docs/gui-tools.md` 与 `docs/L1-uia-bridge.md`，这里只作为「当时是怎么试的」证据保留。

- 不再维护，也不要当成 API 使用
- 它们依赖当时的窗口布局和屏幕坐标，换个分辨率就会失效
- 仍然在用的是上一层的 `probe.py`（通用探针）和 `probe_push.py`（交互会话里 `git push`）
