from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .agent_graph import build_agent_graph
from .model import MeetingModel, RuleBasedMeetingModel
from .recording_graph import build_recording_workflow
from .repository import MeetingRepository
from .state import AgentContext


class MeetingAgentRuntime:
    def __init__(
        self,
        app_db: str | Path,
        checkpoint_db: str | Path,
        model: MeetingModel | None = None,
    ):
        self.repository = MeetingRepository(app_db)
        self.model = model or RuleBasedMeetingModel()
        checkpoint_path = Path(checkpoint_db)
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_conn = sqlite3.connect(checkpoint_path, check_same_thread=False)
        self.checkpointer = SqliteSaver(self.checkpoint_conn)
        self.agent_graph = build_agent_graph(self.repository, self.model).compile(
            checkpointer=self.checkpointer
        )
        # recording_state belongs to the modal/backend repository, not to an
        # Agent or recording-graph checkpoint.
        self.recording_graph = build_recording_workflow(self.repository).compile()
        self._locks_guard = threading.Lock()
        self._thread_locks: dict[str, threading.RLock] = {}
        self._pending_contexts: dict[str, AgentContext] = {}

    def close(self) -> None:
        self.repository.close()
        self.checkpoint_conn.close()

    def __enter__(self) -> "MeetingAgentRuntime":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @staticmethod
    def _config(thread_id: str, namespace: str) -> dict[str, Any]:
        # checkpoint_ns is reserved by LangGraph for nested subgraphs.  Keep the
        # agent and deterministic recording workflow isolated by giving each a
        # stable, namespaced persistence thread instead.
        return {"configurable": {"thread_id": f"{namespace}:{thread_id}"}}

    def seed(self) -> None:
        self.repository.seed_dummy_data()

    def _lock_for(self, thread_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._thread_locks.setdefault(thread_id, threading.RLock())

    @staticmethod
    def _response_from_state(state: dict[str, Any]) -> str:
        for event in reversed(state.get("ScratchPad", [])):
            if event.get("kind") == "user_request":
                break
            if "response" in event:
                return str(event["response"])
        return ""

    def _result_view(self, state: dict[str, Any]) -> dict[str, Any]:
        # response is an API convenience view and is not a persisted State field.
        return {**state, "response": self._response_from_state(state)}

    def run_agent(
        self,
        user_id: str,
        thread_id: str,
        request: str,
    ) -> dict[str, Any]:
        with self._lock_for(thread_id):
            config = self._config(thread_id, "meeting-agent")
            snapshot = self.agent_graph.get_state(config)
            payload: dict[str, Any] = {
                "recording_modal_status": self.repository.get_modal_status(thread_id)
            }
            if not snapshot.values:
                payload.update({"ScratchPad": [], "authorized_meeting_ids": []})
            context = AgentContext(user_id=user_id, thread_id=thread_id, request=request)
            self._pending_contexts[thread_id] = context
            result = self.agent_graph.invoke(payload, config, context=context)
            if not result.get("__interrupt__"):
                self._pending_contexts.pop(thread_id, None)
            return self._result_view(result)

    def resume_agent(self, thread_id: str, answer: Any) -> dict[str, Any]:
        with self._lock_for(thread_id):
            context = self._pending_contexts.get(thread_id)
            if context is None:
                raise RuntimeError("현재 프로세스에 재개할 HITL 실행 컨텍스트가 없습니다.")
            result = self.agent_graph.invoke(
                Command(resume=answer),
                self._config(thread_id, "meeting-agent"),
                context=context,
            )
            if not result.get("__interrupt__"):
                self._pending_contexts.pop(thread_id, None)
            return self._result_view(result)

    def run_recording(
        self, user_id: str, thread_id: str, command: str
    ) -> dict[str, Any]:
        with self._lock_for(thread_id):
            result = self.recording_graph.invoke(
                {"user_id": user_id, "thread_id": thread_id, "command": command}
            )
            config = self._config(thread_id, "meeting-agent")
            snapshot = self.agent_graph.get_state(config)
            update: dict[str, Any] = {
                "recording_modal_status": result["recording_modal_status"]
            }
            if not snapshot.values:
                update.update({"ScratchPad": [], "authorized_meeting_ids": []})
            self.agent_graph.update_state(config, update)
            return result

    def get_agent_state(self, thread_id: str) -> dict[str, Any]:
        snapshot = self.agent_graph.get_state(self._config(thread_id, "meeting-agent"))
        return dict(snapshot.values) if snapshot.values else {}

    def list_agent_checkpoints(self, thread_id: str, limit: int = 20) -> list[dict[str, Any]]:
        config = self._config(thread_id, "meeting-agent")
        items = []
        for index, checkpoint in enumerate(self.checkpointer.list(config)):
            if index >= limit:
                break
            items.append(
                {
                    "config": checkpoint.config,
                    "metadata": checkpoint.metadata,
                    "parent_config": checkpoint.parent_config,
                }
            )
        return items

    def list_agent_checkpoint_states(
        self, thread_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Return parent Agent State snapshots for persistence-focused tests."""

        config = self._config(thread_id, "meeting-agent")
        return [
            dict(snapshot.values)
            for index, snapshot in enumerate(self.agent_graph.get_state_history(config))
            if index < limit
        ]


def interrupt_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None
    first = interrupts[0]
    value = getattr(first, "value", first)
    return value if isinstance(value, dict) else {"message": str(value)}
