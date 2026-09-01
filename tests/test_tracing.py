from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from meeting_agent.model import RuleBasedMeetingModel
from meeting_agent.runtime import MeetingAgentRuntime
from meeting_agent.tracing import debug_event_to_trace


class PresentationTraceTest(unittest.TestCase):
    def test_trace_error_does_not_expose_exception_text(self):
        raw = "회의록 원문 전체가 포함된 모델 오류"

        trace = debug_event_to_trace(
            (),
            {
                "type": "task_result",
                "step": 1,
                "payload": {
                    "name": "Q3_question_response",
                    "result": None,
                    "error": RuntimeError(raw),
                },
            },
        )

        self.assertNotIn(raw, str(trace))

    def test_runtime_yields_trace_before_final_result(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = MeetingAgentRuntime(
                Path(directory) / "app.db",
                Path(directory) / "checkpoint.db",
                RuleBasedMeetingModel(),
            )
            try:
                runtime.seed()
                events = list(
                    runtime.iter_agent_traced(
                        "user-eric",
                        "stream-thread",
                        "meeting-001 회의록을 가져오고 결정 사항을 설명해줘",
                    )
                )
            finally:
                runtime.close()

        self.assertEqual(events[-1]["type"], "final")
        first_node = next(index for index, event in enumerate(events) if event["type"] == "node")
        self.assertLess(first_node, len(events) - 1)
        self.assertNotIn("transcript", str(events).lower())

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
                self.assertIn("S1_candidate_search_and_judgment", nodes)
                self.assertIn("Q1_tool_2_source_lookup", nodes)
                self.assertIn("Q2B_build_source_context", nodes)
                self.assertIn("Q3_question_response", nodes)
                decisions = [
                    event.get("decision")
                    for event in trace
                    if event.get("node") == "llm_goal_condition"
                    and event.get("phase") == "end"
                ]
                self.assertIn("search", decisions)
                self.assertEqual(decisions, ["search", "question"])
                self.assertNotIn("done", decisions)
                self.assertNotIn("transcript", str(trace).lower())
                self.assertTrue(any(event.get("type") == "state" for event in trace))
                self.assertNotIn("graph_runtime_checkpoint", nodes)
                self.assertNotIn("request_finished", nodes)
                self.assertNotIn(
                    "결정 사항: 회의 녹화는 결정적 Workflow로 유지한다. "
                    "검색과 질문은 LangGraph Agent로 구현한다. SQLite POC를 먼저 만든다.",
                    str(trace),
                )
            finally:
                runtime.close()

    def test_recording_trace_streams_real_graph_then_checkpoint_update(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = MeetingAgentRuntime(
                Path(directory) / "app.db",
                Path(directory) / "checkpoint.db",
                RuleBasedMeetingModel(),
            )
            try:
                runtime.seed()
                trace = list(
                    runtime.iter_recording_traced(
                        "user-eric", "recording-trace", "start"
                    )
                )
            finally:
                runtime.close()

        self.assertEqual(trace[-1]["type"], "final")
        nodes = [event for event in trace if event.get("type") == "node"]
        self.assertEqual(nodes[0]["graph"], "Recording Workflow")
        self.assertEqual(nodes[0]["node"], "recording_modal_and_backend")
        self.assertTrue(any(event.get("edge") == "→ END" for event in trace))
        checkpoint = next(
            event for event in trace if event.get("type") == "business_checkpoint"
        )
        self.assertEqual(checkpoint["state"], {"recording_modal_status": "healthy"})


if __name__ == "__main__":
    unittest.main()
