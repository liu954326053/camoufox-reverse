# 逆向分析浏览器基础平台实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `camoufox-reverse` 的 Python 启动层建立工程目录、隔离 session、原始证据落盘、索引重建和 CLI/MCP 可复用的基础平台。

**Architecture:** 新增一个与普通 Camoufox API 隔离的 reverse project/session 核心。它负责校验 `project_dir`、创建 session、生成 manifest、注入 PropertyTracer 的 `logDir`，并提供原始证据索引；同步/异步启动器、CLI 和外部 MCP 桥接只调用这套核心。MCP 桥接源码位于本机独立 checkout，Task 8 将把已确认的 JSON 契约接入真实 `launch_browser`、trace 和 artifact 生命周期。

**Tech Stack:** Python 3.10+、现有 `pathlib`/`json`/`orjson`、Click、Playwright Python、Camoufox `CAMOU_CONFIG`/PropertyTracer、pytest。

## Global Constraints

- `project_dir` 必填；缺少、不可写、路径穿越或符号链接逃逸时启动失败。
- 每次启动默认创建全新的隔离 `session_id`；只有显式 `resume_session` 才能恢复旧 session。
- 原始网络、脚本、Cookie、Storage、动态值和执行事件保存到 session 的 `raw/`，不脱敏。
- 原始证据只追加；索引、报告和派生分析写入 `derived/` 或 `report/`，不能覆盖 raw。
- 浏览器二进制和普通依赖缓存留在系统缓存目录；分析产物全部进入 `project_dir`。
- 默认不上传、不遥测、不自动提交 Git；stdout/stderr 不打印密码、Cookie 或 token。
- 第一阶段只提供 Python 核心、CLI 契约和 MCP 可调用接口，不实现独立 GUI。
- 第一阶段不实现 SpiderMonkey 全量 opcode 记录，也不把浏览器作为 `web_google` 运行时依赖。
- 所有任务保持现有 Camoufox 普通 API 兼容；reverse 能力通过新模块和显式入口启用。

## File Map

- Create: `pythonlib/camoufox/reverse_project.py` — 工程目录、session 生命周期、权限和 manifest。
- Create: `pythonlib/camoufox/reverse_evidence.py` — raw 事件追加、文件登记、索引重建和 session 查询。
- Create: `pythonlib/camoufox/reverse_launch.py` — reverse 启动配置、代理转换和 PropertyTracer 配置注入。
- Create: `pythonlib/camoufox/reverse_cli.py` — `reverse-browser` CLI 薄入口。
- Modify: `pythonlib/camoufox/__init__.py` — 导出稳定的 reverse Python API。
- Modify: `pythonlib/pyproject.toml` — 注册 `reverse-browser` console script。
- Test: `pythonlib/tests/test_reverse_project.py` — 工程和 session 生命周期。
- Test: `pythonlib/tests/test_reverse_evidence.py` — raw 追加、索引和原子文件登记。
- Test: `pythonlib/tests/test_reverse_launch.py` — 启动配置和 PropertyTracer 注入。
- Test: `pythonlib/tests/test_reverse_cli.py` — CLI 参数和 JSON 输出。
- Modify: `tests/test_property_tracer_runtime.py` — 补充外部 `logDir` session 目录验收所需的最小运行断言。
- Test: `tests/test_reverse_project_contract.py` — native tracer 与工程 session 路径契约。
- Modify: `/Users/magic/.codex/mcp-servers/camoufox-reverse-mcp/source/src/camoufox_reverse_mcp/__main__.py` — MCP 启动参数。
- Modify: `/Users/magic/.codex/mcp-servers/camoufox-reverse-mcp/source/src/camoufox_reverse_mcp/browser.py` — browser/session 传递和关闭生命周期。
- Modify: `/Users/magic/.codex/mcp-servers/camoufox-reverse-mcp/source/src/camoufox_reverse_mcp/property_trace.py` — project-scoped trace root。
- Modify: `/Users/magic/.codex/mcp-servers/camoufox-reverse-mcp/source/src/camoufox_reverse_mcp/tools/navigation.py` — `launch_browser(project_dir, capture_profile, ...)`。
- Modify: `/Users/magic/.codex/mcp-servers/camoufox-reverse-mcp/source/src/camoufox_reverse_mcp/tools/trace.py` — session-scoped trace queries。
- Modify: `/Users/magic/.codex/mcp-servers/camoufox-reverse-mcp/source/src/camoufox_reverse_mcp/tools/environment.py` — session-aware environment report。
- Test: `/Users/magic/.codex/mcp-servers/camoufox-reverse-mcp/source/tests/test_browser.py` — real MCP browser lifecycle contract。
- Test: `/Users/magic/.codex/mcp-servers/camoufox-reverse-mcp/source/tests/test_tools.py` — tool request/error/path contract。
- Create: `docs/superpowers/plans/2026-09-23-reverse-analysis-browser-foundation.md` — 本计划。
- Later, separate plan: SpiderMonkey 深度执行 hook、VM adapter 和值生成链分析，不在本计划实施。

