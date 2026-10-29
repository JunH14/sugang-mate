"""Conservative normalization of extracted source text; never infer missing values."""
from __future__ import annotations

import re

_PLACEHOLDER = re.compile(r"평가방법이\s*입력\s*되지\s*않았습니다[.]?")
_SOURCE = re.compile(r"(?=^\[(?:첨부 강의계획서:.*|강의계획안 HTML)\]$)", re.M)


def sanitize_retrieval_text(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    # Legacy plain text has no checkbox state. Do not interpret the full option list.
    text = re.sub(r"활동유형(?!\s*선택 확인).*?(?=(?:▷\s*)?평가방법)",
                  lambda match: match.group(0) if "선택 확인:" in match.group(0) else "", text,
                  flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(
        r"수업유형\s+(?:대면\s+대면\s+)?(?:비대면\s+비대면\s+)?"
        r"(?:병행\(대면&비대면 동시\)\s+병행\(대면&비대면 동시\)\s+)?미정", "", text,
        flags=re.IGNORECASE,
    )
    # A blank HTML form is not a negative fact about the attached syllabus.
    text = _PLACEHOLDER.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def assessment_source_sections(text: str, section_splitter) -> list[tuple[str, str]]:
    """Keep attachment and HTML assessment evidence separate, including conflicts."""
    results = []
    for block in _SOURCE.split(text):
        source = ""
        if block.startswith("[첨부 강의계획서:"):
            source = block.splitlines()[0]
        elif block.startswith("[강의계획안 HTML]"):
            source = "[강의계획안 HTML]"
        parts = []
        for name, part in section_splitter(block):
            if name != "평가방법":
                continue
            part = _PLACEHOLDER.sub("", part)
            # Headings alone are not an assessment policy.
            content = re.sub(r"(?:평가방법|평가계획|Evaluation(?: Method| Plan)?|[▷◆:：])", "", part, flags=re.I)
            if content.strip():
                parts.append(part)
        if parts:
            results.append((source, "\n".join(parts)))
    return results
