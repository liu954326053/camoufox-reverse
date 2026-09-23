# Task 3: Reverse Launch Configuration

## Status

Implemented and verified on September 24, 2026.

## Delivered

- Added `camoufox.reverse_launch.reverse_launch_options` with mandatory
  `project_dir` and optional session resume.
- Added isolated `session.path/trace` creation and exposed it as
  `session.trace_dir` for the reverse launch contract.
- Added explicit `overview`, `targeted`, and `deep` PropertyTracer profiles.
- Merged `config.propertyTrace` without removing caller fingerprint settings or
  caller trace fields; `enabled` and the session `logDir` are enforced.
- Normalized proxy URLs into Playwright's `{server, username, password}` form.
  Credentials are not written to the manifest or printed.
- Passed `browser_version` through the existing `browser` launch selector.
- Updated the session manifest to `running` only after launch options succeed,
  recording the selected profile, browser selector, and redacted proxy metadata.
- Exported `reverse_launch_options` from `camoufox` while leaving ordinary
  `launch_options` unchanged.

## Verification

Commands run from `pythonlib`:

```text
python3 -m pytest tests/test_reverse_launch.py -q
7 passed

python3 -m pytest tests/test_reverse_launch.py tests/test_server.py tests/test_reverse_project.py -q
45 passed
```

Additional checks:

- `python3 -m compileall -q pythonlib/camoufox/reverse_launch.py pythonlib/camoufox/__init__.py`
- `git diff --check`

## Concerns

- Task 1's committed `ReverseSession` does not expose a native `trace_dir`
  property or a public `running` transition method. Task 3 keeps the requested
  file boundary by attaching `trace_dir` during reverse launch and using the
  existing atomic manifest writer for the status update. A later compatibility
  cleanup can move those lifecycle primitives into `reverse_project.py`.
- The profile object whitelist and event caps are launch-layer defaults. The
  native tracer still owns event filtering and runtime behavior.
