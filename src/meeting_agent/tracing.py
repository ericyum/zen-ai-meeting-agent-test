from __future__ import annotations

from typing import Any, Iterable


STATE_KEYS = {
    "ScratchPad",
    "authorized_meeting_ids",
    "recording_modal_status",
    "candidates",
    "selected_ids",
    "merge_mode",
    "tool_status",
    "tool_error",
    "last_result",
}


def graph_name(namespace: tuple[str, ...]) -> str:
    if not namespace:
        return "Agent Graph"
    name = namespace[-1].split(":", 1)[0]
    return {
        "search_subgraph": "Search Subgraph",
        "question_subgraph": "Question Subgraph",
    }.get(name, name)


def safe_value(value: Any) -> Any:
    """Make a browser-safe trace value without exposing meeting transcripts."""

    if isinstance(value, dict):
        return {
            key: safe_value(item)
            for key, item in value.items()
            if key not in {"transcript", "api_key", "Authorization"}
        }
    if isinstance(value, (list, tuple)):
        return [safe_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def safe_state(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: safe_value(value)
        for key, value in values.items()
        if key in STATE_KEYS
    }


def debug_stream_to_trace(
    chunks: Iterable[tuple[tuple[str, ...], dict[str, Any]]]
) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    for namespace, event in chunks:
        event_type = event.get("type")
        payload = event.get("payload", {})
        graph = graph_name(namespace)
        if event_type == "task":
            node = payload.get("name", "unknown")
            if node == "__start__":
                continue
            triggers = [str(item) for item in payload.get("triggers", ())]
            trace.append(
                {
                    "type": "node",
                    "phase": "start",
                    "graph": graph,
                    "node": node,
                    "step": event.get("step"),
                    "edge": triggers[0] if triggers else "",
                }
            )
        elif event_type == "task_result":
            node = payload.get("name", "unknown")
            if node == "__start__":
                continue
            result = payload.get("result")
            trace.append(
                {
                    "type": "node",
                    "phase": "end",
                    "graph": graph,
                    "node": node,
                    "step": event.get("step"),
                    "output": safe_state(result) if isinstance(result, dict) else safe_value(result),
                    "error": safe_value(payload.get("error")),
                    "interrupts": safe_value(payload.get("interrupts", [])),
                }
            )
        elif event_type == "checkpoint" and event.get("step", -1) >= 0:
            values = payload.get("values", {})
            next_nodes = list(payload.get("next", []))
            trace.append(
                {
                    "type": "state",
                    "graph": graph,
                    "step": event.get("step"),
                    "next": next_nodes,
                    "edge": f"→ {', '.join(next_nodes)}" if next_nodes else "→ END",
                    "state": safe_state(values) if isinstance(values, dict) else {},
                }
            )
    return trace
