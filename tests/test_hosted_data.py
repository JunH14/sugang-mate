"""Synthetic regression fixtures for the real-course server export."""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_hosted_data", ROOT / "scripts/prepare_hosted_data.py")
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


def fixture():
    return {
        "course_code": "TEST201", "class_no": "01", "course_name": "가상 통계학",
        "professor": "가상교수", "completion_type": "전공필수", "completion_type_source": "html",
        "credit": "3", "class_hours": "3(3)", "schedule_raw": "화(3-4)",
        "schedule_summary": "화요일 3-4교시 (11:00-12:50), 예시관 204호",
        "schedule_entries": [{"class_no": "01", "day": "화", "start_period": 3, "end_period": 4,
                              "start_time": "11:00", "end_time": "12:50", "room": "예시관 204호"}],
        "syllabus_url": "https://infodepot.korea.ac.kr/lecture1/lecsubjectPlanView.jsp?year=2026&term=1R&cour_cd=TEST201&cour_cls=01&std_id=fixture-person&device=WW",
        "text_path": r"C:\private-fixture\original.txt", "sources": "hwp;html",
        "text": "학습목표\n확률분포와 추정을 학습한다.\n평가방법\n중간시험 100점, 기말시험 100점, 과제 40점, 출석 40점.\n1주 03.02-03.07, 16주 최종 평가.\n",
        "char_count": 0,
    }


