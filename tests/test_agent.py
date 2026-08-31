from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from meeting_agent.agent_graph import MODEL_CONTEXT_BUDGET_CHARS
from meeting_agent.model import RuleBasedMeetingModel
from meeting_agent.runtime import MeetingAgentRuntime, interrupt_payload


def current_goal_events(result):
    events = result.get("ScratchPad", [])
    start = max(
        (index for index, item in enumerate(events) if item.get("kind") == "user_request"),
        default=0,
    )
    return events[start:]


def collect_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from collect_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from collect_keys(child)


class ContextSpyModel(RuleBasedMeetingModel):
    def __init__(self):
        self.candidate_interpretations = 0
        self.answer_context = None

    def interpret_candidate_count(self, request, candidates, scratchpad):
        self.candidate_interpretations += 1
        return super().interpret_candidate_count(request, candidates, scratchpad)

    def answer_question(
        self, system_instruction, request, scratchpad, documents, tool_result
    ):
        self.answer_context = {
            "system_instruction": system_instruction,
            "scratchpad": scratchpad,
            "tool_result": tool_result,
        }
        return super().answer_question(
            system_instruction, request, scratchpad, documents, tool_result
        )


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
        )
        self.assertIsNone(interrupt_payload(result))
        self.assertEqual(result["authorized_meeting_ids"], ["meeting-001"])
        self.assertIn("SQLite POC", result["response"])
        kinds = [item["kind"] for item in current_goal_events(result)]
        self.assertIn("search_response", kinds)
        self.assertIn("question_response", kinds)
        self.assertGreaterEqual(kinds.count("business_checkpoint"), 2)

    def test_persisted_agent_state_has_only_three_design_fields(self):
        self.runtime.run_agent("user-eric", "thread-minimal", "meeting-001 회의록 찾아줘")
        expected = {"ScratchPad", "authorized_meeting_ids", "recording_modal_status"}
        self.assertEqual(set(self.runtime.get_agent_state("thread-minimal")), expected)
        for state in self.runtime.list_agent_checkpoint_states("thread-minimal"):
            self.assertTrue(set(state).issubset(expected))
            self.assertNotIn("request_id", set(collect_keys(state)))
            self.assertNotIn("recording_state", set(collect_keys(state)))

    def test_raw_transcript_is_not_checkpointed_in_agent_state(self):
        self.runtime.run_agent(
            "user-eric",
            "thread-no-raw",
            "meeting-003 회의록을 가져오고 결정 내용을 설명해줘",
        )
        for state in self.runtime.list_agent_checkpoint_states("thread-no-raw"):
            self.assertNotIn("transcript", set(collect_keys(state)))

    def test_multiple_candidates_interrupt_and_resume(self):
        result = self.runtime.run_agent("user-eric", "thread-hitl", "회의록 검색해줘")
        payload = interrupt_payload(result)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["kind"], "meeting_selection")
        resumed = self.runtime.resume_agent(
            "thread-hitl", {"meeting_ids": ["meeting-002"]}
        )
        self.assertIsNone(interrupt_payload(resumed))
        self.assertEqual(resumed["authorized_meeting_ids"], ["meeting-002"])

    def test_repeated_search_for_same_authorized_id_needs_no_merge_hitl(self):
        first = self.runtime.run_agent(
            "user-eric", "thread-repeat", "meeting-001 회의록을 찾아줘"
        )
        self.assertIsNone(interrupt_payload(first))
        second = self.runtime.run_agent(
            "user-eric",
            "thread-repeat",
            "meeting-001 회의록을 가져오고 결정 사항을 설명해줘",
        )
        self.assertIsNone(interrupt_payload(second))
        self.assertEqual(second["authorized_meeting_ids"], ["meeting-001"])
        self.assertIn("결정 사항", second["response"])

    def test_backend_filters_unauthorized_explicit_id(self):
        result = self.runtime.run_agent(
            "user-eric", "thread-denied", "meeting-004 회의록을 가져와줘"
        )
        self.assertEqual(result.get("authorized_meeting_ids", []), [])
        self.assertIn("찾지 못했습니다", result["response"])

    def test_model_handles_candidate_count_and_full_question_context(self):
        self.runtime.close()
        root = Path(self.temp.name)
        model = ContextSpyModel()
        self.runtime = MeetingAgentRuntime(
            root / "spy-app.db", root / "spy-checkpoints.db", model=model
        )
        self.runtime.seed()
        self.runtime.run_agent(
            "user-eric",
            "thread-context",
            "meeting-001 회의록을 가져오고 결정 사항을 설명해줘",
        )
        self.assertGreater(model.candidate_interpretations, 0)
        self.assertTrue(model.answer_context["system_instruction"])
        self.assertTrue(model.answer_context["scratchpad"])
        self.assertEqual(model.answer_context["tool_result"]["status"], "source_ready")

    def test_tool_failure_is_retried_and_structured_for_model(self):
        attempts = 0

        def fail_search(user_id, request):
            nonlocal attempts
            attempts += 1
            raise RuntimeError("backend unavailable")

        self.runtime.repository.search_meetings = fail_search
        result = self.runtime.run_agent("user-eric", "thread-failure", "회의록 검색해줘")
        event = next(
            item for item in current_goal_events(result) if item.get("kind") == "search_response"
        )
        self.assertEqual(attempts, 2)
        self.assertEqual(event["status"], "failed")
        self.assertEqual(event["error"]["code"], "TOOL_EXECUTION_FAILED")
        self.assertIn("완료하지 못했습니다", result["response"])

    def test_scratchpad_compacts_at_half_context_budget(self):
        config = self.runtime._config("thread-compact", "meeting-agent")
        large_events = [
            {"kind": "old_event", "payload": "x" * (MODEL_CONTEXT_BUDGET_CHARS // 2)}
        ]
        self.runtime.agent_graph.update_state(
            config,
            {
                "ScratchPad": large_events,
                "authorized_meeting_ids": [],
                "recording_modal_status": "healthy",
            },
        )
        self.runtime.run_agent("user-eric", "thread-compact", "안녕")
        state = self.runtime.get_agent_state("thread-compact")
        self.assertEqual(state["ScratchPad"][0]["kind"], "compact_summary")
        self.assertLess(len(str(state["ScratchPad"])), MODEL_CONTEXT_BUDGET_CHARS)


if __name__ == "__main__":
    unittest.main()
