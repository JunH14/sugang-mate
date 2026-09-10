"""Offline tests for the independent hosted-demo checker; never contact Render."""
from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from scripts.check_hosted_demo import (
    AnonymousClient, SmokeFailure, read_complete_event, run_checks,
    validate_origin, verify_chat_contract, verify_chat_result, verify_hosted_status,
)


HASH = "a" * 64
EXPECTED = {"hosted_dataset_sha256": HASH, "document_count": 25}


def config(kind="collected", count="25", digest=HASH):
    return {"api_prefix": "/gradio_api", "components": [{"type": "html", "props": {"value": (
        '<div class="status-strip" '
        f'data-dataset-kind="{kind}" data-course-count="{count}" data-dataset-sha256="{digest}"></div>'
    )}}, {"id": 9, "type": "textbox"}, {"id": 22, "type": "state"},
        {"id": 21, "type": "json"}, {"id": 10, "type": "markdown"},
        {"id": 11, "type": "markdown"}], "dependencies": [{
            "api_name": "chat", "inputs": [9, 22], "outputs": [21, 22, 10, 11],
        }]}


INFO = {"named_endpoints": {"/chat": {
    "api_visibility": "public", "parameters": [{"parameter_name": "message"}],
    "returns": [{}, {}, {}],
}}}


def result(code="BDSC205", mode="Gemini", cache=False):
    return [f"{code} confirmed", f"source: {code}", f"처리 방식: **{mode}**\n캐시 사용: **{'예' if cache else '아니오'}**"]


