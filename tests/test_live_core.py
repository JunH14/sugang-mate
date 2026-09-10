"""Live-path boundaries using fake SDK objects; never call an external model."""
from __future__ import annotations

from contextlib import contextmanager, redirect_stderr
import importlib
import io
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def fake_google_sdk(client):
    google = ModuleType("google")
    genai = ModuleType("google.genai")
    genai.types = SimpleNamespace(
        HttpOptions=SimpleNamespace,
        HttpRetryOptions=SimpleNamespace,
        GenerateContentConfig=SimpleNamespace,
        ThinkingConfig=SimpleNamespace,
    )
    genai.Client = Mock(return_value=client)
    google.genai = genai
    with patch.dict(sys.modules, {"google": google, "google.genai": genai}):
        yield genai


class ApiFailure(RuntimeError):
    code = 429


class LiveCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(os.environ, {
            "SUGANG_OFFLINE": "1",
            "SUGANG_DATA_PATH": str(ROOT / "data/sample/syllabus_texts.jsonl"),
            "ANSWER_CACHE_MAX": "0",
        })
        cls.environment.start()
        cls.core = importlib.import_module("app")

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()

    def setUp(self):
        self.rag = self.core.SyllabusRag(ROOT / "data/sample/syllabus_texts.jsonl")
        self.rag_patch = patch.object(self.core, "RAG", self.rag)
        self.rag_patch.start()
        self.addCleanup(self.rag_patch.stop)
        # A missing mock must fail locally instead of reaching a real endpoint.
        self.connect_patch = patch("socket.socket.connect", side_effect=AssertionError("Network forbidden"))
        self.connect_patch.start()
        self.addCleanup(self.connect_patch.stop)

    def test_visual_encoding_question_retrieves_visualization_instead_of_coding_filter(self):
        result = self.rag.answer("대시보드와 시각적 인코딩을 경험할 수 있는 수업은?")
        self.assertEqual(result["mode"], "extractive")
        self.assertEqual({source["course_code"] for source in result["sources"]}, {"DEMO301"})
        expanded = self.core.expand_query_tokens(self.core.tokenize("인코딩 encoding decoding"))
        self.assertNotIn("프로그래밍", expanded)

    def test_programming_queries_keep_coding_intent_and_korean_particles(self):
        for question in ("코딩하는 과목을 알려줘", "프로그래밍 과목을 알려줘", "coding 과목을 알려줘"):
            with self.subTest(question=question):
                result = self.rag.answer(question)
                self.assertEqual(result["mode"], "structured_feature")
                self.assertIn("DEMO202", {source["course_code"] for source in result["sources"]})
        self.assertTrue(self.core.contains_phrase("인코딩과 코딩을 비교", ("코딩",)))
        self.assertTrue(self.core.contains_any("바이브코딩", {"코딩"}))
        for text in ("인코딩", "디코딩", "엔코딩", "encoding", "decoding"):
            self.assertFalse(self.core.contains_phrase(text, ("코딩", "coding")), text)

    def test_generated_sources_match_only_the_five_courses_sent_to_model(self):
        matches = []
        for course in self.rag.syllabi:
            chunk = next(chunk for chunk in self.rag.chunks if chunk.syllabus is course)
            matches.append(self.core.Match(chunk=chunk, score=1.0))
        generate = Mock(return_value=SimpleNamespace(text="가상 강의계획서에 근거한 답변"))
        client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        self.rag.gemini_client = client
        with fake_google_sdk(client):
            result = self.rag._answer_with_gemini("과목의 특징을 설명해줘", matches)
        prompt = generate.call_args.kwargs["contents"]
        expected = {course.course_code for course in self.rag.syllabi[:5]}
        self.assertEqual({source["course_code"] for source in result["sources"]}, expected)
        for code in expected:
            self.assertIn(code, prompt)
        self.assertNotIn(self.rag.syllabi[5].course_code, prompt)

    def test_narrow_generation_context_recovers_objectives_without_adding_courses(self):
        course = next(item for item in self.rag.syllabi if item.course_code == "DEMO302")
        overview = next(chunk for chunk in self.rag.chunks if chunk.syllabus is course and chunk.section == "교과목개요")
        selected = self.rag._generation_context_matches([self.core.Match(chunk=overview, score=1.0)])
        self.assertEqual({item.chunk.syllabus.course_code for item in selected}, {"DEMO302"})
        self.assertTrue(any("감성 분석" in item.chunk.text for item in selected))
        self.assertLessEqual(len(selected), 9)

    def test_comparison_context_preserves_each_primary_match_and_both_overviews(self):
        matches = []
        for code in ("DEMO101", "DEMO302"):
            chunk = next(chunk for chunk in self.rag.chunks if chunk.syllabus.course_code == code and chunk.section == "주별학습내용")
            matches.append(self.core.Match(chunk=chunk, score=1.0))
        selected = self.rag._generation_context_matches(matches)
        self.assertEqual(selected[:2], matches)
        self.assertEqual({item.chunk.syllabus.course_code for item in selected if item.chunk.section == "교과목개요"}, {"DEMO101", "DEMO302"})
        self.assertLessEqual(len(selected), 9)

    def test_supplements_do_not_displace_existing_direct_evidence(self):
        course_chunks = [chunk for chunk in self.rag.chunks if chunk.syllabus.course_code == "DEMO101"]
        matches = [self.core.Match(chunk=chunk, score=1.0) for chunk in course_chunks[:5]]
        selected = self.rag._generation_context_matches(matches)
        self.assertEqual(selected[:5], matches)
        self.assertLessEqual(len(selected), 9)

    def test_generated_source_quote_contains_actual_context_body(self):
        chunk = next(chunk for chunk in self.rag.chunks if chunk.syllabus.course_code == "DEMO101" and chunk.section == "교과목개요")
        source = self.core.source_payload([self.core.Match(chunk=chunk, score=1.0)], question="CSV 결측치 처리")[0]
        self.assertIn("결측치", source["snippet"])
        self.assertIn(source["snippet"], chunk.parent_text or chunk.text)

    def test_client_timeout_is_bounded_and_sdk_does_not_add_retries(self):
        self.rag.offline = False
        for configured, expected_ms in ((None, 20_000), ("3.5", 3_500)):
            with self.subTest(configured=configured), patch.dict(os.environ, {"GOOGLE_API_KEY": "fixture-key"}):
                os.environ.pop("GEMINI_TIMEOUT_SECONDS", None)
                if configured is not None:
                    os.environ["GEMINI_TIMEOUT_SECONDS"] = configured
                client = object()
                with fake_google_sdk(client) as sdk:
                    self.assertIs(self.rag._load_gemini_client(), client)
                options = sdk.Client.call_args.kwargs["http_options"]
                self.assertEqual(options.timeout, expected_ms)
                self.assertEqual(options.retry_options.attempts, 1)

    def test_invalid_timeout_does_not_silently_create_an_unbounded_client(self):
        self.rag.offline = False
        for value in ("0", "-1", "nan", "inf", "not-a-number"):
            with self.subTest(value=value), patch.dict(os.environ, {
                "GOOGLE_API_KEY": "fixture-secret-key",
                "GEMINI_TIMEOUT_SECONDS": value,
            }), fake_google_sdk(object()) as sdk, redirect_stderr(io.StringIO()) as output:
                self.assertIsNone(self.rag._load_gemini_client())
                sdk.Client.assert_not_called()
                self.assertIn("type=ValueError", output.getvalue())
                self.assertNotIn("fixture-secret-key", output.getvalue())

    def test_failed_generation_uses_safe_warning_and_logs_without_request_payload(self):
        failure = ApiFailure("fixture-secret-key https://example.invalid/?key=private user request")
        generate = Mock(side_effect=failure)
        client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        self.rag.gemini_client = client
        self.rag.gemini_retry_attempts = 2
        self.rag.gemini_retry_base_seconds = 0
        matches = [self.core.Match(chunk=self.rag.chunks[0], score=1.0)]
        with fake_google_sdk(client), patch.object(self.core, "GENERATIVE_MODELS", ["fixture-model"]), redirect_stderr(io.StringIO()) as output:
            self.assertIsNone(self.rag._answer_with_gemini("fixture request", matches))
        self.assertEqual(generate.call_count, 2)
        self.assertIn("type=ApiFailure status=429", output.getvalue())
        self.assertNotIn("fixture-secret-key", output.getvalue())
        self.assertNotIn("https://", output.getvalue())
        self.assertIn("한도", self.rag.request_state.generation_warning)

    def test_failed_followup_rewrite_keeps_question_and_safe_diagnostic(self):
        generate = Mock(side_effect=ApiFailure("fixture-secret-key"))
        client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        self.rag.gemini_client = client
        with fake_google_sdk(client), redirect_stderr(io.StringIO()) as output:
            question = self.rag._rewrite_followup_question("그래서?", [{"role": "user", "content": "안녕"}])
        self.assertEqual(question, "그래서?")
        self.assertIn("follow-up rewrite", output.getvalue())
        self.assertNotIn("fixture-secret-key", output.getvalue())

    def test_response_total_includes_time_spent_rewriting_conversation(self):
        clock = [100.0]
        question = "CSV 파일에서 결측치를 처리하려면 어떤 과목이 관련돼?"

        def rewrite(*_args):
            clock[0] += 2.0
            return question

        def answer(_question):
            clock[0] += 3.0
            return {"answer": "가상 답변", "sources": [], "mode": "keyword_gemini", "timings": {
                "search_seconds": 0.0, "generation_seconds": 3.0,
                "total_seconds": 3.0, "cache_hit": False,
            }}

        self.rag.gemini_client = object()
        with patch.object(self.core.time, "perf_counter", side_effect=lambda: clock[0]), patch.object(
            self.rag, "_rewrite_followup_question", side_effect=rewrite
        ), patch.object(self.rag, "_answer_question", side_effect=answer):
            result = self.rag.answer(question, [{"role": "user", "content": "안녕"}])
        self.assertEqual(result["timings"]["context_seconds"], 2.0)
        self.assertEqual(result["timings"]["total_seconds"], 5.0)


if __name__ == "__main__":
    unittest.main()
