from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import app  # noqa: E402


@dataclass
class GeneratedCase:
    case_id: str
    category: str
    question: str
    expected_codes: list[str] = field(default_factory=list)
    exact_codes: bool = True
    expected_terms: list[str] = field(default_factory=list)
    allowed_modes: list[str] = field(default_factory=list)
    require_no_sources: bool = False
    equivalence_group: str = ""
    history: list[dict[str, str]] = field(default_factory=list)


def unique_codes(items: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    codes: list[str] = []
    for item in items:
        code = str(item.get("course_code", "")).strip()
        if code and code not in seen:
            codes.append(code)
            seen.add(code)
    return codes


def schedule_codes(day: str | None = None, period: int | None = None) -> list[str]:
    codes: list[str] = []
    for syllabus in app.RAG.syllabi:
        matched = False
        for entry in syllabus.schedule_entries:
            if day and str(entry.get("day", "")) != day:
                continue
            start = int(entry.get("start_period", 0) or 0)
            end = int(entry.get("end_period", start) or start)
            if period is not None and not (start <= period <= end):
                continue
            matched = True
            break
        if matched and syllabus.course_code not in codes:
            codes.append(syllabus.course_code)
    return codes


def intersection(*groups: list[str]) -> list[str]:
    if not groups:
        return []
    allowed = set(groups[0])
    for group in groups[1:]:
        allowed.intersection_update(group)
    return [code for code in groups[0] if code in allowed]


def add_templates(
    cases: list[GeneratedCase],
    *,
    prefix: str,
    category: str,
    templates: list[str],
    values: dict[str, str],
    expected_codes: list[str],
    exact_codes: bool = True,
    expected_terms: list[str] | None = None,
    allowed_modes: list[str] | None = None,
    require_no_sources: bool = False,
    group: str = "",
) -> None:
    for index, template in enumerate(templates, 1):
        cases.append(
            GeneratedCase(
                case_id=f"{prefix}_{index:02d}",
                category=category,
                question=template.format(**values),
                expected_codes=list(expected_codes),
                exact_codes=exact_codes,
                expected_terms=list(expected_terms or []),
                allowed_modes=list(allowed_modes or []),
                require_no_sources=require_no_sources,
                equivalence_group=group or prefix,
            )
        )


def mutate_question(question: str, mutation: int) -> str:
    variants = [
        lambda text: f"안녕하세요. {text}",
        lambda text: f"{text.rstrip('?!.')} 부탁해요!",
        lambda text: f"혹시 {text.rstrip('?!.')} 알려줄 수 있어?",
        lambda text: text.replace(" 알려줘", " 좀 알려줘").replace("?", "??"),
        lambda text: text.replace("과목", "강의").replace("수업", "강의"),
        lambda text: "  ".join(text.split()),
        lambda text: text.rstrip("?!."),
        lambda text: f"{text.rstrip('?!.')} 정확히 알려줘.",
        lambda text: text.replace("PBL", "pbl").replace("AI", "ai"),
        lambda text: f"저기, {text.rstrip('?!.')} 가능하면 간단히 답해줘",
    ]
    return variants[mutation % len(variants)](question)


def build_cases(target: int, seed: int) -> list[GeneratedCase]:
    rng = random.Random(seed)
    cases: list[GeneratedCase] = []
    syllabi = list(app.RAG.syllabi)

    course_templates = {
        "course_professor": [
            "{name} 교수님 누구야?",
            "{name} 담당 교수 알려줘",
            "{code} 교수는 누구인가요?",
            "{code} 강의하시는 분 성함이 뭐야?",
            "{name} 누가 가르쳐?",
        ],
        "course_schedule": [
            "{name} 시간표 알려줘",
            "{code} 언제 수업해?",
            "{name} 요일과 교시가 어떻게 돼?",
            "{code} 강의실 어디야?",
            "{name} 수업 시간하고 장소 알려주세요",
        ],
        "course_credit": [
            "{name} 몇 학점이야?",
            "{code} 학점 알려줘",
            "{name} 학점 수가 어떻게 돼?",
            "{code}는 3학점 과목이야?",
        ],
        "course_completion": [
            "{name} 이수구분 알려줘",
            "{code} 전공필수야 전공선택이야?",
            "{name} 필수 과목인가요?",
            "{code} 과목 구분이 뭐야?",
        ],
        "course_learning": [
            "{name}에서는 뭘 배워?",
            "{code} 학습내용 알려줘",
            "{name} 강의 주제가 뭐야?",
            "{name} 주차별로 무엇을 배우나요?",
            "{code} 수업 내용을 요약해줘",
        ],
        "course_assessment": [
            "{name} 평가방식 알려줘",
            "{code} 시험하고 과제 비중이 어떻게 돼?",
            "{name} 중간고사 있어?",
            "{name} 성적은 어떻게 매겨?",
            "{code} 출석 과제 시험 기준 알려주세요",
        ],
    }

    for syllabus in syllabi:
        values = {"name": syllabus.course_name, "code": syllabus.course_code}
        expected = [syllabus.course_code]
        for category, templates in course_templates.items():
            terms: list[str] = []
            if category == "course_professor" and syllabus.professor:
                terms = [syllabus.professor]
            elif category == "course_credit" and syllabus.credit:
                terms = [syllabus.credit]
            elif category == "course_completion" and syllabus.completion_type:
                terms = [syllabus.completion_type]
            add_templates(
                cases,
                prefix=f"{category}_{syllabus.course_code}",
                category=category,
                templates=templates,
                values=values,
                expected_codes=expected,
                expected_terms=terms,
            )

    professors: dict[str, list[str]] = defaultdict(list)
    completions: dict[str, list[str]] = defaultdict(list)
    credits: dict[str, list[str]] = defaultdict(list)
    for syllabus in syllabi:
        if syllabus.professor:
            professors[syllabus.professor].append(syllabus.course_code)
        if syllabus.completion_type:
            completions[syllabus.completion_type].append(syllabus.course_code)
        if syllabus.credit:
            credits[syllabus.credit].append(syllabus.course_code)

    for professor, codes in professors.items():
        add_templates(
            cases,
            prefix=f"professor_catalog_{professor}",
            category="professor_catalog",
            templates=[
                "{professor} 교수님 수업 알려줘",
                "{professor} 교수 담당 과목은?",
                "{professor} 교수님이 맡은 강의 전부 보여줘",
                "이번 학기 {professor} 교수 수업 뭐 있어?",
                "{professor} 선생님 과목 목록 부탁해",
            ],
            values={"professor": professor},
            expected_codes=codes,
            exact_codes=True,
        )

    for completion, codes in completions.items():
        add_templates(
            cases,
            prefix=f"completion_{completion}",
            category="completion_catalog",
            templates=[
                "{completion} 과목 알려줘",
                "{completion} 수업 목록 보여줘",
                "이번 학기 {completion}는 뭐가 있어?",
                "{completion} 강의 전부 찾아줘",
                "{completion}만 모아서 알려주세요",
            ],
            values={"completion": completion},
            expected_codes=codes,
            exact_codes=True,
        )

    for credit, codes in credits.items():
        add_templates(
            cases,
            prefix=f"credit_{credit}",
            category="credit_catalog",
            templates=[
                "{credit}학점 과목 알려줘",
                "{credit}학점짜리 수업 목록 보여줘",
                "이번 학기 {credit}학점 강의는?",
                "학점이 {credit}인 과목 전부 찾아줘",
            ],
            values={"credit": credit},
            expected_codes=codes,
            exact_codes=True,
        )

    day_names = {"월": "월요일", "화": "화요일", "수": "수요일", "목": "목요일", "금": "금요일"}
    for day, day_name in day_names.items():
        day_codes = schedule_codes(day=day)
        add_templates(
            cases,
            prefix=f"schedule_day_{day}",
            category="schedule_day",
            templates=[
                "{day_name} 수업 알려줘",
                "{day_name}에 듣는 과목 뭐야?",
                "{day_name} 강의 목록 보여줘",
                "이번 학기 {day_name} 수업 전부 찾아줘",
                "{day_name} 시간표 부탁해",
            ],
            values={"day_name": day_name},
            expected_codes=day_codes,
            exact_codes=True,
            allowed_modes=["structured_schedule"],
        )
        for period in range(1, 10):
            expected = schedule_codes(day=day, period=period)
            add_templates(
                cases,
                prefix=f"schedule_{day}_{period}",
                category="schedule_day_period",
                templates=[
                    "{day_name} {period}교시 수업 알려줘",
                    "{day_name} {period}교시에 듣는 과목은?",
                    "{day_name} {period}교시 강의 목록 보여줘",
                    "이번 학기 {day_name} {period}교시 시간표 찾아줘",
                ],
                values={"day_name": day_name, "period": str(period)},
                expected_codes=expected,
                exact_codes=True,
                allowed_modes=["structured_schedule"],
            )

    pbl_codes = [s.course_code for s in syllabi if "PBL" in s.course_name.upper()]
    capstone_codes = [s.course_code for s in syllabi if "캡스톤" in s.course_name]
    english_codes = [s.course_code for s in syllabi if "영강" in s.course_name or "영어강의" in s.course_name]
    internship_codes = [s.course_code for s in syllabi if "현장실습" in s.course_name]
    title_groups = {
        "pbl": (pbl_codes, ["PBL", "pbl", "피비엘"]),
        "capstone": (capstone_codes, ["캡스톤", "캡스톤디자인"]),
        "english": (english_codes, ["영강", "영어강의"]),
        "internship": (internship_codes, ["현장실습", "인턴십"]),
    }
    for label, (codes, terms) in title_groups.items():
        for term_index, term in enumerate(terms, 1):
            add_templates(
                cases,
                prefix=f"title_{label}_{term_index}",
                category="title_catalog",
                templates=[
                    "{term} 과목 알려줘",
                    "{term} 수업 목록 보여줘",
                    "이번 학기 {term} 강의는 뭐가 있어?",
                    "과목명에 {term} 들어간 수업 찾아줘",
                ],
                values={"term": term},
                expected_codes=codes,
                exact_codes=True,
            )

    for completion, completion_codes in completions.items():
        for day, day_name in day_names.items():
            for period in range(1, 10):
                expected = intersection(schedule_codes(day=day, period=period), completion_codes)
                add_templates(
                    cases,
                    prefix=f"combined_{completion}_{day}_{period}",
                    category="combined_schedule_completion",
                    templates=[
                        "{day_name} {period}교시 {completion} 수업 알려줘",
                        "{completion} 중 {day_name} {period}교시에 있는 과목은?",
                    ],
                    values={
                        "day_name": day_name,
                        "period": str(period),
                        "completion": completion,
                    },
                    expected_codes=expected,
                    exact_codes=True,
                )

    for day, day_name in day_names.items():
        for period in range(1, 10):
            expected = intersection(schedule_codes(day=day, period=period), pbl_codes)
            add_templates(
                cases,
                prefix=f"combined_pbl_{day}_{period}",
                category="combined_schedule_title",
                templates=[
                    "{day_name} {period}교시 PBL 수업 알려줘",
                    "PBL 과목 중 {day_name} {period}교시에 있는 건 뭐야?",
                ],
                values={"day_name": day_name, "period": str(period)},
                expected_codes=expected,
                exact_codes=True,
            )

    out_of_scope = [
        "오늘 서울 날씨 알려줘",
        "비트코인 가격 전망해줘",
        "파이썬으로 웹 크롤러 만들어줘",
        "고려대학교 맛집 추천해줘",
        "내일 축구 경기 결과 알려줘",
        "자바스크립트 프론트엔드 과목 추천해줘",
        "양자역학 전공 수업 알려줘",
        "법학과 헌법 강의 추천해줘",
        "의과대학 해부학 시간표 알려줘",
        "2027학년도 2학기 과목 알려줘",
    ]
    for index, question in enumerate(out_of_scope, 1):
        cases.append(
            GeneratedCase(
                case_id=f"out_of_scope_{index:02d}",
                category="out_of_scope",
                question=question,
                expected_codes=[],
                exact_codes=True,
                allowed_modes=["out_of_scope", "fallback", "clarification"],
                require_no_sources=True,
            )
        )

    base_cases = list(cases)
    mutation_counter = 0
    while len(cases) < target:
        source = rng.choice(base_cases)
        mutation_counter += 1
        mutation = mutation_counter % 10
        mutated = GeneratedCase(**asdict(source))
        mutated.case_id = f"mutated_{mutation_counter:05d}_{source.case_id}"
        mutated.category = f"mutated/{source.category}"
        mutated.question = mutate_question(source.question, mutation)
        mutated.equivalence_group = source.equivalence_group or source.case_id
        cases.append(mutated)

    rng.shuffle(cases)
    return cases[:target]


def evaluate_case(case: GeneratedCase) -> dict[str, Any]:
    started = time.perf_counter()
    error = ""
    result: dict[str, Any] = {}
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            result = app.RAG.answer(case.question, case.history)
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - started

    sources = result.get("sources", []) or []
    codes = unique_codes(sources)
    raw_codes = [str(source.get("course_code", "")) for source in sources if source.get("course_code")]
    expected_set = set(case.expected_codes)
    actual_set = set(codes)
    if case.exact_codes:
        codes_ok = actual_set == expected_set
    else:
        codes_ok = expected_set.issubset(actual_set)
    answer = str(result.get("answer", ""))
    terms_ok = all(term in answer for term in case.expected_terms)
    mode = str(result.get("mode", ""))
    mode_ok = not case.allowed_modes or mode in case.allowed_modes
    no_sources_ok = not case.require_no_sources or not sources
    answer_ok = bool(answer.strip())
    duplicate_count = max(0, len(raw_codes) - len(set(raw_codes)))
    passed = not error and codes_ok and terms_ok and mode_ok and no_sources_ok and answer_ok

    return {
        **asdict(case),
        "mode": mode,
        "model": str(result.get("model", "")),
        "actual_codes": codes,
        "source_count": len(sources),
        "duplicate_source_count": duplicate_count,
        "codes_ok": codes_ok,
        "terms_ok": terms_ok,
        "mode_ok": mode_ok,
        "no_sources_ok": no_sources_ok,
        "answer_ok": answer_ok,
        "passed": passed,
        "elapsed_seconds": round(elapsed, 4),
        "error": error,
        "answer": answer,
    }


def write_outputs(output_dir: Path, rows: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    category_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        category_rows[row["category"]].append(row)

    category_summary = {}
    for category, items in sorted(category_rows.items()):
        category_summary[category] = {
            "count": len(items),
            "passed": sum(bool(item["passed"]) for item in items),
            "pass_rate": round(sum(bool(item["passed"]) for item in items) / len(items), 4),
            "duplicate_sources": sum(int(item["duplicate_source_count"]) for item in items),
            "average_seconds": round(statistics.mean(float(item["elapsed_seconds"]) for item in items), 4),
        }

    latencies = [float(row["elapsed_seconds"]) for row in rows]
    failure_reasons = Counter()
    for row in rows:
        if row["error"]:
            failure_reasons["exception"] += 1
        if not row["codes_ok"]:
            failure_reasons["wrong_sources"] += 1
        if not row["terms_ok"]:
            failure_reasons["missing_answer_terms"] += 1
        if not row["mode_ok"]:
            failure_reasons["wrong_mode"] += 1
        if not row["no_sources_ok"]:
            failure_reasons["unexpected_sources"] += 1
        if not row["answer_ok"]:
            failure_reasons["empty_answer"] += 1

    passed = sum(bool(row["passed"]) for row in rows)
    duplicate_queries = sum(int(row["duplicate_source_count"]) > 0 for row in rows)
    summary = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seed": seed,
        "rag_mode": app.RAG.rag_mode,
        "course_count": len(app.RAG.syllabi),
        "chunk_count": len(app.RAG.chunks),
        "case_count": len(rows),
        "passed": passed,
        "failed": len(rows) - passed,
        "pass_rate": round(passed / max(len(rows), 1), 4),
        "source_set_accuracy": round(
            sum(bool(row["codes_ok"]) for row in rows) / max(len(rows), 1), 4
        ),
        "answer_term_accuracy": round(
            sum(bool(row["terms_ok"]) for row in rows) / max(len(rows), 1), 4
        ),
        "mode_accuracy": round(
            sum(bool(row["mode_ok"]) for row in rows) / max(len(rows), 1), 4
        ),
        "no_source_accuracy": round(
            sum(bool(row["no_sources_ok"]) for row in rows) / max(len(rows), 1), 4
        ),
        "questions_per_second": round(len(rows) / max(sum(latencies), 0.0001), 2),
        "average_seconds": round(statistics.mean(latencies), 4) if latencies else 0,
        "median_seconds": round(statistics.median(latencies), 4) if latencies else 0,
        "p95_seconds": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 4) if latencies else 0,
        "max_seconds": round(max(latencies), 4) if latencies else 0,
        "duplicate_source_count": sum(int(row["duplicate_source_count"]) for row in rows),
        "duplicate_query_count": duplicate_queries,
        "duplicate_query_rate": round(duplicate_queries / max(len(rows), 1), 4),
        "failure_reasons": dict(failure_reasons),
        "category_summary": category_summary,
    }

    (output_dir / "generated_stress_results.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    failures = [row for row in rows if not row["passed"]]
    with (output_dir / "generated_stress_failures.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_id",
                "category",
                "question",
                "expected_codes",
                "actual_codes",
                "mode",
                "codes_ok",
                "terms_ok",
                "mode_ok",
                "no_sources_ok",
                "source_count",
                "duplicate_source_count",
                "elapsed_seconds",
                "error",
                "answer",
            ],
        )
        writer.writeheader()
        for row in failures:
            writer.writerow({key: row.get(key, "") for key in writer.fieldnames})

    lines = [
        "# 자동 생성 대규모 질문 스트레스 테스트",
        "",
        f"- 실행 시각: {summary['created_at']}",
        f"- 실행 모드: `{summary['rag_mode']}`",
        f"- 질문 수: {summary['case_count']:,}개",
        f"- 통과: {summary['passed']:,}개",
        f"- 실패: {summary['failed']:,}개",
        f"- 통과율: {summary['pass_rate'] * 100:.2f}%",
        f"- 출처 과목 집합 정확도: {summary['source_set_accuracy'] * 100:.2f}%",
        f"- 필수 답변 내용 정확도: {summary['answer_term_accuracy'] * 100:.2f}%",
        f"- 라우팅 모드 정확도: {summary['mode_accuracy'] * 100:.2f}%",
        f"- 무근거 출처 억제 정확도: {summary['no_source_accuracy'] * 100:.2f}%",
        f"- 평균 응답시간: {summary['average_seconds']:.4f}초",
        f"- p95 응답시간: {summary['p95_seconds']:.4f}초",
        f"- 최대 응답시간: {summary['max_seconds']:.4f}초",
        f"- 중복 출처 항목: {summary['duplicate_source_count']:,}개",
        f"- 중복 출처가 발생한 질문: {summary['duplicate_query_count']:,}개 "
        f"({summary['duplicate_query_rate'] * 100:.2f}%)",
        "",
        "## 실패 원인",
        "",
    ]
    if failure_reasons:
        for reason, count in failure_reasons.most_common():
            lines.append(f"- {reason}: {count:,}건")
    else:
        lines.append("- 실패 없음")
    lines.extend(["", "## 범주별 결과", "", "| 범주 | 질문 | 통과 | 통과율 | 중복 출처 | 평균 시간 |", "|---|---:|---:|---:|---:|---:|"])
    for category, item in category_summary.items():
        lines.append(
            f"| {category} | {item['count']:,} | {item['passed']:,} | "
            f"{item['pass_rate'] * 100:.2f}% | {item['duplicate_sources']:,} | "
            f"{item['average_seconds']:.4f}초 |"
        )
    (output_dir / "generated_stress_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and run a large deterministic RAG stress test.")
    parser.add_argument("--target", type=int, default=2500)
    parser.add_argument("--seed", type=int, default=20260618)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_DIR / "data" / "evaluation" / "generated_stress",
    )
    args = parser.parse_args()
    cases = build_cases(max(1, args.target), args.seed)
    rows = []
    for index, case in enumerate(cases, 1):
        rows.append(evaluate_case(case))
        if index % 100 == 0 or index == len(cases):
            passed = sum(bool(row["passed"]) for row in rows)
            print(f"[{index}/{len(cases)}] passed={passed} failed={index - passed}", flush=True)
    summary = write_outputs(args.output_dir, rows, args.seed)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
