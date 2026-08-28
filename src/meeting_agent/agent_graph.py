from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .model import MeetingModel
from .repository import MeetingRepository
from .state import AgentState


def _event(state: AgentState, kind: str, **payload: Any) -> dict[str, Any]:
    return {
        "request_id": state["request_id"],
        "kind": kind,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }


def build_search_subgraph(repository: MeetingRepository, model: MeetingModel):
    builder = StateGraph(AgentState)

    def tool_1_search(state: AgentState) -> dict[str, Any]:
        candidates = repository.search_meetings(state["user_id"], state["request"])
        return {
            "candidates": candidates,
            "selected_ids": [],
            "merge_mode": "",
            "last_result": {
                "tool": "tool_1_search_select",
                "candidate_count": len(candidates),
                "candidate_ids": [item["id"] for item in candidates],
            },
        }

    def candidate_route(state: AgentState) -> str:
        count = len(state.get("candidates", []))
        return "none" if count == 0 else "one" if count == 1 else "many"

    def no_candidates(state: AgentState) -> dict[str, Any]:
        return {"selected_ids": [], "merge_mode": ""}

    def auto_select(state: AgentState) -> dict[str, Any]:
        candidate = state["candidates"][0]
        selected = repository.validate_selection(
            state["user_id"], [candidate["id"]], [candidate["id"]]
        )
        return {"selected_ids": selected}

    def hitl_select(state: AgentState) -> dict[str, Any]:
        payload = {
            "kind": "meeting_selection",
            "message": "접근 가능한 후보가 여러 개입니다. 하나 이상의 meeting_id를 선택하세요.",
            "candidates": state["candidates"],
        }
        answer = interrupt(payload)
        selected_ids = answer.get("meeting_ids", []) if isinstance(answer, dict) else answer
        if isinstance(selected_ids, str):
            selected_ids = [item.strip() for item in selected_ids.split(",") if item.strip()]
        selected = repository.validate_selection(
            state["user_id"],
            [item["id"] for item in state["candidates"]],
            selected_ids,
        )
        return {"selected_ids": selected}

    def existing_route(state: AgentState) -> str:
        if not state.get("selected_ids"):
            return "none"
        return "existing" if state.get("authorized_meeting_ids") else "new"

    def set_new_ids(state: AgentState) -> dict[str, Any]:
        return {
            "authorized_meeting_ids": state["selected_ids"],
            "merge_mode": "set",
        }

    def hitl_merge(state: AgentState) -> dict[str, Any]:
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
        if mode == "add":
            ids = list(dict.fromkeys(state.get("authorized_meeting_ids", []) + state["selected_ids"]))
        else:
            ids = state["selected_ids"]
        return {"authorized_meeting_ids": ids, "merge_mode": mode}

    def search_response(state: AgentState) -> dict[str, Any]:
        selected_lookup = {item["id"]: item for item in state.get("candidates", [])}
        selected = [selected_lookup[item] for item in state.get("selected_ids", []) if item in selected_lookup]
        mode = state.get("merge_mode") or "set"
        response = model.search_response(state["request"], selected, mode)
        result = {
            "status": "ok" if selected else "no_candidates",
            "selected_ids": state.get("selected_ids", []),
            "authorized_meeting_ids": state.get("authorized_meeting_ids", []),
            "mode": mode if selected else "unchanged",
        }
        return {
            "response": response,
            "last_result": result,
            "ScratchPad": [
                _event(
                    state,
                    "search_response",
                    response=response,
                    selected_ids=result["selected_ids"],
                    authorized_meeting_ids=result["authorized_meeting_ids"],
                    mode=result["mode"],
                )
            ],
        }

    builder.add_node("S1_tool_1_search", tool_1_search)
    builder.add_node("S2A_no_candidates", no_candidates)
    builder.add_node("S2B_auto_select", auto_select)
    builder.add_node("S2C_hitl_select", hitl_select)
    builder.add_node("S2_set_new_ids", set_new_ids)
    builder.add_node("S2_hitl_add_or_replace", hitl_merge)
    builder.add_node("S3_search_response_ready", search_response)
    builder.add_edge(START, "S1_tool_1_search")
    builder.add_conditional_edges(
        "S1_tool_1_search",
        candidate_route,
        {"none": "S2A_no_candidates", "one": "S2B_auto_select", "many": "S2C_hitl_select"},
    )
    builder.add_edge("S2A_no_candidates", "S3_search_response_ready")
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
    builder = StateGraph(AgentState)

    def has_ids(state: AgentState) -> str:
        return "yes" if state.get("authorized_meeting_ids") else "no"

    def selection_required(state: AgentState) -> dict[str, Any]:
        response = "질문에 사용할 회의록이 선택되지 않았습니다. 먼저 관련 회의록을 검색·선택해 주세요."
        return {
            "response": response,
            "last_result": {"status": "selection_required"},
            "ScratchPad": [_event(state, "question_response", response=response, status="selection_required")],
        }

    def tool_2_and_answer(state: AgentState) -> dict[str, Any]:
        try:
            # Raw transcripts stay in this local variable only. They are never
            # returned into AgentState and therefore never enter checkpoints.
            documents = repository.get_meeting_documents(
                state["user_id"], state.get("authorized_meeting_ids", [])
            )
            response = model.answer_question(state["request"], documents)
            metadata = [{"id": doc["id"], "title": doc["title"]} for doc in documents]
            result = {"status": "source_ready", "documents": metadata}
        except (PermissionError, ValueError) as exc:
            response = f"회의록 원문을 조회하지 못했습니다: {exc}"
            result = {"status": "source_denied_or_failed", "reason": str(exc)}
        return {
            "response": response,
            "last_result": result,
            "ScratchPad": [
                _event(
                    state,
                    "question_response",
                    response=response,
                    status=result["status"],
                    document_metadata=result.get("documents", []),
                )
            ],
        }

    builder.add_node("Q1_check_authorized_ids", lambda state: {})
    builder.add_node("Q2A_selection_required", selection_required)
    builder.add_node("Q2B_Q2C_tool_2_and_answer", tool_2_and_answer)
    builder.add_node("Q3_question_response_ready", lambda state: {})
    builder.add_edge(START, "Q1_check_authorized_ids")
    builder.add_conditional_edges(
        "Q1_check_authorized_ids",
        has_ids,
        {"yes": "Q2B_Q2C_tool_2_and_answer", "no": "Q2A_selection_required"},
    )
    builder.add_edge("Q2A_selection_required", "Q3_question_response_ready")
    builder.add_edge("Q2B_Q2C_tool_2_and_answer", "Q3_question_response_ready")
    builder.add_edge("Q3_question_response_ready", END)
    return builder.compile()


