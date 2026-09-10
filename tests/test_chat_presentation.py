"""Synthetic presentation fixtures; no real corpus, API key or network needed."""
from __future__ import annotations

import copy
import json
import unittest

from sugang_mate.chat_presentation import present_answer, present_sources, safe_http_url


def source(code="TEST201", section="01", **updates):
    row = {
        "course_code": code, "class_no": section, "course_name": "가상 통계학",
        "professor": "가상교수", "completion_type": "전공필수", "credit": "3",
        "class_hours": "3(3)", "schedule_summary": "화요일 3-4교시 · 예시관 204호",
        "snippet": "확률분포와 추정을 학습한다.",
        "syllabus_url": "https://example.invalid/course?code=TEST201&class=01",
    }
    row.update(updates)
    return row


class ChatPresentationTests(unittest.TestCase):
    def test_view_model_drops_storage_paths_and_unapproved_nested_metadata(self):
        raw = {"answer": "확인했습니다.", "mode": "keyword_gemini", "sources": [source(
            text_path=r"C:\private-fixture\syllabus.txt", parent_text="private full document",
            debug={"secret": "fixture-only"},
        )], "internal_context": "private request"}
        view = present_answer(raw)
        serialized = json.dumps(view, ensure_ascii=False)
        for forbidden in ("text_path", "parent_text", "private", "fixture-only", "internal_context"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(view["answer"], raw["answer"])
        self.assertEqual(view["sources"][0]["course_code"], "TEST201")

    def test_citations_deduplicate_case_insensitive_course_sections_and_fill_missing_fields(self):
        rows = [source(snippet="", syllabus_url="javascript:bad"),
                source(code="test201", snippet="첫 번째 실제 근거"),
                source(snippet="덮어쓰지 않는 다른 근거", professor="다른 이름"),
                source(section="02"), source(code="TEST301")]
        result = present_sources(rows)
        self.assertEqual([row["id"] for row in result], ["source-1", "source-2", "source-3"])
        self.assertEqual(result[0]["snippet"], "첫 번째 실제 근거")
        self.assertEqual(result[0]["professor"], "가상교수")
        self.assertTrue(result[0]["syllabus_url"].startswith("https://"))
        self.assertEqual([row["class_no"] for row in result], ["01", "02", "01"])

    def test_only_explicit_single_course_metadata_or_schedule_answers_get_cards(self):
        for mode in ("structured_course_metadata", "structured_schedule"):
            with self.subTest(mode=mode):
                view = present_answer({"answer": "과목 정보", "sources": [source(), source()], "mode": mode})
                self.assertEqual(view["course_card"]["course_code"], "TEST201")
                self.assertEqual(view["course_card"]["source_id"], "source-1")
                self.assertNotIn("snippet", view["course_card"])
        for mode in ("structured_course_assessment", "structured_followup", "keyword_gemini", "extractive", "structured_catalog"):
            self.assertIsNone(present_answer({"sources": [source()], "mode": mode})["course_card"])

    def test_comparisons_multi_section_results_and_empty_sources_never_get_single_course_cards(self):
        for rows in ([], [source(), source(code="TEST301")], [source(), source(section="02")]):
            self.assertIsNone(present_answer({"sources": rows, "mode": "structured_schedule"})["course_card"])

    def test_url_accepts_absolute_http_links_and_preserves_course_queries(self):
        for url in (
            "https://example.invalid/course?year=2026&course=TEST201#assessment",
            "http://example.invalid:8080/course", "https://예시.한국/강의",
            "https://[2001:4860:4860::8888]/course",
        ):
            self.assertEqual(safe_http_url(url), url)

    def test_url_rejects_credentials_control_characters_and_browser_parser_ambiguities(self):
        for url in (
            "javascript:alert(1)", "data:text/html,unsafe", "//example.invalid/path", "/course",
            "https://user:password@example.invalid/course", "https://@example.invalid/course",
            "\nhttps://example.invalid/course", "https://exam\tple.invalid/course",
            "https://example.invalid/%0aheader", "https://example.invalid/\u202ehidden",
            "https://example.invalid\\@other.invalid/path", "https://example.invalid/%5cpath",
            "https://bad host.invalid/course", "https://example.invalid:bad/course",
            "https://example.invalid:99999/course", "https:///course", "https://%65xample.invalid/",
        ):
            with self.subTest(url=repr(url)):
                self.assertEqual(safe_http_url(url), "")

    def test_plain_strings_are_not_double_escaped_and_input_is_not_mutated(self):
        raw = {"answer": "A < B & C", "mode": "structured_course_metadata", "sources": [
            source(course_name='<script>literal text</script>', snippet='"근거" & <조건>')
        ]}
        snapshot = copy.deepcopy(raw)
        view = present_answer(raw)
        self.assertEqual(view["answer"], "A < B & C")
        self.assertEqual(view["sources"][0]["course_name"], '<script>literal text</script>')
        self.assertEqual(raw, snapshot)
        view["sources"][0]["course_name"] = "changed view"
        view["course_card"]["course_name"] = "changed card"
        self.assertEqual(raw, snapshot)

    def test_invalid_timings_and_sources_still_produce_valid_json(self):
        view = present_answer({
            "sources": [None, 12, {}, source(professor={"private": "not allowed"})],
            "timings": {"context_seconds": float("nan"), "search_seconds": -1,
                        "generation_seconds": float("inf"), "total_seconds": "2.5", "cache_hit": "false"},
        })
        self.assertEqual(len(view["sources"]), 1)
        self.assertEqual(view["sources"][0]["professor"], "")
        self.assertEqual(view["timings"], {"context_seconds": 0.0, "search_seconds": 0.0,
                         "generation_seconds": 0.0, "total_seconds": 2.5, "cache_hit": False})
        json.dumps(view, allow_nan=False)
        self.assertEqual(present_sources({"course_code": "TEST201"}), [])


if __name__ == "__main__":
    unittest.main()
