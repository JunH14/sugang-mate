"""Run bounded real-API checks; publish only metrics and keep answers in artifacts.

This command sends synthetic sample excerpts to Google. The separate
--with-private-corpus option also sends local university excerpts and must only
be used with the data owner's permission. It needs google-genai and chromadb
from requirements-rag.txt. API secrets are read only into memory; neither the
key file nor key values are copied into an artifact.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import statistics
import sys
import time
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DETAILS = ROOT / "artifacts/live-api-details.json"
REPORT = ROOT / "evaluation/live-api-checks.json"
DEFAULT_INDEX = ROOT / "data/vector_db/live-validation"
USAGE_FIELDS = (
    "prompt_token_count", "candidates_token_count", "total_token_count",
    "thoughts_token_count", "cached_content_token_count",
)

SAMPLE_CASES = [
    ("S01", "CSV 파일에서 결측치를 처리하려면 어떤 과목이 관련돼?", ["DEMO101"], True),
    ("S02", "교차검증과 데이터 누수를 다루는 과목을 근거와 함께 설명해줘", ["DEMO202"], True),
    ("S03", "텍스트를 감성별로 분류하는 법이 궁금한데 어느 수업과 관련돼?", ["DEMO302"], True),
    ("S04", "대시보드와 시각적 인코딩을 경험할 수 있는 수업은?", ["DEMO301"], True),
    ("S05", "DEMO101과 DEMO302의 차이를 강의계획서 근거로 두 문장으로 요약해줘", ["DEMO101", "DEMO302"], True),
    ("S06", "DEMO401의 선수과목이 선형대수학으로 지정되어 있어?", ["DEMO401"], True),
    ("S07", "DEMO201과 DEMO202의 평가방식을 비교해줘", ["DEMO201", "DEMO202"], False),
    ("S08", "그중 전공필수는 몇 개야?", ["DEMO201"], False),
]
PRIVATE_CASES = [
    ("P01", "BDSC205에서 SAS를 다룬다는 근거가 있어?", ["BDSC205"], True),
    ("P02", "BDSC155의 미분과 적분을 다루는 목적을 설명해줘", ["BDSC155"], True),
    ("P03", "BDSC331은 프로그래밍 경험이 없어도 수강할 수 있어?", ["BDSC331"], True),
    ("P04", "BDSC401의 강의계획서만 근거로 딥러닝이론의 특징을 두 문장으로 요약해줘", ["BDSC401"], True),
    ("P05", "BDSC201에서 사용하는 교재를 반드시 새 책으로 사야 해?", ["BDSC201"], True),
    ("P06", "BDSC121에서 사용하는 프로그래밍 언어가 뭐야?", ["BDSC121"], True),
    ("P07", "BDSC201과 BDSC203의 평가방식을 비교해줘", ["BDSC201", "BDSC203"], False),
    ("P08", "다음 학기 등록금이 얼마야?", [], False),
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def secret_from_environment_file(path: Path | None) -> str:
    if path is None:
        value = os.environ.get("GOOGLE_API_KEY", "").strip()
    else:
        value = ""
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, separator, content = line.strip().partition("=")
            if separator and name.strip() == "GOOGLE_API_KEY":
                value = content.strip().strip('"').strip("'")
                break
    if not value:
        raise RuntimeError("A GOOGLE_API_KEY is required; no secret value was printed")
    return value


def error_metadata(error: Exception) -> dict:
    code = getattr(error, "code", None) or getattr(error, "status_code", None)
    status = int(code) if str(code).isdigit() and 100 <= int(code) <= 599 else None
    metadata = {"error_type": type(error).__name__, "http_status": status}
    message = str(getattr(error, "message", "") or "").casefold()
    metadata["message_categories"] = [label for label, markers in {
        "daily_quota": ("per day", "daily", "per_day"),
        "minute_quota": ("per minute", "per_minute"),
        "quota": ("quota",), "throttling": ("throttl", "rate limit"),
        "capacity": ("capacity", "overload"),
    }.items() if any(marker in message for marker in markers)]
    details = getattr(error, "details", {})
    if isinstance(details, dict):
        for detail in details.get("error", details).get("details", []):
            if not isinstance(detail, dict):
                continue
            delay = str(detail.get("retryDelay", ""))
            if re.fullmatch(r"\d+(?:\.\d+)?s", delay):
                metadata["retry_after_seconds"] = float(delay[:-1])
            quotas = []
            for violation in detail.get("violations", []):
                safe = {key: str(violation[key]) for key in ("quotaMetric", "quotaId", "quotaValue")
                        if key in violation and re.fullmatch(r"[A-Za-z0-9_./-]{1,200}", str(violation[key]))}
                if safe:
                    quotas.append(safe)
            if quotas:
                metadata["quotas"] = quotas
    return metadata


class MeasuredModels:
    """Count SDK dispatches before calls, including failures; SDK retries are off."""

    def __init__(self, models, limit: int):
        self.models = models
        self.limit = limit
        self.phase = "setup"
        self.events: list[dict] = []
        self.private_prompts: dict[int, str] = {}

    def _call(self, operation: str, kwargs: dict):
        if len(self.events) >= self.limit:
            raise RuntimeError("Live validation API request budget exhausted")
        config = kwargs.get("config")
        purpose = getattr(config, "task_type", None)
        if operation == "generate_content":
            purpose = {80: "query_rewrite", 220: "followup_rewrite", 700: "answer"}.get(
                getattr(config, "max_output_tokens", None), "generation"
            )
        event = {"id": len(self.events) + 1, "phase": self.phase,
                 "operation": operation, "purpose": str(purpose or "unknown"),
                 "model": kwargs.get("model"), "status": "started"}
        self.events.append(event)
        if operation == "generate_content" and isinstance(kwargs.get("contents"), str):
            self.private_prompts[event["id"]] = kwargs["contents"]
        started = time.perf_counter()
        try:
            response = getattr(self.models, operation)(**kwargs)
            event["status"] = "success"
            usage = getattr(response, "usage_metadata", None)
            event["usage"] = {name: getattr(usage, name) for name in USAGE_FIELDS
                              if getattr(usage, name, None) is not None}
            if operation == "generate_content":
                event["model_version"] = getattr(response, "model_version", None)
                event["nonempty_text"] = bool(getattr(response, "text", ""))
            else:
                embeddings = getattr(response, "embeddings", None) or []
                event["vector_count"] = len(embeddings)
                event["dimensions"] = sorted({len(item.values or []) for item in embeddings})
                event["finite_vectors"] = all(
                    math.isfinite(value) for item in embeddings for value in (item.values or [])
                )
                billable = getattr(getattr(response, "metadata", None), "billable_character_count", None)
                event["billable_character_count"] = billable
            return response
        except Exception as error:
            event.update(status="failed", **error_metadata(error))
            raise
        finally:
            event["seconds"] = round(time.perf_counter() - started, 3)

    def generate_content(self, **kwargs):
        return self._call("generate_content", kwargs)

    def embed_content(self, **kwargs):
        return self._call("embed_content", kwargs)


def build_private_index(core, measured: MeasuredModels, client, index_dir: Path, batch_size: int) -> dict:
    import chromadb
    from build_vector_db import load_records, make_chunks, embed_batch

    records = load_records(ROOT)
    chunks = make_chunks(records)
    index_dir.mkdir(parents=True, exist_ok=True)
    chroma = chromadb.PersistentClient(path=str(index_dir))
    collection = chroma.get_or_create_collection(
        name=core.CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine", "embedding_model": core.EMBEDDING_MODEL},
    )
    fingerprint = core.dataset_fingerprint(core.resolve_data_path(ROOT))
    metadata = collection.metadata or {}
    reused = (collection.count() == len(chunks) and metadata.get("dataset_sha256") == fingerprint
              and metadata.get("embedding_model") == core.EMBEDDING_MODEL)
    if not reused:
        if metadata.get("embedding_model") != core.EMBEDDING_MODEL:
            raise RuntimeError("Validation index uses another embedding model; select a new directory")
        existing = collection.get(include=["documents"])
        existing_docs = dict(zip(existing.get("ids", []), existing.get("documents", [])))
        expected_docs = {chunk.chunk_id: chunk.document for chunk in chunks}
        if any(expected_docs.get(chunk_id) != document for chunk_id, document in existing_docs.items()):
            raise RuntimeError("Partial index contains another dataset; select a new directory")
        pending = [chunk for chunk in chunks if chunk.chunk_id not in existing_docs]
        collection.modify(metadata={"embedding_model": core.EMBEDDING_MODEL,
                                    "dataset_sha256": "building"})
        measured.phase = "index_build"
        for start in range(0, len(pending), batch_size):
            if start:
                # Sequential batches with pacing avoid bursts against free-tier quotas.
                print("Index: pacing the next embedding batch (35 seconds)", flush=True)
                time.sleep(35)
            batch = pending[start:start + batch_size]
            vectors = embed_batch(client, [chunk.document for chunk in batch], "RETRIEVAL_DOCUMENT")
            collection.add(ids=[chunk.chunk_id for chunk in batch],
                           documents=[chunk.document for chunk in batch],
                           metadatas=[chunk.metadata for chunk in batch], embeddings=vectors)
            print(f"Index: {collection.count()}/{len(chunks)} chunks", flush=True)
        if collection.count() != len(chunks):
            raise RuntimeError("Completed index count does not match the source dataset")
        collection.modify(metadata={"embedding_model": core.EMBEDDING_MODEL,
                                    "dataset_sha256": fingerprint})
    return {"course_count": len(records), "chunk_count": len(chunks),
            "dataset_sha256": fingerprint, "embedding_model": core.EMBEDDING_MODEL,
            "index_count": collection.count(), "reused_verified_index": reused,
            "batch_size": batch_size, "batch_spacing_seconds": 35,
            "resumed_chunk_count": len(existing_docs) if not reused else len(chunks), "status": "ready"}


def check_cases(core, measured: MeasuredModels, rag, cases: list, dataset: str) -> list[dict]:
    core.RAG = rag  # Explicit-course prioritization uses the app's current corpus.
    results = []
    for case_id, question, expected, generated in cases:
        measured.phase = case_id
        before = len(measured.events)
        history = []
        if case_id == "S08":
            previous = next(row for row in results if row["id"] == "S07")
            history = [{"role": "assistant", "content": previous["result"].get("answer", "")}]
        started = time.perf_counter()
        failure = None
        try:
            answer = rag.answer(question, history)
        except Exception as error:
            answer = {}
            failure = error_metadata(error)
        elapsed = round(time.perf_counter() - started, 3)
        calls = measured.events[before:]
        successful_generation = [call for call in calls if call["operation"] == "generate_content"
                                 and call["purpose"] == "answer" and call["status"] == "success"
                                 and call.get("nonempty_text")]
        successful_embedding = [call for call in calls if call["operation"] == "embed_content"
                                and call["status"] == "success" and call.get("finite_vectors")
                                and call.get("vector_count") == 1]
        actual = sorted({source["course_code"] for source in answer.get("sources", [])})
        checks = {
            "nonempty_answer": bool(answer.get("answer")),
            "expected_source_set": actual == sorted(expected),
            "cache_disabled": not answer.get("timings", {}).get("cache_hit", False),
            "generation_dispatch_matches_expected": bool(successful_generation) == generated,
            "no_failed_sdk_calls": all(call["status"] == "success" for call in calls),
            "expected_query_embedding": bool(successful_embedding) == (generated and dataset == "private"),
            "expected_generation_mode": (answer.get("mode") == (
                "chroma_gemini" if dataset == "private" else "keyword_gemini"
            )) if generated else answer.get("mode") not in {"keyword_gemini", "chroma_gemini"},
        }
        row = {"id": case_id, "dataset": dataset, "question": question,
               "expected_sources": expected, "actual_sources": actual,
               "expected_generation": generated, "mode": answer.get("mode"),
               "wall_seconds": elapsed, "sdk_event_ids": [call["id"] for call in calls],
               "checks": checks, "technical_passed": failure is None and all(checks.values()),
               "result": answer, "exception": failure,
               "generation_prompts": [measured.private_prompts[call["id"]] for call in calls
                                      if call["id"] in measured.private_prompts]}
        results.append(row)
        print(f"{case_id}: {row['mode']} technical_passed={row['technical_passed']} sdk_calls={len(calls)}", flush=True)
    return results


def publish_summary(details: dict, review_path: Path | None = None) -> dict:
    reviews = {}
    if review_path is not None:
        review = json.loads(review_path.read_text(encoding="utf-8"))
        if review.get("details_sha256") != sha(DETAILS):
            raise ValueError("Manual review belongs to a different detailed run")
        reviews = {row["id"]: row for row in review.get("cases", [])}
    events = details.get("sdk_events", [])
    cases = []
    for row in details.get("cases", []):
        public = {key: row[key] for key in (
            "id", "dataset", "question", "expected_sources", "actual_sources", "expected_generation",
            "mode", "wall_seconds", "sdk_event_ids", "checks", "technical_passed", "exception",
        )}
        public["source_review"] = reviews.get(row["id"], {"status": "not_reviewed"})
        cases.append(public)
    timings = [row["wall_seconds"] for row in cases if row["expected_generation"]]
    report = {
        "schema_version": 1, "run_started_at_utc": details["run_started_at_utc"],
        "run_finished_at_utc": details["run_finished_at_utc"],
        "environment": details["environment"], "settings": details["settings"],
        "artifacts": {"private_details_sha256": sha(DETAILS), **details["source_hashes"]},
        "index": details.get("index"), "run_error": details.get("run_error"),
        "summary": {
            "case_count": len(cases), "technical_passed": sum(row["technical_passed"] for row in cases),
            "source_reviewed": sum(row["source_review"].get("status") != "not_reviewed" for row in cases),
            "source_review_passed": sum(row["source_review"].get("status") == "passed" for row in cases),
            "sdk_dispatch_count": len(events),
            "sdk_success_count": sum(event["status"] == "success" for event in events),
            "sdk_failure_count": sum(event["status"] != "success" for event in events),
            "operations": dict(Counter(event["operation"] for event in events)),
            "token_usage_reported_by_sdk": {field: sum(event.get("usage", {}).get(field, 0) for event in events)
                                            for field in USAGE_FIELDS
                                            if any(field in event.get("usage", {}) for event in events)},
            "generation_cases_wall_median_seconds": round(statistics.median(timings), 3) if timings else None,
            "generation_cases_wall_max_seconds": max(timings) if timings else None,
        },
        "cases": cases, "sdk_events": events,
        "limitations": [
            "Small developer-authored diagnostic set; not an independent holdout or user study.",
            "Source review is AI-assisted comparison against the local corpus, not blind human annotation.",
            "Technical checks cover observed SDK dispatches, source sets and routing, not complete answer factuality.",
            "SDK retries are disabled; counts represent dispatches made by this script, not billing records.",
            "Embedding usage metadata may be absent; missing usage is not interpreted as zero cost.",
            "Latency includes local processing and network time for sequential requests in this one run.",
            "Full university documents, prompts and answers remain in ignored local paths.",
        ],
    }
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Explicitly allow the real API diagnostic run")
    parser.add_argument("--with-private-corpus", action="store_true",
                        help="Also send local university excerpts to Google; requires permission")
    parser.add_argument("--continue-private", action="store_true",
                        help="Extend the preceding sample-only run without repeating sample calls")
    parser.add_argument("--key-env", type=Path, help="Read only GOOGLE_API_KEY from this local file")
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--index-batch-size", type=int, default=16)
    parser.add_argument("--max-calls", type=int, default=50)
    parser.add_argument("--review-file", type=Path, help="Optional source review bound to the details SHA256")
    parser.add_argument("--summarize-only", action="store_true", help="Refresh public summary with no API calls")
    args = parser.parse_args()
    if args.summarize_only:
        report = publish_summary(json.loads(DETAILS.read_text(encoding="utf-8")), args.review_file)
        print(json.dumps(report["summary"], ensure_ascii=False))
        return 0
    if not args.run:
        parser.error("Use --run to opt in to sending excerpts to the real API")
    if not 1 <= args.max_calls <= 50:
        parser.error("--max-calls must be between 1 and 50")
    if not 1 <= args.index_batch_size <= 32:
        parser.error("--index-batch-size must be between 1 and 32")
    previous = None
    if args.continue_private:
        if not args.with_private_corpus:
            parser.error("--continue-private requires --with-private-corpus")
        previous = json.loads(DETAILS.read_text(encoding="utf-8"))
        if (len(previous.get("cases", [])) != len(SAMPLE_CASES)
                or {row["dataset"] for row in previous["cases"]} != {"sample"}):
            parser.error("Only a completed sample set without private question results can be extended")
        if (previous["source_hashes"]["app_sha256"] != sha(ROOT / "app.py")
                or previous["source_hashes"]["sample_dataset_sha256"] != sha(ROOT / "data/sample/syllabus_texts.jsonl")):
            parser.error("Code or sample data changed; run fresh checks instead of combining versions")
    index_dir = args.index_dir.resolve()
    if not index_dir.is_relative_to((ROOT / "data/vector_db").resolve()):
        parser.error("The index must remain within ignored data/vector_db")
    # Importing the app must not implicitly connect using local configuration.
    os.environ.update(SUGANG_OFFLINE="1", SUGANG_DATA_PATH="data/sample/syllabus_texts.jsonl",
                      ANSWER_CACHE_MAX="0", GEMINI_MODEL="gemini-3.1-flash-lite",
                      GEMINI_FALLBACK_MODELS="", GEMINI_RETRY_ATTEMPTS="1", GEMINI_TIMEOUT_SECONDS="20",
                      GRADIO_ANALYTICS_ENABLED="False", ANONYMIZED_TELEMETRY="False")
    import app as core
    from google import genai
    from google.genai import types

    key = secret_from_environment_file(args.key_env)
    actual_client = genai.Client(api_key=key, http_options=types.HttpOptions(
        timeout=20_000, retry_options=types.HttpRetryOptions(attempts=1)))
    del key
    measured = MeasuredModels(actual_client.models, args.max_calls)
    client = SimpleNamespace(models=measured)
    details = {
        "run_started_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {"python": platform.python_version(), "platform": platform.system(),
                        "google_genai": importlib.metadata.version("google-genai"),
                        "chromadb": importlib.metadata.version("chromadb")},
        "settings": {"generation_model": core.GENERATIVE_MODEL, "embedding_model": core.EMBEDDING_MODEL,
                     "timeout_seconds": 20, "sdk_attempts": 1, "app_attempts": 1,
                     "max_sdk_dispatches": args.max_calls, "answer_cache_disabled": True,
                     "index_batch_size": args.index_batch_size,
                     "sample_search": "BM25", "private_search": "Chroma + BM25 + MMR",
                     "with_private_corpus": args.with_private_corpus},
        "source_hashes": {"app_sha256": sha(ROOT / "app.py"), "validator_sha256": sha(Path(__file__)),
                          "sample_dataset_sha256": sha(ROOT / "data/sample/syllabus_texts.jsonl")},
        "cases": [],
    }
    if previous:
        details["run_started_at_utc"] = previous["run_started_at_utc"]
        details["continued_private_at_utc"] = datetime.now(timezone.utc).isoformat()
        details["source_hashes"]["sample_validator_sha256"] = previous["source_hashes"].get(
            "sample_validator_sha256", previous["source_hashes"]["validator_sha256"])
        details["cases"] = previous["cases"]
        measured.events.extend(previous["sdk_events"])
    try:
        if not previous:
            sample = core.SyllabusRag(ROOT / "data/sample/syllabus_texts.jsonl")
            sample.offline = False
            sample.gemini_client = client
            sample.rag_mode = sample._detect_mode()
            details["cases"].extend(check_cases(core, measured, sample, SAMPLE_CASES, "sample"))
        if args.with_private_corpus:
            details["source_hashes"]["private_dataset_sha256"] = sha(ROOT / "data/processed/syllabus_texts.jsonl")
            os.environ["SUGANG_DATA_PATH"] = "data/processed/syllabus_texts.jsonl"
            details["index"] = build_private_index(core, measured, client, index_dir, args.index_batch_size)
            private = core.SyllabusRag(ROOT / "data/processed/syllabus_texts.jsonl")
            private.offline = False
            private.gemini_client = client
            private.chroma_dir = index_dir
            private.chroma_collection = private._load_chroma_collection()
            private.rag_mode = private._detect_mode()
            if private.rag_mode != "chroma_gemini":
                raise RuntimeError("Fresh vector index was not accepted by the real app")
            details["cases"].extend(check_cases(core, measured, private, PRIVATE_CASES, "private"))
    except Exception as error:
        details["run_error"] = error_metadata(error)
    finally:
        details["sdk_events"] = measured.events
        details["run_finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        DETAILS.parent.mkdir(exist_ok=True)
        DETAILS.write_text(json.dumps(details, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        actual_client.close()
    report = publish_summary(details, args.review_file)
    print(json.dumps(report["summary"], ensure_ascii=False))
    return int(bool(details.get("run_error")) or any(not row["technical_passed"] for row in details["cases"]))


if __name__ == "__main__":
    raise SystemExit(main())