## Stable Interfaces

### Project/session

```python
from camoufox.reverse_project import ReverseProject, ReverseSession

project = ReverseProject.open("/absolute/project")
session = project.create_session()
session.manifest_path
session.raw_dir
session.trace_dir
session.close(status="complete")
```

Required behavior:

- `ReverseProject.open(project_dir: str | Path) -> ReverseProject`
- `ReverseProject.create_session(resume_session: str | None = None) -> ReverseSession`
- `ReverseProject.list_sessions() -> list[dict[str, object]]`
- `ReverseSession.mark_incomplete(reason: str) -> None`
- `ReverseSession.close(status: Literal["complete", "incomplete"]) -> None`

### Evidence

```python
from camoufox.reverse_evidence import EvidenceStore

store = EvidenceStore(session)
store.append_jsonl("trace/events.jsonl", {"kind": "script", "id": "..."})
store.register_artifact("raw/scripts/a.js", sha256="...", size=123)
store.rebuild_index()
```

`EvidenceStore` must reject paths outside the current session, preserve raw bytes, write files with user-only permissions, and make index rebuild deterministic.

### Reverse launch

```python
from camoufox.reverse_launch import reverse_launch_options

options, session = reverse_launch_options(
    project_dir="/absolute/project",
    proxy="http://127.0.0.1:7890",
    browser_version="whitenightshadow/152.0.4-beta.30-reverse.5",
    trace_profile="targeted",
)
```

The returned Playwright launch options must contain a `CAMOU_CONFIG` payload whose `propertyTrace.logDir` points inside `session.trace_dir`. The caller owns browser close and must close the session in a `finally` block.

---

### Task 1: Project and Session Lifecycle

**Files:**
- Create: `pythonlib/camoufox/reverse_project.py`
- Test: `pythonlib/tests/test_reverse_project.py`

**Interfaces:**
- Consumes: filesystem path and optional `resume_session`.
- Produces: `ReverseProject`, `ReverseSession`, manifest paths and lifecycle status used by Tasks 2–4.

- [ ] **Step 1: Write failing tests for required project directory behavior**

Add tests for:

```python
def test_open_rejects_missing_project_dir_argument():
    with pytest.raises(TypeError):
        ReverseProject.open()

def test_open_creates_only_the_requested_leaf(tmp_path):
    project = ReverseProject.open(tmp_path / "analysis")
    assert project.path == (tmp_path / "analysis").resolve()
    assert (project.path / "runs").is_dir()

def test_open_rejects_file_project_path(tmp_path):
    path = tmp_path / "project-file"
    path.write_text("fixture")
    with pytest.raises(ProjectError):
        ReverseProject.open(path)
```

- [ ] **Step 2: Run the focused tests and verify the expected failure**

Run:

```sh
cd pythonlib
python -m pytest tests/test_reverse_project.py -q
```

Expected: collection or assertion failure because `reverse_project.py` does not exist.

- [ ] **Step 3: Implement path validation and permission setup**

Implement `ReverseProject.open()` so it:

1. Requires a non-empty path.
2. Resolves the path without following a final symlink into another project.
3. Creates the requested leaf and `runs/`/`indexes/` with mode `0700` where supported.
4. Rejects a regular file, unreadable directory, or path whose parent is not writable.
5. Never falls back to a temporary directory.

