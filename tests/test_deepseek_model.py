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
        mocked_urlopen.return_value = _Response('{"action":"search"}')
        model = DeepSeekMeetingModel("test-key")

        action = model.decide_next_action("회의록 찾아줘", [], [])

        self.assertEqual(action, "search")
        request = mocked_urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "deepseek-v4-pro")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertNotIn("test-key", request.data.decode("utf-8"))

    @patch("meeting_agent.model.urlopen")
    def test_rejects_action_outside_graph_contract(self, mocked_urlopen):
        mocked_urlopen.return_value = _Response('{"action":"delete_everything"}')
        model = DeepSeekMeetingModel("test-key")

        with self.assertRaisesRegex(RuntimeError, "허용되지 않은 action"):
            model.decide_next_action("요청", [], [])


if __name__ == "__main__":
    unittest.main()
