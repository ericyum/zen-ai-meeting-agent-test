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
CORE_EVENT_KINDS = {
    "user_request",
    "llm_decision",
    "search_response",
    "question_response",
    "direct_response",
}


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


def _call_with_retry(
    operation: Callable[[], T], *, permission_error_code: str | None = None
) -> tuple[T | None, dict[str, Any] | None]:
    last_error: Exception | None = None
    for _attempt in range(1, TOOL_ATTEMPTS + 1):
        try:
            return operation(), None
        except Exception as exc:  # Tool/backend boundary; converted to a structured result.
            last_error = exc
    assert last_error is not None
    return None, {
        "code": (
            permission_error_code
            if permission_error_code and isinstance(last_error, PermissionError)
            else "TOOL_EXECUTION_FAILED"
        ),
        "message": "Tool 실행에 실패했습니다.",
        "attempts": TOOL_ATTEMPTS,
        "retryable": False,
    }


def _compact_event(event: dict[str, Any], text_limit: int = 300) -> dict[str, Any]:
    """Keep goal-loop meaning while removing large or server-managed payloads."""

    compacted: dict[str, Any] = {"kind": str(event.get("kind", "unknown"))[:60]}
    for key in (
        "request", "response", "payload", "action", "status", "mode",
        "follow_up", "selected_count", "document_count", "selected_titles",
        "document_titles",
    ):
        value = event.get(key)
        if isinstance(value, str):
            compacted[key] = value[:text_limit]
        elif isinstance(value, (bool, int, float)) or value is None:
            if key in event:
                compacted[key] = value
        elif isinstance(value, list):
            compacted[key] = [str(item)[:100] for item in value[:10]]
    return compacted