class HostedSmokeTests(unittest.TestCase):
    def test_collected_status_requires_exact_count_and_fingerprint(self):
        self.assertEqual(verify_hosted_status(config(), EXPECTED)["course_count"], 25)
        for bad in (config(kind="sample", count="6"), config(count="24"), config(digest="b" * 64)):
            with self.subTest(bad=bad), self.assertRaises(SmokeFailure):
                verify_hosted_status(bad, EXPECTED)

    def test_status_rejection_happens_before_any_chat_request(self):
        class SampleClient:
            def get_json(self, path):
                self_path = path
                if self_path != "/config":
                    raise AssertionError("Do not proceed beyond a mismatching dataset")
                return config(kind="sample", count="6")

            def chat(self, *args):
                raise AssertionError("Sample mode must not spend the real API budget")

        with self.assertRaises(SmokeFailure):
            run_checks(SampleClient(), EXPECTED, {"checks": []})

    def test_authentication_or_changed_api_shape_fails(self):
        guarded = config()
        guarded["auth_required"] = True
        with self.assertRaisesRegex(SmokeFailure, "authentication_required"):
            verify_hosted_status(guarded, EXPECTED)
        self.assertEqual(verify_chat_contract(config(), INFO), "/gradio_api")
        with self.assertRaises(SmokeFailure):
            verify_chat_contract(config(), {"named_endpoints": {"/chat": {"api_visibility": "private"}}})
        changed = config()
        changed["dependencies"][0]["inputs"] = [9]
        with self.assertRaisesRegex(SmokeFailure, "raw_chat_contract_changed"):
            verify_chat_contract(changed, INFO)

    def test_raw_gradio_call_includes_state_but_lets_server_choose_event_session(self):
        application_output = result()
        raw_output = [application_output[0], None, *application_output[1:]]
        sse = ("event: complete\ndata: " + json.dumps(raw_output) + "\n\n").encode()
        client = AnonymousClient("https://example.com")
        with patch.object(client, "get_json", return_value={"event_id": "server-event-id"}) as post:
            with patch.object(client, "_request", return_value=io.BytesIO(sse)) as get:
                self.assertEqual(client.chat("/gradio_api", "question"), application_output)
        post.assert_called_once_with("/gradio_api/call/chat", {"data": ["question", None]})
        get.assert_called_once_with("/gradio_api/call/chat/server-event-id", stream=True)

    def test_raw_state_output_must_be_hidden_and_not_return_conversation_history(self):
        raw = ["answer", "unexpected history", "sources", "diagnostics"]
        sse = ("event: complete\ndata: " + json.dumps(raw) + "\n\n").encode()
        client = AnonymousClient("https://example.com")
        with patch.object(client, "get_json", return_value={"event_id": "test"}):
            with patch.object(client, "_request", return_value=io.BytesIO(sse)):
                with self.assertRaisesRegex(SmokeFailure, "invalid_raw_chat_result_shape"):
                    client.chat("/gradio_api", "question")

    def test_sse_discards_heartbeat_and_reads_utf8_complete_event(self):
        payload = result()
        stream = io.BytesIO(("event: heartbeat\ndata: null\n\n" + "event: complete\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode())
        self.assertEqual(read_complete_event(stream, deadline=1, clock=lambda: 0), payload)

    def test_sse_errors_do_not_expose_remote_content(self):
        for raw in (
            b'event: error\ndata: "private-secret-fixture"\n\n',
            b'event: complete\ndata: invalid-private-fixture\n\n', b'event: heartbeat\n\n',
        ):
            with self.subTest(raw=raw), self.assertRaises(SmokeFailure) as raised:
                read_complete_event(io.BytesIO(raw), deadline=1, clock=lambda: 0)
            self.assertNotIn("private", str(raised.exception))
        with self.assertRaisesRegex(SmokeFailure, "timeout"):
            read_complete_event(io.BytesIO(), deadline=0, clock=lambda: 0)

    def test_gemini_evidence_and_cache_provenance_are_distinct(self):
        self.assertTrue(verify_chat_result(result(), "BDSC205", "gemini")["fresh_gemini_generation"])
        cached = verify_chat_result(result(cache=True), "BDSC205", "gemini")
        self.assertTrue(cached["cache_hit"])
        self.assertFalse(cached["fresh_gemini_generation"])
        for payload in (
            result(code="DEMO101"), result(mode="검색 근거 직접 추출"),
            ["BDSC205 answer", "BDSC205 evidence", "Gemini\n캐시 사용: **아니오**\n> 안내: API 제한"],
            ["BDSC205 answer", "missing source", "Gemini\n캐시 사용: **아니오**"],
        ):
            with self.subTest(payload=payload), self.assertRaises(SmokeFailure):
                verify_chat_result(payload, "BDSC205", "gemini")

    def test_report_has_only_aggregates_and_three_independent_checks(self):
        class Client:
            def get_json(self, path):
                return config() if path == "/config" else INFO

            def get_http_status(self, path):
                return 403

            def chat(self, prefix, question):
                if "SAS" in question:
                    return result()
                if "수업시간" in question:
                    return result(code="BDSC201", mode="시간표 데이터 직접 조회")
                return result(code="", mode="강의계획서 범위 밖 질문")

        report = {"checks": []}
        run_checks(Client(), EXPECTED, report)
        self.assertEqual(len(report["checks"]), 3)
        self.assertNotIn("confirmed", json.dumps(report))
        self.assertNotIn("source:", json.dumps(report))
        self.assertEqual(report["checks"][0]["expected_course_evidence"], "BDSC205")
        self.assertTrue(report["protected_dataset_file"]["download_blocked"])

    def test_downloadable_private_dataset_stops_before_chat(self):
        class Client:
            def get_json(self, path):
                return config() if path == "/config" else INFO

            def get_http_status(self, path):
                return 200

            def chat(self, *args):
                raise AssertionError("Do not proceed when the protected file is downloadable")

        with self.assertRaisesRegex(SmokeFailure, "protected_dataset_download_not_blocked"):
            run_checks(Client(), EXPECTED, {"checks": []})

    def test_protected_file_status_check_never_reads_a_success_body(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self, *args):
                raise AssertionError("Never consume a protected dataset body")

        with patch("urllib.request.build_opener") as builder:
            builder.return_value.open.return_value = Response()
            client = AnonymousClient("https://example.com")
            self.assertEqual(client.get_http_status("/gradio_api/file=/etc/secrets/sugang-syllabi.jsonl"), 200)

    def test_fresh_opener_sends_no_cookie_or_authorization(self):
        response = io.BytesIO(b'{"ok": true}')
        with patch("urllib.request.build_opener") as builder:
            builder.return_value.open.return_value = response
            client = AnonymousClient("https://example.com")
            self.assertEqual(client.get_json("/config"), {"ok": True})
        request = builder.return_value.open.call_args.args[0]
        self.assertNotIn("Cookie", request.headers)
        self.assertNotIn("Authorization", request.headers)

    def test_origin_cannot_contain_login_credentials_or_non_https_redirect_targets(self):
        self.assertEqual(validate_origin("https://example.com/"), "https://example.com")
        for value in ("http://example.com", "https://user:secret@example.com", "https://example.com/?token=value", "https://example.com/login"):
            with self.subTest(value=value), self.assertRaises(SmokeFailure):
                validate_origin(value)


if __name__ == "__main__":
    unittest.main()
