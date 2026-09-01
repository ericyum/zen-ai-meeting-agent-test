from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from meeting_agent.agent_graph import MODEL_CONTEXT_BUDGET_CHARS, compact_scratchpad_events
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

    def interpret_candidate_count(self, request, candidate_count, scratchpad):
        self.candidate_interpretations += 1
        return super().interpret_candidate_count(request, candidate_count, scratchpad)

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


class OrderedModel(RuleBasedMeetingModel):
    def __init__(self):
        self.roles = []

    def decide_next_action(self, request, request_events):
        self.roles.append("goal")
        return super().decide_next_action(request, request_events)

    def interpret_candidate_count(self, request, candidate_count, scratchpad):
        self.roles.append("candidate")
        return super().interpret_candidate_count(request, candidate_count, scratchpad)

    def search_response(self, request, selected, mode, tool_result, scratchpad):
        self.roles.append("search_response")
        return super().search_response(request, selected, mode, tool_result, scratchpad)

    def answer_question(
        self, system_instruction, request, scratchpad, documents, tool_result
    ):
        self.roles.append("question_response")
        return super().answer_question(
            system_instruction, request, scratchpad, documents, tool_result
        )


class PromptBoundarySpyModel(RuleBasedMeetingModel):
    def __init__(self):
        self.model_inputs = []

    def decide_next_action(self, request, request_events):
        self.model_inputs.append(request_events)
        return super().decide_next_action(request, request_events)

    def interpret_candidate_count(self, request, candidate_count, scratchpad):
        self.model_inputs.extend([candidate_count, scratchpad])
        return super().interpret_candidate_count(request, candidate_count, scratchpad)

    def search_response(self, request, selected, mode, tool_result, scratchpad):
        self.model_inputs.extend([selected, tool_result, scratchpad])
        return super().search_response(request, selected, mode, tool_result, scratchpad)

    def answer_question(
        self, system_instruction, request, scratchpad, documents, tool_result
    ):
        self.model_inputs.extend([scratchpad, documents, tool_result])
        return super().answer_question(
            system_instruction, request, scratchpad, documents, tool_result
        )


class FullSourceEchoModel(RuleBasedMeetingModel):
    def answer_question(
        self, system_instruction, request, scratchpad, documents, tool_result
    ):
        return {"response": documents[0]["transcript"], "follow_up": False}


class ExtraFieldModel(RuleBasedMeetingModel):
    def answer_question(
        self, system_instruction, request, scratchpad, documents, tool_result
    ):
        return {
            "response": "안전한 답변",
            "follow_up": False,
            "transcript": documents[0]["transcript"],
        }


class OversizedSearchModel(RuleBasedMeetingModel):
    def __init__(self):
        self.goal_inputs = []

    def decide_next_action(self, request, request_events):
        self.goal_inputs.append(request_events)
        return super().decide_next_action(request, request_events)

    def search_response(self, request, selected, mode, tool_result, scratchpad):
        return {
            "response": "COMPACT_MEANING_SURVIVES " + "x" * MODEL_CONTEXT_BUDGET_CHARS,
            "follow_up": True,
        }


