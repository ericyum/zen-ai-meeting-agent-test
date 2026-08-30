from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Literal, TypeVar

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from .model import MeetingModel
from .repository import MeetingRepository
from .state import AgentContext, AgentState, QuestionState, SearchState


SYSTEM_INSTRUCTION = (
    "권한이 검증된 회의록만 사용하고, Tool 결과와 현재 ScratchPad를 근거로 "
    "사용자의 목표를 정확하고 간결하게 처리한다."
)
MODEL_CONTEXT_BUDGET_CHARS = 8_000
TOOL_ATTEMPTS = 2
T = TypeVar("T")


def _event(kind: str, **payload: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }


def _current_goal_events(state: AgentState) -> list[dict[str, Any]]:
    events = state.get("ScratchPad", [])
    for index in range(len(events) - 1, -1, -1):
        if events[index].get("kind") == "user_request":
            return events[index:]
    return events


def _call_with_retry(operation: Callable[[], T]) -> tuple[T | None, dict[str, Any] | None]:
    last_error: Exception | None = None
    for _attempt in range(1, TOOL_ATTEMPTS + 1):
        try:
            return operation(), None
        except Exception as exc:  # Tool/backend boundary; converted to a structured result.
            last_error = exc
    assert last_error is not None
    return None, {
        "code": "TOOL_EXECUTION_FAILED",
        "message": str(last_error),
        "attempts": TOOL_ATTEMPTS,
        "retryable": False,
    }


def build_search_subgraph(repository: MeetingRepository, model: MeetingModel):
    builder = StateGraph(SearchState, context_schema=AgentContext)

    def tool_1_search(
        state: SearchState, runtime: Runtime[AgentContext]
    ) -> dict[str, Any]:
        candidates, error = _call_with_retry(
            lambda: repository.search_meetings(runtime.context.user_id, runtime.context.request)
        )
        if error:
            return {
                "candidates": [],
                "selected_ids": [],
                "merge_mode": "",
                "tool_status": "failed",
                "tool_error": error,
            }
        return {
            "candidates": candidates or [],
            "selected_ids": [],
            "merge_mode": "",
            "tool_status": "ok",
            "tool_error": {},
        }

    def candidate_route(
        state: SearchState, runtime: Runtime[AgentContext]
    ) -> str:
        if state.get("tool_status") == "failed":
            return "failed"
        return model.interpret_candidate_count(
            runtime.context.request,
            state.get("candidates", []),
            state.get("ScratchPad", []),
        )

    def no_candidates(state: SearchState) -> dict[str, Any]:
        return {"selected_ids": [], "merge_mode": ""}

    def tool_failed(state: SearchState) -> dict[str, Any]:
        return {"selected_ids": [], "merge_mode": ""}

    def auto_select(
        state: SearchState, runtime: Runtime[AgentContext]
    ) -> dict[str, Any]:
        candidate = state["candidates"][0]
        selected = repository.validate_selection(
            runtime.context.user_id, [candidate["id"]], [candidate["id"]]
        )
        return {"selected_ids": selected}

    def hitl_select(
        state: SearchState, runtime: Runtime[AgentContext]
    ) -> dict[str, Any]:
        answer = interrupt(
            {
                "kind": "meeting_selection",
                "message": "접근 가능한 후보가 여러 개입니다. 하나 이상의 meeting_id를 선택하세요.",
                "candidates": state["candidates"],
            }
        )
        selected_ids = answer.get("meeting_ids", []) if isinstance(answer, dict) else answer
        if isinstance(selected_ids, str):
            selected_ids = [item.strip() for item in selected_ids.split(",") if item.strip()]
        selected = repository.validate_selection(
            runtime.context.user_id,
            [item["id"] for item in state["candidates"]],
            selected_ids,
        )
        return {"selected_ids": selected}

    def existing_route(state: SearchState) -> str:
        if not state.get("selected_ids"):
            return "none"
        return "existing" if state.get("authorized_meeting_ids") else "new"

    def set_new_ids(state: SearchState) -> dict[str, Any]:
        return {"authorized_meeting_ids": state["selected_ids"], "merge_mode": "set"}

    def hitl_merge(state: SearchState) -> dict[str, Any]:
        answer = interrupt(
            {
                "kind": "id_merge",
                "message": "기존 허용 ID 목록에 새 ID를 추가할지, 새 목록으로 대체할지 선택하세요.",
                "existing_ids": state.get("authorized_meeting_ids", []),
                "new_ids": state["selected_ids"],
                "choices": ["add", "replace"],
            }
        )
        mode = answer.get("mode") if isinstance(answer, dict) else str(answer)
        if mode not in {"add", "replace"}:
            raise ValueError("mode는 add 또는 replace여야 합니다.")
        ids = (
            list(dict.fromkeys(state.get("authorized_meeting_ids", []) + state["selected_ids"]))
            if mode == "add"
            else state["selected_ids"]
        )
        return {"authorized_meeting_ids": ids, "merge_mode": mode}

    def search_response(
        state: SearchState, runtime: Runtime[AgentContext]
    ) -> dict[str, Any]:
        selected_lookup = {item["id"]: item for item in state.get("candidates", [])}
        selected = [
            selected_lookup[item]
            for item in state.get("selected_ids", [])
            if item in selected_lookup
        ]
        failed = state.get("tool_status") == "failed"
        mode = state.get("merge_mode") or "set"
        result: dict[str, Any] = {
            "status": "failed" if failed else "ok" if selected else "no_candidates",
            "selected_ids": state.get("selected_ids", []),
            "authorized_meeting_ids": state.get("authorized_meeting_ids", []),
            "mode": mode if selected else "unchanged",
        }
        if failed:
            result["error"] = state.get("tool_error", {})
        response = model.search_response(
            runtime.context.request, selected, mode, result
        )
        return {
            "ScratchPad": [
                _event("search_response", response=response, **result)
            ]
        }

    builder.add_node("S1_tool_1_search", tool_1_search)
    builder.add_node("S2A_no_candidates", no_candidates)
    builder.add_node("S2B_auto_select", auto_select)
    builder.add_node("S2C_hitl_select", hitl_select)
    builder.add_node("S2_tool_failed", tool_failed)
    builder.add_node("S2_set_new_ids", set_new_ids)
    builder.add_node("S2_hitl_add_or_replace", hitl_merge)
    builder.add_node("S3_search_response_ready", search_response)
    builder.add_edge(START, "S1_tool_1_search")
    builder.add_conditional_edges(
        "S1_tool_1_search",
        candidate_route,
        {
            "none": "S2A_no_candidates",
            "one": "S2B_auto_select",
            "many": "S2C_hitl_select",
            "failed": "S2_tool_failed",
        },
    )
    builder.add_edge("S2A_no_candidates", "S3_search_response_ready")
    builder.add_edge("S2_tool_failed", "S3_search_response_ready")
    builder.add_conditional_edges(
        "S2B_auto_select",
        existing_route,
        {"none": "S3_search_response_ready", "new": "S2_set_new_ids", "existing": "S2_hitl_add_or_replace"},
    )
    builder.add_conditional_edges(
        "S2C_hitl_select",
        existing_route,
        {"none": "S3_search_response_ready", "new": "S2_set_new_ids", "existing": "S2_hitl_add_or_replace"},
    )
    builder.add_edge("S2_set_new_ids", "S3_search_response_ready")
    builder.add_edge("S2_hitl_add_or_replace", "S3_search_response_ready")
    builder.add_edge("S3_search_response_ready", END)
    return builder.compile()


