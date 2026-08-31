from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from meeting_agent.model import RuleBasedMeetingModel
from meeting_agent.runtime import MeetingAgentRuntime
from meeting_agent.web_demo import recording_trace


class PresentationTraceTest(unittest.TestCase):
    def test_trace_contains_parent_and_both_subgraphs_without_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = MeetingAgentRuntime(
                Path(directory) / "app.db",
                Path(directory) / "checkpoint.db",
                RuleBasedMeetingModel(),
            )
            try:
                runtime.seed()
                result, trace = runtime.run_agent_traced(
                    "user-eric",
                    "trace-thread",
                    "meeting-001 회의록을 가져오고 결정 사항을 설명해줘",
                )
                self.assertIn("결정 사항", result["response"])
                graphs = {event["graph"] for event in trace}
                self.assertIn("Agent Graph", graphs)
                self.assertIn("Search Subgraph", graphs)
                self.assertIn("Question Subgraph", graphs)
                nodes = [event.get("node") for event in trace]
                self.assertIn("S1_tool_1_search", nodes)
                self.assertIn("Q1_tool_2_context_and_answer", nodes)
                self.assertNotIn("transcript", str(trace).lower())
            finally:
                runtime.close()

    def test_recording_trace_shows_deterministic_node_and_end_edge(self):
        trace = recording_trace(
            "start",
            {
                "previous_state": "none",
                "current_state": "recording",
                "recording_modal_status": "healthy",
            },
        )
        self.assertEqual(trace[0]["graph"], "Recording Workflow")
        self.assertEqual(trace[0]["node"], "recording_modal_and_backend")
        self.assertEqual(trace[-1]["edge"], "→ END")


if __name__ == "__main__":
    unittest.main()
