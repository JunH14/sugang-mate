"""Hosted entrypoint boundaries, exercised without external requests."""
import importlib
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
from unittest.mock import patch

from run_public import configure_public_environment, public_dataset
from sugang_mate.public_limits import PublicLimits, PublicLimitError

ROOT = Path(__file__).resolve().parents[1]


class PublicDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {"SUGANG_OFFLINE": "1", "SUGANG_DATA_PATH": "data/sample/syllabus_texts.jsonl"}):
            cls.ui = importlib.import_module("gradio_app")

    def test_explicit_sample_mode_overrides_private_data_and_administration(self):
        with patch.dict(os.environ, {
            "SUGANG_DATA_PATH": "private.jsonl", "ADMIN_PASSWORD": "fixture",
            "SUGANG_PUBLIC_DATA_MODE": "sample",
            "SUGANG_OFFLINE": "1", "GEMINI_RETRY_ATTEMPTS": "9",
        }):
            configure_public_environment()
            self.assertEqual(Path(os.environ["SUGANG_DATA_PATH"]), ROOT / "data/sample/syllabus_texts.jsonl")
            self.assertEqual(os.environ["ADMIN_PASSWORD"], "")
            self.assertEqual(os.environ["SUGANG_OFFLINE"], "0")
            self.assertEqual(os.environ["GEMINI_RETRY_ATTEMPTS"], "1")
            self.assertEqual(os.environ["GEMINI_FALLBACK_MODELS"], "")

    def test_collected_mode_requires_server_data_without_falling_back(self):
        with patch.dict(os.environ, {"SUGANG_PUBLIC_DATA_MODE": "collected", "SUGANG_PUBLIC_DATA_PATH": "missing-hosted-fixture.jsonl"}):
            with self.assertRaisesRegex(ValueError, "Hosted dataset missing"):
                public_dataset(ROOT)

    def test_collected_mode_checks_reviewed_content_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "evaluation").mkdir()
            path = root / "reviewed.jsonl"
            path.write_bytes(b'{"course_code":"TEST101","text":"course fixture"}\n')
            report = {"hosted_dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "document_count": 1}
            (root / "evaluation/hosted-data-checks.json").write_text(json.dumps(report), encoding="utf-8")
            with patch.dict(os.environ, {"SUGANG_PUBLIC_DATA_MODE": "collected", "SUGANG_PUBLIC_DATA_PATH": "reviewed.jsonl"}):
                selected, mode, fingerprint = public_dataset(root)
                self.assertEqual(selected, path.resolve())
                self.assertEqual(mode, "collected")
                self.assertEqual(fingerprint, report["hosted_dataset_sha256"])
                path.write_bytes(b'{"course_code":"TEST101","text":"different course"}\n')
                with self.assertRaisesRegex(ValueError, "does not match"):
                    public_dataset(root)

    def test_public_status_uses_loaded_course_count(self):
        with patch.object(self.ui, "PUBLIC_DEMO", True), patch.object(self.ui, "is_sample_data", return_value=False), patch.object(self.ui.core.RAG, "syllabi", [None] * 25):
            status = self.ui.status_html()
        self.assertIn('data-course-count="25"', status)
        self.assertIn('data-dataset-kind="collected"', status)
        self.assertIn("실제 수집 과목", status)
        self.assertNotIn("가상 과목", status)

    def test_changing_session_does_not_reset_address_quota(self):
        limits = PublicLimits(chat_limit=1)
        response = {"answer": "fixture", "sources": []}
        with patch.object(self.ui, "PUBLIC_LIMITS", limits), patch.object(self.ui.core.RAG, "answer", return_value=response) as answer:
            first = SimpleNamespace(client=SimpleNamespace(host="test-address"), session_hash="one")
            second = SimpleNamespace(client=SimpleNamespace(host="test-address"), session_hash="two")
            self.assertEqual(self.ui.chat("첫 질문", [], first)[0], "fixture")
            blocked = self.ui.chat("다음 질문", [], second)
        answer.assert_called_once()
        self.assertIn("잠시 기다린", blocked[0])
        self.assertNotIn("fixture", blocked[1])

    def test_missing_request_uses_shared_bounded_bucket(self):
        with patch.object(self.ui, "PUBLIC_LIMITS", PublicLimits(chat_limit=0)), patch.object(self.ui.core.RAG, "answer") as answer:
            result = self.ui.chat("질문", [])
        answer.assert_not_called()
        self.assertIn("중지", result[0])

    def test_public_admin_cannot_run_even_with_valid_password(self):
        with patch.object(self.ui, "PUBLIC_DEMO", True), patch.object(self.ui, "ADMIN_PASSWORD", "fixture"), patch.object(self.ui, "_run_update_step") as update:
            result = self.ui.update_data("fixture")
        update.assert_not_called()
        self.assertIn("제공하지 않습니다", result[0])

    def test_api_limit_is_explained_when_grounded_fallback_is_used(self):
        message = self.ui.core.friendly_api_warning(PublicLimitError("fixture"))
        self.assertIn("공개 데모", message)
        self.assertIn("저장된 강의계획서 근거", message)

    def test_public_queue_has_a_finite_waiting_room(self):
        with patch.object(self.ui, "PUBLIC_DEMO", True):
            demo = self.ui.build_demo()
        self.assertEqual(demo._queue.max_size, 16)
        self.assertEqual(demo._queue.default_concurrency_limit, 2)


if __name__ == "__main__":
    unittest.main()
