from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict


Action = Literal["search", "question", "direct", "none"]
RecordingModalStatus = Literal["healthy", "error"]
RecordingCommand = Literal["start", "pause", "resume", "stop"]


def reduce_scratchpad(
    current: list[dict[str, Any]], update: Any
) -> list[dict[str, Any]]:
    """Append ordinary events or atomically replace them after compaction."""

    if isinstance(update, dict) and "__replace__" in update:
        return list(update["__replace__"])
    return list(current) + list(update)


class AgentState(TypedDict, total=False):
    """The complete durable Agent harness state from the design.

    `search` and `question` are intentionally not stored as durable fields. The
    active LangGraph node and edge embody those conceptual Graph states.
    """

    ScratchPad: Annotated[list[dict[str, Any]], reduce_scratchpad]
    authorized_meeting_ids: list[str]
    recording_modal_status: RecordingModalStatus


@dataclass(frozen=True)
class AgentContext:
    """Per-invocation data that must not enter the durable Agent State."""

    user_id: str
    thread_id: str
    request: str


class SearchState(AgentState, total=False):
    """Search Subgraph-local execution data."""

    candidates: list[dict[str, Any]]
    selected_ids: list[str]
    merge_mode: Literal["set", "add", "replace", "unchanged", ""]
    tool_status: Literal["ok", "failed"]
    tool_error: dict[str, Any]


class QuestionState(AgentState, total=False):
    """Question Subgraph-local execution data."""

    tool_status: Literal["selection_required", "source_ready", "source_denied_or_failed"]
    last_result: dict[str, Any]


class RecordingWorkflowState(TypedDict, total=False):
    user_id: str
    thread_id: str
    command: RecordingCommand
    previous_state: str
    current_state: str
    recording_modal_status: RecordingModalStatus
    response: str
