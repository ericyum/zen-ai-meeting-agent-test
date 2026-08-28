from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict


Action = Literal["search", "question", "direct", "done"]
RecordingModalStatus = Literal["healthy", "error"]
RecordingCommand = Literal["start", "pause", "resume", "stop"]


class AgentState(TypedDict, total=False):
    """Persisted Agent data.

    `search` and `question` are intentionally not stored as durable fields. The
    active LangGraph node and edge embody those conceptual Graph states.
    """

    user_id: str
    thread_id: str
    request_id: str
    request: str
    ScratchPad: Annotated[list[dict[str, Any]], operator.add]
    authorized_meeting_ids: list[str]
    recording_modal_status: RecordingModalStatus
    next_action: Action
    candidates: list[dict[str, Any]]
    selected_ids: list[str]
    merge_mode: Literal["set", "add", "replace", ""]
    response: str
    last_result: dict[str, Any]


class RecordingWorkflowState(TypedDict, total=False):
    user_id: str
    thread_id: str
    command: RecordingCommand
    previous_state: str
    current_state: str
    recording_modal_status: RecordingModalStatus
    response: str