class RepeatedSearchModel(OversizedSearchModel):
    def __init__(self, repeats=12):
        super().__init__()
        self.repeats = repeats

    def decide_next_action(self, request, request_events):
        self.goal_inputs.append(request_events)
        if len(self.goal_inputs) <= self.repeats:
            return {
                "action": "search",
                "search_query": {
                    "meeting_ids": ["meeting-001"],
                    "keywords": [],
                    "meeting_date": None,
                },
            }
        return {"action": "direct", "search_query": None}


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
        self.assertIn("Graph Engineering", result["response"])
        kinds = [item["kind"] for item in current_goal_events(result)]
        self.assertIn("search_response", kinds)
        self.assertIn("question_response", kinds)
        self.assertNotIn("business_checkpoint", kinds)

    def test_compound_request_uses_original_llm_role_order_without_final_none_call(self):
        self.runtime.close()
        root = Path(self.temp.name)
        model = OrderedModel()
        self.runtime = MeetingAgentRuntime(
            root / "ordered-app.db", root / "ordered-checkpoints.db", model=model
        )
        self.runtime.seed()

        result = self.runtime.run_agent(
            "user-eric",
            "thread-ordered",
            "meeting-001 회의록을 가져오고 결정 사항을 설명해줘",
        )

        self.assertIn("결정 사항", result["response"])
        self.assertEqual(
            model.roles,
            ["goal", "candidate", "search_response", "goal", "question_response"],
        )

    def test_subgraphs_do_not_duplicate_scratchpad_events(self):
        result = self.runtime.run_agent(
            "user-eric",
            "thread-no-duplicates",
            "meeting-001 회의록을 가져오고 결정 사항을 설명해줘",
        )

        kinds = [event["kind"] for event in current_goal_events(result)]
        self.assertEqual(
            kinds,
            [
                "user_request",
                "llm_decision",
                "search_response",
                "llm_decision",
                "question_response",
            ],
        )

    def test_question_without_authorized_ids_uses_question_subgraph(self):
        result = self.runtime.run_agent(
            "user-eric",
            "thread-selection-required",
            "결정 사항을 설명해줘",
        )

        events = current_goal_events(result)
        self.assertEqual(
            [event["action"] for event in events if event["kind"] == "llm_decision"],
            ["question"],
        )
        response = next(event for event in events if event["kind"] == "question_response")
        self.assertEqual(response["status"], "selection_required")
        self.assertIn("먼저 관련 회의록을 검색", response["response"])

    def test_persisted_agent_state_has_only_three_design_fields(self):
        self.runtime.run_agent("user-eric", "thread-minimal", "meeting-001 회의록 찾아줘")
        expected = {"ScratchPad", "authorized_meeting_ids", "recording_modal_status"}
        self.assertEqual(set(self.runtime.get_agent_state("user-eric", "thread-minimal")), expected)
        for state in self.runtime.list_agent_checkpoint_states("user-eric", "thread-minimal"):
            self.assertTrue(set(state).issubset(expected))
            self.assertNotIn("request_id", set(collect_keys(state)))
            self.assertNotIn("recording_state", set(collect_keys(state)))

    def test_raw_transcript_is_not_checkpointed_in_agent_state(self):
        result, trace = self.runtime.run_agent_traced(
            "user-eric",
            "thread-no-raw",
            "meeting-003 회의록을 가져오고 결정 내용을 설명해줘",
        )
        raw_body = (
            "권한은 LLM이 아니라 백엔드가 검사한다. "
            "원문은 Model Context에만 넣고 Checkpoint에는 저장하지 않는다."
        )
        self.assertNotIn(raw_body, result["response"])
        self.assertNotIn(raw_body, str(trace))
        for checkpoint in self.runtime.checkpointer.list(None):
            self.assertNotIn(raw_body, str(checkpoint.checkpoint))

    def test_full_source_model_answer_is_replaced_before_persistence(self):
        self.runtime.close()
        root = Path(self.temp.name)
        self.runtime = MeetingAgentRuntime(
            root / "echo-app.db",
            root / "echo-checkpoints.db",
            model=FullSourceEchoModel(),
        )
        self.runtime.seed()
        raw = (
            "결정 사항: 권한은 LLM이 아니라 백엔드가 검사한다. "
            "원문은 Model Context에만 넣고 Checkpoint에는 저장하지 않는다."
        )

        result, trace = self.runtime.run_agent_traced(
            "user-eric",
            "thread-echo-source",
            "meeting-003 회의록을 가져오고 결정 내용을 설명해줘",
        )

        self.assertNotIn(raw, result["response"])
        self.assertNotIn(raw, str(trace))
        for checkpoint in self.runtime.checkpointer.list(None):
            self.assertNotIn(raw, str(checkpoint.checkpoint))

    def test_model_contract_extra_fields_are_not_persisted(self):
        self.runtime.close()
        root = Path(self.temp.name)
        self.runtime = MeetingAgentRuntime(
            root / "extra-app.db",
            root / "extra-checkpoints.db",
            model=ExtraFieldModel(),
        )
        self.runtime.seed()
        raw = self.runtime.repository.get_meeting_documents(
            "user-eric", ["meeting-003"]
        )[0]["transcript"]

        result = self.runtime.run_agent(
            "user-eric",
            "thread-extra-field",
            "meeting-003 회의록을 가져오고 결정 내용을 설명해줘",
        )

        self.assertNotIn("transcript", set(collect_keys(result)))
        for checkpoint in self.runtime.checkpointer.list(None):
            self.assertNotIn(raw, str(checkpoint.checkpoint))

    def test_search_and_question_do_not_read_recording_modal_status(self):
        def unexpected_modal_read(thread_id):
            raise AssertionError("검색·질문은 모달 상태를 읽으면 안 됩니다.")

        self.runtime.repository.get_modal_status = unexpected_modal_read

        result = self.runtime.run_agent("user-eric", "thread-no-modal-read", "안녕")

        self.assertEqual(result["recording_modal_status"], "healthy")

    def test_multiple_candidates_interrupt_and_resume(self):
        result = self.runtime.run_agent("user-eric", "thread-hitl", "회의록 검색해줘")
        payload = interrupt_payload(result)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["kind"], "meeting_selection")
        resumed = self.runtime.resume_agent(
            "user-eric", "thread-hitl", {"meeting_ids": ["meeting-002"]}
        )
        self.assertIsNone(interrupt_payload(resumed))
        self.assertEqual(resumed["authorized_meeting_ids"], ["meeting-002"])

    def test_invalid_candidate_selection_reasks_same_hitl(self):
        result = self.runtime.run_agent(
            "user-eric", "thread-invalid-selection", "회의록 검색해줘"
        )
        self.assertEqual(interrupt_payload(result)["kind"], "meeting_selection")

        retried = self.runtime.resume_agent(
            "user-eric", "thread-invalid-selection", {"meeting_ids": ["meeting-999"]}
        )

        retry_payload = interrupt_payload(retried)
        self.assertEqual(retry_payload["kind"], "meeting_selection")
        self.assertIn("후보", retry_payload["error"])
        completed = self.runtime.resume_agent(
            "user-eric", "thread-invalid-selection", {"meeting_ids": ["meeting-001"]}
        )
        self.assertIsNone(interrupt_payload(completed))
        self.assertEqual(completed["authorized_meeting_ids"], ["meeting-001"])

    def test_invalid_candidate_selection_types_reask_same_hitl(self):
        result = self.runtime.run_agent(
            "user-eric", "thread-invalid-selection-types", "회의록 검색해줘"
        )
        self.assertEqual(interrupt_payload(result)["kind"], "meeting_selection")

        for invalid in (7, [["meeting-001"]], {"unexpected": "value"}):
            retried = self.runtime.resume_agent(
                "user-eric",
                "thread-invalid-selection-types",
                {"meeting_ids": invalid},
            )
            payload = interrupt_payload(retried)
            self.assertEqual(payload["kind"], "meeting_selection")
            self.assertIn("다시 선택", payload["error"])

        completed = self.runtime.resume_agent(
            "user-eric",
            "thread-invalid-selection-types",
            {"meeting_ids": ["meeting-001"]},
        )
        self.assertIsNone(interrupt_payload(completed))

    def test_new_request_during_hitl_reissues_pending_question(self):
        result = self.runtime.run_agent(
            "user-eric", "thread-pending-preserved", "회의록 검색해줘"
        )
        original = interrupt_payload(result)

        blocked = self.runtime.run_agent(
            "user-eric", "thread-pending-preserved", "새로운 질문입니다"
        )

        self.assertEqual(interrupt_payload(blocked), original)
        completed = self.runtime.resume_agent(
            "user-eric", "thread-pending-preserved", {"meeting_ids": ["meeting-001"]}
        )
        self.assertIsNone(interrupt_payload(completed))

    def test_invalid_merge_mode_reasks_same_hitl(self):
        self.runtime.run_agent(
            "user-eric", "thread-invalid-merge", "meeting-001 회의록을 찾아줘"
        )
        interrupted = self.runtime.run_agent(
            "user-eric", "thread-invalid-merge", "meeting-002 회의록을 찾아줘"
        )
        self.assertEqual(interrupt_payload(interrupted)["kind"], "id_merge")

        retried = self.runtime.resume_agent(
            "user-eric", "thread-invalid-merge", {"mode": "invalid"}
        )

        retry_payload = interrupt_payload(retried)
        self.assertEqual(retry_payload["kind"], "id_merge")
        self.assertIn("add 또는 replace", retry_payload["error"])
        completed = self.runtime.resume_agent(
            "user-eric", "thread-invalid-merge", {"mode": "add"}
        )
        self.assertIsNone(interrupt_payload(completed))
        self.assertEqual(
            completed["authorized_meeting_ids"], ["meeting-001", "meeting-002"]
        )

    def test_selection_revalidation_failure_becomes_structured_search_result(self):
        secret = "SERVER_MEETING_ID meeting-777"

        def reject_selection(user_id, candidate_ids, selected_ids):
            raise PermissionError(secret)

        self.runtime.repository.validate_selection = reject_selection

        result = self.runtime.run_agent(
            "user-eric", "thread-selection-revoked", "meeting-001 회의록을 찾아줘"
        )

        event = next(
            item
            for item in current_goal_events(result)
            if item.get("kind") == "search_response"
        )
        self.assertEqual(event["status"], "failed")
        self.assertEqual(event["error"]["code"], "SELECTION_VALIDATION_FAILED")
        self.assertNotIn(secret, str(result))

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
        secret = "SERVER_MEETING_ID meeting-777"

        def fail_search(user_id, request):
            nonlocal attempts
            attempts += 1
            raise RuntimeError(secret)

        self.runtime.repository.search_meetings = fail_search
        result = self.runtime.run_agent("user-eric", "thread-failure", "회의록 검색해줘")
        event = next(
            item for item in current_goal_events(result) if item.get("kind") == "search_response"
        )
        self.assertEqual(attempts, 2)
        self.assertEqual(event["status"], "failed")
        self.assertEqual(event["error"]["code"], "TOOL_EXECUTION_FAILED")
        self.assertIn("완료하지 못했습니다", result["response"])
        self.assertNotIn(secret, str(result))
        for checkpoint in self.runtime.checkpointer.list(None):
            self.assertNotIn(secret, str(checkpoint.checkpoint))

    def test_tool_2_permission_failure_has_design_error_code(self):
        self.runtime.run_agent(
            "user-eric", "thread-tool2-code", "보안 회의록을 찾아줘"
        )
        self.runtime.repository.conn.execute(
            "DELETE FROM meeting_access WHERE meeting_id = ? AND user_id = ?",
            ("meeting-003", "user-eric"),
        )
        self.runtime.repository.conn.commit()

        result = self.runtime.run_agent(
            "user-eric", "thread-tool2-code", "결정 내용을 설명해줘"
        )

        event = next(
            item for item in current_goal_events(result)
            if item.get("kind") == "question_response"
        )
        self.assertEqual(event["error"]["code"], "UNAUTHORIZED_MEETING_ID")

    def test_scratchpad_compacts_at_half_context_budget(self):
        config = self.runtime._config("user-eric", "thread-compact", "meeting-agent")
        marker = "과거 결정은 권한을 백엔드에서 검증한다"
        large_events = [{"kind": "old_event", "payload": marker + "x" * 4_000}]
        self.runtime.agent_graph.update_state(
            config,
            {
                "ScratchPad": large_events,
                "authorized_meeting_ids": [],
                "recording_modal_status": "healthy",
            },
        )
        self.runtime.run_agent("user-eric", "thread-compact", "안녕")
        state = self.runtime.get_agent_state("user-eric", "thread-compact")
        self.assertEqual(state["ScratchPad"][0]["kind"], "compact_summary")
        self.assertLess(len(str(state["ScratchPad"])), MODEL_CONTEXT_BUDGET_CHARS)
        self.assertIn(marker, str(state["ScratchPad"][0]))

    def test_compact_reduces_large_current_goal_before_next_llm_call(self):
        self.runtime.close()
        root = Path(self.temp.name)
        model = OversizedSearchModel()
        self.runtime = MeetingAgentRuntime(
            root / "compact-current-app.db",
            root / "compact-current-checkpoints.db",
            model=model,
        )
        self.runtime.seed()

        self.runtime.run_agent(
            "user-eric",
            "thread-compact-current",
            "meeting-001 회의록을 가져오고 결정 사항을 설명해줘",
        )

        second_goal = model.goal_inputs[1]
        serialized = json.dumps(second_goal, ensure_ascii=False)
        self.assertLess(len(serialized), MODEL_CONTEXT_BUDGET_CHARS // 2)
        self.assertIn("COMPACT_MEANING_SURVIVES", serialized)

    def test_compact_bounds_many_current_goal_events(self):
        self.runtime.close()
        root = Path(self.temp.name)
        model = RepeatedSearchModel()
        self.runtime = MeetingAgentRuntime(
            root / "compact-many-app.db",
            root / "compact-many-checkpoints.db",
            model=model,
        )
        self.runtime.seed()

        self.runtime.run_agent(
            "user-eric", "thread-compact-many", "meeting-001 회의록을 반복 검색해줘"
        )

        for goal_input in model.goal_inputs[1:]:
            serialized = json.dumps(goal_input, ensure_ascii=False)
            self.assertLess(len(serialized), MODEL_CONTEXT_BUDGET_CHARS // 2)
        self.assertIn(
            "COMPACT_MEANING_SURVIVES",
            json.dumps(model.goal_inputs[-1], ensure_ascii=False),
        )

    def test_compact_bounds_corrupt_long_event_kinds_and_keeps_request(self):
        marker = "CURRENT_REQUEST_MEANING"
        events = [{"kind": "user_request", "request": marker}]
        events.extend(
            {"kind": f"unexpected-{index}-" + "z" * 500, "payload": "x" * 500}
            for index in range(100)
        )

        compacted = compact_scratchpad_events(events)
        serialized = json.dumps(compacted, ensure_ascii=False)

        self.assertLess(len(serialized), MODEL_CONTEXT_BUDGET_CHARS // 2)
        self.assertIn(marker, serialized)

    def test_same_thread_id_is_isolated_by_authenticated_user(self):
        eric = self.runtime.run_agent(
            "user-eric", "shared-name", "meeting-003 회의록을 찾아줘"
        )
        alice = self.runtime.run_agent(
            "user-alice", "shared-name", "결정 사항을 설명해줘"
        )

        self.assertEqual(eric["authorized_meeting_ids"], ["meeting-003"])
        self.assertEqual(alice.get("authorized_meeting_ids", []), [])
        self.assertIn("먼저 관련 회의록을 검색", alice["response"])

    def test_hitl_cannot_be_resumed_by_another_user(self):
        self.runtime.run_agent("user-eric", "owned-hitl", "회의록 검색해줘")

        with self.assertRaisesRegex(RuntimeError, "재개할 HITL"):
            self.runtime.resume_agent(
                "user-alice", "owned-hitl", {"meeting_ids": ["meeting-001"]}
            )

    def test_compiled_graph_uses_original_search_node_boundaries(self):
        graph = self.runtime.agent_graph.get_graph(xray=True)
        nodes = set(graph.nodes)

        self.assertIn("search_subgraph:S1_candidate_search_and_judgment", nodes)
        self.assertIn("search_subgraph:S2A_no_candidates", nodes)
        self.assertIn("search_subgraph:S2B_one_candidate", nodes)
        self.assertIn("search_subgraph:S2C_multiple_candidates", nodes)
        self.assertIn("search_subgraph:S3_search_response_ready", nodes)
        self.assertNotIn("search_subgraph:S1_tool_1_search", nodes)
        self.assertNotIn("search_subgraph:S1_llm_candidate_interpretation", nodes)
        self.assertNotIn("request_finished", nodes)

    def test_server_managed_meeting_ids_never_enter_model_inputs(self):
        self.runtime.close()
        root = Path(self.temp.name)
        model = PromptBoundarySpyModel()
        self.runtime = MeetingAgentRuntime(
            root / "prompt-app.db", root / "prompt-checkpoints.db", model=model
        )
        self.runtime.seed()

        self.runtime.run_agent(
            "user-eric", "prompt-boundary", "로드맵 회의록을 가져오고 내용을 설명해줘"
        )

        self.assertNotIn("meeting-001", str(model.model_inputs))
        self.assertNotIn("authorized_meeting_ids", str(model.model_inputs))

    def test_tool_2_failure_does_not_send_server_managed_id_to_model(self):
        self.runtime.close()
        root = Path(self.temp.name)
        model = PromptBoundarySpyModel()
        self.runtime = MeetingAgentRuntime(
            root / "failure-prompt-app.db",
            root / "failure-prompt-checkpoints.db",
            model=model,
        )
        self.runtime.seed()
        self.runtime.run_agent(
            "user-eric", "failure-prompt", "보안 회의록을 찾아줘"
        )
        self.runtime.repository.conn.execute(
            "DELETE FROM meeting_access WHERE meeting_id = ? AND user_id = ?",
            ("meeting-003", "user-eric"),
        )
        self.runtime.repository.conn.commit()
        model.model_inputs.clear()

        result = self.runtime.run_agent(
            "user-eric", "failure-prompt", "결정 내용을 설명해줘"
        )

        self.assertIn("조회하지 못했습니다", result["response"])
        self.assertNotIn("meeting-003", str(model.model_inputs))

    def test_checkpoint_and_recording_keys_do_not_collide_on_delimiters(self):
        self.runtime.repository.conn.execute(
            "INSERT INTO users(id, name) VALUES (?, ?)", ("user-eric:a", "Eric A")
        )
        self.runtime.repository.conn.commit()

        self.runtime.run_agent("user-eric:a", "audit", "안녕")
        self.assertEqual(self.runtime.get_agent_state("user-eric", "a:audit"), {})

        first = self.runtime.run_recording("user-eric:a", "audit", "start")
        second = self.runtime.run_recording("user-eric", "a:audit", "start")
        self.assertEqual(first["current_state"], "recording")
        self.assertEqual(second["current_state"], "recording")
        self.assertEqual(second["previous_state"], "none")


if __name__ == "__main__":
    unittest.main()
