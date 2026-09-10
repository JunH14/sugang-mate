"""Offline regression tests for configuration and cross-request isolation.

Fixtures below are synthetic; no real API key or original corpus is required.
Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from sugang_mate.cache import TTLCache
from sugang_mate.config import (
    dataset_fingerprint,
    load_dotenv,
    resolve_chroma_dir,
    resolve_data_path,
)


PROJECT_DIR = Path(__file__).resolve().parents[1]
FIXTURE_RECORDS = [
    {
        "course_code": code,
        "class_no": "00",
        "course_name": name,
        "professor": professor,
        "completion_type": "전공선택",
        "credit": "3",
        "schedule_summary": schedule,
        "text": (
            f"과목명: {name}\n교수명: {professor}\n"
            f"교과목 개요\n{name}의 기본 개념과 응용을 학습한다.\n"
            "평가방법\n중간고사 30%, 기말고사 40%, 과제 20%, 출석 10%."
        ),
    }
    for code, name, professor, schedule in [
        ("TEST101", "자료분석입문", "가상교수하나", "월 10:00-11:15"),
        ("TEST202", "확률모형입문", "가상교수둘", "화 13:00-14:15"),
    ]
]


def write_fixture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in FIXTURE_RECORDS)
        + "\n",
        encoding="utf-8",
    )


class ConfigurationTests(unittest.TestCase):
    def test_sample_fallback_processed_priority_and_explicit_path(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            project = Path(directory)
            sample = project / "data/sample/syllabus_texts.jsonl"
            processed = project / "data/processed/syllabus_texts.jsonl"
            write_fixture(sample)
            self.assertEqual(resolve_data_path(project), sample)
            write_fixture(processed)
            self.assertEqual(resolve_data_path(project), processed)
            os.environ["SUGANG_DATA_PATH"] = "missing.jsonl"
            self.assertEqual(resolve_data_path(project), project / "missing.jsonl")
            os.environ["SUGANG_CHROMA_DIR"] = "custom-index"
            self.assertEqual(resolve_chroma_dir(project), project / "custom-index")

    def test_dotenv_is_local_and_caller_environment_wins(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            parent = Path(directory)
            release = parent / "release"
            release.mkdir()
            (parent / ".env").write_text("PARENT_ONLY=forbidden\n", encoding="utf-8")
            (release / ".env").write_text(
                "# synthetic test configuration\nSUGANG_OFFLINE=0\nGEMINI_MODEL=fixture-model\n",
                encoding="utf-8",
            )
            os.environ["SUGANG_OFFLINE"] = "1"
            load_dotenv(release)
            self.assertEqual(os.environ["SUGANG_OFFLINE"], "1")
            self.assertEqual(os.environ["GEMINI_MODEL"], "fixture-model")
            self.assertNotIn("PARENT_ONLY", os.environ)

    def test_fingerprint_detects_same_size_data_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "data.jsonl"
            data_path.write_text('{"course_code":"TEST101"}', encoding="utf-8")
            before = dataset_fingerprint(data_path)
            data_path.write_text('{"course_code":"TEST202"}', encoding="utf-8")
            self.assertNotEqual(before, dataset_fingerprint(data_path))

    def test_fresh_process_offline_blocks_optional_clients_even_with_dotenv_key(self):
        # Copy only source files; the .env below is a generated fake fixture.
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory) / "release"
            release.mkdir()
            for filename in ("app.py", "retrieval_core.py"):
                shutil.copyfile(PROJECT_DIR / filename, release / filename)
            package = release / "sugang_mate"
            package.mkdir()
            for filename in ("__init__.py", "config.py", "cache.py"):
                shutil.copyfile(PROJECT_DIR / "sugang_mate" / filename, package / filename)
            write_fixture(release / "data/sample/syllabus_texts.jsonl")
            (release / "data/vector_db/chroma").mkdir(parents=True)
            (release / ".env").write_text(
                "SUGANG_OFFLINE=0\nGOOGLE_API_KEY=fake-test-key\n"
                "GEMINI_MODEL=offline-fixture-model\nANSWER_CACHE_MAX=0\n",
                encoding="utf-8",
            )
            (release.parent / ".env").write_text("PARENT_ONLY=forbidden\n", encoding="utf-8")
            script = '''
import builtins, os, socket
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name.split('.')[0] in {'google', 'chromadb'}:
        raise AssertionError('Offline imported an external client: ' + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
def network_forbidden(*args, **kwargs):
    raise AssertionError('Offline attempted a network connection')
socket.socket.connect = network_forbidden
socket.create_connection = network_forbidden
import app
assert app.GENERATIVE_MODEL == 'offline-fixture-model'
assert app.ANSWER_CACHE_MAX == 0
assert app.RAG.offline
assert app.RAG.gemini_client is None
assert app.RAG.chroma_collection is None
assert app.RAG.rag_mode == 'keyword_extract'
assert app.DATA_PATH.parts[-3:] == ('data', 'sample', 'syllabus_texts.jsonl')
assert 'PARENT_ONLY' not in os.environ
assert app.RAG.answer('TEST101 담당 교수 알려줘')['answer']
assert 'google.genai' not in __import__('sys').modules
assert 'chromadb' not in __import__('sys').modules
'''
            environment = os.environ.copy()
            for key in (
                "GOOGLE_API_KEY", "GEMINI_MODEL", "ANSWER_CACHE_MAX",
                "SUGANG_DATA_PATH", "SUGANG_CHROMA_DIR", "PARENT_ONLY",
            ):
                environment.pop(key, None)
            environment.update(SUGANG_OFFLINE="1", PYTHONUTF8="1")
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=release, env=environment,
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


class CacheTests(unittest.TestCase):
    def test_ttl_does_not_extend_on_read_and_lru_keeps_recent_entry(self):
        clock = [0.0]
        cache = TTLCache(max_size=2, ttl_seconds=10, clock=lambda: clock[0])
        cache.put("a", {"value": 1})
        cache.put("b", {"value": 2})
        clock[0] = 5
        self.assertEqual(cache.get("a"), {"value": 1})
        cache.put("c", {"value": 3})
        self.assertIsNone(cache.get("b"))
        clock[0] = 10
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("c"), {"value": 3})

    def test_zero_capacity_or_ttl_disables_cache(self):
        for cache in (TTLCache(max_size=0), TTLCache(ttl_seconds=0)):
            cache.put("question", {"answer": "answer"})
            self.assertIsNone(cache.get("question"))
            self.assertEqual(len(cache), 0)

    def test_original_and_returned_nested_values_are_isolated(self):
        cache = TTLCache()
        value = {"sources": [{"course_code": "TEST101"}]}
        cache.put("question", value)
        value["sources"][0]["course_code"] = "MUTATED"
        result = cache.get("question")
        self.assertEqual(result["sources"][0]["course_code"], "TEST101")
        result["sources"].clear()
        self.assertEqual(cache.get("question")["sources"][0]["course_code"], "TEST101")

    def test_concurrent_shared_key_reads_are_isolated_and_capacity_is_bounded(self):
        cache = TTLCache(max_size=8)
        cache.put("shared", {"values": []})

        def visit(index):
            cache.put(str(index), {"values": [index]})
            value = cache.get("shared")
            if value is not None:
                self.assertEqual(value, {"values": []})
                value["values"].append(index)

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(visit, range(200)))
        self.assertLessEqual(len(cache), 8)
        cache.clear()
        self.assertEqual(len(cache), 0)


class RagIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.data_path = Path(cls.directory.name) / "fixtures.jsonl"
        write_fixture(cls.data_path)
        cls.environment = patch.dict(os.environ, {
            "SUGANG_OFFLINE": "1", "SUGANG_DATA_PATH": str(cls.data_path),
            "ANSWER_CACHE_MAX": "128", "ANSWER_CACHE_TTL_SECONDS": "1800",
        })
        cls.environment.start()
        cls.app = importlib.import_module("app")

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        cls.directory.cleanup()

    def setUp(self):
        self.rag = self.app.SyllabusRag(self.data_path)

    def test_same_followup_has_different_results_for_different_histories(self):
        question = "그 과목의 담당 교수는 누구야?"
        history_a = [{"role": "user", "content": "TEST101 자료분석입문 알려줘"}]
        history_b = [{"role": "user", "content": "TEST202 확률모형입문 알려줘"}]
        result_a = self.rag.answer(question, history_a)
        result_b = self.rag.answer(question, history_b)
        self.assertIn("가상교수하나", result_a["answer"])
        self.assertNotIn("가상교수둘", result_a["answer"])
        self.assertIn("가상교수둘", result_b["answer"])
        self.assertNotIn("가상교수하나", result_b["answer"])
        self.assertFalse(result_a["timings"]["cache_hit"])
        self.assertFalse(result_b["timings"]["cache_hit"])
        repeated = self.rag.answer(question, history_a)
        self.assertTrue(repeated["timings"]["cache_hit"])
        self.assertEqual(repeated["context_resolved"]["course_code"], "TEST101")

    def test_cache_hit_timing_contract_and_response_copy_isolation(self):
        question = "TEST101 담당 교수 알려줘"
        first = self.rag.answer(question)
        expected = first["answer"]
        first["answer"] = "tampered"
        first["sources"].clear()
        second = self.rag.answer(question)
        self.assertEqual(second["answer"], expected)
        self.assertTrue(second["sources"])
        self.assertTrue(second["timings"]["cache_hit"])
        self.assertEqual(second["timings"]["search_seconds"], 0.0)
        self.assertEqual(second["timings"]["generation_seconds"], 0.0)
        self.assertGreaterEqual(second["timings"]["total_seconds"], 0.0)

    def test_warning_results_are_not_cached(self):
        self.rag._cache_answer("failed", {"warning_code": "gemini_unavailable"})
        self.assertIsNone(self.rag.answer_cache.get("failed"))

    def test_cache_disabled_on_rag_instance(self):
        with patch.dict(os.environ, {"ANSWER_CACHE_MAX": "0"}):
            rag = self.app.SyllabusRag(self.data_path)
        for _ in range(2):
            self.assertFalse(rag.answer("TEST101 담당 교수 알려줘")["timings"]["cache_hit"])

    def test_chroma_rejects_equal_count_wrong_content_or_model(self):
        # Simulate only the local Chroma interface; no client is installed or called.
        self.rag.offline = False
        self.rag.gemini_client = object()
        self.rag.chroma_dir = Path(self.directory.name)
        valid = {
            "dataset_sha256": self.rag.dataset_sha256,
            "embedding_model": self.app.EMBEDDING_MODEL,
        }
        for metadata, accepted in (
            ({}, False),
            ({**valid, "dataset_sha256": "another-dataset"}, False),
            ({**valid, "embedding_model": "another-model"}, False),
            (valid, True),
        ):
            with self.subTest(metadata=metadata):
                collection = SimpleNamespace(
                    metadata=metadata, count=lambda: len(self.rag.chunks)
                )
                client = SimpleNamespace(get_collection=lambda name: collection)
                module = SimpleNamespace(PersistentClient=lambda **kwargs: client)
                with patch.dict(sys.modules, {"chromadb": module}), patch("sys.stderr"):
                    actual = self.rag._load_chroma_collection()
                self.assertIs(actual, collection if accepted else None)


if __name__ == "__main__":
    unittest.main()
