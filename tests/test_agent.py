from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from meeting_agent.runtime import MeetingAgentRuntime, interrupt_payload


class AgentGraphTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.runtime = MeetingAgentRuntime(root / "app.db", root / "checkpoints.db")
        self.runtime.seed()

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def test_compound_search_then_question(self):
        result = self.runtime.run_agent(
            "user-eric",
            "thread-compound",
            "meeting-001 회의록을 가져오고 결정 사항을 설명해줘",
            request_id="request-compound",
        )
        self.assertIsNone(interrupt_payload(result))
        self.assertEqual(result["authorized_meeting_ids"], ["meeting-001"])
        self.assertIn("SQLite POC", result["response"])
        kinds = [item["kind"] for item in result["ScratchPad"] if item.get("request_id") == "request-compound"]
        self.assertIn("search_response", kinds)
        self.assertIn("question_response", kinds)
        self.assertGreaterEqual(kinds.count("business_checkpoint"), 2)

    def test_raw_transcript_is_not_checkpointed_in_agent_state(self):
        result = self.runtime.run_agent(
            "user-eric",
            "thread-no-raw",
            "meeting-003 회의록을 가져오고 결정 내용을 설명해줘",
            request_id="request-no-raw",
        )
        def collect_keys(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield key
                    yield from collect_keys(child)
            elif isinstance(value, list):
                for child in value:
                    yield from collect_keys(child)

        self.assertNotIn("transcript", set(collect_keys(result)))
        self.assertIn("보안 검토 회의", result["response"])

    def test_multiple_candidates_interrupt_and_resume(self):
        result = self.runtime.run_agent(
            "user-eric",
            "thread-hitl",
            "회의록 검색해줘",
            request_id="request-hitl",
        )
        payload = interrupt_payload(result)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["kind"], "meeting_selection")
        resumed = self.runtime.resume_agent(
            "thread-hitl", {"meeting_ids": ["meeting-002"]}
        )
        self.assertIsNone(interrupt_payload(resumed))
        self.assertEqual(resumed["authorized_meeting_ids"], ["meeting-002"])

    def test_backend_filters_unauthorized_explicit_id(self):
        result = self.runtime.run_agent(
            "user-eric",
            "thread-denied",
            "meeting-004 회의록을 가져와줘",
            request_id="request-denied",
        )
        self.assertEqual(result.get("authorized_meeting_ids", []), [])
        self.assertIn("찾지 못했습니다", result["response"])


if __name__ == "__main__":
    unittest.main()
