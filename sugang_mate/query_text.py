"""Pure query normalization and lexical matching rules shared by retrieval.

Routing and conversation state remain in app.py.
"""
from __future__ import annotations

import re


SYNONYMS = {
    "팀활동": [
        "팀활동", "팀 활동", "팀플", "팀프젝", "팀플젝", "팀 프로젝트", "팀프로젝트", "팀 과제",
        "팀과제", "팀 협력", "팀협력", "팀워크", "협업", "협동", "조별",
        "조별 활동", "조별활동", "조별 과제", "조별과제", "조별 프로젝트", "조별플젝",
        "그룹 활동", "그룹활동", "그룹 프로젝트", "group project", "team project",
    ],
    "프로젝트": [
        "프로젝트", "프로젝트형", "프로젝트 중심", "PBL", "캡스톤", "project",
        "project based learning",
    ],
    "코딩": [
        "코딩", "프로그래밍", "개발", "구현", "코드", "coding", "programming",
    ],
    "실습": [
        "실습", "실습형", "실습 중심", "실습위주", "실습 위주", "직접 해보는",
        "practice", "hands-on", "lab",
    ],
    "발표": ["발표", "프레젠테이션", "presentation", "세미나 발표"],
    "토론": ["토론", "토의", "discussion", "debate"],
    "파이썬": ["파이썬", "python"],
    "R언어": ["R언어", "R 언어", "R프로그래밍", "R 프로그래밍"],
    "시험": ["시험", "중간고사", "기말고사", "고사", "평가"],
    "중간고사": ["중간고사", "중간", "midterm"],
    "기말고사": ["기말고사", "기말", "final"],
    "과제": ["과제", "숙제", "레포트", "리포트", "assignment", "homework", "보고서"],
    "퀴즈": ["퀴즈", "쪽지시험", "쪽지 시험", "quiz", "퀴즈형 시험"],
    "출석": ["출석", "attendance"],
    "평가": ["평가", "성적", "비율", "배점", "grading"],
    "영강": ["영강", "영어 수업", "영어수업", "영어 강의", "영어강의", "English"],
    "학습내용": [
        "학습내용", "학습 내용", "수업내용", "수업 내용", "강의내용", "강의 내용",
        "주별학습내용", "주별 학습내용", "커리큘럼", "뭘 배워", "무엇을 배워",
        "배우는 내용", "다루는 내용",
    ],
}

TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9_./+-]+")

QUERY_STOPWORDS = {
    "강의", "강의가", "과목", "과목이", "과목은", "과목을", "교과목",
    "수업", "수업이", "수업은", "수업을", "내용", "내용을",
    "알려줘", "보여줘", "설명해줘", "추천해줘", "말해줘",
    "있어", "있나요", "있는", "뭐가", "무엇", "어떤", "어때",
    "배우는", "배우고", "대해", "대한", "관련", "그리고", "또는",
}

KOREAN_QUERY_SUFFIXES = (
    "에서는", "으로는", "에게는", "이라는", "이라고", "에서", "으로",
    "이나", "하고", "처럼", "보다", "부터", "까지", "은", "는", "이",
    "가", "을", "를", "과", "와", "로", "도", "만", "나",
)

def normalize_cache_key(question: str) -> str:
    normalized = re.sub(r"\s+", " ", question.strip()).casefold()
    return re.sub(r"[?!.,。！？]+$", "", normalized).strip()

def tokenize(text: str) -> list[str]:
    tokens = [token.lower() for token in TOKEN_RE.findall(text)]
    return [token for token in tokens if len(token) >= 2]

def expand_query_tokens(tokens: list[str]) -> list[str]:
    expanded: list[str] = []
    for token in tokens:
        if token in QUERY_STOPWORDS:
            continue
        expanded.append(token)
        for suffix in KOREAN_QUERY_SUFFIXES:
            if token.endswith(suffix) and len(token) - len(suffix) >= 2:
                stem = token[: -len(suffix)]
                if stem not in QUERY_STOPWORDS:
                    expanded.append(stem)
                break
    query = " ".join(expanded)
    for key, values in SYNONYMS.items():
        if contains_any(query, {key, *values}):
            expanded.extend(tokenize(" ".join(values)))
    return list(dict.fromkeys(expanded))

def _contains_term(text: str, term: str) -> bool:
    # Encoding/decoding describe a representation, not a programming activity.
    # Keep Korean particles and compounds such as "코딩을" and "바이브코딩".
    if term == "코딩":
        return re.search(r"(?<![인디엔])코딩", text) is not None
    if term == "coding":
        return re.search(r"(?<![a-z])coding(?![a-z])", text) is not None
    return term in text

def contains_any(text: str, terms: set[str]) -> bool:
    lowered = text.casefold()
    return any(_contains_term(lowered, term.casefold()) for term in terms)

def contains_phrase(text: str, phrases: tuple[str, ...] | list[str]) -> bool:
    compact = re.sub(r"\s+", "", text).casefold()
    return any(
        _contains_term(compact, re.sub(r"\s+", "", phrase).casefold())
        for phrase in phrases
    )
