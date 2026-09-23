# Task 2 修复报告

## 状态

已完成 Task 2 review 中的 P1/P2 修复。实现提交：`9c31fff`（`fix: harden raw evidence immutability and recovery`）。

## 修复项

1. `rebuild_index()` 在 session manifest 为 `complete` 时拒绝写入，保留既有 index。
2. artifact 写入改为基于 POSIX directory fd 的逐级 `openat`/`mkdirat` 语义，父目录和最终文件均使用 `O_NOFOLLOW`，消除普通 symlink/TOCTOU 路径替换窗口。
3. registry 先 fsync 追加，manifest 更新失败时保留 registry 作为恢复来源；后续 `rebuild_index()` 会 reconciliation manifest，并覆盖 manifest 写入故障注入测试。
4. `register_artifact()` 必须打开实际 regular file，并校验声明的 `size` 与 SHA-256。
5. artifact sort 增加 kind、size、content hash、字节数、事件数及 canonical JSON 作为完整 tie-breaker。
6. 增加公开 `EvidenceStore.finalize()`，先 flush/rebuild index，再调用 `ReverseSession.close()`；`EvidenceStore.close()` 作为兼容别名，明确完整收尾入口。

## 验证

- `python3 -m pytest tests/test_reverse_evidence.py -q`：`17 passed`
- `python3 -m pytest tests/test_reverse_evidence.py tests/test_reverse_project.py -q`：`62 passed`
- `python3 -m pytest tests/test_reverse_evidence.py tests/test_reverse_launch.py tests/test_reverse_mcp_contract.py -q`：`48 passed`
- `git diff --check`：通过

## Concerns

- 直接调用 `ReverseSession.close()` 仍只负责 manifest 生命周期，不会自动生成 evidence index；调用方应使用 `EvidenceStore.finalize()` 或 `EvidenceStore.close()` 完成 evidence session 收尾。
- 本次未修改 CLI、launch 或 `reverse_project.py`；工作区中与 Task 6 相关的未跟踪文件未纳入提交。
