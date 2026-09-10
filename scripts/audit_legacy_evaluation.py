"""Produce aggregate-only provenance audit of existing evaluation JSON files."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parents[1]
RUNS = ("gemini_chroma_baseline_300", "gemini_chroma_improved_300", "gemini_chroma_improved_2500")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def audit_file(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source = path.read_bytes()
    payload = json.loads(source.decode("utf-8-sig"))
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list) or not payload["rows"]:
        raise ValueError(f"Missing nonempty rows in {path.name}")
    rows = payload["rows"]
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("question"), str):
            raise ValueError("Every legacy row must have a question")
        if not isinstance(row.get("passed"), bool):
            raise ValueError("Legacy passed flag must be boolean")
        for key in ("expected_codes", "expected_terms", "allowed_modes"):
            if not isinstance(row.get(key), list):
                raise ValueError(f"Legacy row requires a {key} list")
    declared = payload.get("summary", {})
    passed = sum(row["passed"] for row in rows)
    modes = dict(sorted(Counter(str(row.get("mode", "")) for row in rows).items()))
    models = dict(sorted(Counter(str(row.get("model", "")) for row in rows if row.get("model")).items()))
    aggregate = {
        "input_sha256": hashlib.sha256(source).hexdigest(),
        "case_count": len(rows), "passed_count": passed, "failed_count": len(rows) - passed,
        "pass_rate": passed / len(rows),
        "unique_question_count": len({row["question"] for row in rows}),
        "equivalence_group_count": len({row["equivalence_group"] for row in rows if row.get("equivalence_group")}),
        "mode_counts": modes, "recorded_model_response_counts": models,
        "recorded_model_response_count": sum(models.values()),
        "nonempty_expected_terms_count": sum(bool(row["expected_terms"]) for row in rows),
        "empty_expected_terms_count": sum(not row["expected_terms"] for row in rows),
        "nonempty_allowed_modes_count": sum(bool(row["allowed_modes"]) for row in rows),
        "require_no_sources_count": sum(bool(row.get("require_no_sources")) for row in rows),
        "ordered_questions_sha256": canonical_hash([row["question"] for row in rows]),
        "ordered_expected_codes_sha256": canonical_hash([row["expected_codes"] for row in rows]),
        "summary_count_matches_rows": declared.get("case_count") == len(rows),
        "summary_passed_matches_rows": declared.get("passed") == passed,
        "declared_run_mode": str(declared.get("rag_mode", "")),
    }
    return aggregate, rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-root", type=Path, required=True,
                        help="Existing data/evaluation directory; never copied into the public release")
    parser.add_argument("--output", type=Path, default=PROJECT_DIR / "evaluation/legacy-audit.json")
    args = parser.parse_args(argv)
    try:
        runs = {}
        all_rows = {}
        for name in RUNS:
            runs[name], all_rows[name] = audit_file(args.legacy_root / name / "generated_stress_results.json")
        before, after = (all_rows[name] for name in RUNS[:2])
        payload = {
            "schema_version": 1,
            "scope": "Audit of saved legacy response records; no evaluation rerun or API calls",
            "audit_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "interpretation": [
                "Model response counts are recorded result rows, not actual API request counts; cached results may retain a model name.",
                "Empty expected_terms automatically satisfy that one string-membership check; other assertions still apply.",
                "Legacy pass rate does not measure full answer factuality, independent generalization, or production reliability.",
            ],
            "runs": runs,
            "comparison_300": {
                "ordered_questions_identical": [row["question"] for row in before] == [row["question"] for row in after],
                "ordered_expected_codes_identical": [row["expected_codes"] for row in before] == [row["expected_codes"] for row in after],
                "ordered_scoring_labels_identical": [
                    {key: row.get(key) for key in ("expected_codes", "expected_terms", "allowed_modes", "exact_codes", "require_no_sources", "history")}
                    for row in before] == [
                    {key: row.get(key) for key in ("expected_codes", "expected_terms", "allowed_modes", "exact_codes", "require_no_sources", "history")}
                    for row in after],
            },
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"runs": {name: {key: run[key] for key in ("case_count", "passed_count", "recorded_model_response_count")}
                                   for name, run in runs.items()}, "comparison_300": payload["comparison_300"]}, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Legacy audit error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
