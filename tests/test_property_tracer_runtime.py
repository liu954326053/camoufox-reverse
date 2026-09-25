"""Native smoke tests for PropertyTracer buffering and control transitions."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "additions" / "camoucfg" / "PropertyTracer.cpp"
INCLUDE = ROOT / "additions" / "camoucfg"
SHUTDOWN_PATCH = ROOT / "patches" / "property-tracer-shutdown.patch"

# The Firefox 152 immediate-exit boundary, without unrelated XPCOM services.
# Apply the shipping patch to this fixture and execute the resulting function.
SHUTDOWN_FIXTURE = r'''/* immediate exit fixture */
#include "ShutdownPhase.h"
#ifdef XP_WIN
#  include <windows.h>
#  include "mozilla/PreXULSkeletonUI.h"
#else
#  include <unistd.h>
#endif

void AppShutdown::DoImmediateExit(int aExitCode) {
#ifdef XP_WIN
  HANDLE process = ::GetCurrentProcess();
  if (::TerminateProcess(process, aExitCode)) {
    ::WaitForSingleObject(process, INFINITE);
  }
  MOZ_CRASH("TerminateProcess failed.");
#else
  _exit(aExitCode);
#endif
}
'''

EXIT_HARNESS = r'''
#include "PropertyTracer.hpp"
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>
#include <thread>
#ifdef _WIN32
#  include <process.h>
#  define getpid _getpid
#else
#  include <unistd.h>
#endif

// EXIT_BOUNDARY

int RunExit(const std::string& base, const std::string& mode) {
  auto& tracer = camou::PropertyTracer::Instance();
  tracer.Initialize(base, {}, mode == "capped" ? 2 : 10000);
  for (int i = 0; i < 258; ++i) {
    tracer.Record("navigator", "userAgent", nullptr, 0, "exit@test");
  }
  if (mode == "abrupt") {
    // A killed producer may already have data on disk. Never infer completion.
    std::this_thread::sleep_for(std::chrono::milliseconds(150));
    std::_Exit(0);
  }
  if (mode == "metadata-error") {
    std::filesystem::create_directory(std::filesystem::u8path(
        base + "/traces/" + std::to_string(getpid()) + "_0.jsonl.meta.json"));
  }
  if (mode == "immediate") {
    AppShutdown::DoImmediateExit(0);
  }
  if (mode == "concurrent") {
    std::atomic<int> ready{0};
    std::atomic<bool> go{false};
    auto shutdown = [&]() {
      ready.fetch_add(1);
      while (!go.load()) std::this_thread::yield();
      tracer.Shutdown();
    };
    std::thread first(shutdown);
    std::thread second(shutdown);
    while (ready.load() != 2) std::this_thread::yield();
    go.store(true);
    // Hot-path calls may overlap the drain, but cannot append after the ACK.
    for (int i = 0; i < 4000; ++i) {
      tracer.Record("navigator", "platform", nullptr, 0, "shutdown-race@test");
    }
    first.join();
    second.join();
  }
  if (mode != "destructor") {
    tracer.Shutdown();
    tracer.Shutdown();  // Explicit shutdown plus later destructor is idempotent.
  }
  return 0;
}

#ifdef _WIN32
int wmain(int argc, wchar_t** argv) {
  if (argc != 3) return 2;
  return RunExit(std::filesystem::path(argv[1]).u8string(),
                 std::filesystem::path(argv[2]).u8string());
}
#else
int main(int argc, char** argv) {
  if (argc != 3) return 2;
  return RunExit(argv[1], argv[2]);
}
#endif
'''


def compile_harness(root: Path, source: str, name: str) -> Path:
    compiler = shutil.which("clang++") if os.name == "nt" else (
        shutil.which("c++") or shutil.which("g++") or shutil.which("clang++"))
    if not compiler:
        raise unittest.SkipTest("no C++ compiler available")
    harness = root / f"{name}.cpp"
    binary = root / (f"{name}.exe" if os.name == "nt" else name)
    harness.write_text(source, encoding="utf-8")
    subprocess.run([
        compiler, "-std=c++17",
        "-D_CRT_SECURE_NO_WARNINGS" if os.name == "nt" else "-pthread",
        "-Wall", "-Wextra", "-Wpedantic", "-Werror", f"-I{INCLUDE}",
        str(harness), str(SOURCE), "-o", str(binary),
    ], check=True, capture_output=True, text=True, timeout=60)
    return binary


class PropertyTracerExitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.binary = compile_harness(cls.root, EXIT_HARNESS.replace(
            "// EXIT_BOUNDARY",
            "struct AppShutdown { static void DoImmediateExit(int code) "
            "{ std::_Exit(code); } };"), "native_exit")

    def run_exit(self, mode, binary=None):
        root = self.root / self._testMethodName
        root.mkdir()
        child = subprocess.Popen([str(binary or self.binary), str(root), mode],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, stderr = child.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate()
            self.fail("native shutdown did not terminate")
        self.assertEqual(child.returncode, 0, (stdout, stderr))
        return root, child.pid

    def assert_finalized(self, root, pid, events=258, dropped=0):
        trace = root / "traces" / f"{pid}_0.jsonl"
        sidecar = Path(str(trace) + ".meta.json")
        self.assertTrue(sidecar.is_file(), "exiting process lost its metadata sidecar")
        metadata = json.loads(sidecar.read_text())
        self.assertEqual(metadata, {"state": "off", "session_id": 0,
                                    "events": events, "dropped": dropped})
        records = [json.loads(line) for line in trace.read_text().splitlines()]
        self.assertEqual([event["q"] for event in records], list(range(events)))
        status = root / "control" / f"status-{pid}.state"
        self.assertTrue(status.is_file(), "exiting process deleted its off ACK")
        self.assertEqual(parse_status_line(status.read_text()), metadata)
        self.assertFalse((root / "control" / f"control-{pid}.cmd").exists())

    def test_explicit_shutdown_retains_final_ack(self):
        self.assert_finalized(*self.run_exit("explicit"))

    def test_destructor_exit_retains_final_ack(self):
        self.assert_finalized(*self.run_exit("destructor"))

    def test_exit_keeps_event_loss_in_final_ack(self):
        self.assert_finalized(*self.run_exit("capped"), events=2, dropped=256)

    def test_concurrent_shutdown_and_recording_drain_once(self):
        root, pid = self.run_exit("concurrent")
        records = (root / "traces" / f"{pid}_0.jsonl").read_text().splitlines()
        self.assertGreaterEqual(len(records), 258)
        self.assertLessEqual(len(records), 4258)
        self.assert_finalized(root, pid, events=len(records))

    def test_metadata_failure_does_not_acknowledge_clean_stop(self):
        root, pid = self.run_exit("metadata-error")
        status = root / "control" / f"status-{pid}.state"
        self.assertTrue(status.is_file(), "metadata failure lost its final status")
        self.assertEqual(parse_status_line(status.read_text()).get("detail"), "write_error")

    def test_abrupt_exit_is_not_reported_complete(self):
        root, pid = self.run_exit("abrupt")
        trace = root / "traces" / f"{pid}_0.jsonl"
        self.assertGreater(trace.stat().st_size, 0)
        self.assertFalse(Path(str(trace) + ".meta.json").exists())
        status = root / "control" / f"status-{pid}.state"
        self.assertNotEqual(parse_status_line(status.read_text())["state"], "off")

    def test_patched_immediate_exit_drains_before_terminating(self):
        self.assertTrue(SHUTDOWN_PATCH.is_file(), "Firefox immediate exit has no tracer shutdown hook")
        patch = shutil.which("patch")
        if not patch:
            self.skipTest("patch command unavailable")
        fixture = self.root / "patch-fixture"
        source = fixture / "xpcom/base/AppShutdown.cpp"
        source.parent.mkdir(parents=True)
        source.write_text(SHUTDOWN_FIXTURE)
        subprocess.run([patch, "--batch", "--forward", "-p1", "-i", str(SHUTDOWN_PATCH)],
                       cwd=fixture, check=True, capture_output=True, text=True)
        patched = source.read_text()
        boundary = re.search(r"void AppShutdown::DoImmediateExit\(int aExitCode\) \{.*?\n\}",
                             patched, re.S).group()
        preamble = '''
#ifdef _WIN32
#include <windows.h>
#define XP_WIN
#define MOZ_CRASH(message) std::abort()
#endif
struct AppShutdown { static void DoImmediateExit(int); };
'''
        binary = compile_harness(self.root, EXIT_HARNESS.replace(
            "// EXIT_BOUNDARY", preamble + boundary), "patched_exit")
        self.assert_finalized(*self.run_exit("immediate", binary))

HARNESS = r"""
#include "PropertyTracer.hpp"

