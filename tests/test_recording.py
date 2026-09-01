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

    def test_modal_execution_error_is_persisted_only_as_agent_status(self):
        secret = "SECRET_MODAL_CONNECTION_DETAIL"

        def fail_modal(user_id, thread_id, command):
            raise ConnectionError(secret)

        self.runtime.repository.execute_recording_command = fail_modal
        result = self.runtime.run_recording("user-eric", "thread-error", "start")
        self.assertEqual(result["recording_modal_status"], "error")
        self.assertEqual(
            self.runtime.repository.get_modal_status(
                '["recording-modal","user-eric","thread-error"]'
            ),
            "error",
        )
        state = self.runtime.get_agent_state("user-eric", "thread-error")
        self.assertEqual(
            set(state), {"ScratchPad", "authorized_meeting_ids", "recording_modal_status"}
        )
        self.assertEqual(state["recording_modal_status"], "error")
        self.assertNotIn("recording_state", state)
        self.assertNotIn(secret, result["response"])

    def test_modal_error_handler_hides_secondary_repository_failure(self):
        secret = "SECONDARY_REPOSITORY_SECRET"

        def fail_repository(*args, **kwargs):
            raise ConnectionError(secret)

        self.runtime.repository.execute_recording_command = fail_repository
        self.runtime.repository.set_modal_status = fail_repository
        self.runtime.repository.get_recording_state = fail_repository

        result = self.runtime.run_recording(
            "user-eric", "thread-secondary-error", "start"
        )

        self.assertEqual(result["recording_modal_status"], "error")
        self.assertNotIn(secret, str(result))


if __name__ == "__main__":
    unittest.main()
