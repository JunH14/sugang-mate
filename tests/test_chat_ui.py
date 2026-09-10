"""Shared safety boundaries and API compatibility for the custom chat screen.

All model responses are fixtures. These tests never contact Google or Render.
"""
from __future__ import annotations

import copy
import importlib
import inspect
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sugang_mate.public_limits import PublicLimits


FIXTURE = {
    "answer": "DEMO101 데이터분석입문은 표 형식 데이터를 다룹니다.",
    "mode": "keyword_gemini",
    "model": "fixture-model",
    "timings": {"total_seconds": 0.125, "cache_hit": False},
    "sources": [{
        "course_code": "DEMO101", "class_no": "00", "course_name": "데이터분석입문",
        "professor": "가상교수", "completion_type": "전공필수", "credit": "3",
        "schedule_summary": "월 10:00", "snippet": "표 형식 데이터의 결측치를 처리한다.",
        "syllabus_url": "https://example.com/syllabus",
        "text_path": "/private/hidden-source-fixture.jsonl",
        "text": "private-full-document-fixture",
        "internal_secret": "private-field-fixture",
    }],
}


def request(session="one"):
    return SimpleNamespace(client=SimpleNamespace(host="test-address"), session_hash=session)


class ChatUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {
            "SUGANG_OFFLINE": "1", "SUGANG_DATA_PATH": "data/sample/syllabus_texts.jsonl",
            "GRADIO_ANALYTICS_ENABLED": "False",
        }):
            cls.ui = importlib.import_module("gradio_app")

    def test_legacy_and_json_endpoints_share_one_quota_and_one_core_call_each(self):
        limits = PublicLimits(chat_limit=2)
        with patch.object(self.ui, "PUBLIC_DEMO", True), patch.object(self.ui, "PUBLIC_LIMITS", limits):
            with patch.object(self.ui.core.RAG, "answer", return_value=copy.deepcopy(FIXTURE)) as core_answer:
                legacy = self.ui.chat("첫 질문", [], request("first"))
                modern = self.ui.respond("두 번째 질문", [], request("second"))
                blocked = self.ui.respond("세 번째 질문", [], request("third"))
        self.assertEqual(legacy[0], FIXTURE["answer"])
        self.assertEqual(modern["answer"], FIXTURE["answer"])
        self.assertNotEqual(blocked["answer"], FIXTURE["answer"])
        self.assertEqual(core_answer.call_count, 2)
        self.assertEqual(limits.tracked_clients, 1)

    def test_both_endpoints_reject_more_than_500_characters_before_core(self):
        for name in ("chat", "respond"):
            with self.subTest(endpoint=name):
                with patch.object(self.ui, "PUBLIC_LIMITS", PublicLimits()), patch.object(self.ui.core.RAG, "answer") as core_answer:
                    response = getattr(self.ui, name)("가" * 501, [], request())
                answer = response[0] if name == "chat" else response["answer"]
                self.assertIn("500", answer)
                core_answer.assert_not_called()

    def test_exactly_500_characters_are_accepted_once(self):
        with patch.object(self.ui, "PUBLIC_LIMITS", PublicLimits(chat_limit=1)), patch.object(self.ui.core.RAG, "answer", return_value=copy.deepcopy(FIXTURE)) as core_answer:
            response = self.ui.respond("가" * 500, [], request())
        self.assertEqual(response["answer"], FIXTURE["answer"])
        core_answer.assert_called_once_with("가" * 500, [])

    def test_typed_json_api_still_rejects_non_string_message_values(self):
        for value in (None, 123, {"text": "question"}, ["question"]):
            with self.subTest(value=value), patch.object(self.ui.core.RAG, "answer") as core_answer:
                response = self.ui.respond(value, [], request())
                self.assertIsInstance(response["answer"], str)
                self.assertTrue(response["answer"])
                core_answer.assert_not_called()

    def test_both_endpoints_normalize_history_before_local_or_model_processing(self):
        history = [{"role": "assistant", "content": str(number) + "나" * 2000} for number in range(12)]
        original = copy.deepcopy(history)
        for name in ("chat", "respond"):
            with self.subTest(endpoint=name):
                with patch.object(self.ui, "PUBLIC_DEMO", True), patch.object(self.ui, "PUBLIC_LIMITS", PublicLimits()):
                    with patch.object(self.ui.core.RAG, "answer", return_value=copy.deepcopy(FIXTURE)) as core_answer:
                        getattr(self.ui, name)("  뒤의 질문  ", history, request())
                core_answer.assert_called_once()
                question, bounded = core_answer.call_args.args
                self.assertEqual(question, "뒤의 질문")
                self.assertEqual(len(bounded), 8)
                self.assertTrue(bounded[0]["content"].startswith("4"))
                self.assertTrue(all(len(item["content"]) <= 1200 for item in bounded))
        self.assertEqual(history, original)

    def test_json_endpoint_failure_has_no_previous_sources_or_internal_details(self):
        with patch.object(self.ui, "PUBLIC_LIMITS", PublicLimits()), patch.object(self.ui.core.RAG, "answer", side_effect=RuntimeError("private-error-fixture")) as core_answer:
            with patch("sys.stderr"):
                response = self.ui.respond("새 질문", [], request())
        core_answer.assert_called_once()
        self.assertTrue(response["answer"])
        self.assertEqual(response["sources"], [])
        self.assertNotIn("private-error-fixture", json.dumps(response, ensure_ascii=False))

    def test_json_response_uses_the_presentation_contract_and_drops_private_fields(self):
        original = copy.deepcopy(FIXTURE)
        with patch.object(self.ui, "PUBLIC_LIMITS", PublicLimits()), patch.object(self.ui.core.RAG, "answer", return_value=original) as core_answer:
            response = self.ui.respond("표 형식 데이터에 대해 알려줘", [], request())
        core_answer.assert_called_once()
        self.assertEqual(set(response), {
            "answer", "sources", "mode", "timings", "warning", "course_card",
            "mode_label", "timing_label", "error",
        })
        self.assertEqual(response["answer"], FIXTURE["answer"])
        self.assertEqual(response["sources"][0]["id"], "source-1")
        self.assertEqual(response["sources"][0]["course_code"], "DEMO101")
        self.assertEqual(response["sources"][0]["syllabus_url"], "https://example.com/syllabus")
        self.assertEqual(response["timings"]["total_seconds"], 0.125)
        self.assertFalse(response["timings"]["cache_hit"])
        self.assertFalse(response["error"])
        self.assertIn("Gemini", response["mode_label"])
        self.assertIsNone(response["course_card"])
        serialized = json.dumps(response, ensure_ascii=False)
        for private_value in ("private-", "text_path", "internal_secret"):
            self.assertNotIn(private_value, serialized)
        self.assertEqual(original, FIXTURE)

    def test_each_json_answer_keeps_its_own_sources_across_followup_calls(self):
        first_result = copy.deepcopy(FIXTURE)
        second_result = copy.deepcopy(FIXTURE)
        second_result["answer"] = "DEMO201 수리통계학의 근거입니다."
        second_result["sources"][0].update(course_code="DEMO201", course_name="수리통계학")
        with patch.object(self.ui, "PUBLIC_LIMITS", PublicLimits()), patch.object(self.ui.core.RAG, "answer", side_effect=[first_result, second_result]) as core_answer:
            first = self.ui.respond("첫 질문", [], request())
            second = self.ui.respond("다음 질문", [{"role": "assistant", "content": first["answer"]}], request())
        self.assertEqual(core_answer.call_count, 2)
        self.assertEqual([source["course_code"] for source in first["sources"]], ["DEMO101"])
        self.assertEqual([source["course_code"] for source in second["sources"]], ["DEMO201"])
        second["sources"][0]["snippet"] = "screen-local change"
        self.assertEqual(first["sources"][0]["snippet"], FIXTURE["sources"][0]["snippet"])
        self.assertEqual(second_result["sources"][0]["snippet"], FIXTURE["sources"][0]["snippet"])

    def test_legacy_raw_state_contract_and_new_json_contract_are_preserved(self):
        with patch.object(self.ui, "PUBLIC_DEMO", True):
            demo = self.ui.build_demo()
        by_name = {dependency.api_name: dependency for dependency in demo.fns.values()}
        legacy = by_name["chat"]
        modern = by_name["respond"]
        self.assertEqual([component.get_block_name() for component in legacy.inputs], ["textbox", "state"])
        self.assertEqual([component.get_block_name() for component in legacy.outputs], ["json", "state", "markdown", "markdown"])
        self.assertEqual(len(modern.inputs), 2)
        self.assertEqual(len(modern.outputs), 1)
        self.assertTrue(all(not component.stateful for component in modern.inputs + modern.outputs))
        self.assertNotEqual(modern.api_visibility, "private")
        self.assertTrue(modern.queue)
        self.assertEqual(legacy.concurrency_id, modern.concurrency_id)
        self.assertEqual(legacy.concurrency_limit, 2)
        self.assertEqual(modern.concurrency_limit, 2)

        aliases = [dependency for dependency in demo.fns.values() if dependency.fn and inspect.unwrap(dependency.fn) in (self.ui.chat, self.ui.respond)]
        self.assertGreaterEqual(len(aliases), 2)
        self.assertEqual({dependency.concurrency_id for dependency in aliases}, {modern.concurrency_id})


if __name__ == "__main__":
    unittest.main()
