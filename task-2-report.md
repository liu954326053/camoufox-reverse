# Task 2 修复报告

## 状态

snapshot hardening 已完成并验证。注册 artifact 的 validation-to-registry
替换窗口已通过稳定 copy snapshot 闭合。

## 统一 Schema

1. 唯一隐藏目录为 `raw/.snapshots/`。
2. registry/index 的注册 artifact 使用
   `path=raw/.snapshots/<sha256(source_path + digest)>.bin`，并保留
   `source_path=原始 raw 路径`。
3. snapshot 从锁内已验证的源 fd 复制，关闭写入后重新从 snapshot fd 校验
   inode identity、size 和 SHA-256；完成后 write-protect。
4. `index.files` 只记录逻辑 source 的当前状态，隐藏 snapshot 不参与普通 raw
   扫描；重建注册 artifact 只读取稳定 snapshot。
5. registry 按 `source_path` 归组，排序首键为逻辑 source path，继续使用完整
   tie-breaker 保证确定性。

## 竞态回归

回归测试在最终源校验后、registry append 前替换原路径，证明 registry/index
仍绑定已观察字节；source 当前 hash 单独出现在 `index.files`，snapshot 不会被
列入普通文件列表。snapshot 复制或 open-fd 校验失败时注册拒绝，不产生 stale
成功 artifact。

## 验证

- `python3 -m pytest tests/test_reverse_evidence.py -q`：`26 passed`
- `python3 -m pytest -q`：`296 passed, 4 skipped`
- `py_compile`：通过
- `git diff --check`：通过