def _latest_event_per_kind(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest = {
        str(event.get("kind", "unknown"))[:60]: (index, event)
        for index, event in enumerate(events)
    }
    return [event for _, event in sorted(latest.values())]


def compact_scratchpad_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a semantic, bounded ScratchPad for the next Model call."""

    current_goal = _current_goal_events({"ScratchPad": events})
    old_events = events[: len(events) - len(current_goal)]
    old_core = _latest_event_per_kind(old_events)[-8:]
    current_core = _latest_event_per_kind(
        [
            event
            for event in current_goal
            if str(event.get("kind", "unknown")) in CORE_EVENT_KINDS
        ]
    )
    event_kinds = dict(
        Counter(str(item.get("kind", "unknown"))[:60] for item in old_events).most_common(8)
    )
    target = MODEL_CONTEXT_BUDGET_CHARS // 2
    for text_limit in (300, 160, 80, 40):
        replacement = [
            _event(
                "compact_summary",
                compacted_event_count=len(old_events),
                event_kinds=event_kinds,
                history=[_compact_event(item, text_limit) for item in old_core],
            ),
            *(_compact_event(item, text_limit) for item in current_core),
        ]
        if len(json.dumps(replacement, ensure_ascii=False, default=str)) < target:
            return replacement

    fallback = [
        _event("compact_summary", compacted_event_count=len(events)),
        *(_compact_event(item, 40) for item in current_core),
    ]
    if len(json.dumps(fallback, ensure_ascii=False, default=str)) >= target:
        raise RuntimeError("ScratchPad Compact 결과가 Context 예산을 초과했습니다.")
    return fallback


def _without_full_sources(
    response: dict[str, Any], documents: list[dict[str, str]]
) -> dict[str, Any]:
    text = response.get("response", "")
    source_bodies = []
    for document in documents:
        transcript = document.get("transcript", "").strip()
        source_bodies.extend([transcript, transcript.partition(":")[2].strip()])
    if not any(source and source in text for source in source_bodies):
        return response
    summaries = "\n".join(
        f"- {document['title']} 요약: {document['summary']}" for document in documents
    )
    return {
        **response,
        "response": f"선택된 회의록을 기준으로 확인한 내용입니다.\n{summaries}",
    }


def build_search_subgraph(repository: MeetingRepository, model: MeetingModel):
    builder = StateGraph(SearchState, context_schema=AgentContext)

    def search_and_judge_candidates(
        state: SearchState, runtime: Runtime[AgentContext]
    ) -> dict[str, Any]:
        decision = next(
            event for event in reversed(state.get("ScratchPad", []))
            if event.get("kind") == "llm_decision" and event.get("action") == "search"
        )
        candidates, error = _call_with_retry(
            lambda: repository.search_meetings(runtime.context.user_id, decision["search_query"])
        )
        if error:
            return {
                "candidates": [],
                "selected_ids": [],
                "merge_mode": "",
                "tool_status": "failed",
                "tool_error": error,
                "candidate_route": "failed",
            }
        candidates = candidates or []
        route = model.interpret_candidate_count(
            runtime.context.request,
            len(candidates),
            state.get("ScratchPad", []),
        )["route"]
        return {
            "candidates": candidates,
            "selected_ids": [],
            "merge_mode": "",
            "tool_status": "ok",
            "tool_error": {},
            "candidate_route": route,
        }

    def candidate_route(state: SearchState) -> str:
        return state["candidate_route"]

    def no_candidates(state: SearchState) -> dict[str, Any]:
        return {"selected_ids": [], "merge_mode": ""}

    def merge_selected_ids(state: SearchState) -> dict[str, Any]:
        selected_ids = state["selected_ids"]
        existing_ids = state.get("authorized_meeting_ids", [])
        if not existing_ids:
            return {"authorized_meeting_ids": selected_ids, "merge_mode": "set"}
        if set(selected_ids).issubset(set(existing_ids)):
            return {"merge_mode": "unchanged"}
        prompt = {
            "kind": "id_merge",
            "message": "기존 허용 ID 목록에 새 ID를 추가할지, 새 목록으로 대체할지 선택하세요.",
            "existing_ids": existing_ids,
            "new_ids": selected_ids,
            "choices": ["add", "replace"],
        }
        while True:
            answer = interrupt(prompt)
            mode = answer.get("mode") if isinstance(answer, dict) else str(answer)
            if mode in {"add", "replace"}:
                break
            prompt = {**prompt, "error": "mode는 add 또는 replace여야 합니다."}
        ids = (
            list(dict.fromkeys(existing_ids + selected_ids))
            if mode == "add"
            else selected_ids
        )
        return {"authorized_meeting_ids": ids, "merge_mode": mode}

    def one_candidate(
        state: SearchState, runtime: Runtime[AgentContext]
    ) -> dict[str, Any]:
        candidate = state["candidates"][0]
        try:
            selected = repository.validate_selection(
                runtime.context.user_id, [candidate["id"]], [candidate["id"]]
            )
        except Exception:
            return {
                "selected_ids": [],
                "merge_mode": "",
                "tool_status": "failed",
                "tool_error": {
                    "code": "SELECTION_VALIDATION_FAILED",
                    "message": "선택한 회의록을 다시 검증하지 못했습니다.",
                    "attempts": 1,
                    "retryable": False,
                },
            }
        return {"selected_ids": selected, **merge_selected_ids({**state, "selected_ids": selected})}

    def multiple_candidates(
        state: SearchState, runtime: Runtime[AgentContext]
    ) -> dict[str, Any]:
        prompt = {
            "kind": "meeting_selection",
            "message": "접근 가능한 후보가 여러 개입니다. 하나 이상의 meeting_id를 선택하세요.",
            "candidates": state["candidates"],
        }
        while True:
            answer = interrupt(prompt)
            selected_ids = answer.get("meeting_ids", []) if isinstance(answer, dict) else answer
            if isinstance(selected_ids, str):
                selected_ids = [item.strip() for item in selected_ids.split(",") if item.strip()]
            if not isinstance(selected_ids, list) or not all(
                isinstance(item, str) for item in selected_ids
            ):
                prompt = {
                    **prompt,
                    "error": "검색 후보 중 하나 이상의 meeting_id를 다시 선택하세요.",
                }
                continue
            try:
                selected = repository.validate_selection(
                    runtime.context.user_id,
                    [item["id"] for item in state["candidates"]],
                    selected_ids,
                )
                return {
                    "selected_ids": selected,
                    **merge_selected_ids({**state, "selected_ids": selected}),
                }
            except ValueError:
                prompt = {
                    **prompt,
                    "error": "검색 후보 중 하나 이상의 meeting_id를 다시 선택하세요.",
                }
            except Exception:
                return {
                    "selected_ids": [],
                    "merge_mode": "",
                    "tool_status": "failed",
                    "tool_error": {
                        "code": "SELECTION_VALIDATION_FAILED",
                        "message": "선택한 회의록을 다시 검증하지 못했습니다.",
                        "attempts": 1,
                        "retryable": False,
                    },
                }

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
            "selected_count": len(selected),
            "selected_titles": [item["title"] for item in selected],
            "mode": mode if selected else "unchanged",
        }
        if failed:
            result["error"] = state.get("tool_error", {})
        response = model.search_response(
            runtime.context.request,
            [{"title": item["title"]} for item in selected],
            mode,
            result,
            state.get("ScratchPad", []),
        )
        return {
            "ScratchPad": [
                _event(
                    "search_response",
                    response=response["response"],
                    follow_up=response["follow_up"],
                    **result,
                )
            ]
        }

    builder.add_node("S1_candidate_search_and_judgment", search_and_judge_candidates)
    builder.add_node("S2A_no_candidates", no_candidates)
    builder.add_node("S2B_one_candidate", one_candidate)
    builder.add_node("S2C_multiple_candidates", multiple_candidates)
    builder.add_node("S3_search_response_ready", search_response)
    builder.add_edge(START, "S1_candidate_search_and_judgment")
    builder.add_conditional_edges(
        "S1_candidate_search_and_judgment",
        candidate_route,
        {
            "none": "S2A_no_candidates",
            "one": "S2B_one_candidate",
            "many": "S2C_multiple_candidates",
            "failed": "S3_search_response_ready",
        },
    )
    builder.add_edge("S2A_no_candidates", "S3_search_response_ready")
    builder.add_edge("S2B_one_candidate", "S3_search_response_ready")
    builder.add_edge("S2C_multiple_candidates", "S3_search_response_ready")
    builder.add_edge("S3_search_response_ready", END)
    return builder.compile()


def build_question_subgraph(repository: MeetingRepository, model: MeetingModel):
    builder = StateGraph(QuestionState, context_schema=AgentContext)

    def tool_2_source_lookup(
        state: QuestionState, runtime: Runtime[AgentContext]
    ) -> dict[str, Any]:
        ids = state.get("authorized_meeting_ids", [])
        documents: list[dict[str, str]] = []
        if not ids:
            result: dict[str, Any] = {"status": "selection_required"}
        else:
            loaded, error = _call_with_retry(
                lambda: repository.get_meeting_documents(runtime.context.user_id, ids),
                permission_error_code="UNAUTHORIZED_MEETING_ID",
            )
            if error:
                result = {
                    "status": "source_denied_or_failed",
                    "reason": "권한 검증 또는 회의록 원문 조회에 실패했습니다.",
                    "error": {
                        key: error[key]
                        for key in ("code", "attempts", "retryable")
                    },
                }
            else:
                documents = loaded or []
                result = {
                    "status": "source_ready",
                    "documents": [
                        {"title": doc["title"]} for doc in documents
                    ],
                }

        return {
            "tool_status": result["status"],
            "last_result": result,
            "documents": documents,
        }

    def result_route(state: QuestionState) -> str:
        return state["tool_status"]

    def build_context(state: QuestionState) -> dict[str, Any]:
        return {
            "model_context": {
                "documents": state.get("documents", []),
                "tool_result": state["last_result"],
            }
        }

    def question_response(
        state: QuestionState, runtime: Runtime[AgentContext]
    ) -> dict[str, Any]:
        context = state["model_context"]
        response = model.answer_question(
            SYSTEM_INSTRUCTION,
            runtime.context.request,
            state.get("ScratchPad", []),
            [
                {key: value for key, value in document.items() if key != "id"}
                for document in context["documents"]
            ],
            context["tool_result"],
        )
        response = _without_full_sources(response, context["documents"])
        result = context["tool_result"]
        return {
            "ScratchPad": [
                _event(
                    "question_response",
                    response=response["response"],
                    follow_up=response["follow_up"],
                    status=result["status"],
                    document_count=len(result.get("documents", [])),
                    document_titles=[
                        document["title"] for document in result.get("documents", [])
                    ],
                    error=result.get("error"),
                )
            ]
        }

    builder.add_node("Q1_tool_2_source_lookup", tool_2_source_lookup)
    builder.add_node("Q2A_build_selection_context", build_context)
    builder.add_node("Q2B_build_source_context", build_context)
    builder.add_node("Q2C_build_failure_context", build_context)
    builder.add_node("Q3_question_response", question_response)
    builder.add_edge(START, "Q1_tool_2_source_lookup")
    builder.add_conditional_edges(
        "Q1_tool_2_source_lookup",
        result_route,
        {
            "selection_required": "Q2A_build_selection_context",
            "source_ready": "Q2B_build_source_context",
            "source_denied_or_failed": "Q2C_build_failure_context",
        },
    )
    builder.add_edge("Q2A_build_selection_context", "Q3_question_response")
    builder.add_edge("Q2B_build_source_context", "Q3_question_response")
    builder.add_edge("Q2C_build_failure_context", "Q3_question_response")
    builder.add_edge("Q3_question_response", END)
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
        return {"ScratchPad": {"__replace__": compact_scratchpad_events(events)}}

    def decide(
        state: AgentState, runtime: Runtime[AgentContext]
    ) -> Command[
        Literal[
            "search_subgraph",
            "question_subgraph",
            "llm_direct_answer",
        ]
    ]:
        decision = model.decide_next_action(
            runtime.context.request,
            _current_goal_events(state),
        )
        action = decision["action"]
        destinations = {
            "search": "search_subgraph",
            "question": "question_subgraph",
            "direct": "llm_direct_answer",
        }
        return Command(
            goto=destinations[action],
            update={"ScratchPad": [_event("llm_decision", **decision)]},
        )

    def direct_answer(
        state: AgentState, runtime: Runtime[AgentContext]
    ) -> dict[str, Any]:
        response = model.direct_response(
            SYSTEM_INSTRUCTION,
            runtime.context.request,
            state.get("ScratchPad", []),
        )
        return {
            "ScratchPad": [
                _event(
                    "direct_response",
                    response=response["response"],
                    follow_up=response["follow_up"],
                    status="direct",
                )
            ]
        }

    def follow_up_route(state: AgentState) -> str:
        event = next(
            item for item in reversed(_current_goal_events(state))
            if item.get("kind") in {"search_response", "question_response"}
        )
        return "continue" if event.get("follow_up") is True else "finish"

    builder.add_node("receive_user_request", receive_request)
    builder.add_node("compact_scratchpad", compact_scratchpad)
    builder.add_node("llm_goal_condition", decide)
    builder.add_node("search_subgraph", search_graph)
    builder.add_node("question_subgraph", question_graph)
    builder.add_node("llm_direct_answer", direct_answer)
    builder.add_edge(START, "receive_user_request")
    builder.add_edge("receive_user_request", "compact_scratchpad")
    builder.add_edge("compact_scratchpad", "llm_goal_condition")
    builder.add_conditional_edges(
        "search_subgraph",
        follow_up_route,
        {"continue": "compact_scratchpad", "finish": END},
    )
    builder.add_conditional_edges(
        "question_subgraph",
        follow_up_route,
        {"continue": "compact_scratchpad", "finish": END},
    )
    builder.add_edge("llm_direct_answer", END)
    return builder
