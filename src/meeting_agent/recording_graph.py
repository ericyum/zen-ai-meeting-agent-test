from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .repository import MeetingRepository
from .state import RecordingWorkflowState


def build_recording_workflow(repository: MeetingRepository):
    """Deterministic workflow: no LLM and no Agent Tool selection."""

    builder = StateGraph(RecordingWorkflowState)

    def execute_modal(state: RecordingWorkflowState):
        try:
            result = repository.execute_recording_command(
                state["user_id"], state["thread_id"], state["command"]
            )
            return {
                "previous_state": result.previous_state,
                "current_state": result.current_state,
                "recording_modal_status": result.modal_status,
                "response": result.message,
            }
        except Exception as exc:  # POC boundary for modal/connection errors.
            return {
                "previous_state": repository.get_recording_state(state["thread_id"]),
                "current_state": repository.get_recording_state(state["thread_id"]),
                "recording_modal_status": "error",
                "response": f"녹화 모달 실행 오류: {exc}",
            }

    builder.add_node("recording_modal_and_backend", execute_modal)
    builder.add_edge(START, "recording_modal_and_backend")
    builder.add_edge("recording_modal_and_backend", END)
    return builder