class HostedDataTests(unittest.TestCase):
    def test_contacts_are_removed_without_changing_assessment_dates_or_times(self):
        record = fixture()
        academic = record["text"].strip()
        record["text"] += "\nE-mail: fixture.person@example.invalid\n연락처: 010-1234-5678 / 0448601234 / +82-10-9876-5432\n학번: 2026123456\n"
        cleaned, report = exporter.sanitize_records([record])
        text = cleaned[0]["text"]
        self.assertIn(academic, text)
        self.assertNotIn("fixture.person", text)
        self.assertNotIn("1234-5678", text)
        self.assertNotIn("0448601234", text)
        self.assertNotIn("9876-5432", text)
        self.assertNotIn("2026123456", text)
        self.assertEqual(report["redaction_counts"]["phone_numbers_removed"], 3)
        self.assertTrue(report["preservation_checks"]["academic_metadata_unchanged"])

    def test_official_course_lookup_keeps_academic_parameters_but_not_identity(self):
        counts = Counter()
        cleaned = exporter.safe_url(fixture()["syllabus_url"] + "&token=fixture-token#private-fragment", counts)
        query = parse_qs(urlsplit(cleaned).query)
        self.assertEqual(query, {"year": ["2026"], "term": ["1R"], "cour_cd": ["TEST201"], "cour_cls": ["01"]})
        self.assertEqual(urlsplit(cleaned).fragment, "")
        self.assertEqual(counts["url_query_parameters_removed"], 3)

    def test_unknown_signed_urls_and_private_endpoints_are_not_broken_base_links(self):
        for url in (
            "https://example.invalid/download?token=fixture-token",
            "https://user:password@example.invalid/path",
            "http://127.0.0.1/private", "http://10.0.0.2/private", "http://localhost/private",
            "https://infodepot.korea.ac.kr/lecture1/lecsubjectPlanView.jsp?std_id=fixture-person",
        ):
            with self.subTest(url=url):
                self.assertEqual(exporter.safe_url(url, Counter()), "")

    def test_complete_redaction_pipeline_preserves_https_citations_as_working_course_links(self):
        record = fixture()
        record["text"] += "\n강의계획서 URL: " + record["syllabus_url"] + "\n[첨부: C:\\fixture\\syllabus.hwp]"
        cleaned, report = exporter.sanitize_records([record])
        citation = cleaned[0]["syllabus_url"]
        self.assertTrue(citation.startswith("https://infodepot.korea.ac.kr/lecture1/lecsubjectPlanView.jsp?"))
        self.assertEqual(parse_qs(urlsplit(citation).query)["cour_cd"], ["TEST201"])
        self.assertIn(citation, cleaned[0]["text"])
        self.assertNotIn("std_id", cleaned[0]["text"])
        self.assertNotIn("fixture-person", cleaned[0]["text"])
        self.assertEqual(report["redaction_counts"]["local_paths_removed"], 1)

    def test_text_and_source_metadata_do_not_retain_local_paths(self):
        record = fixture()
        record["text"] += "\n[첨부: C:\\private-fixture\\lecture original.hwp]\n본문은 남긴다.\n/home/fixture/original.txt\n"
        record["sources"] = "hwp; C:\\private-fixture\\original.hwp"
        cleaned, report = exporter.sanitize_records([record])
        self.assertEqual(cleaned[0]["text_path"], "")
        self.assertNotIn("private-fixture", json.dumps(cleaned))
        self.assertIn("본문은 남긴다", cleaned[0]["text"])
        self.assertEqual(report["residual_pattern_counts"]["local_paths"], 0)

    def test_duplicate_section_schedule_and_professor_are_preserved(self):
        record = fixture()
        section = {key: copy.deepcopy(record[key]) for key in ("course_code", "class_no", "professor", "schedule_summary", "schedule_entries", "syllabus_url")}
        section["class_no"] = "02"
        section["schedule_entries"][0]["class_no"] = "02"
        section["similarity"] = 0.99
        record["duplicate_sections"] = [section]
        cleaned, report = exporter.sanitize_records([record])
        self.assertEqual(cleaned[0]["duplicate_sections"][0]["schedule_entries"], section["schedule_entries"])
        self.assertEqual(cleaned[0]["duplicate_sections"][0]["class_no"], "02")
        self.assertTrue(report["preservation_checks"]["duplicate_sections_unchanged_except_citation_url"])

    def test_only_known_extraction_markers_are_removed_without_inventing_values(self):
        record = fixture()
        record["text"] = "평가방법\n氠瑢 중간시험 40점 漠杳 기말시험 60점\n漢字保持\n평가 비율은 미기재"
        cleaned, report = exporter.sanitize_records([record])
        self.assertEqual(cleaned[0]["text"], "평가방법\n 중간시험 40점  기말시험 60점\n漢字保持\n평가 비율은 미기재")
        self.assertEqual(report["redaction_counts"]["known_extraction_markers_removed"], 2)
        self.assertNotIn("%", cleaned[0]["text"])
        self.assertEqual(cleaned[0]["char_count"], len(cleaned[0]["text"]))

    def test_unrecognized_metadata_is_not_exposed_and_unsafe_academic_metadata_fails(self):
        record = fixture()
        record["personal_note"] = "fixture confidential note"
        cleaned, report = exporter.sanitize_records([record])
        self.assertNotIn("personal_note", cleaned[0])
        self.assertEqual(report["redaction_counts"]["unrecognized_metadata_fields_removed"], 1)
        record["professor"] = "fixture.person@example.invalid"
        with self.assertRaisesRegex(ValueError, "academic metadata"):
            exporter.sanitize_records([record])

    def test_repeated_export_has_identical_bytes_and_verifiable_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.jsonl"
            source.write_text(json.dumps(fixture(), ensure_ascii=False) + "\n", encoding="utf-8")
            output = root / "output.jsonl"
            report_path = root / "report.json"
            first = exporter.prepare(source, output, report_path)
            payload = output.read_bytes()
            second = exporter.prepare(source, output, report_path)
            self.assertEqual(payload, output.read_bytes())
            self.assertEqual(first["hosted_dataset_sha256"], second["hosted_dataset_sha256"])
            self.assertEqual(second["hosted_dataset_sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(second["source_dataset_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(second["document_count"], 1)
            self.assertEqual(second["byte_count"], len(payload))

    def test_colliding_paths_fail_before_overwriting_any_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.jsonl"
            original = (json.dumps(fixture(), ensure_ascii=False) + "\n").encode("utf-8")
            source.write_bytes(original)
            output, report = root / "output.jsonl", root / "report.json"
            for destination, report_path in ((source, report), (output, source), (output, output)):
                with self.subTest(destination=destination.name, report=report_path.name):
                    with self.assertRaisesRegex(ValueError, "distinct files"):
                        exporter.prepare(source, destination, report_path)
                    self.assertEqual(source.read_bytes(), original)
                    self.assertFalse(output.exists())
                    self.assertFalse(report.exists())


if __name__ == "__main__":
    unittest.main()
