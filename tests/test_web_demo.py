from __future__ import annotations

import json
import unittest

from meeting_agent import web_demo
from meeting_agent.web_demo import encode_sse


class WebDemoSseTest(unittest.TestCase):
    def test_encode_sse_produces_one_json_data_frame(self):
        frame = encode_sse({"type": "node", "node": "llm_goal_condition"})

        self.assertTrue(frame.startswith(b"data: "))
        self.assertTrue(frame.endswith(b"\n\n"))
        payload = json.loads(frame.removeprefix(b"data: ").strip())
        self.assertEqual(payload, {"type": "node", "node": "llm_goal_condition"})

    def test_public_error_event_does_not_expose_exception_text(self):
        secret = "SECRET_HTTP_ERROR_DETAIL"
        public_error_event = getattr(web_demo, "public_error_event", None)

        self.assertTrue(callable(public_error_event))
        self.assertNotIn(secret, str(public_error_event(RuntimeError(secret))))


if __name__ == "__main__":
    unittest.main()
