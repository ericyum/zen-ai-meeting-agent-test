from __future__ import annotations

import re
import json
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-pro"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class MeetingModel(Protocol):
    def decide_next_action(
        self,
        request: str,
        request_events: list[dict[str, Any]],
        authorized_ids: list[str],
    ) -> str: ...

    def interpret_candidate_count(
        self,
        request: str,
        candidates: list[dict[str, Any]],
        scratchpad: list[dict[str, Any]],
    ) -> str: ...

    def search_response(
        self,
        request: str,
        selected: list[dict[str, Any]],
        mode: str,
        tool_result: dict[str, Any],
    ) -> str: ...

    def answer_question(
        self,
        system_instruction: str,
        request: str,
        scratchpad: list[dict[str, Any]],
        documents: list[dict[str, str]],
        tool_result: dict[str, Any],
    ) -> str: ...

    def direct_response(
        self, system_instruction: str, request: str, scratchpad: list[dict[str, Any]]
    ) -> str: ...


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

    def interpret_candidate_count(
        self,
        request: str,
        candidates: list[dict[str, Any]],
        scratchpad: list[dict[str, Any]],
    ) -> str:
        count = len(candidates)
        return "none" if count == 0 else "one" if count == 1 else "many"

    def search_response(
        self,
        request: str,
        selected: list[dict[str, Any]],
        mode: str,
        tool_result: dict[str, Any],
    ) -> str:
        if tool_result.get("status") == "failed":
            error = tool_result.get("error", {})
            return f"회의록 검색을 완료하지 못했습니다: {error.get('message', '알 수 없는 오류')}"
        if not selected:
            return "접근 가능한 관련 회의록을 찾지 못했습니다. 기존 허용 ID는 유지합니다."
        ids = ", ".join(item["id"] for item in selected)
        titles = ", ".join(item["title"] for item in selected)
        mode_text = {"set": "새 목록으로 설정", "add": "기존 목록에 추가", "replace": "새 목록으로 대체"}[mode]
        return f"회의록 {ids} ({titles})을 확인하여 {mode_text}했습니다."

    def answer_question(
        self,
        system_instruction: str,
        request: str,
        scratchpad: list[dict[str, Any]],
        documents: list[dict[str, str]],
        tool_result: dict[str, Any],
    ) -> str:
        status = tool_result.get("status")
        if status == "selection_required":
            return "질문에 사용할 회의록이 선택되지 않았습니다. 먼저 관련 회의록을 검색해 주세요."
        if status == "source_denied_or_failed":
            return f"회의록 원문을 조회하지 못했습니다: {tool_result.get('reason', '알 수 없는 오류')}"
        if not documents:
            return "질문에 사용할 회의록이 선택되지 않았습니다. 먼저 관련 회의록을 검색해 주세요."
        sections = []
        for doc in documents:
            transcript = doc["transcript"].strip()
            sections.append(f"- {doc['title']} ({doc['id']}): {transcript}")
        return "선택된 회의록을 기준으로 확인한 내용입니다.\n" + "\n".join(sections)

    def direct_response(
        self, system_instruction: str, request: str, scratchpad: list[dict[str, Any]]
    ) -> str:
        return "이 요청은 회의록 Tool 없이 답변했습니다. 회의록 검색이나 내용 질문을 요청할 수 있습니다."


