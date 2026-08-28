from __future__ import annotations

import re
from typing import Any, Protocol


class MeetingModel(Protocol):
    def decide_next_action(
        self,
        request: str,
        request_events: list[dict[str, Any]],
        authorized_ids: list[str],
    ) -> str: ...

    def search_response(
        self, request: str, selected: list[dict[str, Any]], mode: str
    ) -> str: ...

    def answer_question(self, request: str, documents: list[dict[str, str]]) -> str: ...

    def direct_response(self, request: str) -> str: ...


class RuleBasedMeetingModel:
    """Offline stand-in for LLM decisions used by the executable POC.

    Swapping this class for a provider-backed implementation does not change
    the graph topology, Tool boundary, persistence, or HITL behavior.
    """

    search_words = ("검색", "찾", "가져오", "선택", "추가", "대체")
    question_words = ("요약", "설명", "질문", "결정", "내용", "무엇", "알려", "어떤")

    def decide_next_action(
        self,
        request: str,
        request_events: list[dict[str, Any]],
        authorized_ids: list[str],
    ) -> str:
        kinds = {event.get("kind") for event in request_events}
        search_event = next(
            (event for event in reversed(request_events) if event.get("kind") == "search_response"),
            None,
        )
        needs_search = bool(re.search(r"meeting-\d{3}", request.lower())) or any(
            word in request for word in self.search_words
        )
        needs_question = any(word in request for word in self.question_words)

        if needs_search and "search_response" not in kinds:
            return "search"
        if search_event is not None and not search_event.get("selected_ids"):
            return "done"
        if needs_question and "question_response" not in kinds:
            return "question"
        if not needs_search and not needs_question and "direct_response" not in kinds:
            return "direct"
        return "done"

    def search_response(
        self, request: str, selected: list[dict[str, Any]], mode: str
    ) -> str:
        if not selected:
            return "접근 가능한 관련 회의록을 찾지 못했습니다. 기존 허용 ID는 유지합니다."
        ids = ", ".join(item["id"] for item in selected)
        titles = ", ".join(item["title"] for item in selected)
        mode_text = {"set": "새 목록으로 설정", "add": "기존 목록에 추가", "replace": "새 목록으로 대체"}[mode]
        return f"회의록 {ids} ({titles})을 확인하여 {mode_text}했습니다."

    def answer_question(self, request: str, documents: list[dict[str, str]]) -> str:
        if not documents:
            return "질문에 사용할 회의록이 선택되지 않았습니다. 먼저 관련 회의록을 검색해 주세요."
        sections = []
        for doc in documents:
            transcript = doc["transcript"].strip()
            sections.append(f"- {doc['title']} ({doc['id']}): {transcript}")
        return "선택된 회의록을 기준으로 확인한 내용입니다.\n" + "\n".join(sections)

    def direct_response(self, request: str) -> str:
        return "이 요청은 회의록 Tool 없이 답변했습니다. 회의록 검색이나 내용 질문을 요청할 수 있습니다."

