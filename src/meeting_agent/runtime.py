from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .agent_graph import build_agent_graph
from .model import MeetingModel, RuleBasedMeetingModel
from .recording_graph import build_recording_workflow
from .repository import MeetingRepository


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
        self.recording_graph = build_recording_workflow(self.repository).compile(
            checkpointer=self.checkpointer
        )

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

    def run_agent(
        self,
        user_id: str,
        thread_id: str,
        request: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        config = self._config(thread_id, "meeting-agent")
        snapshot = self.agent_graph.get_state(config)
        payload: dict[str, Any] = {
            "user_id": user_id,
            "thread_id": thread_id,
            "request_id": request_id or str(uuid.uuid4()),
            "request": request,
            "recording_modal_status": self.repository.get_modal_status(thread_id),
        }
        if not snapshot.values:
            payload.update({"ScratchPad": [], "authorized_meeting_ids": []})
        return self.agent_graph.invoke(payload, config)

    def resume_agent(self, thread_id: str, answer: Any) -> dict[str, Any]:
        return self.agent_graph.invoke(
            Command(resume=answer), self._config(thread_id, "meeting-agent")
        )

    def run_recording(
        self, user_id: str, thread_id: str, command: str
    ) -> dict[str, Any]:
        return self.recording_graph.invoke(
            {"user_id": user_id, "thread_id": thread_id, "command": command},
            self._config(thread_id, "recording-workflow"),
        )

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


def interrupt_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None
    first = interrupts[0]
    value = getattr(first, "value", first)
    return value if isinstance(value, dict) else {"message": str(value)}
