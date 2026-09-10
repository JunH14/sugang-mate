"""Audit a stored collection snapshot without network calls or optional packages.

Public outputs contain counts, course codes and relative file names only. This
checks stored artifacts, not live collection coverage or extraction accuracy.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import statistics


INPUTS = (
    "data/processed/courses.csv",
    "data/processed/courses_deduplicated.csv",
    "data/processed/syllabus_texts.jsonl",
    "data/processed/text_extraction_report.csv",
    "data/processed/duplicate_report.csv",
    "data/processed/dedup_manifest.json",
)
MISSING_TOKENS = {"", "none", "null", "nan", "n/a", "no data", "no data."}
SCHEDULE_KEYS = ("class_no", "day", "start_period", "end_period", "start_time", "end_time", "room")


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def stored_path(root: Path, value: str) -> Path | None:
    if not value:
        return None
    if PureWindowsPath(value).is_absolute() or Path(value).is_absolute():
        raise ValueError("Expected a relative artifact path in source metadata")
    result = root.joinpath(*value.replace("\\", "/").split("/")).resolve()
    if not result.is_relative_to(root):
        raise ValueError("Artifact path escapes the source snapshot")
    return result


def exists(root: Path, value: str) -> bool:
    path = stored_path(root, value)
    return bool(path and path.is_file() and path.stat().st_size)


def entries(row: dict) -> list[dict]:
    value = row.get("schedule_entries", row.get("schedule_entries_json", []))
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = ast.literal_eval(value)
    if not isinstance(parsed, list):
        raise ValueError("schedule_entries must be a list")
    return parsed


def schedule_set(rows: list[dict]) -> set[tuple]:
    return {tuple(str(entry.get(key, "")) for key in SCHEDULE_KEYS)
            for row in rows for entry in entries(row)}


def missing(value: object) -> bool:
    return str(value if value is not None else "").strip().lower() in MISSING_TOKENS


def count_dict(values) -> dict:
    return dict(sorted(Counter(values).items()))


def audit(source: Path) -> dict:
    source = source.resolve()
    for name in INPUTS:
        if not (source / name).is_file():
            raise FileNotFoundError(f"Snapshot is missing required artifact: {name}")
    original = read_csv(source / INPUTS[0])
    canonical = read_csv(source / INPUTS[1])
    documents = [json.loads(line) for line in (source / INPUTS[2]).read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    extraction = read_csv(source / INPUTS[3])
    duplicates = read_csv(source / INPUTS[4])
    manifest = json.loads((source / INPUTS[5]).read_text(encoding="utf-8-sig"))
    originals_by_code = defaultdict(list)
    for row in original:
        originals_by_code[row["course_code"]].append(row)
    docs_by_code = {row["course_code"]: row for row in documents}
    canonical_by_key = {(r["course_code"], r["class_no"]): r for r in canonical}

    formats = []
    for extension in (".pdf", ".hwp", ".docx"):
        collected = [row for row in original if row.get("attachment_ext") == extension]
        retained = [row for row in canonical if row.get("attachment_ext") == extension]
        recorded = [row for row in documents if extension[1:] in str(row.get("sources", "")).split(";")]
        formats.append({
            "format": extension[1:], "collected_records": len(collected),
            "nonempty_attachment_files": sum(exists(source, row["attachment_path"]) for row in collected),
            "canonical_records": len(retained), "documents_recording_format": len(recorded),
            "warning": "Recorded source presence is not character or table extraction accuracy.",
        })

    preservation = []
    for row in duplicates:
        code = row["canonical_course_code"]
        doc = docs_by_code.get(code, {})
        before = schedule_set(originals_by_code[code])
        after = schedule_set([doc])
        canonical_row = canonical_by_key[(code, row["canonical_class_no"])]
        csv_after = schedule_set([canonical_row])
        preservation.append({
            "course_code": code,
            "canonical_class": row["canonical_class_no"],
            "removed_text_class": row["duplicate_class_no"],
            "similarity": float(row["similarity"]),
            "source_schedule_entries": len(before),
            "canonical_csv_schedule_entries": len(csv_after),
            "document_schedule_entries": len(after),
            "source_schedules_preserved_in_csv": before <= csv_after,
            "source_schedules_preserved_in_document": before <= after,
            "document_schedule_classes": sorted({str(e.get("class_no", "")) for e in entries(doc)}),
        })

    text_mismatch = []
    count_mismatch = []
    report_by_key = {(r["course_code"], r["class_no"]): r for r in extraction}
    for row in documents:
        key = (row["course_code"], row["class_no"])
        path = stored_path(source, row["text_path"])
        if not path or not path.is_file() or path.read_text(encoding="utf-8") != row["text"]:
            text_mismatch.append(row["course_code"])
        observed = len(row["text"])
        if int(row["char_count"]) != observed or int(report_by_key.get(key, {}).get("char_count", -1)) != observed:
            count_mismatch.append(row["course_code"])

    html_only = [r for r in documents if r["sources"] == "html"]
    no_eval_marker = [r for r in documents if "평가방법이 입력 되지 않았습니다" in r["text"]]
    artifact_markers = ("氠瑢", "漠杳", "捤獥", "潴景")
    suspicious = [r for r in documents if any(marker in r["text"] for marker in artifact_markers)]
    alternative_headings = ("▷ 강의개요", "▷ *주차별 강의계획", "▷ 수업목표")
    heading_documents = [r for r in documents if any(marker in r["text"] for marker in alternative_headings)]
    chars = [len(r["text"]) for r in documents]
    metadata_fields = ("course_code", "class_no", "course_name", "professor", "completion_type", "credit", "class_hours", "schedule_summary", "syllabus_url")
    fingerprints = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in INPUTS}
    html_files = list((source / "data/raw/html").glob("*.html"))
    referenced_html = {stored_path(source, row["html_path"]) for row in original if row.get("html_path")}
    return {
        "schema_version": 1,
        "scope": "Stored 2026-1R course snapshot audit; no live collection, OCR accuracy measurement, user study, or LLM calls.",
        "input_sha256": fingerprints,
        "snapshot": {
            "year_terms": sorted({f"{r['year']}-{r['term']}" for r in original}),
            "collection_records": len(original),
            "unique_course_codes": len({r["course_code"] for r in original}),
            "canonical_records": len(canonical),
            "processed_documents": len(documents),
            "nonempty_documents": sum(bool(r["text"].strip()) for r in documents),
            "referenced_html_records_present": sum(exists(source, row["html_path"]) for row in original),
            "raw_html_files_on_disk": len(html_files),
            "raw_html_files_not_referenced_by_current_csv": sum(p.resolve() not in referenced_html for p in html_files),
            "collection_status_counts": count_dict(r["collection_status"] for r in original),
            "source_combination_counts": count_dict(r["sources"] for r in documents),
        },
        "formats": formats,
        "metadata_missingness": {
            "denominator": len(documents),
            "fields": {field: {"missing": sum(missing(r.get(field)) for r in documents),
                                "missing_pct": round(100 * sum(missing(r.get(field)) for r in documents) / len(documents), 2)} for field in metadata_fields},
            "no_schedule_course_codes": sorted(r["course_code"] for r in documents if not entries(r)),
            "interpretation": "A missing timetable for project/internship courses is unknown, not zero class time and not proof of a collection failure.",
        },
        "text_consistency": {
            "char_count_total": sum(chars), "char_count_min": min(chars),
            "char_count_median": statistics.median(chars), "char_count_max": max(chars),
            "jsonl_vs_text_file_mismatch_codes": text_mismatch,
            "char_count_or_extraction_report_mismatch_codes": count_mismatch,
        },
        "deduplication": {"stored_manifest": manifest, "preservation_checks": preservation},
        "quality_signals": {
            "html_only_documents": len(html_only),
            "html_missing_evaluation_marker_documents": len(no_eval_marker),
            "missing_evaluation_marker_with_attachment_documents": sum(r["sources"] != "html" for r in no_eval_marker),
            "missing_evaluation_marker_html_only_codes": sorted(r["course_code"] for r in no_eval_marker if r["sources"] == "html"),
            "known_hwp_artifact_marker_documents": len(suspicious),
            "artifact_marker_codes": sorted(r["course_code"] for r in suspicious),
            "alternative_heading_documents": len(heading_documents),
            "alternative_heading_codes": sorted(r["course_code"] for r in heading_documents),
            "checklist_label_duplicate_documents": sum("대면 대면" in r["text"] and "비대면 비대면" in r["text"] for r in documents),
            "interpretation": "Signals flag documents for review; they do not prove every field is missing or quantify extraction accuracy. HTML form labels include unselected choices.",
        },
    }


def report(data: dict) -> str:
    snap, consistency, signals = data["snapshot"], data["text_consistency"], data["quality_signals"]
    lines = [
        "# 데이터 품질 보고서", "",
        "이 보고서는 저장된 2026학년도 1학기(`2026-1R`) 자료를 다시 집계한 결과다. 현재 대학 개설 과목 전체를 대조하거나 웹에서 재수집한 결과가 아니다. 연락처와 원문 전체는 이 보고서에 포함하지 않는다.", "",
        "## 수집과 추출 범위", "",
        "| 항목 | 저장 자료에서 확인한 수 |", "|---|---:|",
        f"| 수집 목록의 과목·분반 레코드 | {snap['collection_records']} |",
        f"| 고유 학수번호 | {snap['unique_course_codes']} |",
        f"| 현재 목록이 참조하는 비어 있지 않은 HTML | {snap['referenced_html_records_present']} |",
        f"| 중복 제거 후 문서 | {snap['processed_documents']} |",
        f"| 비어 있지 않은 추출 문서 | {snap['nonempty_documents']} |",
        f"| HTML만으로 구성된 문서 | {signals['html_only_documents']} |", "",
        f"HTML 폴더에는 {snap['raw_html_files_on_disk']}개 파일이 있으나 현재 목록에서 참조하지 않는 파일이 {snap['raw_html_files_not_referenced_by_current_csv']}개 있다. 폴더의 파일 수를 수집 과목 수로 보고하지 않는다. 현재 목록의 저장 파일 존재 여부를 검증한 것이며, 전체 수집 대상 대비 성공률은 원래 모집단을 알 수 없어 계산하지 않았다.", "",
        "| 첨부 형식 | 수집 분반 | 비어 있지 않은 첨부 파일 | 중복 제거 후 대상 | 해당 형식 추출 기록 |",
        "|---|---:|---:|---:|---:|",
    ]
    for item in data["formats"]:
        lines.append(f"| {item['format'].upper()} | {item['collected_records']} | {item['nonempty_attachment_files']} | {item['canonical_records']} | {item['documents_recording_format']} |")
    lines += [
        "", "‘추출 기록’은 `sources` 필드에 해당 형식이 기록되어 있다는 뜻이다. PDF·HWP·DOCX의 글자나 표를 정확하게 복원했다는 의미의 추출 정확도는 아니다.", "",
        f"추출 텍스트는 총 {consistency['char_count_total']:,}자, 문서당 중앙값 {consistency['char_count_median']:,}자다. 최소 {consistency['char_count_min']:,}자, 최대 {consistency['char_count_max']:,}자이며 길이만으로 내용의 충실도를 판단하지 않는다.", "",
        f"JSONL 본문과 개별 텍스트 파일 불일치: **{len(consistency['jsonl_vs_text_file_mismatch_codes'])}개**. 저장 문자 수 및 추출 보고서 문자 수 불일치: **{len(consistency['char_count_or_extraction_report_mismatch_codes'])}개**.", "",
        "## 메타데이터 누락", "",
        f"분모는 중복 제거 후 {data['metadata_missingness']['denominator']}개 문서다. 공백과 명시적인 `null`·`No data`류 값을 누락으로 처리한다. 값의 존재를 확인할 뿐 원문과의 정답 일치율을 측정하지 않는다.", "",
        "| 필드 | 누락 문서 | 누락률 |", "|---|---:|---:|",
    ]
    for field, value in data["metadata_missingness"]["fields"].items():
        lines.append(f"| `{field}` | {value['missing']} | {value['missing_pct']:.1f}% |")
    codes = ", ".join(data["metadata_missingness"]["no_schedule_course_codes"])
    lines += [
        "", f"시간표가 없는 학수번호는 {codes}이다. 프로젝트학기·현장실습 과목의 고정 시간표가 없다는 사실을 ‘수업시간 0시간’, ‘공강’, 또는 수집 실패로 변환하면 안 된다.", "",
        "## 중복 본문 제거와 분반 정보 보존", "",
        "중복 판정은 동일 과목명 안에서 `difflib.SequenceMatcher` 유사도 0.90을 적용한 기존 결과다. 이 분석은 그 판정의 의미적 타당성을 자동 보증하지 않고 시간표 보존을 별도로 검사한다.", "",
    ]
    for item in data["deduplication"]["preservation_checks"]:
        lines += [
            f"- `{item['course_code']}`: {item['canonical_class']}분반을 대표 본문으로 유지하고 {item['removed_text_class']}분반의 유사 본문을 제거했다. 저장된 유사도는 {item['similarity']:.6f}다.",
            f"- 원래 시간표 {item['source_schedule_entries']}개, 대표 CSV {item['canonical_csv_schedule_entries']}개, 검색 문서 메타데이터 {item['document_schedule_entries']}개다. 분반·요일·교시·시간·강의실을 함께 비교한 보존 검사: CSV **{'통과' if item['source_schedules_preserved_in_csv'] else '실패'}**, 문서 **{'통과' if item['source_schedules_preserved_in_document'] else '실패'}**.",
            f"- 대표 문서의 시간표에는 {', '.join(item['document_schedule_classes'])}분반이 남아 있다. 본문 중복 제거와 분반 시간표 삭제를 같은 작업으로 처리하지 않는다.",
        ]
    lines += [
        "", "## 실제 확인한 품질 문제", "",
        f"1. **HTML의 ‘평가방법 미입력’ 안내를 그대로 정답으로 쓰면 안 된다.** 이 문구가 {signals['html_missing_evaluation_marker_documents']}개 문서에 있고, 그중 {signals['missing_evaluation_marker_with_attachment_documents']}개는 첨부파일 텍스트도 있다. 예를 들어 `BDSC302`의 HTML은 미입력 안내를 포함하지만 PDF에는 과제·시험 배점이 존재한다. 원문 채널 간 우선순위와 충돌 처리가 필요하다.",
        f"2. **HWP 변환 흔적이 남아 있다.** 알려진 깨진 문자열 신호가 {signals['known_hwp_artifact_marker_documents']}개 문서에서 발견됐다. 이는 특정 패턴의 존재 검사이며 OCR 오류율이 아니다. 숫자·표의 관계와 원래 체크 상태는 별도 원문 대조가 필요하다.",
        f"3. **섹션 제목 형식이 통일되어 있지 않다.** `강의개요`, `수업목표`, `주차별 강의계획` 같은 별도 제목 형식을 가진 문서가 {signals['alternative_heading_documents']}개다. 현재 분할기는 일부 HWP의 강의개요를 이전 평가방법 구역에 붙인다. 검색 진단 라벨은 현재 파서 구역 이름을 그대로 기록하고, 검색된 본문에 실제 근거가 있는지로 판정한다.",
        f"4. **HTML 체크박스 라벨은 선택 값과 다르다.** ‘대면 대면’과 ‘비대면 비대면’이 함께 추출된 문서가 {signals['checklist_label_duplicate_documents']}개다. 이를 근거로 대면·비대면을 모두 운영한다고 판단하면 안 된다.",
        "5. **한 학기·한 학부의 소규모 스냅샷이다.** 다른 학기와 학과에 대한 일반화나 실제 학생의 수강 성공률을 이 자료로 주장하지 않는다.", "",
        "## 재현 방법", "",
        "이 공개 저장소의 합성 예시 데이터와 원래 수집 자료를 구분한다. 아래 `SOURCE_SNAPSHOT`에는 원래 `data/processed`와 `data/raw`가 들어 있는 폴더를 지정한다. 공개 저장소에 원문 자료가 없으면 원래 스냅샷을 보유한 환경에서 실행한다.", "",
        "```sh", "python scripts/analyze_data.py --source SOURCE_SNAPSHOT --output evaluation", "```", "",
        "Python 표준 라이브러리만 사용한다. `evaluation/data-quality.json`의 입력 SHA-256을 통해 같은 스냅샷인지 확인할 수 있다. 개인정보 값, 로컬 절대 경로, API 키는 분석 출력에 기록하지 않는다.", "",
        "## 평가 해석과 남은 검증", "",
        "새 검색 진단 질문 30개는 저장 원문을 읽고 에이전트가 작성한 것이다. 정답 근거는 생성 답변에서 추출하지 않았으며 검색 결과를 보기 전에 작성했다. 사람이 작성한 블라인드 홀드아웃이나 실제 사용자 평가가 아니다. 이 세트로 구현을 튜닝하고 같은 점수를 최종 성능으로 발표하면 안 된다.", "",
        "공개 전후의 후속 실험에서는 원문 표·체크박스 대조, 새 학생 질문의 별도 수집, 학기 변경 시 데이터 갱신 검증을 수행할 수 있다. 현재 보고서가 완료한 것은 저장 자료의 수량·메타데이터·일관성·시간표 보존 확인이다.", "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Original collection root containing data/processed and data/raw")
    parser.add_argument("--output", type=Path, default=Path("evaluation"), help="Directory for the aggregate JSON report")
    parser.add_argument("--report", type=Path, help="Markdown report path; defaults to OUTPUT/../docs/data-quality.md")
    args = parser.parse_args()
    result = audit(args.source)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "data-quality.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path = args.report or args.output.parent / "docs/data-quality.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(report(result), encoding="utf-8")
    print(json.dumps({"records": result["snapshot"]["collection_records"], "documents": result["snapshot"]["processed_documents"], "status": "stored snapshot audited"}))


if __name__ == "__main__":
    main()
