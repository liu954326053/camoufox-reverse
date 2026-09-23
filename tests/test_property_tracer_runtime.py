"""Native smoke tests for PropertyTracer buffering and control transitions."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "additions" / "camoucfg" / "PropertyTracer.cpp"
INCLUDE = ROOT / "additions" / "camoucfg"

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
  }
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
            self.assertEqual(len(files), 4)
            self.assertTrue(all(trace_root in path.parents for path in files))
            sessions = {}
            for path in files:
                events = [json.loads(line) for line in path.read_text().splitlines()]
                sessions[path] = events
                self.assertTrue({"k", "q", "u", "w", "s"} <= events[0].keys())
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
            self.assertEqual(list(trace_root.rglob("status-*.state")), [])


if __name__ == "__main__":
    unittest.main()
