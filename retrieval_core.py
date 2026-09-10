from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass


SECTION_PATTERNS = [
    ("기본정보", re.compile(r"^(?:과목명|교과목명|학수번호|분반|교수명|이수구분|연도/학기|학점|수업시간|강의계획서 URL)\s*[:：]", re.I)),
    ("수업운영", re.compile(r"(?:수업방법|수업운영|Class Type|Class Activity)", re.I)),
    ("평가방법", re.compile(r"(?:평가방법|평가계획|Evaluation(?: Plan)?|Midterm|Final|Attendance)", re.I)),
    ("교과목개요", re.compile(r"(?:교과목개요|Course Description|Course Planning|Course Outline)", re.I)),
    ("학습목표", re.compile(r"(?:학습목표|Study Objectives|Course Objectives)", re.I)),
    ("교재및과제", re.compile(r"(?:수업자료|교재|Textbook|References|과제물|Homework)", re.I)),
    (
        "주별학습내용",
        re.compile(
            r"(?:주별 학습내용|주별학습내용|Course Schedule|Weekly Learning Contents?|Week\s*\|\s*Period)",
            re.I,
        ),
    ),
    (
        "지원및윤리",
        re.compile(
            r"(?:장애학생 지원|장애학생은|Support for Disabled|학생 학습윤리|Student Learning Ethics|본교의 교육활동에 참여하는 학생)",
            re.I,
        ),
    ),
    ("강의계획안HTML", re.compile(r"^\[강의계획안 HTML\]", re.I)),
]

LOW_VALUE_MARKERS = (
    "학과(부)별 전공역량",
    "학생 학습윤리 의무",
    "Support for Disabled Students",
)


@dataclass(frozen=True)
class SectionChunk:
    text: str
    section: str
    parent_text: str
    local_index: int


def clean_text(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def detect_section(line: str, current: str) -> str:
    stripped = line.strip()
    if SECTION_PATTERNS[-1][1].search(stripped):
        return "강의계획안HTML"

    # Weekly tables contain column labels such as "교재" and rows mentioning
    # assignments or exams. Those are table contents, not new document sections.
    if current == "주별학습내용":
        if SECTION_PATTERNS[-2][1].search(stripped):
            return "지원및윤리"
        return current

    # Ethics/support boilerplate frequently mentions assignments and textbooks.
    # Keep it isolated until the appended HTML copy begins.
    if current == "지원및윤리":
        if re.search(
            r"(?:주\s*기간\s*회차.*학습내용|Week\s+Learning\s+Contents)",
            stripped,
            re.IGNORECASE,
        ):
            return "주별학습내용"
        return current

    for label, pattern in SECTION_PATTERNS:
        if pattern.search(stripped):
            return label
    if stripped.startswith("[page") and current == "기본정보":
        return "강의계획서본문"
    return current


def split_sections(text: str) -> list[tuple[str, str]]:
    text = clean_text(text)
    if not text:
        return []

    sections: list[tuple[str, list[str]]] = []
    current_name = "기본정보"
    current_lines: list[str] = []
    for line in text.splitlines():
        next_name = detect_section(line, current_name)
        if next_name != current_name and current_lines:
            sections.append((current_name, current_lines))
            current_lines = []
        current_name = next_name
        current_lines.append(line)
    if current_lines:
        sections.append((current_name, current_lines))

    merged: list[tuple[str, str]] = []
    for name, lines in sections:
        section_text = clean_text("\n".join(lines))
        if not section_text:
            continue
        if merged and merged[-1][0] == name:
            merged[-1] = (name, clean_text(f"{merged[-1][1]}\n{section_text}"))
        else:
            merged.append((name, section_text))
    return merged


def split_with_overlap(text: str, chunk_size: int, overlap: int) -> list[str]:
    text = clean_text(text)
    if len(text) <= chunk_size:
        return [text] if text else []
    chunks: list[str] = []
    cursor = 0
    while cursor < len(text):
        end = min(len(text), cursor + chunk_size)
        if end < len(text):
            candidates = (
                text.rfind("\n", cursor, end),
                text.rfind(". ", cursor, end),
                text.rfind(" | ", cursor, end),
            )
            boundary = max(candidates)
            if boundary > cursor + max(280, chunk_size // 3):
                end = boundary + 1
        chunk = text[cursor:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        cursor = max(end - overlap, cursor + 1)
    return chunks


def make_section_chunks(
    text: str,
    chunk_size: int = 900,
    overlap: int = 120,
    parent_size: int = 2200,
) -> list[SectionChunk]:
    chunks: list[SectionChunk] = []
    for section_name, section_text in split_sections(text):
        parent_text = section_text[:parent_size]
        for local_index, child in enumerate(split_with_overlap(section_text, chunk_size, overlap)):
            chunks.append(
                SectionChunk(
                    text=child,
                    section=section_name,
                    parent_text=parent_text,
                    local_index=local_index,
                )
            )
    return chunks


def section_priority(question: str, section: str) -> float:
    q = question.casefold()
    boosts = {
        "평가방법": ("평가", "시험", "중간", "기말", "과제", "출석", "성적", "배점"),
        "주별학습내용": (
            "무엇", "내용", "배우", "학습", "다루", "커리큘럼", "주차", "주별",
            "프로젝트", "project", "발표", "presentation", "실습", "practice", "hands-on",
        ),
        "수업운영": (
            "수업방법", "운영", "실습", "practice", "hands-on", "lab", "토론",
            "discussion", "발표", "presentation", "팀", "team", "group", "프로젝트", "project",
        ),
        "교과목개요": ("내용", "무엇", "배우", "개요", "추천"),
        "학습목표": ("목표", "배우", "역량", "추천"),
        "교재및과제": ("교재", "책", "과제", "소프트웨어", "도구"),
        "기본정보": ("교수", "학점", "이수구분", "전공필수", "전공선택"),
    }
    terms = boosts.get(section, ())
    boost = 0.32 if any(term in q for term in terms) else 0.0
    if section == "지원및윤리" and not any(term in q for term in ("장애", "지원", "윤리", "표절")):
        boost -= 0.35
    return boost


class BM25Index:
    def __init__(self, documents: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.documents = documents
        self.k1 = k1
        self.b = b
        self.lengths = [len(document) for document in documents]
        self.avgdl = sum(self.lengths) / max(len(self.lengths), 1)
        self.term_counts = [Counter(document) for document in documents]
        doc_freq: Counter[str] = Counter()
        for document in documents:
            doc_freq.update(set(document))
        total = max(len(documents), 1)
        self.idf = {
            term: math.log(1.0 + (total - freq + 0.5) / (freq + 0.5))
            for term, freq in doc_freq.items()
        }

    def score(self, query_tokens: list[str], index: int) -> float:
        if not query_tokens or index >= len(self.term_counts):
            return 0.0
        counts = self.term_counts[index]
        doc_len = self.lengths[index]
        score = 0.0
        for term, query_tf in Counter(query_tokens).items():
            freq = counts.get(term, 0)
            if not freq:
                continue
            denominator = freq + self.k1 * (
                1.0 - self.b + self.b * doc_len / max(self.avgdl, 1.0)
            )
            score += self.idf.get(term, 0.0) * (freq * (self.k1 + 1.0) / denominator) * query_tf
        return score


def token_jaccard(left: list[str], right: list[str]) -> float:
    left_set, right_set = set(left), set(right)
    return len(left_set & right_set) / max(len(left_set | right_set), 1)