- [ ] **Step 4: Add isolated session tests before implementation**

Add tests for two sessions:

```python
def test_each_launch_gets_a_unique_isolated_session(tmp_path):
    project = ReverseProject.open(tmp_path / "analysis")
    first = project.create_session()
    second = project.create_session()
    assert first.session_id != second.session_id
    assert first.path != second.path
    assert first.raw_dir != second.raw_dir
```

Also test that `resume_session` only resumes an existing `incomplete` session. A `complete` session is immutable and must be rejected, so restoring it cannot overwrite original evidence.

- [ ] **Step 5: Implement session manifest and lifecycle**

Create `manifest.json` atomically with:

```json
{
  "schema": 1,
  "session_id": "...",
  "status": "starting",
  "capture_mode": "raw",
  "contains_sensitive_data": true,
  "telemetry": false,
  "started_at": "...",
  "ended_at": null,
  "event_loss": 0,
  "artifacts": []
}
```

Implement `mark_incomplete()` for exceptions and `close()` for complete/incomplete finalization. Never print secret fields while reporting errors.

- [ ] **Step 6: Run the focused project tests**

Run:

```sh
cd pythonlib
python -m pytest tests/test_reverse_project.py -q
```

Expected: all lifecycle, permission, isolation and incomplete-session tests pass.

- [ ] **Step 7: Commit the session boundary**

```sh
git add pythonlib/camoufox/reverse_project.py pythonlib/tests/test_reverse_project.py
git commit -m "feat: add reverse analysis project sessions"
```

### Task 2: Raw Evidence Store and Deterministic Index

**Files:**
- Create: `pythonlib/camoufox/reverse_evidence.py`
- Test: `pythonlib/tests/test_reverse_evidence.py`

**Interfaces:**
- Consumes: `ReverseSession` from Task 1.
- Produces: append-only JSONL, raw artifact registry and deterministic `indexes/session.json`.

- [ ] **Step 1: Write failing tests for raw append and path containment**

Cover complete raw values and traversal rejection:

```python
def test_append_jsonl_preserves_raw_value(tmp_path):
    session = ReverseProject.open(tmp_path / "p").create_session()
    store = EvidenceStore(session)
    store.append_jsonl("raw/network/0001.jsonl", {"body": "raw-secret-fixture"})
    assert "raw-secret-fixture" in (session.path / "raw/network/0001.jsonl").read_text()

def test_artifact_path_cannot_escape_session(tmp_path):
    session = ReverseProject.open(tmp_path / "p").create_session()
    with pytest.raises(EvidenceError):
        EvidenceStore(session).append_bytes("../outside", b"bad")
```

- [ ] **Step 2: Run the focused tests and verify failure**

```sh
cd pythonlib
python -m pytest tests/test_reverse_evidence.py -q
```

Expected: failure because `EvidenceStore` is not implemented.

- [ ] **Step 3: Implement atomic raw writers**

Implement:

- `append_jsonl(relative_path, event)` with one JSON object per line;
- `append_bytes(relative_path, data)` for scripts, responses and screenshots;
- `register_artifact(relative_path, sha256, size, kind)`;
- user-only file mode `0600` where supported;
- append locks or session-exclusive file naming for multi-process writers;
- flush and `fsync` at session stop.

Raw values must not pass through redaction, truncation or lossy Unicode conversion.

- [ ] **Step 4: Add deterministic index rebuild tests**

Create two raw files in different creation orders and assert `rebuild_index()` produces stable sorting by session-relative path, event sequence and artifact hash. Assert that a second rebuild does not modify raw files.

- [ ] **Step 5: Implement index and event-loss accounting**

Write `indexes/<session_id>.json` with artifact list, per-file byte counts, event counts, hashes and `event_loss`. The index must be rebuildable after an incomplete session without requiring a running browser.

- [ ] **Step 6: Run evidence tests and a filesystem permission check**

```sh
cd pythonlib
python -m pytest tests/test_reverse_evidence.py -q
```

Expected: raw content, containment, append ordering, index determinism and permission tests pass.

- [ ] **Step 7: Commit the evidence layer**

