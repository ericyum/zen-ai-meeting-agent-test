from __future__ import annotations

import re
import json
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-pro"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class SearchQuery(TypedDict):
    meeting_ids: list[str]
    keywords: list[str]
    meeting_date: str | None


class ActionDecision(TypedDict):
    action: Literal["search", "question", "direct"]
    search_query: SearchQuery | None


class CandidateDecision(TypedDict):
    route: Literal["none", "one", "many"]


class AgentResponse(TypedDict):
    response: str
    follow_up: bool


class MeetingModel(Protocol):
    def decide_next_action(
        self,
        request: str,
        request_events: list[dict[str, Any]],
    ) -> ActionDecision: ...

    def interpret_candidate_count(
        self,
        request: str,
        candidate_count: int,
        scratchpad: list[dict[str, Any]],
    ) -> CandidateDecision: ...

    def search_response(
        self,
        request: str,
        selected: list[dict[str, Any]],
        mode: str,
        tool_result: dict[str, Any],
        scratchpad: list[dict[str, Any]],
    ) -> AgentResponse: ...

    def answer_question(
        self,
        system_instruction: str,
        request: str,
        scratchpad: list[dict[str, Any]],
        documents: list[dict[str, str]],
        tool_result: dict[str, Any],
    ) -> AgentResponse: ...

    def direct_response(
        self, system_instruction: str, request: str, scratchpad: list[dict[str, Any]]
    ) -> AgentResponse: ...


