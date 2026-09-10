"""Create a public ZIP from an explicit allowlist; never include local datasets."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = ["README.md", ".env.example", ".gitignore", ".gitattributes",
              "requirements.txt", "requirements-lock.txt", "requirements-rag.txt",
              "app.py", "gradio_app.py", "retrieval_core.py", "run_demo.py"]
SCRIPT_FILES = ["collect_syllabi.py", "extract_texts.py", "build_vector_db.py",
                "evaluate_rag.py", "stress_test_generated_queries.py", "analyze_data.py",
                "audit_legacy_evaluation.py", "benchmark_retrieval.py",
                "verify_release.py", "export_public.py", "record_validation.py"]
SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"gh[pousr]_[0-9A-Za-z]{25,}"),
    re.compile(r"github_pat_[0-9A-Za-z_]{30,}"),
    re.compile(r"sk-[0-9A-Za-z_-]{30,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]


def public_files() -> list[Path]:
    paths = [ROOT / name for name in ROOT_FILES]
    paths += [ROOT / "scripts" / name for name in SCRIPT_FILES]
    for folder, extensions in [
        ("sugang_mate", {".py"}), ("tests", {".py"}), ("docs", {".md", ".png", ".svg"}),
        ("evaluation", {".json"}), (".github/workflows", {".yml"}),
        ("data/sample", {".jsonl"}),
    ]:
        paths += [p for p in (ROOT / folder).rglob("*")
                  if p.is_file() and p.suffix in extensions and "__pycache__" not in p.parts]
    paths.append(ROOT / "data/README.md")
    return sorted(set(paths), key=lambda p: p.relative_to(ROOT).as_posix())


def validate(paths: list[Path]) -> None:
    for path in paths:
        if not path.is_file():
            raise ValueError(f"Missing public file: {path.relative_to(ROOT)}")
        if not path.resolve().is_relative_to(ROOT.resolve()) or path.is_symlink():
            raise ValueError("Public file escapes repository")
        if path.suffix not in {".png"}:
            text = path.read_text(encoding="utf-8-sig")
            if any(pattern.search(text) for pattern in SECRET_PATTERNS):
                raise ValueError(f"Potential secret in {path.relative_to(ROOT)} (value not printed)")
            if re.search(r"[A-Za-z]:[\\/]Users[\\/]", text, re.I):
                raise ValueError(f"Private absolute path in {path.relative_to(ROOT)}")
    env = (ROOT / ".env.example").read_text(encoding="utf-8-sig")
    for key in ("GOOGLE_API_KEY", "ADMIN_PASSWORD"):
        if not re.search(rf"(?m)^{key}=\s*$", env):
            raise ValueError(f"{key} must be blank in example configuration")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT.parent / "sugang-mate-public.zip")
    args = parser.parse_args()
    paths = public_files()
    validate(paths)
    manifest = [{"path": p.relative_to(ROOT).as_posix(),
                 "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in paths]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, "sugang-mate/" + path.relative_to(ROOT).as_posix())
        archive.writestr("sugang-mate/RELEASE_MANIFEST.json",
                         json.dumps({"files": manifest}, ensure_ascii=False, indent=2) + "\n")
    with zipfile.ZipFile(args.output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("ZIP integrity check failed")
    print(json.dumps({"file_count": len(paths), "zip_bytes": args.output.stat().st_size,
                      "excluded": ["real .env", "raw or processed university corpus",
                                   "vector databases", "logs", "installed packages",
                                   "submission archives"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
