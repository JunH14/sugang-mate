"""Public demo behavior, including error handling and complete source display."""
import importlib
from dataclasses import replace
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class DemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(os.environ, {"SUGANG_OFFLINE": "1", "GRADIO_ANALYTICS_ENABLED": "False"})
        cls.environment.start()
        cls.core = importlib.import_module("app")
        cls.ui = importlib.import_module("gradio_app")

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()

    def setUp(self):
        rag = self.core.SyllabusRag(ROOT / "data/sample/syllabus_texts.jsonl")
        self.rag_patch = patch.object(self.core, "RAG", rag)
        self.rag_patch.start()
        self.addCleanup(self.rag_patch.stop)

    def test_demo_answers_match_authored_sample(self):
        for question, expected in [
            ("전공필수 과목을 알려줘", {"DEMO101", "DEMO201"}),
            ("PBL 과목을 알려줘", {"DEMO301"}),
            ("월요일 수업을 알려줘", {"DEMO101", "DEMO401"}),
        ]:
            with self.subTest(question=question):
                result = self.core.RAG.answer(question)
                self.assertEqual({s["course_code"] for s in result["sources"]}, expected)

    def test_assessment_comparison_keeps_each_courses_facts(self):
        result = self.core.RAG.answer("DEMO201과 DEMO202의 평가방식을 비교해줘")
        self.assertIn("중간고사 40%", result["answer"])
        self.assertIn("개인 프로젝트 30%", result["answer"])
        self.assertEqual({s["course_code"] for s in result["sources"]}, {"DEMO201", "DEMO202"})

    def test_source_panel_does_not_hide_sixth_course(self):
        result = self.core.RAG.answer("현재 수집된 전체 과목 목록을 모두 알려줘")
        sources = self.ui.format_sources(result["sources"])
        for code in ("DEMO101", "DEMO201", "DEMO202", "DEMO301", "DEMO302", "DEMO401"):
            self.assertIn(code, sources)

    def test_input_limit_applies_to_direct_api_calls(self):
        with patch.object(self.core.RAG, "answer") as answer:
            result = self.ui.chat("a" * 501, [])
        answer.assert_not_called()
        self.assertIn("500", result[0])

    def test_unavailable_answer_has_no_stale_sources_or_internal_error(self):
        with patch.object(self.core.RAG, "answer", side_effect=RuntimeError("internal-secret-fixture")):
            with patch("sys.stderr"):
                result = self.ui.chat("질문", [])
        self.assertIn("처리하지 못했습니다", result[0])
        self.assertNotIn("internal-secret-fixture", " ".join(result))
        self.assertNotIn("DEMO", result[1])

    def test_unknown_topic_does_not_invent_a_course(self):
        answer = self.core.RAG.answer("천체물리학 실험 수업이 있어?")
        self.assertEqual(answer["sources"], [])
        self.assertIn("찾지 못", answer["answer"])

    def test_followup_completion_filter_and_count_stay_within_previous_courses(self):
        first = self.core.RAG.answer("DEMO201과 DEMO202의 평가방식을 비교해줘")
        history = [{"role": "assistant", "content": first["answer"]}]
        for question in ("그중 전공필수는 뭐야?", "그중 전공필수는 몇 개야?"):
            with self.subTest(question=question):
                result = self.core.RAG.answer(question, history)
                self.assertEqual({s["course_code"] for s in result["sources"]}, {"DEMO201"})
                self.assertNotIn("DEMO101", result["answer"])
                self.assertIn("1개", result["answer"])

    def test_followup_filter_can_return_empty_subset(self):
        history = [{"role": "assistant", "content": "DEMO202 머신러닝과 DEMO301 데이터시각화PBL"}]
        result = self.core.RAG.answer("그중 전공필수는 몇 개야?", history)
        self.assertEqual(result["sources"], [])
        self.assertIn("0개", result["answer"])

    def test_individual_projects_are_evidence_without_implying_teamwork(self):
        course = next(item for item in self.core.RAG.syllabi if item.course_code == "DEMO202")
        for phrase in ("개인 프로젝트 30%", "개별 프로젝트로 진행", "Individual project 30%", "Personal project 30%"):
            with self.subTest(phrase=phrase):
                fixture = replace(course, text="평가방법\n" + phrase)
                self.assertIn(phrase, self.core.feature_evidence(fixture, "프로젝트"))
                self.assertEqual(self.core.teamwork_evidence(fixture), "")

    def test_followup_project_filter_finds_individual_project_evidence(self):
        first = self.core.RAG.answer("DEMO201과 DEMO202의 평가방식을 비교해줘")
        history = [{"role": "assistant", "content": first["answer"]}]
        result = self.core.RAG.answer("그중 프로젝트가 있는 과목은?", history)
        self.assertEqual({s["course_code"] for s in result["sources"]}, {"DEMO202"})
        self.assertIn("개인 프로젝트", result["answer"])

    def test_followup_count_applies_conditions_before_counting(self):
        first = self.core.RAG.answer("DEMO201과 DEMO202의 평가방식을 비교해줘")
        history = [{"role": "assistant", "content": first["answer"]}]
        for question, expected in [
            ("그중 프로젝트가 있는 과목은 몇 개야?", {"DEMO202"}),
            ("그중 PBL 과목은 몇 개야?", set()),
            ("그중 전공필수이면서 프로젝트가 있는 과목은 몇 개야?", set()),
            ("그중 3학점 과목은 몇 개야?", {"DEMO201", "DEMO202"}),
            ("그중 월요일 수업은 몇 개야?", set()),
        ]:
            with self.subTest(question=question):
                result = self.core.RAG.answer(question, history)
                self.assertEqual({s["course_code"] for s in result["sources"]}, expected)
                self.assertIn(f"{len(expected)}개", result["answer"])
                self.assertEqual(set(result["context_resolved"]["course_codes"]), {"DEMO201", "DEMO202"})

    def test_unfiltered_followup_count_keeps_all_previous_courses(self):
        first = self.core.RAG.answer("DEMO201과 DEMO202의 평가방식을 비교해줘")
        history = [{"role": "assistant", "content": first["answer"]}]
        result = self.core.RAG.answer("그중 몇 개야?", history)
        self.assertEqual({s["course_code"] for s in result["sources"]}, {"DEMO201", "DEMO202"})
        self.assertIn("2개", result["answer"])


if __name__ == "__main__":
    unittest.main()
