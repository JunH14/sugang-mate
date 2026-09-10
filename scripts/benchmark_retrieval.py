"""Offline diagnostic of chunking only; never imports app or calls an API."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from retrieval_core import BM25Index, clean_text, make_section_chunks, split_sections

TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9_./+-]+")


def tokenize(text: str) -> list[str]:
    """Same basic tokenizer as the application; no query expansion in either arm."""
    return [token.lower() for token in TOKEN_RE.findall(text) if len(token) >= 2]


def sanitize_text(text: str) -> str:
    text = clean_text(text)
    text = re.sub(r"활동유형.*?(?=(?:▷\s*)?평가방법)", "", text, flags=re.I | re.S)
    text = re.sub(
        r"수업유형\s+(?:대면\s+대면\s+)?(?:비대면\s+비대면\s+)?"
        r"(?:병행\(대면&비대면 동시\)\s+병행\(대면&비대면 동시\)\s+)?미정",
        "", text, flags=re.I,
    )
    return clean_text(text)


def normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class RankedChunk:
    course_code: str
    text: str
    section: str = "fixed_window"
    course_name: str = ""


def load_corpus(path: Path) -> list[dict[str, str]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or not str(row.get("course_code", "")).strip():
            raise ValueError(f"Invalid corpus course at line {line_number}")
        if not isinstance(row.get("text"), str) or not row["text"].strip():
            raise ValueError(f"Missing corpus text at line {line_number}")
        rows.append({
            "course_code": str(row["course_code"]).strip(),
            "course_name": str(row.get("course_name", "")).strip(),
            "text": sanitize_text(row["text"]),
        })
    if not rows:
        raise ValueError("Corpus must contain at least one document")
    return rows


def validate_cases(payload: Any, corpus: list[dict[str, str]]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Labels require schema_version=1")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Labels require a nonempty cases list")
    by_code: dict[str, list[tuple[str, str]]] = {}
    for row in corpus:
        by_code.setdefault(row["course_code"], []).extend(split_sections(row["text"]))
    seen_ids = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"]:
            raise ValueError("Every case needs an id")
        if case["id"] in seen_ids:
            raise ValueError(f"Duplicate case id: {case['id']}")
        seen_ids.add(case["id"])
        if not isinstance(case.get("question"), str) or not tokenize(case["question"]):
            raise ValueError(f"Missing usable question: {case['id']}")
        expected = case.get("expected_courses")
        if not isinstance(expected, list) or not expected:
            raise ValueError(f"Missing positive course labels: {case['id']}")
        codes = []
        for label in expected:
            if not isinstance(label, dict) or label.get("course_code") not in by_code:
                raise ValueError(f"Unknown expected course: {case['id']}")
            code = label["course_code"]
            if code in codes:
                raise ValueError(f"Duplicate expected course: {case['id']}")
            codes.append(code)
            evidence = label.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise ValueError(f"Missing evidence labels: {case['id']}")
            for item in evidence:
                if not isinstance(item, dict) or not isinstance(item.get("section"), str):
                    raise ValueError(f"Missing evidence section: {case['id']}")
                snippets = item.get("snippets")
                if not isinstance(snippets, list) or not snippets:
                    raise ValueError(f"Missing evidence snippets: {case['id']}")
                matching_sections = [normalized(text) for section, text in by_code[code]
                                     if section == item["section"]]
                for snippet in snippets:
                    if not isinstance(snippet, str) or len(normalized(snippet)) < 8:
                        raise ValueError(f"Evidence snippet too short: {case['id']}")
                    if not any(normalized(snippet) in text for text in matching_sections):
                        raise ValueError(f"Evidence absent from labeled source section: {case['id']}")
        if "expected_codes" in case and set(case["expected_codes"]) != set(codes):
            raise ValueError(f"Conflicting course labels: {case['id']}")
    return cases


def make_chunks(corpus: list[dict[str, str]], method: str, size: int, overlap: int) -> list[RankedChunk]:
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("Require chunk_size > overlap >= 0")
    if method not in {"fixed_length", "section_aware"}:
        raise ValueError(f"Unknown chunking method: {method}")
    result = []
    for row in corpus:
        if method == "fixed_length":
            pieces = []
            for start in range(0, len(row["text"]), size - overlap):
                pieces.append(("fixed_window", row["text"][start:start + size].strip()))
                if start + size >= len(row["text"]):
                    break
        else:
            pieces = [(item.section, item.text) for item in make_section_chunks(
                row["text"], chunk_size=size, overlap=overlap)]
        result.extend(RankedChunk(row["course_code"], text, section, row["course_name"])
                      for section, text in pieces if text)
    return result


def score_case(case: dict[str, Any], ranked: list[RankedChunk], k: int = 5) -> dict[str, Any]:
    if k <= 0:
        raise ValueError("k must be positive")
    labels = case.get("expected_courses")
    if not isinstance(labels, list) or not labels:
        raise ValueError("Cannot score without positive course labels")
    expected = {label["course_code"] for label in labels}
    if len(expected) != len(labels):
        raise ValueError("Duplicate expected course labels")
    top = ranked[:k]
    distinct = list(dict.fromkeys(chunk.course_code for chunk in top))
    found = expected.intersection(distinct)
    evidence_hits = set()
    for label in labels:
        snippets = [normalized(snippet) for item in label["evidence"] for snippet in item["snippets"]]
        if any(chunk.course_code == label["course_code"] and
               any(snippet in normalized(chunk.text) for snippet in snippets) for chunk in top):
            evidence_hits.add(label["course_code"])
    reciprocal_rank = next((1.0 / rank for rank, code in enumerate(distinct, 1) if code in expected), 0.0)
    return {
        "id": case["id"],
        "expected_course_count": len(expected),
        "retrieved_chunk_count": len(top),
        "retrieved_distinct_course_count": len(distinct),
        "distinct_course_recall_at_5": len(found) / len(expected),
        "evidence_course_recall_at_5": len(evidence_hits) / len(expected),
        "unique_course_reciprocal_rank_within_top_5_chunks": reciprocal_rank,
        "all_expected_courses_found": found == expected,
        "all_expected_course_evidence_found": evidence_hits == expected,
    }


def evaluate(corpus: list[dict[str, str]], cases: list[dict[str, Any]], method: str, size: int, overlap: int) -> dict[str, Any]:
    chunks = make_chunks(corpus, method, size, overlap)
    index = BM25Index([tokenize(f"{chunk.course_code} {chunk.course_name}\n{chunk.text}") for chunk in chunks])
    results = []
    for case in cases:
        tokens = tokenize(case["question"])
        scored = [(index.score(tokens, number), number) for number in range(len(chunks))]
        scored = sorted((item for item in scored if item[0] > 0), key=lambda item: (-item[0], item[1]))
        results.append(score_case(case, [chunks[number] for _, number in scored[:5]]))
    return {
        "method": method,
        "chunk_count": len(chunks),
        "case_count": len(results),
        "mean_distinct_course_recall_at_5": statistics.mean(row["distinct_course_recall_at_5"] for row in results),
        "mean_evidence_course_recall_at_5": statistics.mean(row["evidence_course_recall_at_5"] for row in results),
        "mean_unique_course_reciprocal_rank_within_top_5_chunks": statistics.mean(
            row["unique_course_reciprocal_rank_within_top_5_chunks"] for row in results),
        "all_courses_found_case_count": sum(row["all_expected_courses_found"] for row in results),
        "all_course_evidence_found_case_count": sum(row["all_expected_course_evidence_found"] for row in results),
        "cases": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=PROJECT_DIR / "data/processed/syllabus_texts.jsonl")
    parser.add_argument("--labels", type=Path, default=PROJECT_DIR / "evaluation/retrieval_cases.json")
    parser.add_argument("--output", type=Path, default=PROJECT_DIR / "evaluation/retrieval-results.json")
    parser.add_argument("--chunk-size", type=int, default=900)
    parser.add_argument("--overlap", type=int, default=120)
    args = parser.parse_args(argv)
    try:
        corpus = load_corpus(args.corpus)
        labels_payload = json.loads(args.labels.read_text(encoding="utf-8-sig"))
        cases = validate_cases(labels_payload, corpus)
        methods = [evaluate(corpus, cases, method, args.chunk_size, args.overlap)
                   for method in ("fixed_length", "section_aware")]
        payload = {
            "schema_version": 1,
            "label_provenance": "agent-authored diagnostic, NOT a human blind holdout",
            "scope": "BM25 chunking diagnostic only; no answer routing, vector search, LLM, or API calls",
            "corpus_sha256": file_hash(args.corpus),
            "labels_sha256": file_hash(args.labels),
            "benchmark_sha256": file_hash(Path(__file__)),
            "retrieval_core_sha256": file_hash(PROJECT_DIR / "retrieval_core.py"),
            "document_count": len(corpus),
            "distinct_course_count": len({row["course_code"] for row in corpus}),
            "case_count": len(cases),
            "configuration": {"k_chunks": 5, "chunk_size_characters": args.chunk_size,
                              "overlap_characters": args.overlap, "bm25_k1": 1.5, "bm25_b": 0.75,
                              "query_expansion": False, "section_boosts": False,
                              "zero_score_chunks": "excluded", "tie_break": "corpus/chunk order",
                              "index_prefix": "course_code and course_name in both arms"},
            "not_measured": ["vector retrieval", "hybrid retrieval", "LLM answer correctness",
                             "hallucination rate", "human usefulness", "production latency or cost"],
            "methods": methods,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"case_count": len(cases), "methods": [
            {key: value for key, value in item.items() if key != "cases"} for item in methods]},
            ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Benchmark error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
