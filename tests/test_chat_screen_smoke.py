"""Offline regression tests for the independent JSON chat-screen checker."""
from __future__ import annotations

import copy
import io
import json
import unittest
from unittest.mock import patch

from scripts.check_chat_screen import ScreenClient, SmokeFailure, verify_screen_contract, verify_screen_result


def view():
    source = {"id": "source-1", "course_code": "BDSC201", "class_no": "00", "course_name": "수리통계학", "professor": "fixture", "completion_type": "전공필수", "credit": "3", "class_hours": "3", "schedule_summary": "fixture schedule", "snippet": "fixture excerpt", "syllabus_url": "https://example.com"}
    card = {key: value for key, value in source.items() if key not in {"id", "snippet"}}
    return {"answer": "BDSC201 fixture answer", "mode": "structured_schedule", "sources": [source], "course_card": {"source_id": "source-1", **card}, "error": False, "warning": "", "timings": {"cache_hit": False}}


class ChatScreenSmokeTests(unittest.TestCase):
    def test_raw_respond_wire_has_two_inputs_no_session_and_one_json_output(self):
        expected = view()
        sse = ("event: complete\ndata: " + json.dumps([expected]) + "\n\n").encode()
        client = ScreenClient("https://example.com")
        with patch.object(client, "get_json", return_value={"event_id": "fixture"}) as post:
            with patch.object(client, "_request", return_value=io.BytesIO(sse)) as get:
                self.assertEqual(client.respond("/gradio_api", "question", []), expected)
        post.assert_called_once_with("/gradio_api/call/respond", {"data": ["question", []]})
        get.assert_called_once_with("/gradio_api/call/respond/fixture", stream=True)

    def test_screen_contract_requires_real_shell_and_stateless_api(self):
        config = {"api_prefix": "/gradio_api", "components": [
            {"type": "html", "props": {"elem_id": "sugang-shell", "value": '<div class="sm-app"></div>', "js_on_load": "fixture"}},
            {"id": 1, "type": "api"}, {"id": 2, "type": "api"}, {"id": 3, "type": "api"},
        ], "dependencies": [{"api_name": "respond", "inputs": [1, 2], "outputs": [3]}]}
        info = {"named_endpoints": {"/respond": {"api_visibility": "public", "parameters": [{"parameter_name": "message"}, {"parameter_name": "history"}], "returns": [{}]}}}
        self.assertEqual(verify_screen_contract(config, info), "/gradio_api")
        for variant in ("missing_shell", "hidden_state"):
            changed = copy.deepcopy(config)
            if variant == "missing_shell":
                changed["components"][0]["props"]["value"] = "old screen"
            else:
                changed["components"][2]["type"] = "state"
            with self.subTest(variant=variant), self.assertRaises(SmokeFailure):
                verify_screen_contract(changed, info)

    def test_aggregate_proves_card_and_source_without_retaining_contents(self):
        report = verify_screen_result(view())
        self.assertTrue(report["course_card_matches_source"])
        self.assertEqual(report["expected_course"], "BDSC201")
        self.assertNotIn("fixture", json.dumps(report))

    def test_private_fields_wrong_card_or_fallback_fail_closed(self):
        for variant in ("private_source", "wrong_card", "fallback", "private_root"):
            changed = view()
            if variant == "private_source":
                changed["sources"][0]["text_path"] = "/private/fixture"
            elif variant == "wrong_card":
                changed["course_card"]["schedule_summary"] = "different"
            elif variant == "fallback":
                changed["mode"] = "extractive"
            else:
                changed["internal_context"] = "private"
            with self.subTest(variant=variant), self.assertRaises(SmokeFailure):
                verify_screen_result(changed)


if __name__ == "__main__":
    unittest.main()
