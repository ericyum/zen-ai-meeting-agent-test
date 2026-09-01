from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from meeting_agent.model import DeepSeekMeetingModel


class _Response:
    def __init__(self, content: str):
        self.payload = json.dumps(
            {"choices": [{"message": {"content": content}}]}
        ).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def read(self) -> bytes:
        return self.payload


class DeepSeekMeetingModelTest(unittest.TestCase):
    @patch("meeting_agent.model.urlopen")
    def test_decision_uses_structured_response(self, mocked_urlopen):
        mocked_urlopen.return_value = _Response(
            '{"action":"search","search_query":{"meeting_ids":["meeting-001"],'
            '"keywords":[],"meeting_date":null}}'
        )
        model = DeepSeekMeetingModel("test-key")

        decision = model.decide_next_action("회의록 찾아줘", [])

        self.assertEqual(decision["action"], "search")
        self.assertEqual(decision["search_query"]["meeting_ids"], ["meeting-001"])
        request = mocked_urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "deepseek-v4-pro")
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertNotIn(
            "authorized_meeting_ids",
            json.dumps(body["messages"], ensure_ascii=False),
        )
        self.assertNotIn("test-key", request.data.decode("utf-8"))

    @patch("meeting_agent.model.urlopen")
    def test_rejects_action_outside_graph_contract(self, mocked_urlopen):
        secret = "SECRET_MODEL_ACTION"
        mocked_urlopen.return_value = _Response(json.dumps({"action": secret}))
        model = DeepSeekMeetingModel("test-key")

        with self.assertRaises(RuntimeError) as raised:
            model.decide_next_action("요청", [])

        self.assertNotIn(secret, str(raised.exception))

    @patch("meeting_agent.model.urlopen")
    def test_rejects_route_without_exposing_model_output(self, mocked_urlopen):
        secret = "SECRET_MODEL_ROUTE"
        mocked_urlopen.return_value = _Response(json.dumps({"route": secret}))
        model = DeepSeekMeetingModel("test-key")

        with self.assertRaises(RuntimeError) as raised:
            model.interpret_candidate_count("회의록 검색", 2, [])

        self.assertNotIn(secret, str(raised.exception))

    @patch("meeting_agent.model.urlopen")
    def test_invalid_structured_response_does_not_expose_model_output(self, mocked_urlopen):
        raw = "회의록 원문 전체가 포함된 잘못된 JSON"
        mocked_urlopen.return_value = _Response(raw)
        model = DeepSeekMeetingModel("test-key")

        with self.assertRaises(RuntimeError) as raised:
            model.decide_next_action("요청", [])

        self.assertNotIn(raw, str(raised.exception))

    @patch("meeting_agent.model.urlopen")
    def test_search_response_returns_text_and_follow_up(self, mocked_urlopen):
        mocked_urlopen.return_value = _Response(
            '{"response":"검색했습니다.","follow_up":true}'
        )
        model = DeepSeekMeetingModel("test-key")

        result = model.search_response(
            "검색하고 설명해줘",
            [{"id": "meeting-001", "title": "회의"}],
            "set",
            {"status": "ok"},
            [],
        )

        self.assertEqual(result, {"response": "검색했습니다.", "follow_up": True})
        request = mocked_urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertIn("검색과 질문이 결합", body["messages"][0]["content"])
        self.assertIn("follow_up=true", body["messages"][0]["content"])

    @patch("meeting_agent.model.urlopen")
    def test_candidate_route_is_an_llm_structured_decision(self, mocked_urlopen):
        mocked_urlopen.return_value = _Response('{"route":"many"}')
        model = DeepSeekMeetingModel("test-key")

        result = model.interpret_candidate_count(
            "회의록 검색",
            2,
            [],
        )

        self.assertEqual(result, {"route": "many"})
        request = mocked_urlopen.call_args.args[0]
        messages = json.loads(request.data.decode("utf-8"))["messages"]
        self.assertIn('"candidate_count": 2', messages[1]["content"])
        self.assertNotIn("meeting-001", messages[1]["content"])


if __name__ == "__main__":
    unittest.main()