#include <chrono>
#include <filesystem>
#include <fstream>
#include <string>
#include <thread>
#include <vector>

#ifdef _WIN32
#  include <process.h>
#  define getpid _getpid
#else
#  include <unistd.h>
#endif

int Run(const std::string& base) {
  auto& tracer = camou::PropertyTracer::Instance();
  tracer.Initialize(base, {}, 10000);

  std::vector<std::thread> workers;
  for (int worker = 0; worker < 4; ++worker) {
    workers.emplace_back([&tracer, worker]() {
      for (int i = 0; i < 250; ++i) {
        const uint32_t kind = static_cast<uint32_t>((worker + i) % 3);
        tracer.Record("navigator", "userAgent", nullptr, kind,
                      "navigator.userAgent@dom/base/Navigator.cpp");
      }
    });
  }
  for (auto& worker : workers) worker.join();

  const std::string control = base + "/control/control-" +
                              std::to_string(getpid()) + ".cmd";
  const std::string status = base + "/control/status-" +
                             std::to_string(getpid()) + ".state";
  { std::ofstream file(std::filesystem::u8path(control)); file << "off"; }
  std::this_thread::sleep_for(std::chrono::milliseconds(180));
  {
    std::ifstream file(std::filesystem::u8path(status));
    std::string state;
    file >> state;
    if (state != "off") return 3;
  }
  for (int i = 0; i < 20; ++i) {
    tracer.Record("document", "cookie.set", nullptr, 1,
                  "document.cookie.set@dom/base/Document.cpp");
  }

  { std::ofstream file(std::filesystem::u8path(base + "/desired.state")); file << "on"; }
  { std::ofstream file(std::filesystem::u8path(control)); file << "on"; }
  std::this_thread::sleep_for(std::chrono::milliseconds(180));
  {
    std::ifstream file(std::filesystem::u8path(status));
    std::string state;
    file >> state;
    if (state != "on") return 4;
  }
  for (int i = 0; i < 25; ++i) {
    tracer.Record("canvas", "getContext", nullptr, 2,
                  "canvas.getContext@dom/html/HTMLCanvasElement.cpp");
  }
  tracer.Shutdown();

  // A newly-created process/session must honor the run-level desired state.
  { std::ofstream file(std::filesystem::u8path(base + "/desired.state")); file << "off"; }
  tracer.Initialize(base, {}, 10000);
  for (int i = 0; i < 20; ++i) {
    tracer.Record("window", "innerWidth", nullptr, 0, "window.innerWidth@test");
  }
  {
    std::ifstream file(std::filesystem::u8path(status));
    std::string state;
    file >> state;
    if (state != "off") return 5;
  }
  { std::ofstream file(std::filesystem::u8path(base + "/desired.state")); file << "on"; }
  { std::ofstream file(std::filesystem::u8path(control)); file << "on"; }
  std::this_thread::sleep_for(std::chrono::milliseconds(180));
  for (int i = 0; i < 5; ++i) {
    tracer.Record("window", "innerWidth", nullptr, 0, "window.innerWidth@test");
  }
  tracer.Shutdown();

  // A capped session must expose loss metadata before its status file is
  // removed, so a session index can account for dropped events.
  const std::string lossBase = base + "/loss-status";
  std::filesystem::create_directories(std::filesystem::u8path(lossBase));
  { std::ofstream file(std::filesystem::u8path(lossBase + "/desired.state")); file << "on"; }
  tracer.Initialize(lossBase, {}, 2);
  for (int i = 0; i < 5; ++i) {
    tracer.Record("window", "innerWidth", nullptr, 0, "window.innerWidth@test");
  }
  const std::string lossControl = lossBase + "/control/control-" +
                                  std::to_string(getpid()) + ".cmd";
  const std::string lossStatus = lossBase + "/control/status-" +
                                 std::to_string(getpid()) + ".state";
  { std::ofstream file(std::filesystem::u8path(lossControl)); file << "off"; }
  std::this_thread::sleep_for(std::chrono::milliseconds(180));
  {
    std::ifstream file(std::filesystem::u8path(lossStatus));
    std::string state;
    std::getline(file, state);
    if (state.find("dropped=3") == std::string::npos) return 6;
    std::ofstream observed(std::filesystem::u8path(lossBase + "/status-observed.txt"));
    observed << state << "\n";
  }
  tracer.Shutdown();

  // Status transitions must remain safe while the hot path records concurrently.
  const std::string raceBase = base + "/race-status";
  std::filesystem::create_directories(std::filesystem::u8path(raceBase));
  { std::ofstream file(std::filesystem::u8path(raceBase + "/desired.state")); file << "on"; }
  tracer.Initialize(raceBase, {}, 100000);
  const std::string raceControl = raceBase + "/control/control-" +
                                  std::to_string(getpid()) + ".cmd";
  std::vector<std::thread> raceWorkers;
  for (int worker = 0; worker < 8; ++worker) {
    raceWorkers.emplace_back([&tracer]() {
      for (int i = 0; i < 2000; ++i) {
        tracer.Record("navigator", "platform", nullptr, 0,
                      "navigator.platform@race");
      }
    });
  }
  for (int transition = 0; transition < 12; ++transition) {
    { std::ofstream file(std::filesystem::u8path(raceControl)); file << "off"; }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    { std::ofstream file(std::filesystem::u8path(raceControl)); file << "on"; }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  for (auto& worker : raceWorkers) worker.join();
  tracer.Shutdown();
  return 0;
}

#ifdef _WIN32
int wmain(int argc, wchar_t** argv) {
  if (argc != 2) return 2;
  return Run(std::filesystem::path(argv[1]).u8string());
}
#else
int main(int argc, char** argv) {
  if (argc != 2) return 2;
  return Run(argv[1]);
}
#endif
"""


class PropertyTracerRuntimeTests(unittest.TestCase):
    def test_buffered_events_are_complete_typed_and_drained(self):
        if os.name == "nt":
            compiler = shutil.which("clang++")
        else:
            compiler = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("no C++ compiler available")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            harness = root / "property_tracer_harness.cpp"
            binary = root / (
                "property_tracer_harness.exe" if os.name == "nt"
                else "property_tracer_harness"
            )
            trace_root = root / "trace-run-追踪测试"
            trace_root.mkdir()
            harness.write_text(textwrap.dedent(HARNESS), encoding="utf-8")
            command = [
                compiler,
                "-std=c++17",
                "-Wall",
                "-Wextra",
                "-Wpedantic",
                "-Werror",
                f"-I{INCLUDE}",
                str(harness),
                str(SOURCE),
                "-o",
                str(binary),
            ]
            if os.name == "nt":
                command.insert(2, "-D_CRT_SECURE_NO_WARNINGS")
            else:
                command.insert(2, "-pthread")
            subprocess.run(
                command,
                check=True,
            )
            subprocess.run([str(binary), str(trace_root)], check=True, timeout=20)

            files = sorted(trace_root.rglob("*.jsonl"))
            self.assertGreaterEqual(len(files), 5)
            self.assertTrue(all(trace_root in path.parents for path in files))
            sessions = {}
            for path in files:
                events = [json.loads(line) for line in path.read_text().splitlines()]
                sessions[path] = events
                for event in events:
                    self.assertTrue({"k", "q", "u", "w", "s"} <= event.keys())
                self.assertEqual([event["q"] for event in events], list(range(len(events))))
                self.assertTrue(all(event["w"] > 0 and event["u"] >= 0 for event in events))
                self.assertTrue(all(event["s"] for event in events))

            normal_sessions = [events for path, events in sessions.items()
                               if path.parent == trace_root / "traces"]
            loss_sessions = [events for path, events in sessions.items()
                             if path.parent == trace_root / "loss-status" / "traces"]
            self.assertEqual([len(events) for events in normal_sessions], [1000, 25, 5])
            self.assertEqual({event["k"] for event in normal_sessions[0]}, {0, 1, 2})
            self.assertEqual({event["k"] for event in normal_sessions[1]}, {2})
            self.assertEqual({event["k"] for event in normal_sessions[2]}, {0})
            self.assertEqual([len(events) for events in loss_sessions], [2])
            self.assertEqual(list(trace_root.rglob("control-*.cmd")), [])
            final_statuses = list(trace_root.rglob("status-*.state"))
            self.assertEqual(len(final_statuses), 3)
            for status in final_statuses:
                self.assertEqual(parse_status_line(status.read_text())["state"], "off")

            loss_metadata = list(
                (trace_root / "loss-status" / "traces").glob("*.meta.json")
            )
            self.assertEqual(len(loss_metadata), 1)
            metadata = json.loads(loss_metadata[0].read_text(encoding="utf-8"))
            self.assertEqual(
                metadata,
                {
                    "state": "off",
                    "session_id": int(loss_metadata[0].stem.split("_")[1].split(".")[0]),
                    "events": 2,
                    "dropped": 3,
                },
            )

            observed_status = (
                trace_root / "loss-status" / "status-observed.txt"
            ).read_text(encoding="utf-8").strip()
            self.assertEqual(
                parse_status_line("on 7"),
                {"state": "on", "session_id": 7},
            )
            self.assertEqual(
                parse_status_line(observed_status),
                {"state": "off", "session_id": metadata["session_id"], "events": 2, "dropped": 3},
            )
            self.assertEqual(
                parse_status_line("off 2 events=2 dropped=3 write_error"),
                {
                    "state": "off",
                    "session_id": 2,
                    "events": 2,
                    "dropped": 3,
                    "detail": "write_error",
                },
            )

            race_traces = list((trace_root / "race-status" / "traces").glob("*.jsonl"))
            race_metadata = list((trace_root / "race-status" / "traces").glob("*.meta.json"))
            self.assertGreaterEqual(len(race_traces), 1)
            self.assertEqual(len(race_traces), len(race_metadata))
            for metadata_path in race_metadata:
                parsed = json.loads(metadata_path.read_text(encoding="utf-8"))
                self.assertEqual(set(parsed), {"state", "session_id", "events", "dropped"})
                self.assertEqual(parsed["state"], "off")


def parse_status_line(line: str) -> dict[str, int | str]:
    fields = line.split()
    result: dict[str, int | str] = {
        "state": fields[0],
        "session_id": int(fields[1]),
    }
    for field in fields[2:]:
        if "=" not in field:
            result["detail"] = field
            continue
        key, value = field.split("=", 1)
        if key in {"events", "dropped"}:
            result[key] = int(value)
    return result


if __name__ == "__main__":
    unittest.main()