```sh
git add pythonlib/camoufox/reverse_evidence.py pythonlib/tests/test_reverse_evidence.py
git commit -m "feat: add raw evidence store and session indexes"
```

### Task 3: Reverse Launch Configuration

**Files:**
- Create: `pythonlib/camoufox/reverse_launch.py`
- Modify: `pythonlib/camoufox/__init__.py`
- Test: `pythonlib/tests/test_reverse_launch.py`

**Interfaces:**
- Consumes: `ReverseProject`, `ReverseSession` and existing `launch_options()`.
- Produces: Playwright launch options, session manifest updates and `propertyTrace.logDir` under the session.

- [ ] **Step 1: Write failing tests for mandatory project and trace configuration**

Mock `camoufox.utils.launch_options` and assert:

```python
def test_reverse_launch_requires_project_dir():
    with pytest.raises(TypeError):
        reverse_launch_options()

def test_reverse_launch_points_property_trace_inside_session(tmp_path, monkeypatch):
    monkeypatch.setattr("camoufox.reverse_launch.launch_options", lambda **kwargs: kwargs)
    options, session = reverse_launch_options(
        project_dir=tmp_path / "p",
        proxy="http://127.0.0.1:7890",
        trace_profile="targeted",
    )
    assert options["config"]["propertyTrace"]["logDir"] == str(session.trace_dir)
    assert options["config"]["propertyTrace"]["enabled"] is True
```

- [ ] **Step 2: Run the focused tests and verify failure**

```sh
cd pythonlib
python -m pytest tests/test_reverse_launch.py -q
```

Expected: failure because the reverse launch module is absent.

- [ ] **Step 3: Implement profile and proxy normalization**

Implement `reverse_launch_options(project_dir, proxy=None, browser_version=None, trace_profile="overview", resume_session=None, **kwargs)`:

1. Open or create the `ReverseProject` and session.
2. Map `overview`, `targeted`, and `deep` to explicit trace configuration.
3. Convert a proxy URL to the existing Playwright proxy dictionary without printing credentials.
4. Merge `config.propertyTrace` without discarding caller-supplied Camoufox fingerprint config.
5. Select `browser_version` through the existing `browser` launch option.
6. Mark the session `running` only after options are built successfully.
7. Return `(options, session)` and leave browser ownership to the caller.

- [ ] **Step 4: Add compatibility tests for sync and async wrappers**

Test that `from camoufox import reverse_launch_options` is available, ordinary `launch_options()` behavior is unchanged, and caller config values remain present beside `propertyTrace`.

- [ ] **Step 5: Run launch tests and existing utility tests**

```sh
cd pythonlib
python -m pytest tests/test_reverse_launch.py tests/test_server.py -q
```

Expected: reverse tests pass and existing server tests remain green.

- [ ] **Step 6: Commit launch integration**

```sh
git add pythonlib/camoufox/reverse_launch.py pythonlib/camoufox/__init__.py pythonlib/tests/test_reverse_launch.py
git commit -m "feat: add project-scoped reverse launch options"
```

### Task 4: Reverse CLI

**Files:**
- Create: `pythonlib/camoufox/reverse_cli.py`
- Modify: `pythonlib/pyproject.toml`
- Test: `pythonlib/tests/test_reverse_cli.py`

**Interfaces:**
- Consumes: Task 1 session and Task 2 evidence APIs.
- Produces: `reverse-browser` commands that use the same core as MCP.

- [ ] **Step 1: Write failing CLI tests**

Test these exact behaviors:

```python
def test_launch_requires_project_dir(runner):
    result = runner.invoke(cli, ["launch"])
    assert result.exit_code != 0
    assert "project-dir" in result.output

def test_session_list_returns_json(runner, tmp_path):
    result = runner.invoke(cli, ["session", "list", "--project-dir", str(tmp_path / "p")])
    assert result.exit_code == 0
    assert json.loads(result.output)["sessions"] == []
```

- [ ] **Step 2: Run CLI tests and verify failure**

```sh
cd pythonlib
python -m pytest tests/test_reverse_cli.py -q
```

Expected: failure because `reverse_cli` and its console entrypoint do not exist.