class RuleBasedMeetingModel:
    """Deterministic stand-in that follows the same structured Model contract."""

    search_words = ("검색", "찾", "가져오", "선택", "추가", "대체")
    question_words = ("요약", "설명", "질문", "결정", "내용", "무엇", "알려", "어떤")

    def decide_next_action(
        self,
        request: str,
        request_events: list[dict[str, Any]],
    ) -> ActionDecision:
        kinds = {event.get("kind") for event in request_events}
        needs_search = bool(re.search(r"meeting-\d{3}", request.lower())) or any(
            word in request for word in self.search_words
        )
        needs_question = any(word in request for word in self.question_words)
        if needs_search and "search_response" not in kinds:
            return {"action": "search", "search_query": self._search_query(request)}
        if needs_question and "question_response" not in kinds:
            return {"action": "question", "search_query": None}
        return {"action": "direct", "search_query": None}

    @staticmethod
    def _search_query(request: str) -> SearchQuery:
        stop_words = {
            "회의록", "회의", "관련", "검색", "검색해줘", "선택", "찾아줘", "가져와줘", "가져오고",
            "내용", "질문", "설명", "알려줘", "해줘", "대한", "대해", "그리고", "있는",
        }
        meeting_ids = list(dict.fromkeys(re.findall(r"meeting-\d{3}", request.lower())))
        dates = re.findall(r"20\d{2}-\d{2}-\d{2}", request)
        keywords = [
            token.lower()
            for token in re.findall(r"[0-9A-Za-z가-힣]{2,}", request)
            if token.lower() not in stop_words
            and not token.lower().startswith("meeting-")
            and token not in dates
        ]
        return {
            "meeting_ids": meeting_ids,
            "keywords": list(dict.fromkeys(keywords)),
            "meeting_date": dates[0] if dates else None,
        }

    def interpret_candidate_count(
        self,
        request: str,
        candidate_count: int,
        scratchpad: list[dict[str, Any]],
    ) -> CandidateDecision:
        return {
            "route": "none" if candidate_count == 0 else "one" if candidate_count == 1 else "many"
        }

    def search_response(
        self,
        request: str,
        selected: list[dict[str, Any]],
        mode: str,
        tool_result: dict[str, Any],
        scratchpad: list[dict[str, Any]],
    ) -> AgentResponse:
        if tool_result.get("status") == "failed":
            error = tool_result.get("error", {})
            return {"response": f"회의록 검색을 완료하지 못했습니다: {error.get('message', '알 수 없는 오류')}", "follow_up": False}
        if not selected:
            return {"response": "접근 가능한 관련 회의록을 찾지 못했습니다. 기존 허용 ID는 유지합니다.", "follow_up": False}
        titles = ", ".join(item["title"] for item in selected)
        mode_text = {
            "set": "새 목록으로 설정",
            "add": "기존 목록에 추가",
            "replace": "새 목록으로 대체",
            "unchanged": "기존 허용 목록에서 다시 확인",
        }[mode]
        return {
            "response": f"회의록 {titles}을 확인하여 {mode_text}했습니다.",
            "follow_up": any(word in request for word in self.question_words),
        }

    def answer_question(
        self,
        system_instruction: str,
        request: str,
        scratchpad: list[dict[str, Any]],
        documents: list[dict[str, str]],
        tool_result: dict[str, Any],
    ) -> AgentResponse:
        status = tool_result.get("status")
        if status == "selection_required":
            response = "질문에 사용할 회의록이 선택되지 않았습니다. 먼저 관련 회의록을 검색해 주세요."
        elif status == "source_denied_or_failed":
            response = f"회의록 원문을 조회하지 못했습니다: {tool_result.get('reason', '알 수 없는 오류')}"
        elif not documents:
            response = "질문에 사용할 회의록이 선택되지 않았습니다. 먼저 관련 회의록을 검색해 주세요."
        else:
            sections = [
                f"- {doc['title']} 결정 사항 요약: "
                f"{doc['summary']}"
                for doc in documents
            ]
            response = "선택된 회의록을 기준으로 확인한 내용입니다.\n" + "\n".join(sections)
        return {"response": response, "follow_up": False}

    def direct_response(
        self, system_instruction: str, request: str, scratchpad: list[dict[str, Any]]
    ) -> AgentResponse:
        return {"response": "이 요청은 회의록 Tool 없이 답변했습니다. 회의록 검색이나 내용 질문을 요청할 수 있습니다.", "follow_up": False}


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
            "thinking": {"type": "disabled"},
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
            raise RuntimeError(f"DeepSeek API HTTP {exc.code}") from exc
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
            raise RuntimeError("DeepSeek API 응답 형식이 올바르지 않습니다.") from exc

    def _object(self, system: str, user: str, max_tokens: int = 800) -> dict[str, Any]:
        raw = self._complete(system, user, json_response=True, max_tokens=max_tokens)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("DeepSeek 구조화 응답을 해석할 수 없습니다.") from exc
        if not isinstance(value, dict):
            raise RuntimeError("DeepSeek 구조화 응답이 JSON 객체가 아닙니다.")
        return value

    @staticmethod
    def _agent_response(value: dict[str, Any]) -> AgentResponse:
        response = value.get("response")
        follow_up = value.get("follow_up")
        if not isinstance(response, str) or not response.strip() or not isinstance(follow_up, bool):
            raise RuntimeError("DeepSeek 응답에는 response 문자열과 follow_up boolean이 필요합니다.")
        return {"response": response.strip(), "follow_up": follow_up}

    def decide_next_action(
        self,
        request: str,
        request_events: list[dict[str, Any]],
    ) -> ActionDecision:
        value = self._object(
            (
                "당신은 회의록 Agent의 목표 판단기다. 반드시 JSON 객체만 반환한다. "
                "action은 search, question, direct 중 하나다. search이면 search_query도 생성한다. "
                "search_query는 meeting_ids 문자열 배열, keywords 문자열 배열, meeting_date 문자열 또는 null이다. "
                "판정 우선순위: (1) 요청이 검색·가져오기를 포함하고 search_response가 없으면 search, "
                "(2) 요청이 내용·결정·요약·설명을 포함하고 question_response가 없으면 question, "
                "(3) Tool이 필요 없으면 direct다. "
                "검색과 질문이 결합된 요청은 search 뒤에 반드시 question을 수행해야 한다."
            ),
            self._json_text(
                {
                    "request": request,
                    "current_goal_events": request_events,
                    "required_output": {
                        "action": "search|question|direct",
                        "search_query": {
                            "meeting_ids": ["meeting-001"],
                            "keywords": ["키워드"],
                            "meeting_date": "YYYY-MM-DD|null",
                        },
                    },
                }
            ),
            max_tokens=1600,
        )
        action = value.get("action")
        if action not in {"search", "question", "direct"}:
            raise RuntimeError("DeepSeek가 허용되지 않은 action 값을 반환했습니다.")
        query = value.get("search_query")
        if action == "search":
            if not isinstance(query, dict):
                raise RuntimeError("DeepSeek search 판단에는 search_query 객체가 필요합니다.")
            meeting_ids = query.get("meeting_ids")
            keywords = query.get("keywords")
            meeting_date = query.get("meeting_date")
            if not isinstance(meeting_ids, list) or not all(isinstance(item, str) for item in meeting_ids):
                raise RuntimeError("search_query.meeting_ids는 문자열 배열이어야 합니다.")
            if not isinstance(keywords, list) or not all(isinstance(item, str) for item in keywords):
                raise RuntimeError("search_query.keywords는 문자열 배열이어야 합니다.")
            if meeting_date is not None and not isinstance(meeting_date, str):
                raise RuntimeError("search_query.meeting_date는 문자열 또는 null이어야 합니다.")
            search_query: SearchQuery | None = {
                "meeting_ids": meeting_ids,
                "keywords": keywords,
                "meeting_date": meeting_date,
            }
        else:
            search_query = None
        return {"action": action, "search_query": search_query}

    def interpret_candidate_count(
        self,
        request: str,
        candidate_count: int,
        scratchpad: list[dict[str, Any]],
    ) -> CandidateDecision:
        value = self._object(
            "Tool 1 후보 결과를 해석해 route를 none, one, many 중 하나로 JSON 반환하라.",
            self._json_text(
                {"request": request, "candidate_count": candidate_count, "scratchpad": scratchpad}
            ),
            max_tokens=512,
        )
        route = value.get("route")
        if route not in {"none", "one", "many"}:
            raise RuntimeError("DeepSeek가 허용되지 않은 route 값을 반환했습니다.")
        return {"route": route}

    def search_response(
        self,
        request: str,
        selected: list[dict[str, Any]],
        mode: str,
        tool_result: dict[str, Any],
        scratchpad: list[dict[str, Any]],
    ) -> AgentResponse:
        value = self._object(
            (
                "검증된 검색 결과만 근거로 한국어 안내 response와 남은 목표 여부 follow_up boolean을 JSON 반환하라. "
                "검색과 질문이 결합된 요청에서 검색이 성공했고 설명·요약·질문·결정 확인 같은 목표가 남아 있으면 "
                "반드시 follow_up=true다. 검색만 요청했거나 검색 실패·후보 없음이면 follow_up=false다. "
                "검색 안내를 생성했다는 이유만으로 전체 사용자 목표가 끝났다고 판단하지 마라."
            ),
            self._json_text(
                {"request": request, "selected": selected, "mode": mode, "tool_result": tool_result, "scratchpad": scratchpad}
            ),
            max_tokens=4096,
        )
        return self._agent_response(value)

    def answer_question(
        self,
        system_instruction: str,
        request: str,
        scratchpad: list[dict[str, Any]],
        documents: list[dict[str, str]],
        tool_result: dict[str, Any],
    ) -> AgentResponse:
        value = self._object(
            system_instruction
            + " 제공된 회의록 원문 밖의 사실은 만들지 마라. response와 follow_up boolean을 JSON 반환하라.",
            self._json_text(
                {
                    "request": request,
                    "scratchpad": scratchpad,
                    "documents": documents,
                    "tool_result": tool_result,
                }
            ),
            max_tokens=4096,
        )
        return self._agent_response(value)

    def direct_response(
        self, system_instruction: str, request: str, scratchpad: list[dict[str, Any]]
    ) -> AgentResponse:
        value = self._object(
            system_instruction + " Tool 없이 처리 가능한 요청의 response와 follow_up=false를 JSON 반환하라.",
            self._json_text({"request": request, "scratchpad": scratchpad}),
            max_tokens=800,
        )
        return self._agent_response(value)