def build_question_subgraph(repository: MeetingRepository, model: MeetingModel):
    builder = StateGraph(QuestionState, context_schema=AgentContext)

    def tool_2_context_and_answer(
        state: QuestionState, runtime: Runtime[AgentContext]
    ) -> dict[str, Any]:
        ids = state.get("authorized_meeting_ids", [])
        documents: list[dict[str, str]] = []
        if not ids:
            result: dict[str, Any] = {"status": "selection_required"}
        else:
            loaded, error = _call_with_retry(
                lambda: repository.get_meeting_documents(runtime.context.user_id, ids)
            )
            if error:
                result = {
                    "status": "source_denied_or_failed",
                    "reason": error["message"],
                    "error": error,
                }
            else:
                documents = loaded or []
                result = {
                    "status": "source_ready",
                    "documents": [
                        {"id": doc["id"], "title": doc["title"]} for doc in documents
                    ],
                }

        response = model.answer_question(
            SYSTEM_INSTRUCTION,
            runtime.context.request,
            state.get("ScratchPad", []),
            documents,
            result,
        )
        return {
            "tool_status": result["status"],
            "last_result": result,
            "ScratchPad": [
                _event(
                    "question_response",
                    response=response,
                    status=result["status"],
                    document_metadata=result.get("documents", []),
                    error=result.get("error"),
                )
            ],
        }

    def result_route(state: QuestionState) -> str:
        return state["tool_status"]

    builder.add_node("Q1_tool_2_context_and_answer", tool_2_context_and_answer)
    builder.add_node("Q2A_selection_required", lambda state: {})
    builder.add_node("Q2B_source_ready", lambda state: {})
    builder.add_node("Q2C_source_denied_or_failed", lambda state: {})
    builder.add_node("Q3_question_response_ready", lambda state: {})
    builder.add_edge(START, "Q1_tool_2_context_and_answer")
    builder.add_conditional_edges(
        "Q1_tool_2_context_and_answer",
        result_route,
        {
            "selection_required": "Q2A_selection_required",
            "source_ready": "Q2B_source_ready",
            "source_denied_or_failed": "Q2C_source_denied_or_failed",
        },
    )
    builder.add_edge("Q2A_selection_required", "Q3_question_response_ready")
    builder.add_edge("Q2B_source_ready", "Q3_question_response_ready")
    builder.add_edge("Q2C_source_denied_or_failed", "Q3_question_response_ready")
    builder.add_edge("Q3_question_response_ready", END)
    return builder.compile()