- [ ] **Step 3: Implement the thin Click CLI**

Register:

```toml
[tool.poetry.scripts]
reverse-browser = "camoufox.reverse_cli:cli"
```

Implement:

- `launch --project-dir PATH [--proxy URL] [--browser-version VERSION] [--trace-profile PROFILE]`;
- `session list --project-dir PATH`;
- `trace index --project-dir PATH --session SESSION_ID`;
- `report build --project-dir PATH --session SESSION_ID`.

Commands must print machine-readable JSON by default, never raw secret values, and return non-zero on invalid paths or incomplete operations. Browser launch/close handling must call `session.mark_incomplete()` on exceptions.

- [ ] **Step 4: Run CLI and packaging tests**

```sh
cd pythonlib
python -m pytest tests/test_reverse_cli.py tests/test_server.py -q
python -m pip install --no-deps --editable .
reverse-browser --help
```

Expected: CLI help lists the reverse commands and the focused tests pass.

- [ ] **Step 5: Commit the CLI**

```sh
git add pythonlib/camoufox/reverse_cli.py pythonlib/pyproject.toml pythonlib/tests/test_reverse_cli.py
git commit -m "feat: add reverse-browser CLI"
```

### Task 5: PropertyTracer Session Wiring and Capabilities

**Files:**
- Modify: `settings/camoufox-reverse-capabilities.json`
- Modify: `tests/test_property_tracer_runtime.py`
- Test: `tests/test_reverse_project_contract.py`

**Interfaces:**
- Consumes: `propertyTrace.logDir` generated by Task 3.
- Produces: verifiable contract that native PropertyTracer writes under the requested session directory and exposes loss/status metadata for index rebuild.

- [ ] **Step 1: Add a contract test for an externally supplied trace directory**

Extend the native harness to set the trace base directory to a session `trace/` path and assert every JSONL file is below that directory, has `k`, `q`, `u`, `w`, and `s`, and no control file remains after shutdown.

- [ ] **Step 2: Run the native tracer test and verify the new assertion fails or is incomplete**

```sh
python -m pytest tests/test_property_tracer_runtime.py -q
```

Expected: the existing test remains green while the new session-path assertion identifies any missing contract field or path behavior.

- [ ] **Step 3: Make only the required native contract changes**

Do not change the 77 existing hook meanings. Only adjust capabilities metadata or status/event fields if the session store requires a field that is not already emitted. Preserve protocol-v1 compatibility and the existing opt-in behavior.

- [ ] **Step 4: Run all tracer and injector tests**

```sh
python -m pytest tests/test_property_tracer_runtime.py tests/test_inject_trace_to_source.py -q
```

Expected: native buffering, control transitions, source injection, event kinds, sequence and path checks pass.

- [ ] **Step 5: Commit the contract update**

```sh
git add settings/camoufox-reverse-capabilities.json tests/test_property_tracer_runtime.py tests/test_reverse_project_contract.py
git commit -m "test: verify project-scoped property tracing"
```

### Task 6: MCP Adapter Contract

**Files:**
- Create: `docs/reverse-browser-mcp-contract.md`
- Create: `pythonlib/tests/test_reverse_mcp_contract.py`

**Interfaces:**
- Consumes: Task 1–5 Python APIs.
- Produces: a stable JSON contract that the external `mcp__camoufox_reverse` bridge can implement without duplicating project/session logic.

- [ ] **Step 1: Write contract fixtures and failing validation tests**

Define request/response fixtures for:

```json
{
  "project_dir": "/absolute/project",
  "proxy": "http://127.0.0.1:7890",
  "trace_profile": "targeted"
}
```

and assert responses contain only paths, IDs, status, counts and explicit artifact references, never implicit temporary directories.

- [ ] **Step 2: Run contract tests and verify failure**

```sh
cd pythonlib
python -m pytest tests/test_reverse_mcp_contract.py -q
```

Expected: failure until the schema and validator exist.

- [ ] **Step 3: Document MCP methods and implement local validator**

Document these methods:

