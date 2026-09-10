"""Start the hosted demo with a verified server dataset and bounded API usage."""
import hashlib
import json
import os
from pathlib import Path
from sugang_mate.config import load_dotenv


def public_dataset(root: Path) -> tuple[Path, str, str]:
    """Fail closed if the collected snapshot is missing or differs from review."""
    mode = os.environ.get("SUGANG_PUBLIC_DATA_MODE", "collected").strip()
    if mode == "sample":
        path = root / "data/sample/syllabus_texts.jsonl"
    elif mode == "collected":
        path = Path(os.environ.get("SUGANG_PUBLIC_DATA_PATH", "/etc/secrets/sugang-syllabi.jsonl"))
        if not path.is_absolute():
            path = root / path
    else:
        raise ValueError("SUGANG_PUBLIC_DATA_MODE must be collected or sample")
    if not path.is_file():
        raise ValueError("Hosted dataset missing. Install the prepared server secret file or explicitly select sample mode.")
    raw = path.read_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    records = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    if not records or any(not row.get("course_code") or not row.get("text") for row in records):
        raise ValueError("Hosted dataset has incomplete course records")
    if mode == "collected":
        report = json.loads((root / "evaluation/hosted-data-checks.json").read_text(encoding="utf-8"))
        if fingerprint != report["hosted_dataset_sha256"] or len(records) != report["document_count"]:
            raise ValueError("Hosted dataset does not match the reviewed snapshot")
    return path.resolve(), mode, fingerprint


def configure_public_environment() -> None:
    root = Path(__file__).resolve().parent
    load_dotenv(root)
    path, mode, fingerprint = public_dataset(root)
    os.environ.update({
        "SUGANG_PUBLIC_DEMO": "1",
        "SUGANG_OFFLINE": "0",
        "SUGANG_DATA_PATH": str(path),
        "SUGANG_PUBLIC_DATA_MODE": mode,
        "SUGANG_PUBLIC_DATA_SHA256": fingerprint,
        "SUGANG_CHROMA_DIR": str(root / "data/vector_db/public-demo"),
        "ADMIN_PASSWORD": "",
        "GRADIO_ANALYTICS_ENABLED": "False",
        "GEMINI_RETRY_ATTEMPTS": "1",
        "GEMINI_FALLBACK_MODELS": "",
    })
    os.environ.setdefault("GEMINI_TIMEOUT_SECONDS", "20")
    os.environ.setdefault("GRADIO_SERVER_NAME", "0.0.0.0")
    os.environ.setdefault("ANSWER_CACHE_MAX", "128")


if __name__ == "__main__":
    configure_public_environment()
    from gradio_app import main
    import app as core

    if core.RAG.gemini_client is None:
        raise SystemExit("Public demo requires a configured Gemini client. Check server secrets and dependencies.")
    main()
