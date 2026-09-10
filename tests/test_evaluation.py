from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


benchmark = load_script("benchmark_retrieval")
audit = load_script("audit_legacy_evaluation")


def label(code, snippet="정답을 검증하는 긴 근거 문장"):
    return {"course_code": code, "evidence": [{"section": "교과목개요", "snippets": [snippet]}]}


class EvaluationTests(unittest.TestCase):
    def test_multi_course_recall_uses_all_expected_courses(self):
        case = {"id": "two_courses", "expected_courses": [label("A"), label("B")]}
        result = benchmark.score_case(case, [benchmark.RankedChunk("A", "정답을 검증하는 긴 근거 문장")])
        self.assertEqual(result["distinct_course_recall_at_5"], 0.5)
        self.assertEqual(result["evidence_course_recall_at_5"], 0.5)
        self.assertFalse(result["all_expected_courses_found"])

    def test_duplicate_chunks_do_not_inflate_recall_or_unique_rank(self):
        case = {"id": "duplicates", "expected_courses": [label("A"), label("B")]}
        ranked = [benchmark.RankedChunk("X", "other"), benchmark.RankedChunk("X", "other"),
                  benchmark.RankedChunk("A", "정답을 검증하는 긴 근거 문장"),
                  benchmark.RankedChunk("A", "정답을 검증하는 긴 근거 문장"),
                  benchmark.RankedChunk("Y", "other"), benchmark.RankedChunk("B", "정답을 검증하는 긴 근거 문장")]
        result = benchmark.score_case(case, ranked)
        self.assertEqual(result["distinct_course_recall_at_5"], 0.5)
        self.assertEqual(result["retrieved_distinct_course_count"], 3)
        self.assertEqual(result["unique_course_reciprocal_rank_within_top_5_chunks"], 0.5)

    def test_right_course_without_evidence_is_not_evidence_hit(self):
        result = benchmark.score_case({"id": "wrong_section", "expected_courses": [label("A")]},
                                      [benchmark.RankedChunk("A", "관련 없는 주차별 안내")])
        self.assertEqual(result["distinct_course_recall_at_5"], 1)
        self.assertEqual(result["evidence_course_recall_at_5"], 0)

    def test_absent_and_duplicate_labels_are_errors(self):
        for labels in ([], [label("A"), label("A")]):
            with self.assertRaises(ValueError):
                benchmark.score_case({"id": "bad", "expected_courses": labels}, [])

    def test_gold_evidence_must_exist_in_labeled_source_section(self):
        corpus = [{"course_code": "A", "course_name": "테스트", "text": "교과목개요\n실제 존재하는 수업 소개 문장입니다"}]
        payload = {"schema_version": 1, "cases": [{"id": "bad", "question": "수업 소개", "expected_courses": [label("A")]}]}
        with self.assertRaisesRegex(ValueError, "absent"):
            benchmark.validate_cases(payload, corpus)

    def test_legacy_audit_never_exports_answers_or_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.json"
            path.write_text(json.dumps({"summary": {"case_count": 1, "passed": 1}, "rows": [
                {"question": "PRIVATE_QUESTION", "answer": "PRIVATE_ANSWER", "passed": True,
                 "expected_codes": ["A"], "expected_terms": [], "allowed_modes": [],
                 "mode": "structured", "model": ""}]}), encoding="utf-8")
            result, _ = audit.audit_file(path)
            public_text = json.dumps(result)
            self.assertNotIn("PRIVATE_ANSWER", public_text)
            self.assertNotIn("PRIVATE_QUESTION", public_text)
            self.assertEqual(result["empty_expected_terms_count"], 1)

    def test_cli_fails_strictly_for_missing_inputs_without_writing_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.json"
            commands = [
                ["benchmark_retrieval.py", "--corpus", str(Path(tmp) / "missing.jsonl")],
                ["audit_legacy_evaluation.py", "--legacy-root", str(Path(tmp) / "missing")],
            ]
            for script, *args in commands:
                result = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args, "--output", str(output)],
                                        text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