- `launch_browser(project_dir, proxy, browser_version, trace_profile)`;
- `get_page_info()`;
- `network_capture(action, url_pattern)`;
- `list_network_requests(...)`;
- `scripts(action, url)`;
- `trace_property_access(...)`;
- `export_state(save_path)`;
- `close_browser()`.

The contract must state that `project_dir` is mandatory and that all returned artifact paths are inside the current session directory.

- [ ] **Step 4: Run contract and documentation checks**

```sh
cd pythonlib
python -m pytest tests/test_reverse_mcp_contract.py -q
git diff --check
```

- [ ] **Step 5: Commit the bridge contract**

```sh
git add docs/reverse-browser-mcp-contract.md pythonlib/tests/test_reverse_mcp_contract.py
git commit -m "docs: define reverse browser MCP contract"
```

### Task 7: Local Integration and Failure Recovery

**Files:**
- Create: `pythonlib/tests/test_reverse_integration.py`
- Modify: `README.md`
- Modify: `docs/releases/_template.md` only if the release notes need a capability entry.

**Interfaces:**
- Consumes: all foundation APIs.
- Produces: a local, browser-free integration test plus documented real-browser smoke commands.

- [ ] **Step 1: Write browser-free integration tests**

Cover this sequence:

```python
project = ReverseProject.open(project_dir)
session = project.create_session()
options, same_session = reverse_launch_options(project_dir=project_dir)
assert same_session.session_id != session.session_id
session.mark_incomplete("fixture crash")
assert project.list_sessions()[0]["status"] == "incomplete"
EvidenceStore(same_session).rebuild_index()
```

Also verify raw values survive index rebuilding and no output is written outside the project tree.

- [ ] **Step 2: Run the integration tests and the existing Python suite**

```sh
cd pythonlib
python -m pytest tests/test_reverse_*.py tests/test_server.py -q
```

Expected: all new foundation tests and existing server tests pass.

- [ ] **Step 3: Document the real-browser smoke test**

Add README instructions using the installed reverse selector and local proxy:

```sh
reverse-browser launch \
  --project-dir /absolute/path/to/project \
  --proxy http://127.0.0.1:7890 \
  --browser-version whitenightshadow/152.0.4-beta.30-reverse.5 \
  --trace-profile targeted
```

The documented acceptance checks must inspect `runs/<session_id>/manifest.json`, raw trace files, index rebuild and incomplete-session behavior. Do not claim Google end-to-end success in this foundation plan.

- [ ] **Step 4: Run final verification for the foundation**

```sh
cd pythonlib
python -m pytest tests/test_reverse_*.py tests/test_server.py -q
cd ..
python -m pytest tests/test_property_tracer_runtime.py tests/test_inject_trace_to_source.py -q
git diff --check
```

- [ ] **Step 5: Commit documentation and integration tests**

```sh
git add pythonlib/tests/test_reverse_integration.py README.md docs/releases/_template.md
git commit -m "test: verify reverse browser foundation"
```

### Task 8: Integrate the External MCP Bridge

**Files:**
- Modify: `/Users/magic/.codex/mcp-servers/camoufox-reverse-mcp/source/src/camoufox_reverse_mcp/__main__.py`
- Modify: `/Users/magic/.codex/mcp-servers/camoufox-reverse-mcp/source/src/camoufox_reverse_mcp/browser.py`
- Modify: `/Users/magic/.codex/mcp-servers/camoufox-reverse-mcp/source/src/camoufox_reverse_mcp/property_trace.py`
- Modify: `/Users/magic/.codex/mcp-servers/camoufox-reverse-mcp/source/src/camoufox_reverse_mcp/tools/navigation.py`
- Modify: `/Users/magic/.codex/mcp-servers/camoufox-reverse-mcp/source/src/camoufox_reverse_mcp/tools/trace.py`
- Modify: `/Users/magic/.codex/mcp-servers/camoufox-reverse-mcp/source/src/camoufox_reverse_mcp/tools/environment.py`
- Test: `/Users/magic/.codex/mcp-servers/camoufox-reverse-mcp/source/tests/test_browser.py`
- Test: `/Users/magic/.codex/mcp-servers/camoufox-reverse-mcp/source/tests/test_tools.py`

