from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .runtime import MeetingAgentRuntime, interrupt_payload


def _runtime(args: argparse.Namespace) -> MeetingAgentRuntime:
    return MeetingAgentRuntime(args.app_db, args.checkpoint_db)


def _resume_value(payload: dict[str, Any]) -> Any:
    print(f"\n[HITL] {payload.get('message', '사용자 입력이 필요합니다.')}")
    if payload.get("kind") == "meeting_selection":
        for item in payload["candidates"]:
            print(f"  - {item['id']} | {item['meeting_date']} | {item['title']}")
        raw = input("선택할 meeting_id(쉼표 구분): ").strip()
        return {"meeting_ids": [item.strip() for item in raw.split(",") if item.strip()]}
    if payload.get("kind") == "id_merge":
        raw = input("add 또는 replace: ").strip().lower()
        return {"mode": raw}
    return input("응답: ")


def _run_with_interrupts(
    runtime: MeetingAgentRuntime,
    result: dict[str, Any],
    thread_id: str,
) -> dict[str, Any]:
    while True:
        payload = interrupt_payload(result)
        if payload is None:
            return result
        result = runtime.resume_agent(thread_id, _resume_value(payload))


def command_seed(args: argparse.Namespace) -> None:
    with _runtime(args) as runtime:
        runtime.seed()
    print(f"더미 데이터를 생성했습니다: {args.app_db}")


def command_ask(args: argparse.Namespace) -> None:
    with _runtime(args) as runtime:
        runtime.seed()
        result = runtime.run_agent(args.user_id, args.thread_id, args.request)
        result = _run_with_interrupts(runtime, result, args.thread_id)
        print(result.get("response", ""))
        if args.debug:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def command_chat(args: argparse.Namespace) -> None:
    with _runtime(args) as runtime:
        runtime.seed()
        print("ZEN AI 회의록 Agent POC입니다. /quit로 종료합니다.")
        while True:
            request = input("\n사용자> ").strip()
            if request in {"/quit", "/exit"}:
                return
            if not request:
                continue
            result = runtime.run_agent(args.user_id, args.thread_id, request)
            result = _run_with_interrupts(runtime, result, args.thread_id)
            print(f"Agent> {result.get('response', '')}")


def command_record(args: argparse.Namespace) -> None:
    with _runtime(args) as runtime:
        runtime.seed()
        result = runtime.run_recording(args.user_id, args.thread_id, args.command)
        print(result["response"])
        print(
            f"recording_state={result['current_state']}, "
            f"recording_modal_status={result['recording_modal_status']}"
        )


def command_state(args: argparse.Namespace) -> None:
    with _runtime(args) as runtime:
        state = runtime.get_agent_state(args.thread_id)
        print(json.dumps(state, ensure_ascii=False, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ZEN AI meeting-agent LangGraph POC")
    parser.add_argument("--app-db", default="data/app.db")
    parser.add_argument("--checkpoint-db", default="data/checkpoints.db")
    sub = parser.add_subparsers(required=True)

    seed = sub.add_parser("seed", help="SQLite 더미 데이터 생성")
    seed.set_defaults(func=command_seed)

    ask = sub.add_parser("ask", help="Agent 요청 1회 실행")
    ask.add_argument("request")
    ask.add_argument("--user-id", default="user-eric")
    ask.add_argument("--thread-id", default="thread-eric")
    ask.add_argument("--debug", action="store_true")
    ask.set_defaults(func=command_ask)

    chat = sub.add_parser("chat", help="대화형 Agent 실행")
    chat.add_argument("--user-id", default="user-eric")
    chat.add_argument("--thread-id", default="thread-eric")
    chat.set_defaults(func=command_chat)

    record = sub.add_parser("record", help="결정적 녹화 Workflow 실행")
    record.add_argument("command", choices=["start", "pause", "resume", "stop"])
    record.add_argument("--user-id", default="user-eric")
    record.add_argument("--thread-id", default="thread-eric")
    record.set_defaults(func=command_record)

    state = sub.add_parser("state", help="현재 Agent Checkpoint State 확인")
    state.add_argument("--thread-id", default="thread-eric")
    state.set_defaults(func=command_state)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    Path(args.app_db).parent.mkdir(parents=True, exist_ok=True)
    Path(args.checkpoint_db).parent.mkdir(parents=True, exist_ok=True)
    args.func(args)

