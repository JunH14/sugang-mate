"""Verify the public demo without a key or a private dataset."""
from __future__ import annotations
import ast
import json
import os
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SUGANG_OFFLINE"] = "1"
os.environ["SUGANG_DATA_PATH"] = str(ROOT / "data/sample/syllabus_texts.jsonl")
os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"


def main() -> int:
    required = ["README.md", ".env.example", ".gitignore", "requirements.txt",
                "requirements-lock.txt", "gradio_app.py", "app.py", "retrieval_core.py",
                "docs/portfolio.md", "docs/evaluation.md", "docs/data-quality.md",
                "data/sample/syllabus_texts.jsonl", ".github/workflows/checks.yml", "run_demo.py",
                "docs/assets/demo-comparison.png"]
    for filename in required:
        path = ROOT / filename
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Missing required release file: {filename}")
    sources = list((ROOT / "sugang_mate").glob("*.py")) + list((ROOT / "scripts").glob("*.py"))
    sources += list((ROOT / "tests").glob("*.py"))
    sources += [ROOT / name for name in ("app.py", "gradio_app.py", "retrieval_core.py")]
    for path in sources:
        ast.parse(path.read_text(encoding="utf-8-sig"), filename=path.name)
    # Documentation is part of the release: local links must remain usable.
    for document in [ROOT / "README.md", *list((ROOT / "docs").glob("*.md"))]:
        for target in re.findall(r"\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            if target.startswith(("https://", "http://", "#", "mailto:")):
                continue
            target = target.split("#", 1)[0]
            # Validation reports are produced immediately after this verifier runs.
            if target.endswith(("release-checks.json", "release-regression.json")):
                continue
            if not (document.parent / target).exists():
                raise RuntimeError(f"Broken documentation link: {document.name} -> {target}")
    import app
    assert len(app.RAG.syllabi) == 6 and app.RAG.rag_mode == "keyword_extract"
    assert app.RAG.gemini_client is None and app.RAG.chroma_collection is None
    checks = [
        ("전공필수 과목을 알려줘", {"DEMO101", "DEMO201"}),
        ("DEMO201과 DEMO202의 평가방식을 비교해줘", {"DEMO201", "DEMO202"}),
        ("PBL 과목을 알려줘", {"DEMO301"}),
        ("월요일 수업을 알려줘", {"DEMO101", "DEMO401"}),
        ("천체물리학 실험 수업이 있어?", set()),
    ]
    for question, expected in checks:
        answer = app.RAG.answer(question)
        assert answer.get("answer"), question
        actual = {row["course_code"] for row in answer.get("sources", [])}
        assert actual == expected, f"Demo scenario failed: {question}"
    import gradio_app
    assert gradio_app.is_sample_data()
    demo = gradio_app.build_demo()
    assert demo.config.get("components")
    print(json.dumps({"status": "passed", "dataset": "synthetic 6-course sample",
                      "scenario_count": len(checks), "mode": app.RAG.rag_mode,
                      "python_sources_parsed": len(sources), "gradio_config": "built",
                      "external_model_calls": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
