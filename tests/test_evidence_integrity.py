"""Synthetic correctness regressions, not a real-service quality benchmark."""
from __future__ import annotations

from dataclasses import replace
import importlib
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from retrieval_core import make_section_chunks, split_sections
from sugang_mate.context import generation_evidence_text
from sugang_mate.html_text import extract_static_html_text
from sugang_mate.source_text import sanitize_retrieval_text
from test_live_core import fake_google_sdk

ROOT = Path(__file__).resolve().parents[1]


class EvidenceIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(os.environ, {
            "SUGANG_OFFLINE": "1", "ANSWER_CACHE_MAX": "0",
            "SUGANG_DATA_PATH": str(ROOT / "data/sample/syllabus_texts.jsonl"),
        })
        cls.environment.start()
        cls.core = importlib.import_module("app")

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()

    def setUp(self):
        self.rag = self.core.SyllabusRag(ROOT / "data/sample/syllabus_texts.jsonl")
        self.network = patch("socket.socket.connect", side_effect=AssertionError("Network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_late_evidence_survives_index_prompt_and_source_card(self):
        text = "주차별 강의계획\n" + "일반 주제 내용을 설명한다.\n" * 180 + "최종 주제: 희소행렬 희귀근거 실습\n"
        course = replace(self.rag.syllabi[0], text=text)
        sections = make_section_chunks(text)
        selected = next(part for part in sections if "희귀근거" in part.text)
        self.assertIn(selected.text, selected.parent_text)
        self.assertLessEqual(len(selected.parent_text), 2200)
        chunk = replace(self.rag.chunks[0], syllabus=course, text=selected.text,
                        parent_text=selected.parent_text, section=selected.section)
        generate = Mock(return_value=SimpleNamespace(text="fixture"))
        client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        self.rag.gemini_client = client
        with fake_google_sdk(client):
            result = self.rag._answer_with_gemini("희귀근거는?", [self.core.Match(chunk=chunk, score=1)])
        self.assertIn("희귀근거", generate.call_args.kwargs["contents"])
        self.assertIn("희귀근거", result["sources"][0]["snippet"])

    def test_legacy_parent_cannot_replace_child_or_exceed_budget(self):
        chunk = SimpleNamespace(text="문서 끝의 선택 근거", parent_text="문서 앞 내용 " * 500)
        self.assertEqual(generation_evidence_text(chunk), chunk.text)
        with self.assertRaises(ValueError):
            generation_evidence_text(SimpleNamespace(text="x" * 2201, parent_text=""))

    def test_shared_parent_does_not_collapse_distinct_selected_children(self):
        original = self.rag.chunks[0]
        common_parent = "앞 근거\n뒤 근거"
        first = replace(original, text="앞 근거", parent_text=common_parent, section="평가방법")
        second = replace(original, text="뒤 근거", parent_text=common_parent, section="평가방법")
        matches = [self.core.Match(chunk=chunk, score=1) for chunk in (first, second, first)]
        selected = self.rag._generation_context_matches(matches)
        self.assertEqual([item.chunk.text for item in selected[:2]], ["앞 근거", "뒤 근거"])
        self.assertEqual(sum(item.chunk.text == "앞 근거" for item in selected), 1)

    def test_keyword_rewrite_stays_keyword_in_generated_mode(self):
        match = self.core.Match(chunk=self.rag.chunks[0], score=10)
        self.rag.gemini_client = SimpleNamespace(models=SimpleNamespace(
            generate_content=Mock(return_value=SimpleNamespace(text="fixture"))))
        with patch.object(self.rag, "_search_keyword", side_effect=[[], [match]]), \
             patch.object(self.rag, "_rewrite_query", return_value="DEMO101"):
            matches = self.rag.search("모호한 질문")
        self.assertEqual(self.rag.request_state.search_backend, "keyword_rewrite")
        with fake_google_sdk(self.rag.gemini_client):
            result = self.rag._answer_with_gemini("모호한 질문", matches)
        self.assertEqual(result["mode"], "keyword_gemini")
        self.assertEqual(result["rewritten_query"], "DEMO101")

    def test_known_section_aliases_and_weekly_content_are_distinct(self):
        sections = dict(split_sections("강의개요\n개요 문장\n수업목표\n목표 문장\n주차별 강의계획\n1주 교재 소개\n2주 중간고사 안내"))
        self.assertIn("개요 문장", sections["교과목개요"])
        self.assertIn("목표 문장", sections["학습목표"])
        self.assertIn("중간고사 안내", sections["주별학습내용"])
        self.assertNotIn("평가방법", sections)

    def test_html_placeholder_does_not_override_attachment_assessment(self):
        text = "[첨부 강의계획서: fixture.pdf]\n평가방법\n중간고사 30%, 기말고사 40%, 과제 30%\n[강의계획안 HTML]\n평가방법\n평가방법이 입력되지 않았습니다."
        course = replace(self.rag.syllabi[0], text=sanitize_retrieval_text(text))
        summary = self.core.contextual_assessment_summary(course)
        self.assertIn("30%", summary)
        self.assertIn("40%", summary)
        self.assertNotIn("입력되지", summary)
        blank = replace(course, text="평가방법\n평가방법이 입력되지 않았습니다.")
        self.assertEqual(self.core.contextual_assessment_summary(blank), "")

    def test_conflicting_assessment_copies_remain_attributed_and_uncertain(self):
        text = "[첨부 강의계획서: fixture.pdf]\n평가방법\n중간고사 30%\n[강의계획안 HTML]\n평가방법\n중간고사 50%"
        summary = self.core.contextual_assessment_summary(replace(self.rag.syllabi[0], text=text))
        for expected in ("30%", "50%", "fixture.pdf", "HTML", "원문 확인"):
            self.assertIn(expected, summary)

    def test_checked_controls_survive_and_unchecked_options_are_not_evidence(self):
        raw = '''<div>활동유형</div><label><input type="checkbox" checked>실습</label>
        <label><input type="checkbox">토론</label><input id="a" type="radio" checked>
        <label for="a">대면</label><input id="b" type="radio"><label for="b">비대면</label>
        <div>평가방법</div><p>과제 100%</p><script>발표</script>'''
        text = sanitize_retrieval_text(extract_static_html_text(raw))
        for selected in ("실습", "대면", "과제 100%"):
            self.assertIn(selected, text)
        for absent in ("토론", "비대면", "발표"):
            self.assertNotIn(absent, text)

    def test_unwrapped_choices_and_select_only_use_explicit_state(self):
        raw = '''<p><input type="checkbox" checked>실습<input type="checkbox">토론</p>
        <select><option>발표</option><option selected>대면</option></select>
        <select><option>미확인 기본값</option></select><p>일반 본문</p>'''
        text = extract_static_html_text(raw)
        self.assertIn("실습", text)
        self.assertIn("대면", text)
        self.assertIn("일반 본문", text)
        self.assertNotIn("토론", text)
        self.assertNotIn("발표", text)
        self.assertNotIn("미확인 기본값", text)

    def test_unbound_table_controls_do_not_promote_all_options_to_evidence(self):
        raw = '<table><tr><td><input type="checkbox" checked></td><td>실습</td><td><input type="checkbox"></td><td>토론</td></tr></table><p>과제 100%</p>'
        text = extract_static_html_text(raw)
        self.assertNotIn("실습", text)
        self.assertNotIn("토론", text)
        self.assertIn("양식 행 생략", text)
        self.assertIn("과제 100%", text)

    def test_inline_unchecked_label_is_not_promoted_and_nested_checked_is_once(self):
        raw = '<div><input type="checkbox"><span>토론</span><input type="checkbox" checked><span>실습</span></div><label><span><input type="checkbox" checked>대면</span></label>'
        text = extract_static_html_text(raw)
        self.assertNotIn("토론", text)
        self.assertIn("실습", text)
        self.assertIn("대면", text)
        self.assertEqual(text.count("선택 확인:"), 2)

    def test_rejected_vector_candidates_do_not_mislabel_keyword_fallback(self):
        match = self.core.Match(chunk=self.rag.chunks[0], score=10)
        self.rag.chroma_collection = object()
        self.rag.gemini_client = object()
        with patch.object(self.rag, "_search_chroma", return_value=[match]), \
             patch.object(self.rag, "_search_keyword", return_value=[match]), \
             patch.object(self.core, "apply_relevance_threshold", side_effect=[[], [match]]):
            self.assertEqual(self.rag.search("DEMO101"), [match])
        self.assertEqual(self.rag.request_state.search_backend, "keyword")


if __name__ == "__main__":
    unittest.main()
