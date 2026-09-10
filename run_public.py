"""Start the hosted Gemini demo with a fixed synthetic dataset and safeguards."""
import os
from pathlib import Path


def configure_public_environment() -> None:
    root = Path(__file__).resolve().parent
    os.environ.update({
        "SUGANG_PUBLIC_DEMO": "1",
        "SUGANG_OFFLINE": "0",
        "SUGANG_DATA_PATH": str(root / "data/sample/syllabus_texts.jsonl"),
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
