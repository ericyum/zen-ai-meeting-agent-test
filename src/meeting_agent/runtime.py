from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterator

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .agent_graph import build_agent_graph
from .model import MeetingModel, RuleBasedMeetingModel
from .recording_graph import build_recording_workflow
from .repository import MeetingRepository
from .state import AgentContext
from .tracing import debug_event_to_trace


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
        self._thread_locks: dict[tuple[str, str], threading.RLock] = {}
        self._pending_contexts: dict[tuple[str, str], AgentContext] = {}

    def close(self) -> None:
        self.repository.close()
        self.checkpoint_conn.close()

    def __enter__(self) -> "MeetingAgentRuntime":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @staticmethod
    def _storage_key(user_id: str, thread_id: str, namespace: str) -> str:
        return json.dumps(
            [namespace, user_id, thread_id], ensure_ascii=False, separators=(",", ":")
        )

    @staticmethod
    def _config(user_id: str, thread_id: str, namespace: str) -> dict[str, Any]:
        # checkpoint_ns is reserved by LangGraph for nested subgraphs.  Keep the
        # agent and deterministic recording workflow isolated by giving each a
        # stable, namespaced persistence thread instead.
        return {
            "configurable": {
                "thread_id": MeetingAgentRuntime._storage_key(
                    user_id, thread_id, namespace
                )
            }
        }

    def seed(self) -> None:
        self.repository.seed_dummy_data()

    def _lock_for(self, user_id: str, thread_id: str) -> threading.RLock:
        key = (user_id, thread_id)
        with self._locks_guard:
            return self._thread_locks.setdefault(key, threading.RLock())

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

    def _pending_result(self, user_id: str, thread_id: str) -> dict[str, Any] | None:
        if (user_id, thread_id) not in self._pending_contexts:
            return None
        snapshot = self.agent_graph.get_state(
            self._config(user_id, thread_id, "meeting-agent")
        )
        interrupts = [
            item
            for task in snapshot.tasks
            for item in getattr(task, "interrupts", ())
        ]
        if not interrupts:
            return None
        return self._result_view({**snapshot.values, "__interrupt__": interrupts})

    def run_agent(
        self,
        user_id: str,
        thread_id: str,
        request: str,
    ) -> dict[str, Any]:
        with self._lock_for(user_id, thread_id):
            key = (user_id, thread_id)
            pending = self._pending_result(user_id, thread_id)
            if pending is not None:
                return pending
            config = self._config(user_id, thread_id, "meeting-agent")
            snapshot = self.agent_graph.get_state(config)
            payload: dict[str, Any] = {}
            if not snapshot.values:
                payload.update({"ScratchPad": [], "authorized_meeting_ids": [], "recording_modal_status": "healthy"})
            context = AgentContext(user_id=user_id, thread_id=thread_id, request=request)
            self._pending_contexts[key] = context
            result = self.agent_graph.invoke(payload, config, context=context)
            if not result.get("__interrupt__"):
                self._pending_contexts.pop(key, None)
            return self._result_view(result)

    def run_agent_traced(
        self,
        user_id: str,
        thread_id: str,
        request: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Run the real graph and convert LangGraph debug events for the demo UI."""

        events = list(self.iter_agent_traced(user_id, thread_id, request))
        final = events.pop()
        return final["result"], events

    def iter_agent_traced(
        self,
        user_id: str,
        thread_id: str,
        request: str,
    ) -> Iterator[dict[str, Any]]:
        """Yield sanitized LangGraph events as they happen, then one final result."""

        with self._lock_for(user_id, thread_id):
            key = (user_id, thread_id)
            pending = self._pending_result(user_id, thread_id)
            if pending is not None:
                yield {"type": "final", "result": pending}
                return
            config = self._config(user_id, thread_id, "meeting-agent")
            snapshot = self.agent_graph.get_state(config)
            payload: dict[str, Any] = {}
            if not snapshot.values:
                payload.update({"ScratchPad": [], "authorized_meeting_ids": [], "recording_modal_status": "healthy"})
            context = AgentContext(user_id=user_id, thread_id=thread_id, request=request)
            self._pending_contexts[key] = context
            for namespace, event in self.agent_graph.stream(
                payload,
                config,
                context=context,
                stream_mode="debug",
                subgraphs=True,
            ):
                yield from debug_event_to_trace(namespace, event)
            snapshot = self.agent_graph.get_state(config)
            interrupts = [
                interrupt
                for task in snapshot.tasks
                for interrupt in getattr(task, "interrupts", ())
            ]
            result = dict(snapshot.values)
            if interrupts:
                result["__interrupt__"] = interrupts
            else:
                self._pending_contexts.pop(key, None)
            yield {"type": "final", "result": self._result_view(result)}

    def resume_agent(
        self, user_id: str, thread_id: str, answer: Any
    ) -> dict[str, Any]:
        with self._lock_for(user_id, thread_id):
            key = (user_id, thread_id)
            context = self._pending_contexts.get(key)
            if context is None:
                raise RuntimeError("현재 프로세스에 재개할 HITL 실행 컨텍스트가 없습니다.")
            result = self.agent_graph.invoke(
                Command(resume=answer),
                self._config(user_id, thread_id, "meeting-agent"),
                context=context,
            )
            if not result.get("__interrupt__"):
                self._pending_contexts.pop(key, None)
            return self._result_view(result)

    def resume_agent_traced(
        self, user_id: str, thread_id: str, answer: Any
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        events = list(self.iter_resume_agent_traced(user_id, thread_id, answer))
        final = events.pop()
        return final["result"], events

    def iter_resume_agent_traced(
        self, user_id: str, thread_id: str, answer: Any
    ) -> Iterator[dict[str, Any]]:
        with self._lock_for(user_id, thread_id):
            key = (user_id, thread_id)
            context = self._pending_contexts.get(key)
            if context is None:
                raise RuntimeError("현재 프로세스에 재개할 HITL 실행 컨텍스트가 없습니다.")
            for namespace, event in self.agent_graph.stream(
                Command(resume=answer),
                self._config(user_id, thread_id, "meeting-agent"),
                context=context,
                stream_mode="debug",
                subgraphs=True,
            ):
                yield from debug_event_to_trace(namespace, event)
            snapshot = self.agent_graph.get_state(
                self._config(user_id, thread_id, "meeting-agent")
            )
            interrupts = [
                interrupt
                for task in snapshot.tasks
                for interrupt in getattr(task, "interrupts", ())
            ]
            result = dict(snapshot.values)
            if interrupts:
                result["__interrupt__"] = interrupts
            else:
                self._pending_contexts.pop(key, None)
            yield {"type": "final", "result": self._result_view(result)}

    def run_recording(
        self, user_id: str, thread_id: str, command: str
    ) -> dict[str, Any]:
        with self._lock_for(user_id, thread_id):
            modal_thread_id = self._storage_key(user_id, thread_id, "recording-modal")
            result = self.recording_graph.invoke(
                {"user_id": user_id, "thread_id": modal_thread_id, "command": command}
            )
            config = self._config(user_id, thread_id, "meeting-agent")
            snapshot = self.agent_graph.get_state(config)
            update: dict[str, Any] = {
                "recording_modal_status": result["recording_modal_status"]
            }
            if not snapshot.values:
                update.update({"ScratchPad": [], "authorized_meeting_ids": []})
            self.agent_graph.update_state(config, update)
            return result

    def iter_recording_traced(
        self, user_id: str, thread_id: str, command: str
    ) -> Iterator[dict[str, Any]]:
        """Stream the real recording Graph, then its actual Agent checkpoint sync."""

        with self._lock_for(user_id, thread_id):
            modal_thread_id = self._storage_key(user_id, thread_id, "recording-modal")
            result: dict[str, Any] = {}
            for namespace, event in self.recording_graph.stream(
                {"user_id": user_id, "thread_id": modal_thread_id, "command": command},
                stream_mode="debug",
                subgraphs=True,
            ):
                payload = event.get("payload", {})
                if (
                    event.get("type") == "task_result"
                    and payload.get("name") == "recording_modal_and_backend"
                    and isinstance(payload.get("result"), dict)
                ):
                    result = payload["result"]
                yield from debug_event_to_trace(
                    namespace, event, root_graph="Recording Workflow"
                )
            if not result:
                raise RuntimeError("녹화 Workflow 결과를 확인할 수 없습니다.")

            config = self._config(user_id, thread_id, "meeting-agent")
            snapshot = self.agent_graph.get_state(config)
            update: dict[str, Any] = {
                "recording_modal_status": result["recording_modal_status"]
            }
            if not snapshot.values:
                update.update({"ScratchPad": [], "authorized_meeting_ids": []})
            self.agent_graph.update_state(config, update)
            yield {
                "type": "business_checkpoint",
                "graph": "Graph Runtime",
                "edge": "recording_modal_status → Agent Checkpoint",
                "state": {"recording_modal_status": result["recording_modal_status"]},
            }
            yield {"type": "final", "result": result}

    def get_agent_state(self, user_id: str, thread_id: str) -> dict[str, Any]:
        snapshot = self.agent_graph.get_state(
            self._config(user_id, thread_id, "meeting-agent")
        )
        return dict(snapshot.values) if snapshot.values else {}

    def list_agent_checkpoints(
        self, user_id: str, thread_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        config = self._config(user_id, thread_id, "meeting-agent")
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
        self, user_id: str, thread_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Return parent Agent State snapshots for persistence-focused tests."""

        config = self._config(user_id, thread_id, "meeting-agent")
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
