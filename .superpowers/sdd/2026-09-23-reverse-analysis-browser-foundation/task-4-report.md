# Task 4 Report: Reverse CLI

## Status

IMPLEMENTED

Implementation commit: `420dafeac7ccea48f59240288709b49c55c0e892`

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
107 passed

python3 -m pip install --no-deps --editable .
reverse-browser --help
```

The installed help output lists `launch`, `session`, `trace`, and `report`. `compileall` and `git diff --check` also passed.

## Concerns

- The CLI `launch` command prepares a project-scoped launch session and returns its metadata through `reverse_launch_options()`. It does not hold an interactive browser process open because the existing core API returns browser launch options and leaves browser ownership to its caller.
- Complete sessions remain immutable by the existing evidence contract. `trace index` therefore reports a non-zero JSON error for a complete session; `report build` can consume an existing index and otherwise rebuilds an incomplete session index.
- The workspace contained unrelated untracked files (`artifacts/`, `.web-reverse-tool-dir`, and `task-6-report.md`); they were left untouched and were not included in the commit.