def build_agent_graph(repository: MeetingRepository, model: MeetingModel):
    search_graph = build_search_subgraph(repository, model)
    question_graph = build_question_subgraph(repository, model)
    builder = StateGraph(AgentState)

    def receive_request(state: AgentState) -> dict[str, Any]:
        return {
            "candidates": [],
            "selected_ids": [],
            "merge_mode": "",
            "response": "",
            "last_result": {},
            "ScratchPad": [
                _event(state, "user_request", request=state["request"])
            ],
        }

    def decide(state: AgentState) -> dict[str, Any]:
        request_events = [
            event
            for event in state.get("ScratchPad", [])
            if event.get("request_id") == state["request_id"]
        ]
        action = model.decide_next_action(
            state["request"], request_events, state.get("authorized_meeting_ids", [])
        )
        return {
            "next_action": action,
            "ScratchPad": [_event(state, "llm_decision", action=action)],
        }

    def route_action(state: AgentState) -> str:
        return state["next_action"]

    def direct_answer(state: AgentState) -> dict[str, Any]:
        response = model.direct_response(state["request"])
        return {
            "response": response,
            "last_result": {"status": "direct"},
            "ScratchPad": [_event(state, "direct_response", response=response)],
        }

    def business_checkpoint(state: AgentState) -> dict[str, Any]:
        # LangGraph's SQLite checkpointer persists every super-step. This event
        # marks the business boundary described in the design documents.
        return {
            "ScratchPad": [
                _event(
                    state,
                    "business_checkpoint",
                    result_status=state.get("last_result", {}).get("status", "unknown"),
                )
            ]
        }

    def finish(state: AgentState) -> dict[str, Any]:
        return {"next_action": "done"}

    builder.add_node("receive_user_request", receive_request)
    builder.add_node("llm_goal_condition", decide)
    builder.add_node("search_subgraph", search_graph)
    builder.add_node("question_subgraph", question_graph)
    builder.add_node("llm_direct_answer", direct_answer)
    builder.add_node("graph_runtime_checkpoint", business_checkpoint)
    builder.add_node("request_finished", finish)
    builder.add_edge(START, "receive_user_request")
    builder.add_edge("receive_user_request", "llm_goal_condition")
    builder.add_conditional_edges(
        "llm_goal_condition",
        route_action,
        {
            "search": "search_subgraph",
            "question": "question_subgraph",
            "direct": "llm_direct_answer",
            "done": "request_finished",
        },
    )
    builder.add_edge("search_subgraph", "graph_runtime_checkpoint")
    builder.add_edge("question_subgraph", "graph_runtime_checkpoint")
    builder.add_edge("llm_direct_answer", "graph_runtime_checkpoint")
    builder.add_edge("graph_runtime_checkpoint", "llm_goal_condition")
    builder.add_edge("request_finished", END)
    return builder

