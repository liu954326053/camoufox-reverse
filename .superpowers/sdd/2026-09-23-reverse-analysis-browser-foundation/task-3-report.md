# Task 3: Reverse Launch Configuration

## Status

Implemented and verified on September 24, 2026.

## Delivered

- Added `camoufox.reverse_launch.reverse_launch_options` with mandatory
  `project_dir` and optional session resume.
- Added a stable public `ReverseSession.trace_dir` path and isolated
  `session.path/trace` creation for the reverse launch contract.
- Added explicit `overview`, `targeted`, and `deep` PropertyTracer profiles.
- Merged `config.propertyTrace` without removing caller fingerprint settings or
  caller trace fields; `enabled` and the session `logDir` are enforced.
- Normalized proxy URLs into Playwright's `{server, username, password}` form.
  Credentials are not written to the manifest or printed.
- Passed `browser_version` through the existing `browser` launch selector.
- Added public `ReverseSession.mark_running(...)` lifecycle transition that
  reads the current manifest before atomically recording the selected profile,
  browser selector, and redacted proxy metadata.
- Any post-session initialization failure, including proxy validation and
  `launch_options()` failure, calls public `session.mark_incomplete(...)` before
  re-raising the original exception.
- Exported `reverse_launch_options` from `camoufox` while leaving ordinary
  `launch_options` unchanged.

## Verification

Commands run from `pythonlib`:

```text
python3 -m pytest tests/test_reverse_launch.py tests/test_reverse_project.py -q
43 passed

python3 -m pytest tests/test_reverse_launch.py tests/test_server.py -q
15 passed
```

Additional checks:

- `python3 -m compileall -q pythonlib/camoufox/reverse_launch.py pythonlib/camoufox/__init__.py`
- `git diff --check`

## Concerns

- The profile object whitelist and event caps are launch-layer defaults. The
  native tracer still owns event filtering and runtime behavior.
- Full `pythonlib` verification passed with `241 passed, 4 skipped`.
