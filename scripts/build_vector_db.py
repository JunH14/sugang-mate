from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from retrieval_core import make_section_chunks
from sugang_mate.config import dataset_fingerprint, offline_enabled, resolve_data_path, resolve_chroma_dir

COLLECTION_NAME = "ku_sejong_bigdata_syllabi_v2"
EMBEDDING_MODEL = "gemini-embedding-001"


@dataclass
class SyllabusRecord:
    course_code: str
    class_no: str
    course_name: str
    professor: str
    completion_type: str
    credit: str
    class_hours: str
    schedule_summary: str
    schedule_entries_json: str
    syllabus_url: str
    text_path: str
    sources: str
    text: str


@dataclass
class ChunkRecord:
    chunk_id: str
    document: str
    metadata: dict[str, str | int | float | bool]


def load_dotenv(project_dir: Path) -> None:
    env_path = project_dir / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def clean_text(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def sanitize_retrieval_text(text: str) -> str:
    text = clean_text(text)
    text = re.sub(
        r"활동유형.*?(?=(?:▷\s*)?평가방법)",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"수업유형\s+(?:대면\s+대면\s+)?(?:비대면\s+비대면\s+)?"
        r"(?:병행\(대면&비대면 동시\)\s+병행\(대면&비대면 동시\)\s+)?미정",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return clean_text(text)


def load_records(project_dir: Path) -> list[SyllabusRecord]:
    data_path = resolve_data_path(project_dir)
    records: list[SyllabusRecord] = []
    with data_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            records.append(
                SyllabusRecord(
                    course_code=str(item.get("course_code", "")),
                    class_no=str(item.get("class_no", "")),
                    course_name=str(item.get("course_name", "")),
                    professor=str(item.get("professor", "")),
                    completion_type=str(item.get("completion_type", "")),
                    credit=str(item.get("credit", "")),
                    class_hours=str(item.get("class_hours", "")),
                    schedule_summary=str(item.get("schedule_summary", "")),
                    schedule_entries_json=json.dumps(
                        item.get("schedule_entries", []), ensure_ascii=False
                    ),
                    syllabus_url=str(item.get("syllabus_url", "")),
                    text_path=str(item.get("text_path", "")),
                    sources=str(item.get("sources", "")),
                    text=sanitize_retrieval_text(str(item.get("text", ""))),
                )
            )
    return records


def make_chunks(records: list[SyllabusRecord]) -> list[ChunkRecord]:
    chunks: list[ChunkRecord] = []
    for record in records:
        header = "\n".join(
            [
                f"과목명: {record.course_name}",
                f"학수번호: {record.course_code}",
                f"분반: {record.class_no}",
                f"교수명: {record.professor}",
                f"이수구분: {record.completion_type}",
                f"학점: {record.credit}",
                f"학점/시수: {record.class_hours}",
                f"수업시간 및 강의실: {record.schedule_summary or '미정 또는 별도 운영'}",
            ]
        )
        for section_chunk in make_section_chunks(record.text, chunk_size=900, overlap=120):
            index = len(chunks)
            chunk_id = f"{record.course_code}_{record.class_no}_{index:04d}"
            document = (
                f"{header}\n문서 구역: {section_chunk.section}\n\n"
                f"{section_chunk.text}"
            )
            chunks.append(
                ChunkRecord(
                    chunk_id=chunk_id,
                    document=document,
                    metadata={
                        "chunk_id": chunk_id,
                        "course_code": record.course_code,
                        "class_no": record.class_no,
                        "course_name": record.course_name,
                        "professor": record.professor,
                        "completion_type": record.completion_type,
                        "credit": record.credit,
                        "class_hours": record.class_hours,
                        "schedule_summary": record.schedule_summary,
                        "schedule_entries_json": record.schedule_entries_json,
                        "syllabus_url": record.syllabus_url,
                        "text_path": record.text_path,
                        "sources": record.sources,
                        "section": section_chunk.section,
                        "parent_text": section_chunk.parent_text,
                        "chunk_index": index,
                    },
                )
            )
    return chunks


def embedding_values(response: Any) -> list[list[float]]:
    embeddings = getattr(response, "embeddings", None)
    if embeddings is None and isinstance(response, dict):
        embeddings = response.get("embeddings")
    values: list[list[float]] = []
    for item in embeddings or []:
        vector = getattr(item, "values", None)
        if vector is None and isinstance(item, dict):
            vector = item.get("values")
        if vector is not None:
            values.append([float(value) for value in vector])
    return values


def embed_batch(client: Any, texts: list[str], task_type: str) -> list[list[float]]:
    from google.genai import types

    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(task_type=task_type),
    )
    vectors = embedding_values(response)
    if len(vectors) != len(texts):
        raise RuntimeError(f"Embedding count mismatch: expected {len(texts)}, got {len(vectors)}")
    return vectors


def embed_batch_with_retry(
    client: Any,
    texts: list[str],
    task_type: str,
    max_retries: int,
    retry_seconds: float,
) -> list[list[float]]:
    for attempt in range(max_retries + 1):
        try:
            return embed_batch(client, texts, task_type=task_type)
        except Exception as error:
            message = str(error)
            is_rate_limit = "429" in message or "RESOURCE_EXHAUSTED" in message
            if not is_rate_limit or attempt >= max_retries:
                raise
            wait_seconds = retry_seconds * (attempt + 1)
            print(
                f"Rate limit reached. Waiting {wait_seconds:.0f}s before retry "
                f"{attempt + 1}/{max_retries}...",
                flush=True,
            )
            time.sleep(wait_seconds)
    raise RuntimeError("Embedding retry loop exited unexpectedly.")


def build(
    project_dir: Path,
    batch_size: int,
    sleep_seconds: float,
    max_retries: int,
    retry_seconds: float,
    reset: bool,
) -> None:
    load_dotenv(project_dir)
    if offline_enabled():
        raise RuntimeError("SUGANG_OFFLINE=1: external embeddings are disabled. Set SUGANG_OFFLINE=0 explicitly to build the vector index.")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY is not set. Create a .env file with GOOGLE_API_KEY=... or set the environment variable."
        )

    import chromadb
    from google import genai

    records = load_records(project_dir)
    chunks = make_chunks(records)
    persist_dir = resolve_chroma_dir(project_dir)
    persist_dir.mkdir(parents=True, exist_ok=True)

    client = genai.Client(api_key=api_key)
    chroma_client = chromadb.PersistentClient(path=str(persist_dir))
    if reset:
        try:
            chroma_client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
    collection = chroma_client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={
            "hnsw:space": "cosine",
            "embedding_model": EMBEDDING_MODEL,
            "description": "KU Sejong Big Data Science syllabi",
        },
    )
    metadata = collection.metadata or {}
    if metadata.get("embedding_model") != EMBEDDING_MODEL:
        raise RuntimeError("Existing index uses another embedding model. Rebuild with --reset.")
    # Invalidate before mutating so a partial rebuild cannot look current.
    collection.modify(metadata={"embedding_model": EMBEDDING_MODEL, "dataset_sha256": "building"})

    try:
        existing = collection.get(include=["documents"])
        existing_ids = set(existing.get("ids", []))
        existing_documents = dict(
            zip(existing.get("ids", []), existing.get("documents", []))
        )
    except Exception:
        existing_ids = set()
        existing_documents = {}
    expected_ids = {chunk.chunk_id for chunk in chunks}
    stale_ids = sorted(existing_ids - expected_ids)
    if stale_ids:
        collection.delete(ids=stale_ids)
        existing_ids.difference_update(stale_ids)
        print(f"Removed {len(stale_ids)} stale chunks from the collection.", flush=True)
    changed_ids = [
        chunk.chunk_id
        for chunk in chunks
        if chunk.chunk_id in existing_ids
        and str(existing_documents.get(chunk.chunk_id, "")) != chunk.document
    ]
    if changed_ids:
        collection.delete(ids=changed_ids)
        existing_ids.difference_update(changed_ids)
        print(f"Removed {len(changed_ids)} changed chunks for re-embedding.", flush=True)
    pending_chunks = [chunk for chunk in chunks if chunk.chunk_id not in existing_ids]
    completed_count = len(chunks) - len(pending_chunks)

    print(
        f"Embedding {len(pending_chunks)} pending chunks from {len(records)} courses "
        f"({completed_count}/{len(chunks)} already present)...",
        flush=True,
    )
    for start in range(0, len(pending_chunks), batch_size):
        batch = pending_chunks[start : start + batch_size]
        texts = [item.document for item in batch]
        embeddings = embed_batch_with_retry(
            client,
            texts,
            task_type="RETRIEVAL_DOCUMENT",
            max_retries=max_retries,
            retry_seconds=retry_seconds,
        )
        collection.add(
            ids=[item.chunk_id for item in batch],
            documents=texts,
            metadatas=[item.metadata for item in batch],
            embeddings=embeddings,
        )
        completed_count += len(batch)
        print(f"Added {completed_count}/{len(chunks)} chunks", flush=True)
        if sleep_seconds and start + batch_size < len(pending_chunks):
            time.sleep(sleep_seconds)

    manifest = {
        "collection_name": COLLECTION_NAME,
        "embedding_model": EMBEDDING_MODEL,
        "course_count": len(records),
        "chunk_count": len(chunks),
        "dataset_sha256": dataset_fingerprint(resolve_data_path(project_dir)),
    }
    if collection.count() != len(chunks):
        raise RuntimeError("Index count does not match the completed dataset")
    collection.modify(metadata={"embedding_model": EMBEDDING_MODEL, "dataset_sha256": manifest["dataset_sha256"]})
    (persist_dir.parent / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Done. collection={COLLECTION_NAME} chunks={len(chunks)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Chroma vector DB with Gemini embeddings.")
    parser.add_argument("--project-dir", type=Path, default=PROJECT_DIR)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--sleep", type=float, default=20.0)
    parser.add_argument("--max-retries", type=int, default=8)
    parser.add_argument("--retry-seconds", type=float, default=75.0)
    parser.add_argument("--reset", action="store_true", help="Delete the existing Chroma collection first.")
    args = parser.parse_args()
    build(
        project_dir=args.project_dir.resolve(),
        batch_size=args.batch_size,
        sleep_seconds=args.sleep,
        max_retries=args.max_retries,
        retry_seconds=args.retry_seconds,
        reset=args.reset,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
