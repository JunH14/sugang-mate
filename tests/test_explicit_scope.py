"""Synthetic regression cases for explicit course scope after another topic."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class ExplicitScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.data_path = Path(cls.directory.name) / "synthetic-courses.jsonl"
        courses = [
            ("DEMO101", "가상자료개론", "전공필수"),
            ("DEMO201", "가상확률개론", "전공필수"),
            ("DEMO202", "가상추론개론", "전공선택"),
        ]
        cls.data_path.write_text("".join(json.dumps({
            "course_code": code, "class_no": "00", "course_name": name,
            "completion_type": kind, "professor": f"가상교수{code[-3:]}",
            "credit": "3", "schedule_summary": "월 10:00",
            "text": f"교과목 개요\n{name}의 원리를 학습한다.\n"
                    "평가방법\n중간고사 30%, 기말고사 40%, 과제 20%, 출석 10%.",
        }, ensure_ascii=False) + "\n" for code, name, kind in courses), encoding="utf-8")
        cls.environment = patch.dict(os.environ, {
            "SUGANG_OFFLINE": "1", "SUGANG_DATA_PATH": str(cls.data_path),
            "ANSWER_CACHE_MAX": "0",
        })
        cls.environment.start()
        cls.app = importlib.import_module("app")

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        cls.directory.cleanup()

    def setUp(self):
        self.rag = self.app.SyllabusRag(self.data_path)
        self.history = [
            {"role": "user", "content": "DEMO101의 담당 교수는?"},
            {"role": "assistant", "content": self.rag.answer("DEMO101의 담당 교수는?")["answer"]},
        ]

    @staticmethod
    def codes(result):
        return {source["course_code"] for source in result["sources"]}

    def test_explicit_comparison_changes_scope_and_followup_uses_only_that_scope(self):
        question = "DEMO201과 DEMO202의 평가방식을 비교해줘"
        comparison = self.rag.answer(question, self.history)
        self.assertEqual(self.codes(comparison), {"DEMO201", "DEMO202"})
        self.assertNotIn("DEMO101", comparison["answer"])
        self.assertNotIn("context_resolved", comparison)
        history = self.history + [
            {"role": "user", "content": question},
            {"role": "assistant", "content": comparison["answer"]},
        ]
        followup = self.rag.answer("그중 전공필수 과목은 뭐야?", history)
        self.assertEqual(self.codes(followup), {"DEMO201"})
        self.assertNotIn("DEMO101", followup["answer"])

    def test_two_explicit_course_names_also_replace_prior_scope(self):
        result = self.rag.answer("가상확률개론과 가상추론개론의 평가방식을 비교해줘", self.history)
        self.assertEqual(self.codes(result), {"DEMO201", "DEMO202"})

    def test_single_explicit_course_followup_chip_does_not_append_previous_course(self):
        for question in ("DEMO201의 평가방식도 알려줘", "가상확률개론의 평가방식도 알려줘"):
            with self.subTest(question=question):
                result = self.rag.answer(question, self.history)
                self.assertEqual(self.codes(result), {"DEMO201"})
                self.assertNotIn("context_resolved", result)

    def test_single_explicit_course_does_not_use_model_rewrite(self):
        question = "DEMO201의 평가방식을 알려줘. " + "평가 항목과 반영 비율을 확인하고 싶습니다. " * 3
        with patch.object(self.rag, "gemini_client", object()), patch.object(
            self.rag, "_rewrite_followup_question", side_effect=AssertionError("Unexpected rewrite")
        ) as rewrite:
            result = self.rag.answer(question, self.history)
        rewrite.assert_not_called()
        self.assertEqual(self.codes(result), {"DEMO201"})

    def test_explicit_comparison_does_not_use_model_rewrite(self):
        # A long independent question would otherwise enter the model rewrite
        # branch. The sentinel client and fail-fast mock prohibit any SDK call.
        question = "DEMO201과 DEMO202의 평가방식을 비교해줘. " + "평가 항목과 반영 비율을 확인하고 싶습니다. " * 3
        with patch.object(self.rag, "gemini_client", object()), patch.object(
            self.rag, "_rewrite_followup_question", side_effect=AssertionError("Unexpected rewrite")
        ) as rewrite:
            result = self.rag.answer(question, self.history)
        rewrite.assert_not_called()
        self.assertEqual(self.codes(result), {"DEMO201", "DEMO202"})

    def test_one_new_course_can_still_be_compared_with_the_previous_course(self):
        result = self.rag.answer("그 과목과 DEMO201의 평가방식을 비교해줘", self.history)
        self.assertEqual(self.codes(result), {"DEMO101", "DEMO201"})

    def test_single_course_pronoun_followup_keeps_previous_course(self):
        result = self.rag.answer("그 과목의 담당 교수는 누구야?", self.history)
        self.assertEqual(self.codes(result), {"DEMO101"})


if __name__ == "__main__":
    unittest.main()
