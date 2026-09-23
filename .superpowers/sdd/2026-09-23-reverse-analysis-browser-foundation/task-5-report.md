# Task 5 Report: PropertyTracer Session Contract Fix Loop 1

## Status

FIXED

Implementation commit: `d08102e` (`fix: persist property tracer session loss metadata`).

## Scope

Only the Task 5 native tracer contract files were changed:

- `additions/camoucfg/PropertyTracer.cpp`
- `additions/camoucfg/PropertyTracer.hpp`
- `settings/camoufox-reverse-capabilities.json`
- `tests/test_property_tracer_runtime.py`
- `tests/test_reverse_project_contract.py`

The commit was cleaned after an existing staged Task 6 change was detected; the
final Task 5 commit does not include `pythonlib` files.

## Fixes

1. Every closed trace session now writes a durable `traces/<pid>_<session>.jsonl.meta.json`
   sidecar containing `state`, `session_id`, `events`, and `dropped`; write errors
   are represented by optional `detail`. The sidecar is written after flush/fsync
   and descriptor close, while transient control/status files are still removed
   during shutdown.
2. `WriteStatus()`, `WriteSessionMetadata()`, and the shutdown dropped-event log
   take their counter snapshots under `mBufferMutex`. `mActiveSessionId` is atomic,
   so status output does not read the session id concurrently with session setup.
3. The obsolete `mSaturated` state was removed. Saturated calls continue entering
   the synchronized cap branch so every dropped event is counted.
4. Capabilities now declare `property_trace_metadata_artifact` as
   `traces/*.meta.json` and advertise `durable_loss_metadata`.

## Test coverage

The native harness now verifies:

- all JSONL events contain `k`, `q`, `u`, `w`, and `s`;
- capped sessions retain `events=2`, `dropped=3` in the sidecar after shutdown;
- sidecar count matches JSONL session count in a concurrent control-transition run;
- concurrent Record calls during repeated on/off transitions complete successfully;
- old protocol-v1 status `state session_id` parsing remains valid;
- additive `events`, `dropped`, and optional `detail` metadata parse correctly;
- control and status files are removed after shutdown;
- the 77-hook injector contract remains intact.

## Verification

Passed:

```text
python3 -m pytest --noconftest \
  tests/test_property_tracer_runtime.py \
  tests/test_inject_trace_to_source.py \
  tests/test_reverse_project_contract.py -q
15 passed in 2.22s

python3 -m unittest discover -s tests -p 'test_property_tracer_runtime.py' -v
OK

python3 -m unittest discover -s tests -p 'test_reverse_project_contract.py' -v
OK

python3 -m json.tool settings/camoufox-reverse-capabilities.json
git diff --check
```

The exact default pytest command remains blocked by the pre-existing missing
`pixelmatch` import in `tests/conftest.py`; the native/injector/contract suite
was run with `--noconftest` and passed all 15 tests.

## Remaining concerns

- The race-sensitive test exercises concurrent transitions but is not a
  ThreadSanitizer run; sanitizer availability is platform-dependent.
- The sidecar is flushed and atomically renamed, but this test does not simulate
  power loss or filesystem corruption.