def build_agent_graph(repository: MeetingRepository, model: MeetingModel):
    search_graph = build_search_subgraph(repository, model)
    question_graph = build_question_subgraph(repository, model)
    builder = StateGraph(AgentState, context_schema=AgentContext)

    def receive_request(
        state: AgentState, runtime: Runtime[AgentContext]
    ) -> dict[str, Any]:
        return {"ScratchPad": [_event("user_request", request=runtime.context.request)]}

    def compact_scratchpad(state: AgentState) -> dict[str, Any]:
        events = state.get("ScratchPad", [])
        size = len(json.dumps(events, ensure_ascii=False, default=str))
        if size < MODEL_CONTEXT_BUDGET_CHARS // 2:
            return {}
        current_goal = _current_goal_events(state)
        old_events = events[: len(events) - len(current_goal)]
        summary = _event(
            "compact_summary",
            compacted_event_count=len(old_events),
            event_kinds=dict(Counter(item.get("kind", "unknown") for item in old_events)),
        )
        return {"ScratchPad": {"__replace__": [summary, *current_goal]}}

    def decide(
        state: AgentState, runtime: Runtime[AgentContext]
    ) -> Command[
        Literal[
            "search_subgraph",
            "question_subgraph",
            "llm_direct_answer",
            "request_finished",
        ]
    ]:
        action = model.decide_next_action(
            runtime.context.request,
            _current_goal_events(state),
            state.get("authorized_meeting_ids", []),
        )
        destinations = {
            "search": "search_subgraph",
            "question": "question_subgraph",
            "direct": "llm_direct_answer",
            "done": "request_finished",
        }
        return Command(
            goto=destinations[action],
            update={"ScratchPad": [_event("llm_decision", action=action)]},
        )

    def direct_answer(
        state: AgentState, runtime: Runtime[AgentContext]
    ) -> dict[str, Any]:
        response = model.direct_response(
            SYSTEM_INSTRUCTION,
            runtime.context.request,
            state.get("ScratchPad", []),
        )
        return {"ScratchPad": [_event("direct_response", response=response, status="direct")]}

    def business_checkpoint(state: AgentState) -> dict[str, Any]:
        latest = _current_goal_events(state)
        status = next(
            (
                event.get("status", "ok")
                for event in reversed(latest)
                if event.get("kind") in {"search_response", "question_response", "direct_response"}
            ),
            "unknown",
        )
        return {"ScratchPad": [_event("business_checkpoint", result_status=status)]}

    builder.add_node("receive_user_request", receive_request)
    builder.add_node("compact_scratchpad", compact_scratchpad)
    builder.add_node("llm_goal_condition", decide)
    builder.add_node("search_subgraph", search_graph)
    builder.add_node("question_subgraph", question_graph)
    builder.add_node("llm_direct_answer", direct_answer)
    builder.add_node("graph_runtime_checkpoint", business_checkpoint)
    builder.add_node("request_finished", lambda state: {})
    builder.add_edge(START, "receive_user_request")
    builder.add_edge("receive_user_request", "compact_scratchpad")
    builder.add_edge("compact_scratchpad", "llm_goal_condition")
    builder.add_edge("search_subgraph", "graph_runtime_checkpoint")
    builder.add_edge("question_subgraph", "graph_runtime_checkpoint")
    builder.add_edge("llm_direct_answer", "graph_runtime_checkpoint")
    builder.add_edge("graph_runtime_checkpoint", "compact_scratchpad")
    builder.add_edge("request_finished", END)
    return builder
