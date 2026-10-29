"""Bounded generation context that never replaces the selected child passage."""
from __future__ import annotations


def local_evidence_window(text: str, child: str, start: int = 0, limit: int = 2200) -> str:
    """Include the complete child plus nearby text, within the requested limit.

    Legacy indexes may contain a section prefix that does not contain the child.
    In that case the child itself is the only defensible generation context.
    """
    if len(child) > limit:
        raise ValueError("Evidence exceeds the generation context limit")
    position = text.find(child, max(0, start)) if child else -1
    if position < 0:
        return child
    left = max(0, position - (limit - len(child)) // 2)
    left = min(left, max(0, len(text) - limit))
    return text[left:left + limit].strip()


def generation_evidence_text(chunk, limit: int = 2200) -> str:
    return local_evidence_window(chunk.parent_text or chunk.text, chunk.text, limit=limit)


def select_generation_matches(matches, chunks, match_factory):
    """Retain five primary passages; supplement at most two courses with four sections."""
    courses = list(dict.fromkeys(match.chunk.syllabus.course_key for match in matches))
    selected = []
    seen = set()

    def add(match):
        # Different children may share a parent window. Deduplicate passages, not parents.
        key = (match.chunk.syllabus.course_key, match.chunk.section, match.chunk.text)
        if key not in seen:
            seen.add(key)
            selected.append(match)

    for match in matches[:5]:
        add(match)
    if 0 < len(courses) <= 2:
        for section in ("교과목개요", "학습목표"):
            for course in courses:
                candidate = next((chunk for chunk in chunks
                                  if chunk.syllabus.course_key == course and chunk.section == section), None)
                if candidate is not None:
                    add(match_factory(chunk=candidate, score=0.0))
    return selected
