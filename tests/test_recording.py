from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from meeting_agent.runtime import MeetingAgentRuntime


class RecordingWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.runtime = MeetingAgentRuntime(root / "app.db", root / "checkpoints.db")
        self.runtime.seed()

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def test_recording_state_machine(self):
        started = self.runtime.run_recording("user-eric", "thread-recording", "start")
        self.assertEqual(started["current_state"], "recording")
        paused = self.runtime.run_recording("user-eric", "thread-recording", "pause")
        self.assertEqual(paused["current_state"], "pause")
        resumed = self.runtime.run_recording("user-eric", "thread-recording", "resume")
        self.assertEqual(resumed["current_state"], "recording")
        stopped = self.runtime.run_recording("user-eric", "thread-recording", "stop")
        self.assertEqual(stopped["current_state"], "none")
        self.assertIn("stop → none", stopped["response"])

    def test_normal_state_rejection_keeps_modal_healthy(self):
        rejected = self.runtime.run_recording("user-eric", "thread-reject", "pause")
        self.assertEqual(rejected["current_state"], "none")
        self.assertEqual(rejected["recording_modal_status"], "healthy")
        self.assertIn("실행할 수 없습니다", rejected["response"])


if __name__ == "__main__":
    unittest.main()

