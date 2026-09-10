"""Portable configuration, with an explicit offline boundary.

Only this release's .env is read. Environment variables supplied by the caller
take precedence; relative dataset and index paths are relative to the release.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


def load_dotenv(project_dir: Path = PROJECT_DIR) -> None:
    """Load simple KEY=value entries without traversing parent directories."""
    env_path = project_dir / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def offline_enabled() -> bool:
    return os.environ.get("SUGANG_OFFLINE", "0").strip().casefold() in {
        "1", "true", "yes", "on"
    }


def _configured_path(name: str, project_dir: Path) -> Path | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (project_dir / path).resolve()


def resolve_data_path(project_dir: Path = PROJECT_DIR) -> Path:
    configured = _configured_path("SUGANG_DATA_PATH", project_dir)
    if configured is not None:
        # An explicit missing path should fail clearly instead of selecting data
        # the caller did not request.
        return configured
    processed = project_dir / "data" / "processed" / "syllabus_texts.jsonl"
    if processed.is_file():
        return processed
    return project_dir / "data" / "sample" / "syllabus_texts.jsonl"


def resolve_chroma_dir(project_dir: Path = PROJECT_DIR) -> Path:
    return _configured_path("SUGANG_CHROMA_DIR", project_dir) or (
        project_dir / "data" / "vector_db" / "chroma"
    )


def dataset_fingerprint(data_path: Path) -> str:
    """Content identity used to reject indexes built from another dataset."""
    digest = hashlib.sha256()
    with data_path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