**Interfaces:**
- Consumes: Task 6 JSON contract and Task 3/4 Python project/session APIs.
- Produces: actual MCP calls that create project-scoped sessions and never fall back to `~/.cache/camoufox-reverse` for an opted-in project.

- [ ] **Step 1: Add failing bridge tests for mandatory project and raw capture**

Test `launch_browser` rejects missing, empty, relative and escaping project paths; accepts `capture_profile="raw"`; returns `session_id`, `session_dir`, `manifest` and `trace_dir`; and rejects unknown profiles before browser startup.

- [ ] **Step 2: Run the external MCP focused tests and verify failure**

```sh
cd /Users/magic/.codex/mcp-servers/camoufox-reverse-mcp/source
.venv/bin/python -m pytest tests/test_browser.py tests/test_tools.py -q
```

Expected: the new project/session contract tests fail against the current cache-root implementation.

- [ ] **Step 3: Thread project/session configuration through MCP startup**

Add `--project-dir` to the MCP process entrypoint and `project_dir`/`capture_profile` to `launch_browser`. `BrowserManager` must create exactly one session through the Python core, pass its trace root into `propertyTrace.logDir`, and retain the session until `close_browser()`.

- [ ] **Step 4: Make trace and environment tools session-aware**

Replace module-global `CACHE_DIR`, `CONTROL_DIR` and `TRACES_DIR` reads with the active session paths. `list_trace_files`, `query_trace_file`, `trace_property_access`, `collect_values`, and `check_environment` must return explicit paths inside the active session. No adapter-local temporary artifact path may be returned.

- [ ] **Step 5: Enforce raw artifact and close/error behavior**

Persist network captures, script saves, screenshots, state exports and trace files through the active session store. Normalize and resolve every returned path before containment checks. On launch failure, mark the session `incomplete`; on close, finalize the manifest; on a second close, return an already-closed status without creating a session.

- [ ] **Step 6: Run external MCP tests and the Python contract tests**

```sh
cd /Users/magic/.codex/mcp-servers/camoufox-reverse-mcp/source
.venv/bin/python -m pytest tests/test_browser.py tests/test_tools.py -q
cd /Users/magic/Workspace/camoufox-reverse/pythonlib
python3 -m pytest tests/test_reverse_mcp_contract.py -q
```

Expected: external bridge tests cover real lifecycle/path behavior and the repository contract remains green.

- [ ] **Step 7: Commit external bridge changes in its own repository**

```sh
cd /Users/magic/.codex/mcp-servers/camoufox-reverse-mcp/source
git add src tests
git commit -m "feat: add project-scoped reverse browser sessions"
```

Record the external commit hash in the foundation ledger and report; do not copy the external repository into `camoufox-reverse`.

## Separate Follow-up Plan: Deep Execution and VM Analysis

After this foundation and Task 8 pass, create a second plan before changing SpiderMonkey. That plan must cover:

- the exact Firefox source version and patch points;
- low-overhead script/frame tracing;
- dynamic `eval`/`Function` source capture;
- property/value snapshots and sensitive raw storage;
- asynchronous task and cross-process correlation;
- VM adapter interfaces for virtual PC/opcode/registers;
- browser-vs-Go first-divergence reports;
- JIT/debugger behavior and perturbation measurements;
- Google BotGuard adapter and `B4hajb` proof-boundary validation.

Do not add SpiderMonkey hooks as an incidental part of Tasks 1–7.

## Verification Matrix

| Requirement | Plan task | Evidence |
|---|---|---|
| Mandatory `project_dir` | 1, 3, 4 | Focused validation tests and CLI failure output |
| Isolated sessions | 1 | Session lifecycle tests and manifest status |
| Raw, non-redacted evidence | 2 | Byte-preservation tests and raw artifact files |
| Project-scoped PropertyTracer | 3, 5 | `logDir` contract and native JSONL tests |
| Index rebuild | 2, 7 | Deterministic index fixture tests |
| MCP/CLI shared core | 3, 4, 6 | Python API tests and MCP contract fixtures |
| No GUI | 4, 6 | CLI/MCP-only acceptance commands |
| Google first-stage coverage | 6, 7 | MCP contract and documented real-browser smoke; VM success remains follow-up |
