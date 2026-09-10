"""Start the public, synthetic-data demo without external model requests."""
import os
from pathlib import Path
os.environ["SUGANG_OFFLINE"] = "1"
os.environ["SUGANG_DATA_PATH"] = str(Path(__file__).resolve().parent / "data/sample/syllabus_texts.jsonl")
os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"

from gradio_app import main

if __name__ == "__main__":
    main()

