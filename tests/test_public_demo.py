"""Hosted entrypoint boundaries, exercised without external requests."""
import importlib
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from run_public import configure_public_environment
from sugang_mate.public_limits import PublicLimits, PublicLimitError

ROOT = Path(__file__).resolve().parents[1]


class PublicDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {"SUGANG_OFFLINE": "1", "SUGANG_DATA_PATH": "data/sample/syllabus_texts.jsonl"}):
            cls.ui = importlib.import_module("gradio_app")

    def test_public_entrypoint_overrides_private_data_and_administration(self):
        with patch.dict(os.environ, {
            "SUGANG_DATA_PATH": "private.jsonl", "ADMIN_PASSWORD": "fixture",
            "SUGANG_OFFLINE": "1", "GEMINI_RETRY_ATTEMPTS": "9",
        }):
            configure_public_environment()
            self.assertEqual(Path(os.environ["SUGANG_DATA_PATH"]), ROOT / "data/sample/syllabus_texts.jsonl")
            self.assertEqual(os.environ["ADMIN_PASSWORD"], "")
            self.assertEqual(os.environ["SUGANG_OFFLINE"], "0")
            self.assertEqual(os.environ["GEMINI_RETRY_ATTEMPTS"], "1")
            self.assertEqual(os.environ["GEMINI_FALLBACK_MODELS"], "")

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