class DeepSeekMeetingModel:
    """Real DeepSeek Chat Completions adapter for the MeetingModel boundary."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_DEEPSEEK_MODEL,
        base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
        timeout_seconds: float = 90.0,
    ):
        if not api_key.strip():
            raise ValueError("DeepSeek API 키가 비어 있습니다.")
        self._api_key = api_key.strip()
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_key_file(
        cls,
        key_file: str | Path,
        model: str = DEFAULT_DEEPSEEK_MODEL,
        base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
    ) -> "DeepSeekMeetingModel":
        path = Path(key_file).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"DeepSeek API 키 파일을 찾을 수 없습니다: {path}")
        return cls(path.read_text(encoding="utf-8").strip(), model=model, base_url=base_url)

    @staticmethod
    def _json_text(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    def _complete(
        self,
        system: str,
        user: str,
        *,
        json_response: bool = False,
        max_tokens: int = 800,
    ) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if json_response:
            body["response_format"] = {"type": "json_object"}
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek API HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"DeepSeek API 연결 실패: {exc.reason}") from exc
        try:
            message = payload["choices"][0]["message"]
            content = message.get("content")
            if not content:
                finish_reason = payload["choices"][0].get("finish_reason", "unknown")
                raise RuntimeError(
                    "DeepSeek API가 빈 최종 답변을 반환했습니다. "
                    f"finish_reason={finish_reason}; max_tokens를 늘리거나 프롬프트를 확인하세요."
                )
            return str(content).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"DeepSeek API 응답 형식이 올바르지 않습니다: {payload}") from exc

    def _enum(
        self,
        field: str,
        allowed: set[str],
        system: str,
        user: str,
    ) -> str:
        # deepseek-v4-pro is a reasoning model. Its thinking tokens share this
        # budget, so even a one-field JSON answer needs a practical allowance.
        raw = self._complete(system, user, json_response=True, max_tokens=512)
        try:
            value = str(json.loads(raw)[field])
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RuntimeError(f"DeepSeek 구조화 응답을 해석할 수 없습니다: {raw}") from exc
        if value not in allowed:
            raise RuntimeError(f"DeepSeek가 허용되지 않은 {field} 값을 반환했습니다: {value}")
        return value

    def decide_next_action(
        self,
        request: str,
        request_events: list[dict[str, Any]],
        authorized_ids: list[str],
    ) -> str:
        return self._enum(
            "action",
            {"search", "question", "direct", "done"},
            (
                "당신은 회의록 Agent의 목표 판단기다. 반드시 JSON 객체만 반환한다. "
                "action은 search, question, direct, done 중 하나다. 회의록을 찾거나 선택해야 하면 search, "
                "선택된 회의록 내용에 답해야 하면 question, Tool이 불필요한 일반 응답이면 direct, "
                "현재 사용자 목표가 이미 완료됐으면 done이다. 완료된 작업을 반복하지 마라. "
                "판정 우선순위: (1) 요청이 검색·가져오기를 포함하고 search_response가 없으면 search, "
                "(2) 요청이 내용·결정·요약·설명을 포함하고 question_response가 없으며 검색이 성공했거나 "
                "authorized_meeting_ids가 있으면 question, (3) 필요한 응답 이벤트가 모두 있으면 done이다. "
                "검색과 질문이 결합된 요청은 search 뒤에 반드시 question을 수행해야 한다."
            ),
            self._json_text(
                {
                    "request": request,
                    "current_goal_events": request_events,
                    "authorized_meeting_ids": authorized_ids,
                    "required_output": {"action": "search|question|direct|done"},
                }
            ),
        )

    def interpret_candidate_count(
        self,
        request: str,
        candidates: list[dict[str, Any]],
        scratchpad: list[dict[str, Any]],
    ) -> str:
        count = len(candidates)
        return "none" if count == 0 else "one" if count == 1 else "many"

    def search_response(
        self,
        request: str,
        selected: list[dict[str, Any]],
        mode: str,
        tool_result: dict[str, Any],
    ) -> str:
        return self._complete(
            "검증된 검색 결과만 근거로 한국어로 간결하게 안내하라. 없는 회의록이나 권한을 만들어내지 마라.",
            self._json_text(
                {"request": request, "selected": selected, "mode": mode, "tool_result": tool_result}
            ),
            # deepseek-v4-pro의 추론 토큰도 같은 예산을 사용하므로
            # 짧은 안내 문장에도 충분한 완료 여유를 둔다.
            max_tokens=800,
        )

    def answer_question(
        self,
        system_instruction: str,
        request: str,
        scratchpad: list[dict[str, Any]],
        documents: list[dict[str, str]],
        tool_result: dict[str, Any],
    ) -> str:
        return self._complete(
            system_instruction
            + " 제공된 회의록 원문 밖의 사실은 만들지 말고, 근거가 없으면 없다고 명시하라.",
            self._json_text(
                {
                    "request": request,
                    "scratchpad": scratchpad,
                    "documents": documents,
                    "tool_result": tool_result,
                }
            ),
            max_tokens=1200,
        )

    def direct_response(
        self, system_instruction: str, request: str, scratchpad: list[dict[str, Any]]
    ) -> str:
        return self._complete(
            system_instruction + " Tool 없이 처리 가능한 요청에 한국어로 답하라.",
            self._json_text({"request": request, "scratchpad": scratchpad}),
            max_tokens=800,
        )
