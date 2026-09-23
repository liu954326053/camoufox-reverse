# Task 4 Report: Reverse CLI

## Status

IMPLEMENTED - FINAL FIXED

Initial implementation commit: `420dafeac7ccea48f59240288709b49c55c0e892`
Final fix commit: `e653362a86a03d72e40a05bb61ad4f04e8fc4aea`

## Delivered

- Added `reverse-browser` as a Poetry console script.
- Added Click commands:
  - `launch --project-dir PATH [--proxy URL] [--browser-version VERSION] [--trace-profile PROFILE]`
  - `session list --project-dir PATH`
  - `trace index --project-dir PATH --session SESSION_ID`
  - `report build --project-dir PATH --session SESSION_ID`
- Added `ReverseProject.get_session()` and `ReverseProject.list_sessions()` so the CLI uses the existing project/session validation and manifest lifecycle code instead of reimplementing path handling.
- `trace index` delegates to `EvidenceStore.rebuild_index()`.
- `report build` writes only derived JSON below `runs/<session>/report/` and leaves raw evidence untouched.
- All successful command output is machine-readable JSON. Error paths return non-zero with JSON error output.
- Launch output contains only session IDs and explicit artifact paths; launch options and proxy credentials are not printed.
- Report and derived files use user-only file/directory permissions and atomic replacement.
- Registered `propertyTrace` as a `dict` property in `settings/properties.json`, so trace configuration validation is silent and does not pollute CLI stdout.
- Added `enable_trace=True` to `reverse_launch_options()` with backward-compatible default behavior; `False` removes caller trace config and skips trace-directory setup.
- Passed the runtime trace switch through `AsyncReverseBrowser`; `--no-trace` now produces launch options without `propertyTrace.enabled`.
- If browser context/page setup fails after Camoufox enters, the runtime exits the underlying context before marking the session incomplete.
- `ReverseProject.list_sessions()` now reads the public `manifest_snapshot()` accessor.

## Verification

Run from `pythonlib/`:

```text
python3 -m pytest tests/test_reverse_cli.py -q
6 passed

python3 -m pytest tests/test_reverse_cli.py tests/test_server.py -q
14 passed

python3 -m pytest tests/test_reverse_project.py tests/test_reverse_evidence.py tests/test_reverse_launch.py -q
72 passed

python3 -m pytest tests/test_reverse_*.py tests/test_server.py -q
130 passed

python3 -m pip install --no-deps --editable .
reverse-browser --help
```

The installed help output lists `launch`, `session`, `trace`, and `report`. Focused Task4 tests pass with `77 passed`; `compileall` and `git diff --check` also passed.

Real smoke:

```text
reverse-browser launch --headless --no-trace --duration 0
exit 0
stdout parses as one JSON object
manifest status: complete
trace directory created: false
```

## Concerns

- The CLI `launch` command prepares a project-scoped launch session and returns its metadata through `reverse_launch_options()`. It does not hold an interactive browser process open because the existing core API returns browser launch options and leaves browser ownership to its caller.
- Complete sessions remain immutable by the existing evidence contract. `trace index` therefore reports a non-zero JSON error for a complete session; `report build` can consume an existing index and otherwise rebuilds an incomplete session index.
- The checked-in `settings/properties.json` is the source schema used for packaged browser resources. The currently cached local Camoufox bundle predates this `propertyTrace` entry; a trace-enabled real launch against that stale cache can still print its old unknown-property warning until the bundle is rebuilt/repackaged. The required `--no-trace` real smoke does not inject that configuration and is clean.
- The workspace contained unrelated untracked files (`artifacts/`, `.web-reverse-tool-dir`, and `task-6-report.md`); they were left untouched and were not included in the commit.
