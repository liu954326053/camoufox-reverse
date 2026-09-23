# Task 7：Local Integration and Failure Recovery

日期：2026-09-23

## 结果

已完成 browser-free reverse-browser 本地集成覆盖，未修改核心实现。测试覆盖：

- `ReverseProject.open()` 创建 project，`create_session()` 创建初始 session。
- `reverse_launch_options()` 复用同一 project 但生成独立 session，并保留 targeted profile、browser selector 和 trace 目录配置。
- `mark_incomplete()` 后 `list_sessions()` 返回 `incomplete`，并可通过 `resume_session` 恢复为 `starting`。
- `EvidenceStore` 写入原始字节和 JSONL 值后重建 index，raw 内容不被转换或覆盖。
- traversal 写入被拒绝，manifest、trace、index 和配置中的 trace 路径均保持在 project tree 内。

## 变更文件

- `pythonlib/tests/test_reverse_integration.py`
  - 新增 3 个 browser-free 集成测试。
- `README.md`
  - 新增 `reverse-browser launch` 本地 smoke 命令和验收检查项。
  - 明确该 smoke 不代表 Google 导航或 Google 端到端成功。
- `docs/releases/_template.md`
  - 未修改；Task 7 未引入需要单独记录的 release capability entry。

## 验证

通过：

```text
python3 -m pytest pythonlib/tests/test_reverse_integration.py -q
3 passed in 0.41s

python3 -m pytest pythonlib/tests/test_reverse_*.py pythonlib/tests/test_server.py -q
134 passed in 1.71s

git diff --check
```

未执行成功：

```text
python3 -m pytest tests/test_property_tracer_runtime.py tests/test_inject_trace_to_source.py -q
ImportError while loading conftest ...
ModuleNotFoundError: No module named 'pixelmatch'
```

该失败发生在根目录 `tests/conftest.py:29` 的依赖导入阶段，未进入测试用例；本次 Task 7 没有修改或补装该依赖。

## 范围与限制

- 未启动真实浏览器，集成测试保持 browser-free。
- 未执行真实目标页面导航、真实网络 capture 或 PropertyTracer 事件验收。
- 未宣称 Google 端到端成功。
- 当前 checkout 有其他既有脏改动；本次提交按路径精确暂存，不包含这些改动。
