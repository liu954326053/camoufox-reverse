"""Static contract checks for project-scoped native PropertyTracer output."""

from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
CAPABILITIES = ROOT / "settings" / "camoufox-reverse-capabilities.json"


class ReverseProjectContractTests(unittest.TestCase):
    def test_property_trace_capabilities_declare_session_and_loss_contract(self):
        capabilities = json.loads(CAPABILITIES.read_text(encoding="utf-8"))

        self.assertEqual(capabilities["property_trace_protocol"], 1)
        self.assertEqual(capabilities["property_trace_hooks"], 77)
        self.assertIn("exclusive_session_files", capabilities["property_trace_features"])
        self.assertIn("loss_status", capabilities["property_trace_features"])
        self.assertEqual(
            capabilities["property_trace_status_fields"],
            ["state", "session_id", "events", "dropped", "detail"],
        )


if __name__ == "__main__":
    unittest.main()
