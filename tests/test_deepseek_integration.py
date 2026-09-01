from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from meeting_agent.model import DeepSeekMeetingModel
from meeting_agent.runtime import MeetingAgentRuntime, interrupt_payload


class DeepSeekIntegrationTest(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("DEEPSEEK_API_KEY_FILE"),
        "DEEPSEEK_API_KEY_FILE을 지정한 명시적 실제 API 테스트에서만 실행합니다.",
    )
    def test_real_model_search_then_question(self):
        key_file = os.environ["DEEPSEEK_API_KEY_FILE"]
        model_name = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
        model = DeepSeekMeetingModel.from_key_file(key_file, model=model_name)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with MeetingAgentRuntime(
                root / "app.db", root / "checkpoints.db", model=model
            ) as runtime:
                runtime.seed()
                result = runtime.run_agent(
                    "user-eric",
                    "thread-real-deepseek",
                    "meeting-001 회의록을 가져오고 결정 사항을 설명해줘",
                )

        self.assertIsNone(interrupt_payload(result))
        self.assertEqual(result["authorized_meeting_ids"], ["meeting-001"])
        self.assertTrue(result["response"].strip())
        event_kinds = [event.get("kind") for event in result["ScratchPad"]]
        self.assertIn("search_response", event_kinds)
        self.assertIn("question_response", event_kinds)
        actions = [
            event.get("action")
            for event in result["ScratchPad"]
            if event.get("kind") == "llm_decision"
        ]
        self.assertEqual(actions, ["search", "question"])


if __name__ == "__main__":
    unittest.main()
