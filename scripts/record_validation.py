"""Record local checks and optionally private-corpus regression as public aggregates."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(arguments, environment, log_name):
    result = subprocess.run([sys.executable, *arguments], cwd=ROOT, env=environment,
                            capture_output=True, text=True, encoding="utf-8", timeout=180)
    (ROOT / "artifacts" / log_name).write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"Validation failed; see artifacts/{log_name}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-private-regression", action="store_true")
    args = parser.parse_args()
    (ROOT / "artifacts").mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment.update(SUGANG_OFFLINE="1", SUGANG_DATA_PATH="data/sample/syllabus_texts.jsonl",
                       GRADIO_ANALYTICS_ENABLED="False", PYTHONIOENCODING="utf-8")
    tests = run(["-m", "unittest", "discover", "-s", "tests", "-v"], environment, "unit-tests.log")
    verification = run(["scripts/verify_release.py"], environment, "public-verification.log")
    count = re.search(r"Ran (\d+) tests", tests.stderr)
    if not count:
        raise RuntimeError("Unable to determine test count")
    code_paths = [ROOT / p for p in ("app.py", "gradio_app.py", "retrieval_core.py", "run_demo.py")]
    for folder in ("sugang_mate", "tests", "scripts"):
        code_paths += list((ROOT / folder).glob("*.py"))
    record = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.system(), "python": platform.python_version(),
        "unit_tests": {"passed": int(count.group(1)), "failed": 0},
        "public_verification": json.loads(verification.stdout.strip().splitlines()[-1]),
        "code_sha256": {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(code_paths)},
        "requirements_lock_sha256": sha(ROOT / "requirements-lock.txt"),
        "scope": "Local validation only; see GitHub Actions for remote CI results",
        "ci_workflow_url": "https://github.com/JunH14/sugang-mate/actions/workflows/checks.yml",
        "external_model_test_executed": False,
    }
    if args.with_private_regression:
        environment.update(SUGANG_DATA_PATH="data/processed/syllabus_texts.jsonl", ANSWER_CACHE_MAX="0")
        run(["scripts/evaluate_rag.py", "--output-dir", "data/evaluation/release_regression"],
            environment, "private-regression.log")
        source = ROOT / "data/evaluation/release_regression/latest_evaluation.json"
        data = json.loads(source.read_text(encoding="utf-8"))
        summary = data["summary"]
        rows = data.get("results", [])
        regression = {
            "scope": "Existing functional regression assertions, not independent answer factuality",
            "summary": summary, "offline": True, "answer_cache_disabled": True,
            "corpus_sha256": sha(ROOT / "data/processed/syllabus_texts.jsonl"),
            "app_sha256": sha(ROOT / "app.py"),
            "evaluator_sha256": sha(ROOT / "scripts/evaluate_rag.py"),
            "private_result_sha256": sha(source),
            "result_mode_counts": dict(Counter(row.get("mode", "unknown") for row in rows)),
        }
        (ROOT / "evaluation/release-regression.json").write_text(
            json.dumps(regression, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        record["private_regression_passed"] = summary["full_case_accuracy"] == 1.0
    (ROOT / "evaluation/release-checks.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"unit_tests_passed": int(count.group(1)),
                      "public_verification": "passed",
                      "private_regression": record.get("private_regression_passed", "not run")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
