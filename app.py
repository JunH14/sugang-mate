from __future__ import annotations

import argparse
import copy
import html
import json
import math
import os
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from sugang_mate.cache import TTLCache
from sugang_mate.config import (
    dataset_fingerprint,
    load_dotenv,
    offline_enabled,
    resolve_chroma_dir,
    resolve_data_path,
)

from retrieval_core import (
    BM25Index,
    make_section_chunks,
    section_priority,
    split_sections,
    token_jaccard,
)


PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR)
DATA_PATH = resolve_data_path(PROJECT_DIR)
CHROMA_DIR = resolve_chroma_dir(PROJECT_DIR)
CHROMA_COLLECTION = "ku_sejong_bigdata_syllabi_v2"
LEGACY_CHROMA_COLLECTION = "ku_sejong_bigdata_syllabi"
EMBEDDING_MODEL = "gemini-embedding-001"
GENERATIVE_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
GENERATIVE_MODELS = [GENERATIVE_MODEL] + [
    model.strip() for model in os.environ.get("GEMINI_FALLBACK_MODELS", "").split(",")
    if model.strip()
]
ANSWER_CACHE_MAX = int(os.environ.get("ANSWER_CACHE_MAX", "128"))
ANSWER_CACHE_TTL_SECONDS = int(os.environ.get("ANSWER_CACHE_TTL_SECONDS", "1800"))
GEMINI_RETRY_ATTEMPTS = max(1, int(os.environ.get("GEMINI_RETRY_ATTEMPTS", "2")))
GEMINI_RETRY_BASE_SECONDS = max(
    0.0, float(os.environ.get("GEMINI_RETRY_BASE_SECONDS", "0.6"))
)


DEPARTMENT_CATALOG = [
    {
        "college": "공공정책대학",
        "departments": [
            {"name": "정부행정학부", "enabled": False},
            {"name": "공공사회·통일외교학부", "enabled": False},
            {"name": "경제통계학부", "enabled": False},
            {
                "name": "빅데이터사이언스학부",
                "enabled": True,
                "slug": "bigdata",
                "badge": "프로토타입",
            },
        ],
    },
    {
        "college": "과학기술대학",
        "departments": [
            {"name": "응용수리과학부", "enabled": False},
            {"name": "디스플레이·반도체물리학부", "enabled": False},
            {"name": "신소재화학과", "enabled": False},
            {"name": "컴퓨터융합소프트웨어학과", "enabled": False},
            {"name": "전자·기계융합공학과", "enabled": False},
            {"name": "전자및정보공학과", "enabled": False},
            {"name": "환경시스템공학과", "enabled": False},
            {"name": "생명정보공학과", "enabled": False},
            {"name": "식품생명공학과", "enabled": False},
        ],
    },
    {
        "college": "글로벌비즈니스대학",
        "departments": [
            {"name": "글로벌학부", "enabled": False},
            {"name": "융합경영학부", "enabled": False},
        ],
    },
    {
        "college": "문화스포츠대학",
        "departments": [
            {"name": "문화유산융합학부", "enabled": False},
            {"name": "문화창의학부", "enabled": False},
            {"name": "국제스포츠학부", "enabled": False},
        ],
    },
    {
        "college": "약학대학",
        "departments": [
            {"name": "약학과", "enabled": False},
        ],
    },
]


EXAMPLE_QUESTIONS = [
    "파이썬을 사용하는 과목 있어?",
    "수리통계학은 무슨 요일 몇 시에 수업해?",
    "팀플이나 프로젝트가 있는 과목 알려줘.",
    "수리통계학 평가방식 알려줘.",
    "중간고사 없는 과목은?",
    "BDSC201과 BDSC205를 비교해줘.",
    "머신러닝 과목은 어떤 내용을 배우는지 알려줘.",
]


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

TEAMWORK_QUERY_TERMS = tuple(SYNONYMS["팀활동"])
TEAMWORK_EVIDENCE_PATTERNS = (
    re.compile(r"팀\s*프로젝트", re.IGNORECASE),
    re.compile(r"팀\s*기반(?:의)?\s*(?:과제|프로젝트|학습|활동)", re.IGNORECASE),
    re.compile(r"팀별로?\s*(?:발표|분석|과제|프로젝트)", re.IGNORECASE),
    re.compile(r"팀원(?:들)?(?:과|의)?\s*협업", re.IGNORECASE),
    re.compile(r"조별\s*(?:활동|과제|프로젝트|발표|준비)", re.IGNORECASE),
    re.compile(r"(?:team|group)\s+projects?", re.IGNORECASE),
)
ACTIVITY_EVIDENCE_NOISE = (
    "학과(부)별 전공역량",
    "타인과 협력할 수 있는 공감능력",
    "핵심역량개척정신공유협력",
    "실험실습 안전",
)

FEATURE_EVIDENCE_NOISE = ACTIVITY_EVIDENCE_NOISE + (
    "본교의 교육활동에 참여하는 학생",
    "Students participating in the educational activities",
    "장애학생은",
    "과제 관련 지원",
    "과제정보는 자유롭게",
    "학습과정에서 과제물 표절",
    "표절검사시스템",
    "1학기는 16주 구성",
    "문화유산 실무‧실습 역량",
    "Pragmatic Practice | Pragmatic Practice",
    "Class Activity | Lecture | Presentation | Discussion | Experiment | Practice",
)

FEATURE_TOPICS = {
    "코딩": {
        "aliases": tuple(SYNONYMS["코딩"]),
        "display": "코딩·프로그래밍·개발·구현",
        "patterns": (
            re.compile(r"(?:Python|파이썬|SAS|JavaScript|R\s*(?:언어)?).{0,45}(?:프로그래밍|programming|프로그램|코딩|구현|실습|활용|이용)", re.IGNORECASE),
            re.compile(r"(?:프로그래밍|programming|프로그램|코딩|구현).{0,45}(?:Python|파이썬|SAS|JavaScript|R\s*(?:언어)?)", re.IGNORECASE),
            re.compile(r"programming\s+(?:skills?|session)", re.IGNORECASE),
            re.compile(r"basic\s+programming", re.IGNORECASE),
            re.compile(r"프로그래밍의\s*기초", re.IGNORECASE),
            re.compile(r"utilize\s+Python.{0,45}JavaScript", re.IGNORECASE),
        ),
    },
    "실습": {
        "aliases": tuple(SYNONYMS["실습"]),
        "display": "실습·실습형·hands-on·practice",
        "patterns": (
            re.compile(r"실습을?\s*(?:통하여|통해|병행|진행)", re.IGNORECASE),
            re.compile(r"(?:이론|강의)(?:과|와|,|\s및)?\s*실습", re.IGNORECASE),
            re.compile(r"실습\s*(?:평가|점수|중심|위주)", re.IGNORECASE),
            re.compile(r"(?:R|딥러닝|소개)\s*(?:및\s*)?실습", re.IGNORECASE),
            re.compile(r"with\s+practices?", re.IGNORECASE),
            re.compile(r"\bV\s+Practice\b", re.IGNORECASE),
        ),
    },
    "발표": {
        "aliases": tuple(SYNONYMS["발표"]),
        "display": "발표·프레젠테이션·presentation",
        "patterns": (
            re.compile(r"발표\s*(?:점수|평가|로|를|와|및|&)", re.IGNORECASE),
            re.compile(r"(?:중간|기말|조별|PPT|프로젝트|결과|분석)\s*(?:팀\s*)?발표", re.IGNORECASE),
            re.compile(r"(?:Problem\s+Solving|Final).{0,40}Presentation", re.IGNORECASE),
            re.compile(r"Presentation.{0,40}(?:Report|Assignment)", re.IGNORECASE),
            re.compile(r"team\s+project\s*/\s*presentation", re.IGNORECASE),
            re.compile(r"to\s+present\s+corresponding\s+results", re.IGNORECASE),
        ),
    },
    "토론": {
        "aliases": tuple(SYNONYMS["토론"]),
        "display": "토론·토의·discussion·debate",
        "patterns": (
            re.compile(r"질문과\s*토의", re.IGNORECASE),
            re.compile(r"질의\s*응답\s*및\s*토의", re.IGNORECASE),
            re.compile(r"team\s+project\s*/\s*presentation\s*/\s*discussion", re.IGNORECASE),
            re.compile(r"\bV\s+Discussion\b", re.IGNORECASE),
        ),
    },
    "과제": {
        "aliases": tuple(SYNONYMS["과제"]),
        "display": "과제·숙제·레포트·assignment·homework",
        "patterns": (
            re.compile(r"과제를?\s*(?:풀어서|부여|출제|제출)", re.IGNORECASE),
            re.compile(r"수시\s*과제(?:가|는|를|\s있)", re.IGNORECASE),
            re.compile(r"(?:개인|팀\s*기반)\s*(?:수시)?과제", re.IGNORECASE),
            re.compile(r"진도에\s*따른\s*수시과제", re.IGNORECASE),
            re.compile(r"수업내용\s*복습을\s*위한\s*과제", re.IGNORECASE),
            re.compile(r"과제는\s*수업시간에", re.IGNORECASE),
            re.compile(r"Quiz\s+and\s+assignments?\s+are\s+given", re.IGNORECASE),
            re.compile(r"(?:Assignment|Homework)\s*\d+", re.IGNORECASE),
            re.compile(r"Assignment\s+\d+\s+(?:Project|Midterm)", re.IGNORECASE),
            re.compile(r"(?:Final\s+)?Report\s+Assignment", re.IGNORECASE),
            re.compile(r"(?:발표와\s*한번의|Final)\s*(?:레포트|Report)", re.IGNORECASE),
        ),
    },
    "퀴즈": {
        "aliases": tuple(SYNONYMS["퀴즈"]),
        "display": "퀴즈·쪽지시험·quiz",
        "patterns": (
            re.compile(r"Quiz:\s*\|\s*\d+", re.IGNORECASE),
            re.compile(r"Quiz\s+and\s+assignments?\s+are\s+given", re.IGNORECASE),
            re.compile(r"\bV\s+Quiz\b", re.IGNORECASE),
            re.compile(r"쪽지\s*시험", re.IGNORECASE),
        ),
    },
    "프로젝트": {
        "aliases": ("프로젝트", "프로젝트형", "프로젝트 중심", "project", "project based learning"),
        "display": "프로젝트·프로젝트형·project",
        "patterns": (
            re.compile(r"팀\s*프로젝트", re.IGNORECASE),
            re.compile(r"(?:개인|개별)\s*프로젝트", re.IGNORECASE),
            re.compile(r"(?:team|group)\s+projects?", re.IGNORECASE),
            re.compile(r"(?:individual|personal)\s+projects?", re.IGNORECASE),
            re.compile(r"Project\s*\d+", re.IGNORECASE),
            re.compile(r"실제\s*데이터를\s*다루는\s*프로젝트", re.IGNORECASE),
        ),
        "title_patterns": (
            re.compile(r"프로젝트학기", re.IGNORECASE),
            re.compile(r"PBL|캡스톤", re.IGNORECASE),
        ),
    },
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


def is_transient_api_error(error: Exception) -> bool:
    message = str(error).casefold()
    markers = (
        "429",
        "500",
        "502",
        "503",
        "504",
        "resource_exhausted",
        "rate limit",
        "quota",
        "unavailable",
        "deadline",
        "timeout",
        "temporarily",
        "internal",
    )
    return any(marker in message for marker in markers)


def friendly_api_warning(error: Exception, stage: str = "generation") -> str:
    message = str(error).casefold()
    if "429" in message or "resource_exhausted" in message or "quota" in message:
        return "Gemini API 요청 한도에 도달해 저장된 강의계획서 근거로 답변했습니다. 잠시 후 다시 시도해 주세요."
    if any(code in message for code in ("500", "502", "503", "504", "unavailable", "timeout")):
        return "Gemini 서비스가 일시적으로 혼잡해 저장된 강의계획서 근거로 답변했습니다."
    if stage == "embedding":
        return "의미 검색을 사용할 수 없어 키워드 검색으로 전환했습니다."
    return "Gemini 응답을 만들지 못해 저장된 강의계획서 근거로 답변했습니다."


@dataclass
class Syllabus:
    course_code: str
    class_no: str
    course_name: str
    professor: str
    completion_type: str
    credit: str
    class_hours: str
    schedule_summary: str
    schedule_entries: list[dict[str, Any]]
    syllabus_url: str
    text_path: str
    sources: str
    text: str

    @property
    def course_key(self) -> str:
        return f"{self.course_code}-{self.class_no}"

    @property
    def title(self) -> str:
        return f"{self.course_key} {self.course_name}"


@dataclass(frozen=True)
class CourseCondition:
    display: str
    source_note: str
    predicate: Callable[[Syllabus], bool]


@dataclass
class Chunk:
    index: int
    syllabus: Syllabus
    text: str
    tokens: list[str]
    section: str = "본문"
    parent_text: str = ""
    chunk_id: str = ""


@dataclass
class Match:
    chunk: Chunk
    score: float
    semantic_score: float = 0.0
    lexical_score: float = 0.0
    evidence_score: float = 0.0


class SyllabusRag:
    def __init__(self, data_path: Path) -> None:
        self.data_path = Path(data_path)
        self.offline = offline_enabled()
        self.chroma_dir = resolve_chroma_dir(PROJECT_DIR)
        self.answer_cache_max = max(
            0, int(os.environ.get("ANSWER_CACHE_MAX", str(ANSWER_CACHE_MAX)))
        )
        self.answer_cache_ttl_seconds = max(
            0,
            int(os.environ.get("ANSWER_CACHE_TTL_SECONDS", str(ANSWER_CACHE_TTL_SECONDS))),
        )
        self.gemini_retry_attempts = max(
            1, int(os.environ.get("GEMINI_RETRY_ATTEMPTS", str(GEMINI_RETRY_ATTEMPTS)))
        )
        self.gemini_retry_base_seconds = max(
            0.0,
            float(
                os.environ.get(
                    "GEMINI_RETRY_BASE_SECONDS", str(GEMINI_RETRY_BASE_SECONDS)
                )
            ),
        )
        self.syllabi = self._load_syllabi()
        self.dataset_sha256 = dataset_fingerprint(self.data_path)
        self.syllabus_by_key = {item.course_key: item for item in self.syllabi}
        self.chunks = self._make_chunks()
        self.bm25 = BM25Index([chunk.tokens for chunk in self.chunks])
        self.gemini_client = self._load_gemini_client()
        self.chroma_collection = self._load_chroma_collection()
        self.rag_mode = self._detect_mode()
        self.answer_cache: TTLCache[dict[str, Any]] = TTLCache(
            self.answer_cache_max, self.answer_cache_ttl_seconds
        )
        self.request_state = threading.local()

    def _load_syllabi(self) -> list[Syllabus]:
        if not self.data_path.exists():
            raise FileNotFoundError(f"Missing dataset: {self.data_path}")

        syllabi: list[Syllabus] = []
        with self.data_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                item = json.loads(line)
                syllabi.append(
                    Syllabus(
                        course_code=str(item.get("course_code", "")),
                        class_no=str(item.get("class_no", "")),
                        course_name=str(item.get("course_name", "")),
                        professor=str(item.get("professor", "")),
                        completion_type=(
                            str(item.get("completion_type", "")).strip()
                            or extract_completion_type(str(item.get("text", "")))
                        ),
                        credit=str(item.get("credit", "")),
                        class_hours=str(item.get("class_hours", "")),
                        schedule_summary=str(item.get("schedule_summary", "")),
                        schedule_entries=[
                            entry
                            for entry in item.get("schedule_entries", [])
                            if isinstance(entry, dict)
                        ],
                        syllabus_url=str(item.get("syllabus_url", "")),
                        text_path=str(item.get("text_path", "")),
                        sources=str(item.get("sources", "")),
                        text=sanitize_retrieval_text(str(item.get("text", ""))),
                    )
                )
        return syllabi

    def _make_chunks(self, chunk_size: int = 900, overlap: int = 120) -> list[Chunk]:
        chunks: list[Chunk] = []
        for syllabus in self.syllabi:
            for section_chunk in make_section_chunks(
                syllabus.text, chunk_size=chunk_size, overlap=overlap
            ):
                chunk_text = section_chunk.text
                chunk_index = len(chunks)
                chunks.append(
                    Chunk(
                        index=chunk_index,
                        syllabus=syllabus,
                        text=chunk_text,
                        tokens=tokenize(
                            " ".join(
                                [
                                    syllabus.course_code,
                                    syllabus.course_name,
                                    syllabus.professor,
                                    syllabus.completion_type,
                                    syllabus.schedule_summary,
                                    section_chunk.section,
                                    chunk_text,
                                ]
                            )
                        ),
                        section=section_chunk.section,
                        parent_text=section_chunk.parent_text,
                        chunk_id=(
                            f"{syllabus.course_code}_{syllabus.class_no}_"
                            f"{chunk_index:04d}"
                        ),
                    )
                )
        return chunks

    def _load_gemini_client(self) -> Any:
        if self.offline:
            return None
        api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
        if not api_key:
            return None
        try:
            from google import genai

            return genai.Client(api_key=api_key)
        except Exception as error:
            print(f"Gemini generate failed: {error}", file=sys.stderr)
            return None

    def _load_chroma_collection(self) -> Any:
        if self.offline or not self.gemini_client or not self.chroma_dir.exists():
            return None
        try:
            import chromadb

            chroma_client = chromadb.PersistentClient(path=str(self.chroma_dir))
            for collection_name in (CHROMA_COLLECTION, LEGACY_CHROMA_COLLECTION):
                try:
                    collection = chroma_client.get_collection(collection_name)
                    collection_count = collection.count()
                    metadata = collection.metadata or {}
                    if (
                        collection_count == len(self.chunks)
                        and metadata.get("dataset_sha256") == self.dataset_sha256
                        and metadata.get("embedding_model") == EMBEDDING_MODEL
                    ):
                        return collection
                    if collection_count > 0:
                        print(
                            "Chroma index has missing or stale dataset/model identity: "
                            f"collection={collection_count}, local={len(self.chunks)}. "
                            "Falling back to the current local chunks.",
                            file=sys.stderr,
                        )
                except Exception:
                    continue
            return None
        except Exception:
            return None

    def _detect_mode(self) -> str:
        if self.gemini_client and self.chroma_collection:
            return "chroma_gemini"
        if self.gemini_client:
            return "keyword_gemini"
        return "keyword_extract"

    def search(self, question: str, top_k: int = 7, allow_rewrite: bool = True) -> list[Match]:
        self.request_state.search_warning = ""
        self.request_state.rewritten_query = ""
        self.request_state.search_backend = "keyword"
        if self.chroma_collection and self.gemini_client:
            vector_matches = self._search_chroma(question, top_k)
            if vector_matches:
                self.request_state.search_backend = "hybrid"
                matches = apply_relevance_threshold(question, vector_matches)
                if matches:
                    return matches
        matches = apply_relevance_threshold(question, self._search_keyword(question, top_k))
        if matches or not allow_rewrite or not self.gemini_client:
            return matches

        rewritten = self._rewrite_query(question)
        if rewritten and normalize_cache_key(rewritten) != normalize_cache_key(question):
            rewritten_matches = self.search(rewritten, top_k=top_k, allow_rewrite=False)
            if rewritten_matches:
                self.request_state.search_backend = "hybrid_rewrite"
                self.request_state.rewritten_query = rewritten
                return rewritten_matches
        return []

    def _search_chroma(self, question: str, top_k: int) -> list[Match]:
        try:
            query_embedding = self._embed_query(build_retrieval_query(question))
            result = self.chroma_collection.query(
                query_embeddings=[query_embedding],
                n_results=max(top_k * 5, 24),
                include=["documents", "metadatas", "distances"],
            )
        except Exception as error:
            self.request_state.search_warning = friendly_api_warning(error, "embedding")
            return []

        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        matches: list[Match] = []
        for index, (document, metadata, distance) in enumerate(zip(documents, metadatas, distances)):
            course_code = str(metadata.get("course_code", ""))
            class_no = str(metadata.get("class_no", ""))
            course_key = f"{course_code}-{class_no}"
            syllabus = self.syllabus_by_key.get(course_key)
            if syllabus is None:
                syllabus = Syllabus(
                    course_code=course_code,
                    class_no=class_no,
                    course_name=str(metadata.get("course_name", "")),
                    professor=str(metadata.get("professor", "")),
                    completion_type=str(metadata.get("completion_type", "")),
                    credit=str(metadata.get("credit", "")),
                    class_hours=str(metadata.get("class_hours", "")),
                    schedule_summary=str(metadata.get("schedule_summary", "")),
                    schedule_entries=parse_schedule_entries_json(
                        str(metadata.get("schedule_entries_json", ""))
                    ),
                    syllabus_url=str(metadata.get("syllabus_url", "")),
                    text_path=str(metadata.get("text_path", "")),
                    sources=str(metadata.get("sources", "")),
                    text=document,
                )
            semantic_score = max(0.0, min(1.0, 1.0 - float(distance or 0.0)))
            document_text = sanitize_retrieval_text(str(document))
            chunk = Chunk(
                index=index,
                syllabus=syllabus,
                text=document_text,
                tokens=tokenize(document_text),
                section=str(metadata.get("section", "본문")),
                parent_text=str(metadata.get("parent_text", "")),
                chunk_id=str(metadata.get("chunk_id", "")),
            )
            matches.append(
                Match(
                    chunk=chunk,
                    score=semantic_score,
                    semantic_score=semantic_score,
                    evidence_score=semantic_score,
                )
            )

        keyword_matches = self._search_keyword(question, max(top_k * 3, 15))
        return rerank_matches(question, matches, keyword_matches, top_k)

    def _rewrite_query(self, question: str) -> str:
        from google.genai import types

        prompt = (
            "다음 질문을 고려대학교 강의계획서 검색에 적합한 짧은 검색어 한 줄로 바꾸세요. "
            "원래 의미를 유지하고 과목명·학수번호는 그대로 두며 설명은 쓰지 마세요.\n"
            f"질문: {question}"
        )
        try:
            response = self.gemini_client.models.generate_content(
                model=GENERATIVE_MODELS[0],
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=80,
                    thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                ),
            )
            return extract_gemini_text(response).strip().strip('"')
        except Exception as error:
            self.request_state.search_warning = friendly_api_warning(error, "embedding")
            return ""

    def _embed_query(self, question: str) -> list[float]:
        from google.genai import types

        response = self.gemini_client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=question,
            config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
        )
        vectors = embedding_values(response)
        if not vectors:
            raise RuntimeError("Gemini query embedding returned no vectors")
        return vectors[0]

    def _search_keyword(self, question: str, top_k: int) -> list[Match]:
        query_tokens = expand_query_tokens(tokenize(question))
        if not query_tokens:
            return []
        course_hint = detect_course_hint(question, self.syllabi)
        matches: list[Match] = []

        for chunk in self.chunks:
            score = self.bm25.score(query_tokens, chunk.index)

            lowered_question = question.lower()
            lowered_text = chunk.text.lower()
            if chunk.syllabus.course_code.lower() in lowered_question:
                score += 8.0
            if chunk.syllabus.course_name and chunk.syllabus.course_name in question:
                score += 8.0
            if chunk.syllabus.professor and chunk.syllabus.professor in question:
                score += 3.0
            if course_hint and chunk.syllabus.course_key == course_hint.course_key:
                score += 10.0
            for key, words in SYNONYMS.items():
                if contains_phrase(question, [key, *words]):
                    score += sum(1.2 for word in words if word.lower() in lowered_text)
            if score > 0:
                score += section_priority(question, chunk.section)
                matches.append(
                    Match(
                        chunk=chunk,
                        score=score,
                        lexical_score=score,
                        evidence_score=score,
                    )
                )

        matches.sort(key=lambda item: item.score, reverse=True)
        return diversify_matches(matches, top_k)

    def answer(
        self,
        question: str,
        history: list[dict[str, Any]] | list[Any] | None = None,
    ) -> dict[str, Any]:
        conversation_history = history or []
        contextual_started = time.perf_counter()
        contextual_set_result = build_contextual_set_answer(
            question, conversation_history, self.syllabi
        )
        if contextual_set_result:
            contextual_set_result["timings"] = {
                "search_seconds": 0.0,
                "generation_seconds": 0.0,
                "total_seconds": round(time.perf_counter() - contextual_started, 3),
                "cache_hit": False,
            }
            return contextual_set_result

        contextual_course_result = build_contextual_course_set_answer(
            question, conversation_history, self.syllabi
        )
        if contextual_course_result:
            contextual_course_result["timings"] = {
                "search_seconds": 0.0,
                "generation_seconds": 0.0,
                "total_seconds": round(time.perf_counter() - contextual_started, 3),
                "cache_hit": False,
            }
            return contextual_course_result

        resolved_question, context_course = resolve_contextual_question(
            question, conversation_history, self.syllabi
        )
        context_metadata: dict[str, Any] | None = None
        if context_course is not None:
            context_metadata = {
                "type": "course_reference",
                "course_code": context_course.course_code,
                "course_name": context_course.course_name,
                "original_question": question,
                "resolved_question": resolved_question,
            }
        elif conversation_history:
            previous_courses = latest_assistant_courses(
                conversation_history, self.syllabi
            )
            if previous_courses and is_contextual_followup_question(question):
                course_codes = " ".join(
                    course.course_code for course in previous_courses
                )
                resolved_question = f"{course_codes} {question}".strip()
                context_metadata = {
                    "type": "course_set_reference",
                    "course_codes": [
                        course.course_code for course in previous_courses
                    ],
                    "original_question": question,
                    "resolved_question": resolved_question,
                }
            elif self.gemini_client:
                rewritten = self._rewrite_followup_question(
                    question, conversation_history
                )
                if rewritten and normalize_cache_key(rewritten) != normalize_cache_key(question):
                    resolved_question = rewritten
                    context_metadata = {
                        "type": "conversation_rewrite",
                        "original_question": question,
                        "resolved_question": resolved_question,
                    }

        result = self._answer_question(resolved_question)
        if context_metadata is not None:
            result = copy.deepcopy(result)
            result["context_resolved"] = context_metadata
        return result

    def _rewrite_followup_question(
        self,
        question: str,
        history: list[Any],
    ) -> str:
        from google.genai import types

        messages: list[str] = []
        for item in history[-8:]:
            role, content = history_message_text(item)
            if not content.strip():
                continue
            label = "사용자" if role == "user" else "챗봇"
            compact = re.sub(r"\s+", " ", content).strip()
            messages.append(f"{label}: {compact[:1200]}")
        if not messages:
            return question

        catalog = ", ".join(
            f"{item.course_code} {item.course_name}" for item in self.syllabi
        )
        prompt = f"""
너는 대학 수강신청 챗봇의 대화 문맥 해석기다.
최근 대화와 현재 질문을 보고, 현재 질문을 대화 없이도 이해되는 독립 질문으로 재작성하라.

규칙:
- 사용자가 앞 답변의 과목, 목록, 조건을 가리키면 해당 과목명과 학수번호를 명시한다.
- '그중', '나머지', '둘 중', '각각', '그러면', '교수는?', '과제는?' 같은 생략을 복원한다.
- 사용자가 완전히 새로운 질문을 했다면 원문을 그대로 반환한다.
- 답변을 작성하지 말고 질문만 재작성한다.
- 대화에 없는 과목이나 사실을 만들지 않는다.
- 아래 과목 목록의 표기를 우선 사용한다.

[과목 목록]
{catalog}

[최근 대화]
{chr(10).join(messages)}

[현재 질문]
{question}

JSON 한 개만 출력:
{{"is_followup": true 또는 false, "standalone_question": "재작성된 질문"}}
""".strip()
        try:
            response = self.gemini_client.models.generate_content(
                model=GENERATIVE_MODELS[0],
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=220,
                    response_mime_type="application/json",
                    thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                ),
            )
            payload = json.loads(extract_gemini_text(response).strip())
            if not bool(payload.get("is_followup")):
                return question
            rewritten = str(payload.get("standalone_question", "")).strip()
            if not rewritten or len(rewritten) > 1200:
                return question
            return rewritten
        except Exception as error:
            print(f"Follow-up rewrite failed: {error}", file=sys.stderr)
            return question

    def _answer_question(self, question: str) -> dict[str, Any]:
        started = time.perf_counter()
        cache_key = normalize_cache_key(question)
        cached = self._cached_answer(cache_key, started)
        if cached is not None:
            return cached

        clarification = build_clarification_answer(question)
        if clarification:
            clarification["timings"] = {
                "search_seconds": 0.0,
                "generation_seconds": 0.0,
                "total_seconds": round(time.perf_counter() - started, 3),
                "cache_hit": False,
            }
            self._cache_answer(cache_key, clarification)
            return clarification

        filtered_detail_result = build_structured_filtered_detail_answer(
            question, self.syllabi
        )
        if filtered_detail_result:
            filtered_detail_result["timings"] = {
                "search_seconds": 0.0,
                "generation_seconds": 0.0,
                "total_seconds": round(time.perf_counter() - started, 3),
                "cache_hit": False,
            }
            self._cache_answer(cache_key, filtered_detail_result)
            return filtered_detail_result

        schedule_result = build_structured_schedule_answer(question, self.syllabi)
        if schedule_result:
            schedule_result["timings"] = {
                "search_seconds": 0.0,
                "generation_seconds": 0.0,
                "total_seconds": round(time.perf_counter() - started, 3),
                "cache_hit": False,
            }
            self._cache_answer(cache_key, schedule_result)
            return schedule_result

        metadata_result = build_structured_course_metadata_answer(
            question, self.syllabi
        )
        if metadata_result:
            metadata_result["timings"] = {
                "search_seconds": 0.0,
                "generation_seconds": 0.0,
                "total_seconds": round(time.perf_counter() - started, 3),
                "cache_hit": False,
            }
            self._cache_answer(cache_key, metadata_result)
            return metadata_result

        assessment_result = build_structured_course_assessment_answer(
            question, self.syllabi
        )
        if assessment_result:
            assessment_result["timings"] = {
                "search_seconds": 0.0,
                "generation_seconds": 0.0,
                "total_seconds": round(time.perf_counter() - started, 3),
                "cache_hit": False,
            }
            self._cache_answer(cache_key, assessment_result)
            return assessment_result

        filter_result = build_structured_filter_answer(question, self.syllabi)
        if filter_result:
            filter_result["timings"] = {
                "search_seconds": 0.0,
                "generation_seconds": 0.0,
                "total_seconds": round(time.perf_counter() - started, 3),
                "cache_hit": False,
            }
            self._cache_answer(cache_key, filter_result)
            return filter_result

        feature_result = build_structured_feature_answer(question, self.syllabi)
        if feature_result:
            feature_result["timings"] = {
                "search_seconds": 0.0,
                "generation_seconds": 0.0,
                "total_seconds": round(time.perf_counter() - started, 3),
                "cache_hit": False,
            }
            self._cache_answer(cache_key, feature_result)
            return feature_result

        activity_result = build_structured_activity_answer(question, self.syllabi)
        if activity_result:
            activity_result["timings"] = {
                "search_seconds": 0.0,
                "generation_seconds": 0.0,
                "total_seconds": round(time.perf_counter() - started, 3),
                "cache_hit": False,
            }
            self._cache_answer(cache_key, activity_result)
            return activity_result

        catalog_result = build_structured_catalog_answer(
            question, self.syllabi, len(self.chunks)
        )
        if catalog_result:
            catalog_result["timings"] = {
                "search_seconds": 0.0,
                "generation_seconds": 0.0,
                "total_seconds": round(time.perf_counter() - started, 3),
                "cache_hit": False,
            }
            self._cache_answer(cache_key, catalog_result)
            return catalog_result

        learning_result = build_structured_learning_answer(question, self.syllabi)
        if learning_result:
            learning_result["timings"] = {
                "search_seconds": 0.0,
                "generation_seconds": 0.0,
                "total_seconds": round(time.perf_counter() - started, 3),
                "cache_hit": False,
            }
            self._cache_answer(cache_key, learning_result)
            return learning_result

        title_result = build_structured_title_answer(question, self.syllabi)
        if title_result:
            title_result["timings"] = {
                "search_seconds": 0.0,
                "generation_seconds": 0.0,
                "total_seconds": round(time.perf_counter() - started, 3),
                "cache_hit": False,
            }
            self._cache_answer(cache_key, title_result)
            return title_result

        if not is_syllabus_question(question, self.syllabi):
            result = {
                "answer": (
                    "이 챗봇은 빅데이터사이언스학부 강의계획서에 포함된 과목 내용, "
                    "평가방식, 과제, 시험, 수업 운영 정보를 답변합니다. "
                    "등록금·학사일정·수강신청 기간은 학교 공식 공지를 확인해 주세요."
                ),
                "sources": [],
                "mode": "out_of_scope",
                "timings": {
                    "search_seconds": 0.0,
                    "generation_seconds": 0.0,
                    "total_seconds": round(time.perf_counter() - started, 3),
                    "cache_hit": False,
                },
            }
            self._cache_answer(cache_key, result)
            return result

        structured = build_structured_absence_answer(question, self.syllabi)
        if structured:
            structured["timings"] = {
                "search_seconds": 0.0,
                "generation_seconds": 0.0,
                "total_seconds": round(time.perf_counter() - started, 3),
                "cache_hit": False,
            }
            self._cache_answer(cache_key, structured)
            return structured

        search_started = time.perf_counter()
        matches = self.search(question)
        matches = prioritize_explicit_course_matches(question, matches, self.syllabi)
        search_seconds = time.perf_counter() - search_started
        if not matches:
            result = {
                "answer": "강의계획서에서 관련 정보를 찾지 못했습니다. 과목명, 학수번호, 평가방식처럼 더 구체적으로 질문해 주세요.",
                "sources": [],
                "mode": "fallback",
                "timings": {
                    "search_seconds": round(search_seconds, 3),
                    "generation_seconds": 0.0,
                    "total_seconds": round(time.perf_counter() - started, 3),
                    "cache_hit": False,
                },
            }
            self._cache_answer(cache_key, result)
            return result

        generation_started = time.perf_counter()
        if self.gemini_client:
            generated = self._answer_with_gemini(question, matches)
            if generated:
                generated["timings"] = {
                    "search_seconds": round(search_seconds, 3),
                    "generation_seconds": round(time.perf_counter() - generation_started, 3),
                    "total_seconds": round(time.perf_counter() - started, 3),
                    "cache_hit": False,
                }
                self._cache_answer(cache_key, generated)
                return generated

        result = {
            "answer": build_extractive_answer(question, matches),
            "sources": source_payload(matches),
            "mode": "extractive",
            "timings": {
                "search_seconds": round(search_seconds, 3),
                "generation_seconds": round(time.perf_counter() - generation_started, 3),
                "total_seconds": round(time.perf_counter() - started, 3),
                "cache_hit": False,
            },
        }
        warning = getattr(self.request_state, "generation_warning", "") or getattr(
            self.request_state, "search_warning", ""
        )
        if warning:
            result["warning"] = warning
            result["warning_code"] = "gemini_unavailable"
        self._cache_answer(cache_key, result)
        return result

    def _cached_answer(self, key: str, started: float) -> dict[str, Any] | None:
        cached = self.answer_cache.get(key)
        if cached is None:
            return None
        cached["timings"] = {
            "search_seconds": 0.0,
            "generation_seconds": 0.0,
            "total_seconds": round(time.perf_counter() - started, 3),
            "cache_hit": True,
        }
        return cached

    def _cache_answer(self, key: str, result: dict[str, Any]) -> None:
        if not key or result.get("warning_code") == "gemini_unavailable":
            return
        self.answer_cache.put(key, result)

    def clear_cache(self) -> None:
        self.answer_cache.clear()

    def _answer_with_gemini(self, question: str, matches: list[Match]) -> dict[str, Any] | None:
        from google.genai import types

        self.request_state.generation_warning = ""

        context = "\n\n".join(
            "\n".join(
                [
                    f"[출처 {index}] {match.chunk.syllabus.title}",
                    f"문서 구역: {match.chunk.section}",
                    f"교수: {match.chunk.syllabus.professor}",
                    f"이수구분: {match.chunk.syllabus.completion_type or '미확인'}",
                    f"수업시간 및 강의실: {match.chunk.syllabus.schedule_summary or '미기재'}",
                    match.chunk.parent_text or match.chunk.text,
                ]
            )
            for index, match in enumerate(matches[:5], start=1)
        )
        prompt = f"""
당신은 고려대학교 세종캠퍼스 빅데이터사이언스학부 학생을 위한 수강 도우미입니다.
아래 강의계획서 내용만 근거로 답변하세요.
문서에 없는 내용은 추측하지 말고 "강의계획서에서 확인되지 않습니다"라고 답하세요.
답변에는 과목명과 학수번호를 함께 제시하세요.
추천이나 비교 질문이면 핵심 과목을 먼저 말하고, 근거를 짧게 덧붙이세요.
과목명만 보고 활동 내용을 추정하지 마세요.
추천 목록에는 과제, 평가방식, 주차계획, 수업운영원칙 등에서 근거가 확인되는 과목만 넣으세요.

[강의계획서 발췌]
{context}

[질문]
{question}
""".strip()
        try:
            generation_config = types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=700,
                thinking_config=types.ThinkingConfig(thinking_level="minimal"),
            )
            models = list(dict.fromkeys(GENERATIVE_MODELS))
            last_error: Exception | None = None
            for model_index, model in enumerate(models):
                for attempt in range(self.gemini_retry_attempts):
                    try:
                        response = self.gemini_client.models.generate_content(
                            model=model,
                            contents=prompt,
                            config=generation_config,
                        )
                        text = extract_gemini_text(response)
                        if text.strip():
                            result = {
                                "answer": text.strip(),
                                "sources": source_payload(matches),
                                "mode": (
                                    "chroma_gemini"
                                    if getattr(self.request_state, "search_backend", "keyword")
                                    in {"chroma", "hybrid", "hybrid_rewrite"}
                                    else "keyword_gemini"
                                ),
                                "model": model,
                            }
                            rewritten_query = getattr(self.request_state, "rewritten_query", "")
                            if rewritten_query:
                                result["rewritten_query"] = rewritten_query
                            search_warning = getattr(self.request_state, "search_warning", "")
                            if search_warning:
                                result["warning"] = search_warning
                            elif model_index > 0:
                                result["warning"] = "기본 생성 모델이 응답하지 않아 대체 모델로 처리했습니다."
                            elif last_error is not None:
                                result["warning"] = "Gemini의 일시 오류가 재시도 후 복구되었습니다."
                            return result
                        last_error = RuntimeError("Gemini returned empty text")
                        print(
                            f"Gemini generate returned empty text: {model}",
                            file=sys.stderr,
                            flush=True,
                        )
                        break
                    except Exception as error:
                        last_error = error
                        print(
                            f"Gemini generate failed: model={model} attempt={attempt + 1} error={error}",
                            file=sys.stderr,
                            flush=True,
                        )
                        should_retry = (
                            is_transient_api_error(error)
                            and attempt + 1 < self.gemini_retry_attempts
                        )
                        if not should_retry:
                            break
                        delay = min(self.gemini_retry_base_seconds * (2**attempt), 2.0)
                        if delay:
                            time.sleep(delay)
            if last_error is not None:
                self.request_state.generation_warning = friendly_api_warning(last_error)
        except Exception as error:
            print(f"Gemini generate failed: {error}", file=sys.stderr, flush=True)
            self.request_state.generation_warning = friendly_api_warning(error)
            return None
        return None


def extract_gemini_text(response: Any) -> str:
    text = getattr(response, "text", "") or ""
    if text:
        return str(text)

    parts_text: list[str] = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", "") or ""
            if part_text:
                parts_text.append(str(part_text))
    return "\n".join(parts_text)


def clean_text(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_schedule_entries_json(value: str) -> list[dict[str, Any]]:
    if not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [entry for entry in parsed if isinstance(entry, dict)] if isinstance(parsed, list) else []


def extract_completion_type(text: str) -> str:
    match = re.search(
        r"이수구분\s*[:：]?\s*(전공필수|전공선택|교양필수|교양선택|일반선택)",
        text,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else ""


def sanitize_retrieval_text(text: str) -> str:
    """Remove form controls whose selected state is lost during text extraction."""
    text = clean_text(text)
    text = re.sub(
        r"활동유형.*?(?=(?:▷\s*)?평가방법)",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"수업유형\s+(?:대면\s+대면\s+)?(?:비대면\s+비대면\s+)?"
        r"(?:병행\(대면&비대면 동시\)\s+병행\(대면&비대면 동시\)\s+)?미정",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return clean_text(text)


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
        if key.lower() in query or any(value.lower() in query for value in values):
            expanded.extend(tokenize(" ".join(values)))
    return list(dict.fromkeys(expanded))


CODING_QUERY_TERMS = {
    "코딩",
    "프로그래밍",
    "파이썬",
    "python",
    "r언어",
    "r 언어",
    "소프트웨어",
    "분석 도구",
}

CODING_EVIDENCE_TERMS = {
    "코딩",
    "프로그래밍",
    "파이썬",
    "python",
    "r ",
    " r/",
    "소프트웨어",
    "데이터분석소프트웨어",
    "통계학과파이썬",
    "실습",
    "분석",
    "알고리즘",
    "머신러닝",
    "딥러닝",
}

PROJECT_QUERY_TERMS = set(SYNONYMS["팀활동"] + SYNONYMS["프로젝트"])
PRACTICE_QUERY_TERMS = set(SYNONYMS["실습"])
ASSESSMENT_QUERY_TERMS = {"평가", "성적", "시험", "중간고사", "기말고사", "과제", "출석"}
BROAD_QUERY_TERMS = {"추천", "알려", "어떤", "있어", "목록", "비교", "수업", "과목"}
SYLLABUS_SCOPE_TERMS = {
    "강의",
    "수업",
    "과목",
    "교과목",
    "학수번호",
    "교수",
    "전공",
    "전공필수",
    "전공 필수",
    "전공선택",
    "전공 선택",
    "이수구분",
    "평가",
    "시험",
    "고사",
    "과제",
    "출석",
    "팀플",
    "팀활동",
    "팀 활동",
    "팀협력",
    "팀 협력",
    "팀워크",
    "협업",
    "조별",
    "프로젝트",
    "실습",
    "코딩",
    "프로그래밍",
    "파이썬",
    "내용",
    "배우",
    "추천",
    "비교",
    "학점",
    "영강",
    "교재",
    "주차",
    "커리큘럼",
    "학습내용",
    "학습 내용",
    "분반",
    "pbl",
    "캡스톤",
}

TITLE_NAME_CUES = {
    "과목명",
    "수업명",
    "강의명",
    "과목 이름",
    "수업 이름",
    "강의 이름",
    "이름에",
    "제목",
}
TITLE_ACTIVITY_CUES = {
    "활동",
    "진행",
    "하는 과목",
    "하는 수업",
    "프로젝트가 있는",
    "팀플",
    "발표",
    "과제",
}
TITLE_DETAIL_CUES = {
    "내용",
    "평가",
    "시험",
    "교수",
    "시간",
    "요일",
    "교시",
    "시간표",
    "강의실",
    "장소",
    "비교",
    "차이",
    "배우",
    "사용",
    "방식",
    "주차",
    "성적",
    "누가",
    "담당",
    "주제",
    "학점",
    "이수구분",
    "필수",
    "선택",
}
TITLE_QUERY_NOISE = sorted(
    {
        "강의 이름",
        "수업 이름",
        "과목 이름",
        "강의이름",
        "수업이름",
        "과목이름",
        "강의명",
        "수업명",
        "과목명",
        "이름에",
        "적혀있는",
        "적혀 있는",
        "들어있는",
        "들어 있는",
        "포함되어있는",
        "포함되어 있는",
        "들어간",
        "포함된",
        "과목들의",
        "수업들의",
        "강의들의",
        "과목들",
        "수업들",
        "강의들",
        "평가방식",
        "비교해주세요",
        "비교해줘",
        "수업내용",
        "강의내용",
        "알려주세요",
        "알려줘",
        "찾아주세요",
        "찾아줘",
        "추천해주세요",
        "추천해줘",
        "말해주세요",
        "말해줘",
        "나열해주세요",
        "나열해줘",
        "나열",
        "해주세요",
        "해줘",
        "이라고",
        "라고",
        "라는",
        "목록",
        "과목",
        "수업",
        "강의",
        "평가",
        "방식",
        "비교",
        "내용",
        "알려",
        "찾아",
        "추천",
        "무엇",
        "뭐야",
        "몇 개야",
        "몇개야",
        "몇 개인가",
        "몇개인가",
        "몇 개",
        "몇개",
        "총",
        "모두",
        "전부",
        "전체",
        "들의",
        "좀",
    },
    key=len,
    reverse=True,
)


def contains_any(text: str, terms: set[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def contains_phrase(text: str, phrases: tuple[str, ...] | list[str]) -> bool:
    compact = re.sub(r"\s+", "", text).casefold()
    return any(re.sub(r"\s+", "", phrase).casefold() in compact for phrase in phrases)


def evidence_excerpt(text: str, match: re.Match[str], max_len: int = 240) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_len:
        return compact
    center = match.start()
    start = max(0, center - max_len // 3)
    end = min(len(compact), start + max_len)
    prefix = "..." if start else ""
    suffix = "..." if end < len(compact) else ""
    return f"{prefix}{compact[start:end].strip()}{suffix}"


def teamwork_evidence(syllabus: Syllabus) -> str:
    for line in syllabus.text.splitlines():
        compact = re.sub(r"\s+", " ", line).strip()
        if not compact or any(marker in compact for marker in ACTIVITY_EVIDENCE_NOISE):
            continue
        for pattern in TEAMWORK_EVIDENCE_PATTERNS:
            match = pattern.search(compact)
            if match:
                return evidence_excerpt(compact, match)
    return ""


def build_structured_activity_answer(
    question: str, syllabi: list[Syllabus]
) -> dict[str, Any] | None:
    if not contains_phrase(question, TEAMWORK_QUERY_TERMS):
        return None
    if extract_course_codes(question) or detect_course_hint(question, syllabi):
        return None

    confirmed: list[tuple[Syllabus, str]] = []
    for syllabus in syllabi:
        evidence = teamwork_evidence(syllabus)
        if evidence:
            confirmed.append((syllabus, evidence))

    if not confirmed:
        return {
            "answer": "강의계획서에서 팀 활동이 명시된 과목을 찾지 못했습니다.",
            "sources": [],
            "mode": "structured_activity",
        }

    lines = [
        f"팀플·팀프로젝트·팀활동·조별활동을 같은 ‘팀 활동’으로 확인한 결과, 총 {len(confirmed)}개 과목입니다.",
        "",
    ]
    sources: list[dict[str, Any]] = []
    for syllabus, evidence in confirmed:
        lines.append(
            f"- {syllabus.course_code} {syllabus.course_name} "
            f"({syllabus.professor or '담당교수 미기재'})"
        )
        lines.append(f"  - 근거: {evidence}")
        sources.extend(syllabus_metadata_sources([syllabus], evidence))
    lines.extend(
        [
            "",
            "과목명에 PBL이나 캡스톤이 있다는 이유만으로 포함하지 않고, 강의계획서에 팀 기반·조별·그룹 활동이 명시된 경우만 포함했습니다.",
        ]
    )
    return {
        "answer": "\n".join(lines),
        "sources": sources,
        "mode": "structured_activity",
    }


def feature_evidence(syllabus: Syllabus, topic: str) -> str:
    config = FEATURE_TOPICS[topic]
    for title_pattern in config.get("title_patterns", ()):
        match = title_pattern.search(syllabus.course_name)
        if match:
            return f"과목명: {syllabus.course_name}"

    for line in syllabus.text.splitlines():
        compact = re.sub(r"\s+", " ", line).strip()
        if not compact or any(marker in compact for marker in FEATURE_EVIDENCE_NOISE):
            continue
        for pattern in config["patterns"]:
            match = pattern.search(compact)
            if match:
                return evidence_excerpt(compact, match)
    return ""


def build_course_condition_filters(
    question: str, syllabi: list[Syllabus]
) -> list[CourseCondition]:
    normalized_question = normalize_title_text(question)
    compact_question = re.sub(r"\s+", "", question).casefold()
    conditions: list[CourseCondition] = []
    seen: set[str] = set()

    def add(
        key: str,
        display: str,
        source_note: str,
        predicate: Callable[[Syllabus], bool],
    ) -> None:
        if key in seen:
            return
        seen.add(key)
        conditions.append(CourseCondition(display, source_note, predicate))

    if "pbl" in normalized_question:
        add(
            "title:pbl",
            "PBL 과목",
            "과목명에 PBL이 포함되어 있습니다.",
            lambda item: "pbl" in normalize_title_text(item.course_name),
        )
    if "캡스톤" in normalized_question:
        add(
            "title:capstone",
            "캡스톤 과목",
            "과목명에 캡스톤이 포함되어 있습니다.",
            lambda item: "캡스톤" in normalize_title_text(item.course_name),
        )
    if contains_phrase(question, SYNONYMS["영강"]):
        add(
            "title:english",
            "영강 과목",
            "과목명에 영강 또는 영어강의가 표시되어 있습니다.",
            lambda item: "영강" in item.course_name or "영어강의" in item.course_name,
        )

    for completion_type in ("전공필수", "전공선택", "교양필수", "교양선택", "일반선택"):
        if completion_type in compact_question:
            add(
                f"completion:{completion_type}",
                f"{completion_type} 과목",
                f"강의계획안 이수구분이 {completion_type}로 기재되어 있습니다.",
                lambda item, completion_type=completion_type: item.completion_type == completion_type,
            )

    credit_match = re.search(r"(\d+(?:\.\d+)?)\s*학점", question.casefold())
    if credit_match:
        requested_credit = re.sub(r"\.0+$", "", credit_match.group(1))

        def credit_matches(item: Syllabus, requested_credit: str = requested_credit) -> bool:
            credit = re.sub(r"\.0+$", "", str(item.credit).strip())
            if credit == requested_credit:
                return True
            pattern = re.compile(rf"학점\s+{re.escape(requested_credit)}(?:\.0+)?(?:\s|$)")
            return bool(pattern.search(item.text))

        add(
            f"credit:{requested_credit}",
            f"{requested_credit}학점 과목",
            f"학점 항목이 {requested_credit}학점으로 기재되어 있습니다.",
            credit_matches,
        )

    professor_names = [
        professor
        for professor in dict.fromkeys(item.professor for item in syllabi)
        if professor and professor in question
    ]
    for professor in professor_names:
        add(
            f"professor:{professor}",
            f"{professor} 교수 과목",
            f"담당교수가 {professor} 교수로 기재되어 있습니다.",
            lambda item, professor=professor: item.professor == professor,
        )

    negative_cues = ("없", "안 보", "보지 않", "미실시", "제외")
    assessment_absence_terms: list[tuple[str, tuple[str, ...]]] = [
        ("중간고사", ("중간고사", "중간시험", "midterm")),
        ("기말고사", ("기말고사", "기말시험", "final exam")),
    ]
    lowered_question = question.casefold()
    if any(cue in lowered_question for cue in negative_cues):
        for label, aliases in assessment_absence_terms:
            if not any(alias in lowered_question for alias in aliases):
                continue
            absence_patterns = [
                re.compile(
                    rf"(?:{'|'.join(re.escape(alias) for alias in aliases)})"
                    r"\s*(?:[:：-]\s*)?(?:없음|없다|미실시|실시하지\s*않음|0(?:\.0+)?\s*%)",
                    re.IGNORECASE,
                ),
                re.compile(
                    rf"(?:없음|미실시|실시하지\s*않음)\s*(?:[:：-]\s*)?"
                    rf"(?:{'|'.join(re.escape(alias) for alias in aliases)})",
                    re.IGNORECASE,
                ),
            ]
            add(
                f"absence:{label}",
                f"{label}가 없다고 명시된 과목",
                f"강의계획서에 {label} 미실시가 명시되어 있습니다.",
                lambda item, absence_patterns=absence_patterns: any(
                    pattern.search(item.text) for pattern in absence_patterns
                ),
            )

    has_teamwork = contains_phrase(question, TEAMWORK_QUERY_TERMS)
    matched_topics = [
        topic
        for topic, config in FEATURE_TOPICS.items()
        if contains_phrase(question, config["aliases"])
    ]
    if has_teamwork and "프로젝트" in matched_topics:
        matched_topics.remove("프로젝트")
    if ("title:pbl" in seen or "title:capstone" in seen) and "프로젝트" in matched_topics:
        matched_topics.remove("프로젝트")

    if has_teamwork:
        add(
            "feature:teamwork",
            "팀활동 명시 과목",
            "강의계획서에 팀 기반·조별·그룹 활동이 명시되어 있습니다.",
            lambda item: bool(teamwork_evidence(item)),
        )
    for topic in matched_topics:
        display = str(FEATURE_TOPICS[topic]["display"])
        add(
            f"feature:{topic}",
            f"{display} 명시 과목",
            f"강의계획서에 {display} 관련 활동이 명시되어 있습니다.",
            lambda item, topic=topic: bool(feature_evidence(item, topic)),
        )

    return conditions


def combine_course_condition_text(
    schedule_parts: list[str], conditions: list[CourseCondition]
) -> str:
    schedule_text = " ".join(schedule_parts).strip()
    if not conditions:
        return f"{schedule_text} 수업" if schedule_text else "전체 시간표"

    if len(conditions) == 1:
        filter_text = conditions[0].display
    elif len(conditions) == 2:
        filter_text = f"{conditions[0].display}이면서 {conditions[1].display}"
    else:
        filter_text = "·".join(condition.display for condition in conditions)
        filter_text = f"{filter_text} 조건을 모두 만족하는 과목"

    if schedule_text:
        return f"{schedule_text} 수업 중 {filter_text}"
    return filter_text


SCHEDULE_CUES = {
    "언제",
    "요일",
    "몇 시",
    "몇시",
    "시간",
    "교시",
    "시간표",
    "강의실",
    "어디서",
    "장소",
}
DAY_ALIASES = {
    "월요일": "월",
    "화요일": "화",
    "수요일": "수",
    "목요일": "목",
    "금요일": "금",
    "토요일": "토",
    "일요일": "일",
    "월욜": "월",
    "화욜": "화",
    "수욜": "수",
    "목욜": "목",
    "금욜": "금",
}
DAY_DISPLAY = {
    "월": "월요일",
    "화": "화요일",
    "수": "수요일",
    "목": "목요일",
    "금": "금요일",
    "토": "토요일",
    "일": "일요일",
}
PERIOD_PATTERN = re.compile(r"(\d+)\s*(?:-|~|부터)?\s*(\d+)?\s*교시")


def parse_schedule_filter(question: str) -> tuple[str, re.Match[str] | None, int, int, list[str]]:
    selected_day = next(
        (day for label, day in DAY_ALIASES.items() if label in question), ""
    )
    period_match = PERIOD_PATTERN.search(question)
    requested_start = int(period_match.group(1)) if period_match else 0
    requested_end = int(period_match.group(2) or period_match.group(1)) if period_match else 0
    parts: list[str] = []
    if selected_day:
        parts.append(DAY_DISPLAY.get(selected_day, f"{selected_day}요일"))
    if period_match:
        period_text = (
            str(requested_start)
            if requested_start == requested_end
            else f"{requested_start}-{requested_end}"
        )
        parts.append(f"{period_text}교시")
    return selected_day, period_match, requested_start, requested_end, parts


def period_value(entry: dict[str, Any], key: str) -> int:
    try:
        return int(entry.get(key, 0))
    except (TypeError, ValueError):
        return 0


def schedule_entries_matching_question(
    syllabus: Syllabus,
    selected_day: str,
    period_match: re.Match[str] | None,
    requested_start: int,
    requested_end: int,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for entry in syllabus.schedule_entries:
        if selected_day and str(entry.get("day", "")) != selected_day:
            continue
        if period_match:
            start = period_value(entry, "start_period")
            end = period_value(entry, "end_period")
            if end < requested_start or start > requested_end:
                continue
        entries.append(entry)
    return entries


def schedule_entry_line(entry: dict[str, Any]) -> str:
    class_no = str(entry.get("class_no", ""))
    display = str(entry.get("display", ""))
    return f"{class_no}분반 {display}".strip()


def filtered_courses_for_question(
    question: str,
    syllabi: list[Syllabus],
) -> tuple[list[tuple[Syllabus, list[dict[str, Any]]]], str, str, list[CourseCondition]] | None:
    conditions = build_course_condition_filters(question, syllabi)
    selected_day, period_match, requested_start, requested_end, schedule_parts = parse_schedule_filter(question)
    has_schedule_constraint = bool(selected_day or period_match)
    if not conditions and not has_schedule_constraint:
        return None

    matched: list[tuple[Syllabus, list[dict[str, Any]]]] = []
    for syllabus in syllabi:
        if not all(condition.predicate(syllabus) for condition in conditions):
            continue
        entries: list[dict[str, Any]] = []
        if has_schedule_constraint:
            entries = schedule_entries_matching_question(
                syllabus,
                selected_day,
                period_match,
                requested_start,
                requested_end,
            )
            if not entries:
                continue
        matched.append((syllabus, entries))

    subject = combine_course_condition_text(schedule_parts, conditions)
    if subject == "전체 시간표":
        subject = "전체 시간표에 포함된 과목"
    source_note = " / ".join(
        (
            ["현재 데이터에 등록된 강의시간/강의실 항목입니다."]
            if has_schedule_constraint
            else []
        )
        + [condition.source_note for condition in conditions]
    )
    return matched, subject, source_note, conditions


FILTER_LIST_CUES = {
    "과목",
    "수업",
    "강의",
    "목록",
    "알려",
    "찾아",
    "추천",
    "뭐야",
    "있어",
    "나열",
    "보여",
    "몇 개",
    "몇개",
    "개야",
}
FILTER_DETAIL_SKIP_CUES = {
    "비교",
    "차이",
    "평가방식",
    "평가 방식",
    "평가는",
    "평가를",
    "교수는",
    "교수님은",
    "과제는",
    "시험은",
    "뭘 배워",
    "뭐 배워",
    "무엇을 배워",
    "수업 내용",
    "학습 내용",
    "주차별",
}


def is_filter_list_question(question: str) -> bool:
    if contains_phrase(question, LEARNING_QUERY_TERMS):
        return False
    if contains_any(question, FILTER_DETAIL_SKIP_CUES):
        return False
    return contains_any(question, FILTER_LIST_CUES)


ASSESSMENT_DETAIL_CUES = {
    "평가방식",
    "평가 방식",
    "평가는",
    "평가를",
    "성적",
    "배점",
    "비율",
    "비중",
    "어떻게 평가",
}


def is_assessment_detail_question(question: str) -> bool:
    if contains_any(question, ASSESSMENT_DETAIL_CUES):
        return True
    if "비교" in question and contains_any(
        question, {"평가", "시험", "중간", "기말", "과제", "출석"}
    ):
        return True
    return bool(
        re.search(
            r"(?:과제|시험|중간|기말|출석)(?:은|는|이|가|을|를)?\s*"
            r"(?:어떻게|뭐|무엇|몇|비중|비율|배점)",
            question,
        )
    )


def build_structured_filter_answer(
    question: str,
    syllabi: list[Syllabus],
) -> dict[str, Any] | None:
    if extract_course_codes(question) or detect_course_hint(question, syllabi):
        return None
    if not is_filter_list_question(question):
        return None

    filtered = filtered_courses_for_question(question, syllabi)
    if not filtered:
        return None
    matched, subject, source_note, conditions = filtered
    has_schedule_constraint = any(entries for _, entries in matched) or bool(
        parse_schedule_filter(question)[0] or parse_schedule_filter(question)[1]
    )
    if not has_schedule_constraint and len(conditions) < 2:
        return None

    lines: list[str] = []
    for syllabus, entries in matched:
        if entries:
            details = " / ".join(schedule_entry_line(entry) for entry in entries)
            lines.append(f"- {syllabus.course_code} {syllabus.course_name}: {details}")
        else:
            lines.append(
                f"- {syllabus.course_code} {syllabus.course_name} "
                f"({syllabus.professor or '담당교수 미기재'})"
            )

    if lines:
        answer = (
            f"현재 등록된 시간표와 강의계획서 기준으로 {subject}은 "
            f"{len(matched)}개입니다.\n\n"
            + "\n".join(lines)
        )
    else:
        answer = f"현재 등록된 시간표와 강의계획서 기준으로 {subject}은 확인되지 않았습니다."

    return {
        "answer": answer,
        "sources": syllabus_metadata_sources(
            [syllabus for syllabus, _ in matched],
            source_note or "질문 조건을 만족하는 과목 메타데이터입니다.",
        ),
        "mode": "structured_filter",
    }


def build_structured_filtered_detail_answer(
    question: str,
    syllabi: list[Syllabus],
) -> dict[str, Any] | None:
    if extract_course_codes(question) or detect_course_hint(question, syllabi):
        return None

    filtered = filtered_courses_for_question(question, syllabi)
    if not filtered:
        return None
    matched, subject, source_note, conditions = filtered
    if not conditions:
        return None
    courses = [syllabus for syllabus, _ in matched]

    if contains_phrase(question, LEARNING_QUERY_TERMS):
        if not courses:
            return {
                "answer": f"{subject}은 확인되지 않아 학습내용을 정리할 수 없습니다.",
                "sources": [],
                "mode": "structured_filter_learning",
            }
        result = build_learning_answer_for_courses(question, courses)
        result["answer"] = (
            f"{subject} {len(courses)}개를 기준으로 학습내용을 정리했습니다.\n\n"
            + result["answer"]
        )
        result["mode"] = "structured_filter_learning"
        return result

    if is_assessment_detail_question(question):
        if not courses:
            return {
                "answer": f"{subject}은 확인되지 않아 평가방법을 정리할 수 없습니다.",
                "sources": [],
                "mode": "structured_filter_assessment",
            }
        lines = [f"{subject} {len(courses)}개 과목의 평가방법을 확인했습니다.", ""]
        sources: list[dict[str, Any]] = []
        for course in courses:
            summary = contextual_assessment_summary(course)
            lines.append(f"### {course.course_code} {course.course_name}")
            if summary:
                lines.append(f"- {summary}")
                snippet = summary
            else:
                lines.append("- 강의계획서에서 구체적인 평가방법을 확인하지 못했습니다.")
                snippet = "구체적인 평가방법을 확인하지 못했습니다."
            lines.append("")
            sources.extend(syllabus_metadata_sources([course], snippet))
        return {
            "answer": "\n".join(lines).strip(),
            "sources": sources,
            "mode": "structured_filter_assessment",
        }

    return None


def build_structured_feature_answer(
    question: str, syllabi: list[Syllabus]
) -> dict[str, Any] | None:
    compact_question = re.sub(r"\s+", "", question).casefold()
    if any(title_term in compact_question for title_term in ("현장실습", "프로젝트학기")):
        return None

    has_teamwork = contains_phrase(question, TEAMWORK_QUERY_TERMS)
    matched_topics = [
        topic
        for topic, config in FEATURE_TOPICS.items()
        if contains_phrase(question, config["aliases"])
    ]

    # `팀프로젝트` 자체가 일반 프로젝트 조건까지 중복으로 켜지지 않게 한다.
    if has_teamwork and "프로젝트" in matched_topics:
        matched_topics.remove("프로젝트")
    if not matched_topics:
        return None
    if extract_course_codes(question) or detect_course_hint(question, syllabi):
        return None

    requested_topics = (["팀활동"] if has_teamwork else []) + matched_topics
    confirmed: list[tuple[Syllabus, list[tuple[str, str]]]] = []
    for syllabus in syllabi:
        evidence_items: list[tuple[str, str]] = []
        if has_teamwork:
            evidence = teamwork_evidence(syllabus)
            if evidence:
                evidence_items.append(("팀활동", evidence))
        for topic in matched_topics:
            evidence = feature_evidence(syllabus, topic)
            if evidence:
                evidence_items.append((topic, evidence))
        if evidence_items:
            confirmed.append((syllabus, evidence_items))

    display_terms = []
    if has_teamwork:
        display_terms.append("팀플·팀프로젝트·팀활동·조별활동")
    display_terms.extend(str(FEATURE_TOPICS[topic]["display"]) for topic in matched_topics)
    display = " / ".join(display_terms)
    if not confirmed:
        return {
            "answer": f"강의계획서에서 {display} 활동이 명시된 과목을 찾지 못했습니다.",
            "sources": [],
            "mode": "structured_feature",
        }

    condition_word = "조건 중 하나 이상" if len(requested_topics) > 1 else "조건"
    lines = [
        f"{display}을(를) 같은 주제로 묶어 강의계획서의 명시적 근거를 확인했습니다.",
        f"{condition_word}에 해당하는 과목은 총 {len(confirmed)}개입니다.",
        "",
    ]
    sources: list[dict[str, Any]] = []
    for syllabus, evidence_items in confirmed:
        lines.append(
            f"- {syllabus.course_code} {syllabus.course_name} "
            f"({syllabus.professor or '담당교수 미기재'})"
        )
        for topic, evidence in evidence_items:
            lines.append(f"  - {topic} 근거: {evidence}")
        source_evidence = " / ".join(
            f"{topic}: {evidence}" for topic, evidence in evidence_items
        )
        sources.extend(syllabus_metadata_sources([syllabus], source_evidence))

    lines.extend(
        [
            "",
            "공통 안내문·빈 입력 항목·역량표 문구는 제외하고, 수업 운영·평가·주차 계획에 실제 활동이 적힌 경우만 포함했습니다.",
        ]
    )
    return {
        "answer": "\n".join(lines),
        "sources": sources,
        "mode": "structured_feature",
    }


LEARNING_QUERY_TERMS = (
    "뭘 배워", "뭘 배우", "뭐 배워", "뭐 배우", "무엇을 배워", "무엇을 배우",
    "뭘 배우나요", "뭐 배우나요", "배우는 거", "배우는 것", "뭐 해", "뭘 해",
    "어떤 걸 배워",
    "어떤 내용을", "수업 내용", "수업내용", "강의 내용", "강의내용",
    "학습 내용", "학습내용", "주별 학습", "주차별", "커리큘럼", "무엇을 다뤄",
    "무슨 내용", "내용을 다뤄", "뭘 다뤄", "다루는 내용", "배우는 내용",
)
LEARNING_SECTION_ORDER = ("주별학습내용", "교과목개요", "학습목표")
LEARNING_LINE_NOISE = {
    "주", "기간", "회차", "학습내용", "핵심역량", "핵심 역량", "교재",
    "활동 및 설계내용", "활동 및", "설계내용", "week", "learning contents",
    "textbook", "activity & design", "배포자료", "별도 교제", "별도 교재",
    "강의계획", "주교제", "주교재", "보조교제", "보조교재",
}


def section_text(syllabus: Syllabus, section_name: str) -> str:
    parts = [text for name, text in split_sections(syllabus.text) if name == section_name]
    return clean_text("\n".join(parts))


def learning_topics(text: str, include_all: bool = False) -> list[str]:
    topics: list[str] = []
    seen: set[str] = set()
    limit = 40 if include_all else 12

    compact_text = re.sub(r"\s+", "", text)
    if "주기간회차" in compact_text and "학습내용" in compact_text:
        competency_terms = (
            "개척정신", "공유협력", "실무실용", "창의융합", "글로벌리더십",
            "도전정신", "미래안목", "공감력", "사회적실천력", "전문성",
        )
        week_pattern = re.compile(
            r"(?=(?:1[0-6]|[1-9])(?=\d{1,2}\.\d{1,2}\s*[~\-]))"
        )
        for segment in week_pattern.split(compact_text):
            if not re.match(r"(?:1[0-6]|[1-9])\d{1,2}\.\d{1,2}", segment):
                continue
            content_start = -1
            for term in competency_terms:
                position = segment.find(term)
                if position >= 0:
                    content_start = position + len(term)
                    break
            if content_start < 0:
                continue
            topic = segment[content_start:]
            topic = re.split(
                r"(?:\d+장|중간(?:고사|시험)|기말(?:고사|시험))",
                topic,
                maxsplit=1,
            )[0]
            topic = re.sub(r"\s+", " ", topic).strip(" ,-/")
            if len(topic) >= 3 and topic not in seen:
                seen.add(topic)
                topics.append(topic[:260])
            if len(topics) >= limit:
                return topics

    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip(" -▷◆※\t")
        lowered = line.casefold()
        if not line or lowered in LEARNING_LINE_NOISE:
            continue
        if re.search(r"(?:주별\s*학습내용|weekly\s+learning\s+content)", line, re.IGNORECASE):
            continue
        if re.fullmatch(r"\[page\s*\d+\]", line, re.IGNORECASE):
            continue
        if "learning contents" in lowered and "textbook" in lowered:
            continue
        if any(marker in line for marker in ACTIVITY_EVIDENCE_NOISE):
            continue
        if re.fullmatch(r"(?:\d{1,2}|\d{1,2}\s*[-~]\s*\d{1,2})", line):
            continue
        if re.fullmatch(r"\d{1,2}[./-]\d{1,2}(?:\s*[-~]\s*\d{1,2}[./-]\d{1,2})?", line):
            continue
        if re.fullmatch(r"(?:\d+\s*)?(?:중간|기말)(?:고사|시험|발표)?", line, re.IGNORECASE):
            continue
        if re.fullmatch(r"(?:midterm|final)(?:\s+exam)?", line, re.IGNORECASE):
            continue
        if re.fullmatch(r"\d{1,2}장", line):
            continue
        if re.search(r"(?:중간|기말)(?:고사|시험).*복습", line):
            continue
        line = re.sub(r"^\d{1,2}\s+", "", line).strip()
        if re.fullmatch(r"(?:중간|기말)(?:고사|시험|발표)?", line, re.IGNORECASE):
            continue
        if re.fullmatch(r"(?:midterm|final)(?:\s+exam)?", line, re.IGNORECASE):
            continue
        if not line or len(line) < 3:
            continue
        if len(line) > 260:
            line = line[:257].rstrip() + "..."
        key = re.sub(r"[^가-힣a-z0-9]+", "", line.casefold())
        if not key or key in seen:
            continue
        seen.add(key)
        topics.append(line)
        if len(topics) >= limit:
            break
    return topics


def section_summary(text: str, heading: str, max_len: int = 420) -> str:
    cleaned = clean_text(text)
    heading_match = re.search(re.escape(heading), cleaned, flags=re.IGNORECASE)
    if heading_match:
        cleaned = cleaned[heading_match.end():]
    cleaned = re.sub(r"^\s*\d*\.?\s*", "", cleaned)
    stop_markers = ("핵심역량", "추천 선수과목", "설계목표", "수강요건")
    for marker in stop_markers:
        if marker in cleaned:
            cleaned = cleaned.split(marker, 1)[0]
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_len] + ("..." if len(cleaned) > max_len else "")


def build_learning_answer_for_courses(
    question: str,
    targets: list[Syllabus],
) -> dict[str, Any]:
    include_all = contains_any(question, {"전부", "모두", "전체", "주차별", "주별"})
    answer_lines: list[str] = []
    sources: list[dict[str, Any]] = []
    for syllabus in targets:
        overview = section_summary(section_text(syllabus, "교과목개요"), "교과목개요")
        objective = section_summary(section_text(syllabus, "학습목표"), "학습목표")
        weekly_text = section_text(syllabus, "주별학습내용")
        topics = learning_topics(weekly_text, include_all=include_all)

        answer_lines.append(f"### {syllabus.course_code} {syllabus.course_name}")
        if overview:
            answer_lines.append(f"- 교과목 개요: {overview}")
        if objective:
            answer_lines.append(f"- 학습목표: {objective}")
        if topics:
            answer_lines.append("- 주별 학습내용의 주요 주제:")
            answer_lines.extend(f"  - {topic}" for topic in topics)
        elif weekly_text:
            answer_lines.append(f"- 주별 학습내용: {re.sub(r'\s+', ' ', weekly_text)[:700]}")
        else:
            answer_lines.append("- 주별 학습내용은 강의계획서에서 별도로 확인되지 않습니다.")

        evidence = " / ".join(topics[:4]) or overview or objective
        sources.extend(
            syllabus_metadata_sources(
                [syllabus],
                f"주별학습내용·교과목개요·학습목표 근거: {evidence[:300]}",
            )
        )
        answer_lines.append("")

    answer_lines.append("강의계획서의 주별 학습내용, 교과목 개요, 학습목표를 우선순위로 확인했습니다.")
    return {
        "answer": "\n".join(answer_lines).strip(),
        "sources": sources,
        "mode": "structured_learning",
    }


def build_structured_learning_answer(
    question: str, syllabi: list[Syllabus]
) -> dict[str, Any] | None:
    if not contains_phrase(question, LEARNING_QUERY_TERMS):
        return None

    targets = explicitly_requested_courses(question, syllabi)
    if not targets:
        return None

    return build_learning_answer_for_courses(question, targets)


def is_syllabus_question(question: str, syllabi: list[Syllabus]) -> bool:
    requested_years = {int(year) for year in re.findall(r"(20\d{2})\s*학년도?", question)}
    if requested_years and requested_years != {2026}:
        return False
    if contains_any(question, {"2학기", "여름학기", "겨울학기", "계절학기"}):
        return False
    if contains_any(
        question,
        {
            "맛집",
            "날씨",
            "비트코인",
            "주가",
            "축구 경기",
            "야구 경기",
            "웹 크롤러",
            "크롤러 만들어",
            "해부학",
            "헌법 강의",
            "양자역학",
        },
    ) and not explicitly_requested_courses(question, syllabi):
        return False
    if re.search(r"(?<![A-Za-z0-9])[A-Za-z]{2,}\d{3}(?![A-Za-z0-9])", question):
        return True
    if any(syllabus.course_name and syllabus.course_name in question for syllabus in syllabi):
        return True
    if contains_any(
        question,
        {
            "등록금",
            "납부일",
            "기숙사",
            "졸업요건",
            "졸업 요건",
            "졸업하려면",
            "장학금",
            "수강신청 기간",
            "학사일정",
        },
    ):
        return False
    return contains_any(question, SYLLABUS_SCOPE_TERMS)


def build_clarification_answer(question: str) -> dict[str, Any] | None:
    lowered = question.casefold().strip()
    reference_terms = {"그거", "이거", "저거", "아까", "그 수업", "그 과목", "해당 과목"}
    has_reference = any(term in lowered for term in reference_terms)
    has_course_code = detect_course_code_text(question) is not None
    has_named_criterion = contains_any(
        question,
        CODING_QUERY_TERMS
        | PROJECT_QUERY_TERMS
        | PRACTICE_QUERY_TERMS
        | ASSESSMENT_QUERY_TERMS
        | {"영강", "영어", "교수", "학점", "교재", "주차", "분반"},
    )

    if has_reference and not has_course_code:
        return {
            "answer": (
                "어떤 과목이나 조건을 가리키는지 확인이 필요합니다. "
                "과목명 또는 학수번호와 함께 궁금한 항목을 적어 주세요.\n\n"
                "예: `BDSC201 평가방식 알려줘`, `PBL 과목들의 과제를 비교해줘`"
            ),
            "sources": [],
            "mode": "clarification",
        }

    vague_terms = {"좋은", "쉬운", "재밌는", "재미있는", "괜찮은", "부담 없는"}
    if contains_any(question, vague_terms) and not has_named_criterion and not has_course_code:
        return {
            "answer": (
                "추천 기준이 조금 더 필요합니다. ‘좋은 과목’은 사람마다 달라서 현재 정보만으로는 "
                "임의로 과목을 고르지 않겠습니다.\n\n"
                "코딩·실습 중심, 팀 프로젝트, 시험 비중, 영어강의, 관심 분야 중 무엇을 중요하게 "
                "보는지 알려 주세요."
            ),
            "sources": [],
            "mode": "clarification",
        }
    return None


def explicitly_requested_courses(
    question: str, syllabi: list[Syllabus]
) -> list[Syllabus]:
    """Return only the most specific course names/codes explicitly present."""
    requested_codes = set(extract_course_codes(question))
    if requested_codes:
        return [
            syllabus
            for syllabus in syllabi
            if syllabus.course_code.upper() in requested_codes
        ]

    normalized_question = normalize_title_text(question)
    candidates: list[tuple[int, Syllabus]] = []
    for syllabus in syllabi:
        full_name = normalize_title_text(syllabus.course_name)
        base_name = normalize_title_text(
            re.sub(r"\((?:영강|영어강의)\)", "", syllabus.course_name)
        )
        matched_length = max(
            (len(name) for name in {full_name, base_name} if name and name in normalized_question),
            default=0,
        )
        if matched_length:
            candidates.append((matched_length, syllabus))
    if not candidates:
        return []

    longest = max(length for length, _ in candidates)
    return [syllabus for length, syllabus in candidates if length == longest]


def build_structured_course_metadata_answer(
    question: str, syllabi: list[Syllabus]
) -> dict[str, Any] | None:
    targets = explicitly_requested_courses(question, syllabi)
    if not targets:
        return None

    requested_fields: list[tuple[str, Callable[[Syllabus], str]]] = []
    if contains_any(question, {"교수", "교수님", "담당", "가르쳐", "성함"}):
        requested_fields.append(("담당교수", lambda item: item.professor or "미기재"))
    if "학점" in question:
        requested_fields.append(("학점", lambda item: item.credit or "미기재"))
    if contains_any(
        question,
        {
            "이수구분",
            "전공필수",
            "전공선택",
            "교양필수",
            "교양선택",
            "필수 과목",
            "선택 과목",
            "과목 구분",
            "필수야",
            "선택이야",
        },
    ):
        requested_fields.append(("이수구분", lambda item: item.completion_type or "미기재"))
    if not requested_fields:
        return None

    lines = []
    for course in targets:
        details = " / ".join(
            f"{label}: {getter(course)}" for label, getter in requested_fields
        )
        lines.append(f"- {course.course_code} {course.course_name}: {details}")
    field_names = "·".join(label for label, _ in requested_fields)
    return {
        "answer": f"강의계획서 메타데이터에서 {field_names}을(를) 확인했습니다.\n\n" + "\n".join(lines),
        "sources": syllabus_metadata_sources(
            targets,
            f"강의계획서의 {field_names} 메타데이터입니다.",
        ),
        "mode": "structured_course_metadata",
    }


def build_structured_course_assessment_answer(
    question: str, syllabi: list[Syllabus]
) -> dict[str, Any] | None:
    if not contains_any(
        question,
        {"평가", "성적", "시험", "중간고사", "기말고사", "과제", "출석", "배점", "비중"},
    ):
        return None
    targets = explicitly_requested_courses(question, syllabi)
    if not targets:
        return None

    lines: list[str] = []
    sources: list[dict[str, Any]] = []
    for course in targets:
        summary = contextual_assessment_summary(course)
        lines.append(f"### {course.course_code} {course.course_name}")
        if summary:
            lines.append(f"- {summary}")
            snippet = summary
        else:
            lines.append("- 강의계획서에서 구체적인 평가방법을 확인하지 못했습니다.")
            snippet = "구체적인 평가방법을 확인하지 못했습니다."
        lines.append("")
        sources.extend(syllabus_metadata_sources([course], snippet))
    return {
        "answer": "\n".join(lines).strip(),
        "sources": sources,
        "mode": "structured_course_assessment",
    }


def build_structured_schedule_answer(
    question: str, syllabi: list[Syllabus]
) -> dict[str, Any] | None:
    schedule_cues = {
        "언제",
        "요일",
        "몇 시",
        "몇시",
        "시간",
        "교시",
        "시간표",
        "강의실",
        "어디서",
        "장소",
    }
    if not contains_any(question, schedule_cues):
        return None

    target_courses = explicitly_requested_courses(question, syllabi)

    def period_value(entry: dict[str, Any], key: str) -> int:
        try:
            return int(entry.get(key, 0))
        except (TypeError, ValueError):
            return 0

    def entry_line(entry: dict[str, Any]) -> str:
        class_no = str(entry.get("class_no", ""))
        display = str(entry.get("display", ""))
        return f"{class_no}분반 {display}".strip()

    day_aliases = {
        "월요일": "월",
        "화요일": "화",
        "수요일": "수",
        "목요일": "목",
        "금요일": "금",
        "토요일": "토",
        "일요일": "일",
        "월욜": "월",
        "화욜": "화",
        "수욜": "수",
        "목욜": "목",
        "금욜": "금",
    }
    selected_day = next(
        (day for label, day in day_aliases.items() if label in question), ""
    )
    period_match = re.search(r"(\d+)\s*(?:-|~|부터)?\s*(\d+)?\s*교시", question)
    requested_start = int(period_match.group(1)) if period_match else 0
    requested_end = int(period_match.group(2) or period_match.group(1)) if period_match else 0
    course_conditions = build_course_condition_filters(question, syllabi)

    def matching_entries(syllabus: Syllabus) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for entry in syllabus.schedule_entries:
            if selected_day and str(entry.get("day", "")) != selected_day:
                continue
            if period_match:
                start = period_value(entry, "start_period")
                end = period_value(entry, "end_period")
                if end < requested_start or start > requested_end:
                    continue
            entries.append(entry)
        return entries

    if target_courses:
        lines: list[str] = []
        available: list[Syllabus] = []
        for syllabus in target_courses:
            entries = matching_entries(syllabus)
            if entries:
                available.append(syllabus)
                lines.append(f"- {syllabus.course_code} {syllabus.course_name}")
                for entry in entries:
                    lines.append(f"  - {entry_line(entry)}")
            elif selected_day or period_match:
                continue
            else:
                available.append(syllabus)
                lines.append(
                    f"- {syllabus.course_code} {syllabus.course_name}: "
                    "수강신청 조회에 정규 요일·교시가 기재되어 있지 않습니다."
                )
        return {
            "answer": (
                "현재 데이터에 등록된 수업시간은 다음과 같습니다.\n\n"
                + "\n".join(lines)
                + "\n\n시각은 1교시 09:00 시작, 교시당 50분 기준으로 함께 표시했습니다."
            ),
            "sources": syllabus_metadata_sources(
                available,
                "현재 데이터에 등록된 강의시간/강의실 항목입니다.",
            ),
            "mode": "structured_schedule",
        }

    compact_question = normalize_title_text(question)
    list_all = contains_any(question, {"전체", "모든", "전부", "모두"}) or compact_question in {
        "시간표",
        "시간표알려줘",
        "시간표보여줘",
    }
    if not selected_day and not period_match and not list_all:
        return None

    matched: list[tuple[Syllabus, list[dict[str, Any]]]] = []
    for syllabus in syllabi:
        entries = matching_entries(syllabus)
        if entries and all(condition.predicate(syllabus) for condition in course_conditions):
            matched.append((syllabus, entries))

    condition_parts: list[str] = []
    if selected_day:
        day_display = {
            "월": "월요일",
            "화": "화요일",
            "수": "수요일",
            "목": "목요일",
            "금": "금요일",
            "토": "토요일",
            "일": "일요일",
        }.get(selected_day, f"{selected_day}요일")
        condition_parts.append(day_display)
    if period_match:
        period_text = (
            str(requested_start)
            if requested_start == requested_end
            else f"{requested_start}-{requested_end}"
        )
        condition_parts.append(f"{period_text}교시")
    condition = combine_course_condition_text(condition_parts, course_conditions)
    answer_subject = (
        "전체 시간표에 포함된 과목" if condition == "전체 시간표" else condition
    )

    lines = []
    for syllabus, entries in matched:
        details = " / ".join(entry_line(entry) for entry in entries)
        lines.append(f"- {syllabus.course_code} {syllabus.course_name}: {details}")
    if lines:
        answer = (
            f"현재 등록된 시간표와 강의계획서 기준으로 {answer_subject}은 "
            f"{len(matched)}개입니다.\n\n" + "\n".join(lines)
        )
    else:
        answer = f"현재 등록된 시간표와 강의계획서 기준으로 {answer_subject}은 확인되지 않았습니다."

    source_note = " / ".join(
        ["현재 데이터에 등록된 강의시간/강의실 항목입니다."]
        + [condition.source_note for condition in course_conditions]
    )
    return {
        "answer": answer,
        "sources": syllabus_metadata_sources(
            [syllabus for syllabus, _ in matched],
            source_note,
        ),
        "mode": "structured_schedule",
    }


def build_structured_catalog_answer(
    question: str, syllabi: list[Syllabus], chunk_count: int
) -> dict[str, Any] | None:
    lowered = question.casefold()
    count_query = any(
        cue in lowered for cue in ("몇 개", "몇개", "총 몇", "개야", "개인가", "개입니까")
    )
    complete_query = any(cue in lowered for cue in ("전체", "모든", "전부", "모두"))

    normalized_question = re.sub(r"\s+", "", lowered)
    completion_type = next(
        (
            label
            for label in ("전공필수", "전공선택", "교양필수", "교양선택", "일반선택")
            if label in normalized_question
        ),
        "",
    )
    if completion_type:
        courses = [item for item in syllabi if item.completion_type == completion_type]
        lines = [
            f"- {item.course_code} {item.course_name} ({item.professor or '담당교수 미기재'})"
            for item in courses
        ]
        if count_query:
            intro = f"강의계획안의 이수구분을 기준으로 {completion_type} 과목은 총 {len(courses)}개입니다."
        else:
            intro = f"강의계획안의 이수구분을 기준으로 확인된 {completion_type} 과목은 {len(courses)}개입니다."
        return {
            "answer": intro + ("\n\n" + "\n".join(lines) if lines else ""),
            "sources": syllabus_metadata_sources(
                courses, f"강의계획안 이수구분이 {completion_type}로 기재되어 있습니다."
            ),
            "mode": "structured_catalog",
        }

    professor_names = [
        professor
        for professor in dict.fromkeys(item.professor for item in syllabi)
        if professor and professor in question
    ]
    if professor_names and (
        complete_query
        or contains_any(question, {"수업", "과목", "강의", "목록", "담당", "맡은"})
    ):
        courses = [item for item in syllabi if item.professor in professor_names]
        lines = [
            f"- {item.course_code} {item.course_name} ({item.professor})" for item in courses
        ]
        return {
            "answer": (
                f"{', '.join(professor_names)} 교수님 담당 과목은 총 {len(courses)}개입니다.\n\n"
                + "\n".join(lines)
            ),
            "sources": syllabus_metadata_sources(courses, "담당교수 메타데이터가 일치합니다."),
            "mode": "structured_catalog",
        }

    if "검색 청크" in lowered and count_query:
        return {
            "answer": f"현재 검색용으로 구성된 청크는 총 {chunk_count}개입니다.",
            "sources": [],
            "mode": "structured_numeric",
        }

    credit_match = re.search(r"(\d+(?:\.\d+)?)\s*학점", lowered)
    if not credit_match:
        credit_match = re.search(r"학점(?:이|은|는)?\s*(\d+(?:\.\d+)?)", lowered)
    if credit_match and contains_any(question, {"과목", "수업", "강의", "목록", "전부", "전체"}):
        credit = credit_match.group(1)
        courses = [
            item
            for item in syllabi
            if str(item.credit).strip().removesuffix(".0") == credit.removesuffix(".0")
        ]
        lines = [f"- {item.course_code} {item.course_name}" for item in courses]
        return {
            "answer": (
                f"강의계획안에서 {credit}학점으로 확인되는 과목은 총 {len(courses)}개입니다."
                + ("\n\n" + "\n".join(lines) if lines else "")
            ),
            "sources": syllabus_metadata_sources(courses, f"학점 항목이 {credit}으로 기재되어 있습니다."),
            "mode": "structured_numeric",
        }

    if detect_title_keyword_courses(question, syllabi):
        return None

    asks_catalog = contains_any(question, {"과목", "교과목", "수업", "강의", "목록"})
    if count_query and asks_catalog and (
        complete_query or contains_any(question, {"수집", "보유", "데이터", "현재"})
    ):
        return {
            "answer": f"현재 수집되어 중복 제거된 강의계획서는 총 {len(syllabi)}개 과목입니다.",
            "sources": syllabus_metadata_sources(
                syllabi, "수집 완료된 과목 메타데이터에 포함되어 있습니다."
            ),
            "mode": "structured_numeric",
        }

    if complete_query and asks_catalog and contains_any(
        question, {"목록", "알려", "보여", "나열"}
    ):
        lines = [
            f"{index}. {item.course_code} {item.course_name} ({item.professor or '담당교수 미기재'})"
            for index, item in enumerate(syllabi, 1)
        ]
        return {
            "answer": (
                f"현재 수집되어 중복 제거된 전체 과목은 총 {len(syllabi)}개입니다.\n\n"
                + "\n".join(lines)
            ),
            "sources": syllabus_metadata_sources(
                syllabi, "수집 완료된 전체 과목 목록에 포함되어 있습니다."
            ),
            "mode": "structured_catalog",
        }

    if contains_any(question, {"평균", "총합", "합계"}) and not detect_course_code_text(question):
        return {
            "answer": (
                "여러 과목의 평가 비율을 한 번에 계산하기에는 강의계획서별 평가 항목과 결측 표기가 "
                "서로 달라 현재 상태에서 신뢰할 수 있는 집계를 만들기 어렵습니다. "
                "비교할 과목명이나 학수번호를 지정해 주세요."
            ),
            "sources": [],
            "mode": "structured_numeric",
        }
    return None


def normalize_title_text(text: str) -> str:
    roman_numerals = str.maketrans(
        {
            "Ⅰ": "I",
            "Ⅱ": "II",
            "Ⅲ": "III",
            "Ⅳ": "IV",
            "Ⅴ": "V",
        }
    )
    return re.sub(r"[^0-9a-z가-힣]+", "", text.translate(roman_numerals).casefold())


def detect_fixed_title_category(
    question: str, syllabi: list[Syllabus]
) -> tuple[str, list[Syllabus]] | None:
    normalized_question = normalize_title_text(question)
    categories = (
        (("pbl", "피비엘"), "pbl", "PBL"),
        (("캡스톤", "캡스톤디자인"), "캡스톤", "캡스톤"),
        (("현장실습", "인턴십"), "현장실습", "현장실습"),
    )
    for query_keywords, course_keyword, display_keyword in categories:
        if not any(keyword in normalized_question for keyword in query_keywords):
            continue
        courses = [
            syllabus
            for syllabus in syllabi
            if course_keyword in normalize_title_text(syllabus.course_name)
        ]
        if courses:
            return display_keyword, courses
    if contains_phrase(question, SYNONYMS["영강"]):
        courses = [
            syllabus
            for syllabus in syllabi
            if "영강" in syllabus.course_name or "영어강의" in syllabus.course_name
        ]
        if courses:
            return "영강", courses
    return None


def detect_title_keyword_courses(
    question: str, syllabi: list[Syllabus]
) -> tuple[str, list[Syllabus]] | None:
    fixed_category = detect_fixed_title_category(question, syllabi)
    if fixed_category:
        return fixed_category

    explicit_name_query = contains_any(question, TITLE_NAME_CUES)
    activity_query = contains_any(question, TITLE_ACTIVITY_CUES)
    if activity_query and not explicit_name_query:
        return None

    keyword = normalize_title_text(question)
    for noise in TITLE_QUERY_NOISE:
        keyword = keyword.replace(normalize_title_text(noise), "")
    keyword = re.sub(r"^(?:에|이|가|은|는|을|를|의)+", "", keyword)
    keyword = re.sub(r"(?:에|이|가|은|는|을|를|의)+$", "", keyword)
    if len(keyword) < 2:
        return None

    courses = [
        syllabus
        for syllabus in syllabi
        if keyword in normalize_title_text(syllabus.course_name)
    ]
    if not courses:
        return None
    return keyword, courses


def build_structured_title_answer(
    question: str, syllabi: list[Syllabus]
) -> dict[str, Any] | None:
    detected = detect_title_keyword_courses(question, syllabi)
    if not detected or contains_any(question, TITLE_DETAIL_CUES):
        return None

    keyword, courses = detected
    display_keyword = keyword.upper() if keyword.isascii() else keyword
    course_lines = [
        f"- {item.course_code} {item.course_name} ({item.professor or '담당교수 미기재'})"
        for item in courses
    ]
    return {
        "answer": (
            f"과목명에 '{display_keyword}'이(가) 포함된 과목은 {len(courses)}개입니다.\n\n"
            + "\n".join(course_lines)
        ),
        "sources": syllabus_metadata_sources(
            courses, f"과목명에 {display_keyword}이(가) 포함되어 있습니다."
        ),
        "mode": "structured_title",
    }


def syllabus_metadata_sources(
    syllabi: list[Syllabus], snippet: str
) -> list[dict[str, Any]]:
    return [
        {
            "course_code": item.course_code,
            "class_no": item.class_no,
            "course_name": item.course_name,
            "professor": item.professor,
            "completion_type": item.completion_type,
            "credit": item.credit,
            "class_hours": item.class_hours,
            "schedule_summary": item.schedule_summary,
            "schedule_entries": item.schedule_entries,
            "text_path": item.text_path,
            "syllabus_url": item.syllabus_url,
            "score": None,
            "snippet": snippet,
        }
        for item in syllabi
    ]


def build_structured_absence_answer(
    question: str, syllabi: list[Syllabus]
) -> dict[str, Any] | None:
    lowered = question.lower()
    negative_cues = ("없", "안 보", "보지 않", "미실시", "제외")
    if not any(cue in lowered for cue in negative_cues):
        return None

    assessment_names: list[tuple[str, tuple[str, ...]]] = [
        ("중간고사", ("중간고사", "중간시험", "midterm")),
        ("기말고사", ("기말고사", "기말시험", "final exam")),
    ]
    selected = next(
        (
            (label, aliases)
            for label, aliases in assessment_names
            if any(alias in lowered for alias in aliases)
        ),
        None,
    )
    if not selected:
        return None

    label, aliases = selected
    explicit_absence_patterns = [
        re.compile(
            rf"(?:{'|'.join(re.escape(alias) for alias in aliases)})"
            r"\s*(?:[:：-]\s*)?(?:없음|없다|미실시|실시하지\s*않음|0(?:\.0+)?\s*%)",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?:없음|미실시|실시하지\s*않음)\s*(?:[:：-]\s*)?"
            rf"(?:{'|'.join(re.escape(alias) for alias in aliases)})",
            re.IGNORECASE,
        ),
    ]

    confirmed: list[Syllabus] = []
    for syllabus in syllabi:
        if any(pattern.search(syllabus.text) for pattern in explicit_absence_patterns):
            confirmed.append(syllabus)

    if confirmed:
        course_lines = [
            f"- {item.course_code} {item.course_name} ({item.professor or '담당교수 미기재'})"
            for item in confirmed
        ]
        answer = (
            f"전체 {len(syllabi)}개 강의계획서를 확인한 결과, {label}가 없다고 명시된 과목은 "
            f"다음 {len(confirmed)}개입니다.\n\n"
            + "\n".join(course_lines)
            + "\n\n강의계획서의 명시적 표현만 근거로 판정했습니다."
        )
    else:
        answer = (
            f"전체 {len(syllabi)}개 강의계획서를 확인했지만, {label}가 없다고 명시된 과목은 "
            "확인되지 않았습니다. 평가 비율이 비어 있거나 상세 정보가 없는 경우는 "
            f"{label}가 없다는 뜻으로 추정하지 않았습니다."
        )

    return {
        "answer": answer,
        "sources": structured_syllabus_sources(confirmed),
        "mode": "structured",
    }


def structured_syllabus_sources(syllabi: list[Syllabus]) -> list[dict[str, Any]]:
    return syllabus_metadata_sources(
        syllabi, "강의계획서에 시험 미실시가 명시되어 있습니다."
    )


def build_retrieval_query(question: str) -> str:
    parts = [question]
    if contains_any(question, CODING_QUERY_TERMS):
        parts.append("Python R 프로그래밍 코딩 데이터분석 소프트웨어 실습 알고리즘 머신러닝")
    if contains_any(question, PROJECT_QUERY_TERMS):
        parts.append("프로젝트 PBL 캡스톤 팀 프로젝트 발표 과제")
    if contains_any(question, PRACTICE_QUERY_TERMS):
        parts.append("실습 구현 분석 소프트웨어 프로젝트 PBL")
    if contains_any(question, ASSESSMENT_QUERY_TERMS):
        parts.append("평가방식 성적 중간고사 기말고사 과제 출석 발표")
    return " ".join(parts)


def is_broad_recommendation(question: str) -> bool:
    return contains_any(question, BROAD_QUERY_TERMS) and detect_course_code_text(question) is None


def detect_course_code_text(question: str) -> str | None:
    codes = extract_course_codes(question)
    return codes[0] if codes else None


def extract_course_codes(question: str) -> list[str]:
    codes = re.findall(
        r"(?<![A-Za-z0-9])[A-Za-z]{2,}\d{3}(?![A-Za-z0-9])",
        question,
    )
    return list(dict.fromkeys(code.upper() for code in codes))


def prioritize_explicit_course_matches(
    question: str, matches: list[Match], syllabi: list[Syllabus]
) -> list[Match]:
    query_tokens = expand_query_tokens(tokenize(question))

    def best_course_matches(course: Syllabus, limit: int) -> list[Match]:
        candidates: dict[str, Match] = {}
        for match in matches:
            if match.chunk.syllabus.course_key != course.course_key:
                continue
            identity = match.chunk.chunk_id or f"retrieved-{match.chunk.index}-{match.chunk.text[:80]}"
            candidates[identity] = match

        for chunk in RAG.chunks:
            if chunk.syllabus.course_key != course.course_key:
                continue
            lexical_score = RAG.bm25.score(query_tokens, chunk.index)
            intent_score = section_priority(question, chunk.section)
            score = lexical_score + intent_score * 10.0
            identity = chunk.chunk_id or f"local-{chunk.index}"
            existing = candidates.get(identity)
            if existing is None or score > existing.score:
                candidates[identity] = Match(
                    chunk=chunk,
                    score=score,
                    lexical_score=lexical_score,
                    evidence_score=score,
                )

        ranked = list(candidates.values())
        ranked.sort(
            key=lambda match: (
                section_priority(question, match.chunk.section),
                match.lexical_score,
                match.score,
            ),
            reverse=True,
        )
        return diversify_matches(ranked, limit, per_course_limit=limit)

    codes = extract_course_codes(question)
    if codes:
        prioritized: list[Match] = []
        for code in codes:
            course = next(
                (item for item in syllabi if item.course_code.upper() == code),
                None,
            )
            if course:
                prioritized.extend(best_course_matches(course, 2))
        return prioritized or matches

    course_hint = detect_course_hint(question, syllabi)
    if course_hint:
        return best_course_matches(course_hint, 3) or matches

    title_courses = detect_title_keyword_courses(question, syllabi)
    if title_courses:
        selected: list[Match] = []
        remaining: list[Match] = []
        for course in title_courses[1]:
            course_matches = [
                match
                for match in matches
                if match.chunk.syllabus.course_key == course.course_key
            ]
            if course_matches:
                selected.append(course_matches[0])
                remaining.extend(course_matches[1:2])
                continue

            snippet = best_snippet(question, course.text, 1000)
            selected.append(
                Match(
                    chunk=Chunk(
                        index=-1,
                        syllabus=course,
                        text=snippet,
                        tokens=tokenize(snippet),
                    ),
                    score=0.001,
                )
            )

        remaining.sort(key=lambda match: match.score, reverse=True)
        limit = min(7, len(title_courses[1]) * 2)
        return (selected + remaining)[:limit]

    professor_names = {
        syllabus.professor
        for syllabus in syllabi
        if syllabus.professor and syllabus.professor in question
    }
    if professor_names:
        professor_matches: list[Match] = []
        for course in syllabi:
            if course.professor in professor_names:
                professor_matches.extend(best_course_matches(course, 2))
        return professor_matches or matches

    return matches


def is_low_information_syllabus(syllabus: Syllabus) -> bool:
    no_data_count = len(re.findall(r"(?i)no\s*data", syllabus.text))
    return no_data_count >= 4 and "평가방법이 입력 되지 않았습니다" in syllabus.text


def requires_substantive_evidence(question: str) -> bool:
    return (
        "추천" in question
        or contains_any(question, CODING_QUERY_TERMS)
        or contains_any(question, PROJECT_QUERY_TERMS)
        or contains_any(question, PRACTICE_QUERY_TERMS)
    )


def intent_boost(question: str, match: Match) -> float:
    syllabus = match.chunk.syllabus
    evidence = f"{syllabus.course_code} {syllabus.course_name} {syllabus.professor} {match.chunk.text}"
    boost = 0.0

    if contains_any(question, CODING_QUERY_TERMS):
        if contains_any(evidence, CODING_EVIDENCE_TERMS):
            boost += 0.85
        if contains_any(syllabus.course_name, {"데이터분석소프트웨어", "통계학과파이썬"}):
            boost += 0.9
        if "현장실습" in syllabus.course_name and not contains_any(question, {"현장실습", "인턴", "기업"}):
            boost -= 0.75

    if contains_any(question, PRACTICE_QUERY_TERMS):
        if contains_any(evidence, {"실습", "구현", "소프트웨어", "python", "파이썬", "pbl", "캡스톤"}):
            boost += 0.35

    if contains_any(question, PROJECT_QUERY_TERMS):
        if contains_any(evidence, {"프로젝트", "pbl", "캡스톤", "발표", "팀"}):
            boost += 0.7

    if contains_any(question, ASSESSMENT_QUERY_TERMS):
        if contains_any(evidence, {"평가", "중간고사", "기말고사", "과제", "출석", "성적"}):
            boost += 0.45

    return boost


def rerank_matches(
    question: str,
    vector_matches: list[Match],
    keyword_matches: list[Match],
    top_k: int,
) -> list[Match]:
    combined: dict[tuple[str, str], Match] = {}

    def add(matches: list[Match], source: str) -> None:
        for rank, match in enumerate(matches):
            identity = match.chunk.chunk_id or match.chunk.text[:160]
            key = (match.chunk.syllabus.course_key, identity)
            if key not in combined:
                combined[key] = Match(chunk=match.chunk, score=0.0)
            target = combined[key]
            reciprocal_rank = 1.0 / (60.0 + rank + 1.0)
            target.score += reciprocal_rank
            if source == "semantic":
                target.semantic_score = max(target.semantic_score, match.semantic_score or match.score)
            else:
                target.lexical_score = max(target.lexical_score, match.lexical_score or match.score)

    add(vector_matches, "semantic")
    add(keyword_matches, "lexical")

    ranked = list(combined.values())
    max_lexical = max((match.lexical_score for match in ranked), default=0.0)
    max_rrf = max((match.score for match in ranked), default=1.0)
    for match in ranked:
        lexical_normalized = match.lexical_score / max(max_lexical, 1e-9)
        rrf_normalized = match.score / max(max_rrf, 1e-9)
        match.evidence_score = (
            0.55 * match.semantic_score
            + 0.30 * lexical_normalized
            + 0.15 * rrf_normalized
        )
        match.score = (
            match.evidence_score
            + intent_boost(question, match)
            + section_priority(question, match.chunk.section)
        )
    ranked.sort(key=lambda item: item.score, reverse=True)

    if requires_substantive_evidence(question):
        informative = [
            match for match in ranked if not is_low_information_syllabus(match.chunk.syllabus)
        ]
        if informative:
            ranked = informative

    per_course_limit = 1 if is_broad_recommendation(question) else 2
    return diversify_matches(ranked, top_k, per_course_limit=per_course_limit)


def apply_relevance_threshold(question: str, matches: list[Match]) -> list[Match]:
    if not matches:
        return []
    if extract_course_codes(question) or detect_course_hint(question, RAG.syllabi if "RAG" in globals() else []):
        return matches

    meaningful_tokens = expand_query_tokens(tokenize(question))
    longest = max((len(token) for token in meaningful_tokens), default=0)
    anchor_tokens = {
        token for token in meaningful_tokens if len(token) >= max(4, longest - 1)
    }

    candidates: list[Match] = []
    for match in matches:
        evidence_text = (
            f"{match.chunk.syllabus.course_code} {match.chunk.syllabus.course_name} "
            f"{match.chunk.syllabus.professor} {match.chunk.text}"
        ).casefold()
        anchor_hit = not anchor_tokens or any(token in evidence_text for token in anchor_tokens)
        if match.semantic_score >= 0.70 or (match.lexical_score > 0.0 and anchor_hit):
            candidates.append(match)
    if not candidates:
        return []

    top_evidence = max(match.evidence_score for match in candidates)
    absolute_floor = 0.12 if is_broad_recommendation(question) else 0.18
    relative_floor = top_evidence * (0.30 if is_broad_recommendation(question) else 0.42)
    threshold = max(absolute_floor, relative_floor)
    filtered = [match for match in candidates if match.evidence_score >= threshold]
    return filtered


def embedding_values(response: Any) -> list[list[float]]:
    embeddings = getattr(response, "embeddings", None)
    if embeddings is None and isinstance(response, dict):
        embeddings = response.get("embeddings")
    values: list[list[float]] = []
    for item in embeddings or []:
        vector = getattr(item, "values", None)
        if vector is None and isinstance(item, dict):
            vector = item.get("values")
        if vector is not None:
            values.append([float(value) for value in vector])
    return values


def detect_course_hint(question: str, syllabi: list[Syllabus]) -> Syllabus | None:
    courses = explicitly_requested_courses(question, syllabi)
    return courses[0] if courses else None


def message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        nested = content.get("content")
        return message_content_text(nested)
    if isinstance(content, list):
        parts = [message_content_text(item) for item in content]
        return "\n".join(part for part in parts if part.strip())
    text_attr = getattr(content, "text", None)
    if isinstance(text_attr, str):
        return text_attr
    content_attr = getattr(content, "content", None)
    if content_attr is not None and content_attr is not content:
        return message_content_text(content_attr)
    return ""


def history_message_text(item: Any) -> tuple[str, str]:
    if isinstance(item, dict):
        role = str(item.get("role", ""))
        return role, message_content_text(item.get("content", ""))
    role_attr = getattr(item, "role", None)
    content_attr = getattr(item, "content", None)
    if role_attr is not None or content_attr is not None:
        return str(role_attr or ""), message_content_text(content_attr)
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        user_text = message_content_text(item[0])
        assistant_text = message_content_text(item[1])
        return "legacy", f"{user_text}\n{assistant_text}"
    return "", ""


def courses_mentioned_in_text(text: str, syllabi: list[Syllabus]) -> list[Syllabus]:
    lowered = text.casefold()
    matches: list[Syllabus] = []
    seen: set[str] = set()
    for syllabus in syllabi:
        if syllabus.course_code.casefold() in lowered or (
            syllabus.course_name and syllabus.course_name in text
        ):
            if syllabus.course_code not in seen:
                matches.append(syllabus)
                seen.add(syllabus.course_code)
    return matches


def latest_assistant_courses(
    history: list[Any], syllabi: list[Syllabus]
) -> list[Syllabus]:
    for item in reversed(history):
        role, text = history_message_text(item)
        if role not in {"assistant", "legacy"} or not text.strip():
            continue
        mentioned = courses_mentioned_in_text(text, syllabi)
        if mentioned:
            return mentioned
    return []


FOLLOWUP_CONTEXT_CUES = (
    "그중",
    "그 중",
    "이중",
    "이 중",
    "저중",
    "저 중",
    "나머지",
    "앞에서",
    "위에서",
    "방금",
    "이 답변",
    "그 답변",
    "이 목록",
    "그 목록",
    "위 과목",
    "위 수업",
    "해당 과목",
    "그 과목들",
    "이 과목들",
    "그 수업들",
    "이 수업들",
    "각각",
    "모두",
    "얘네",
    "이들은",
    "그들은",
    "그러면",
    "그럼",
    "그렇다면",
    "또",
)

FOLLOWUP_DETAIL_CUES = (
    "교수",
    "담당",
    "요일",
    "몇 시",
    "몇시",
    "시간",
    "교시",
    "강의실",
    "평가",
    "성적",
    "시험",
    "중간",
    "기말",
    "과제",
    "출석",
    "학점",
    "이수구분",
    "전공필수",
    "전공선택",
    "뭘 배워",
    "뭐 배워",
    "무엇을 배워",
    "수업 내용",
    "학습 내용",
    "주차",
    "교재",
    "팀플",
    "팀프로젝트",
    "실습",
    "코딩",
    "발표",
    "토론",
    "몇 개",
    "몇개",
    "비교",
)


def is_contextual_followup_question(question: str) -> bool:
    compact = re.sub(r"\s+", "", question).casefold()
    if any(re.sub(r"\s+", "", cue).casefold() in compact for cue in FOLLOWUP_CONTEXT_CUES):
        return True
    if len(question.strip()) <= 60 and contains_phrase(question, FOLLOWUP_DETAIL_CUES):
        return True
    return False


def contextual_assessment_summary(syllabus: Syllabus, max_len: int = 650) -> str:
    raw = section_text(syllabus, "평가방법")
    if not raw:
        return ""

    for marker in (
        "◆ Learning Plan",
        "학습계획",
        "장애학생조정사항",
        "Support for Disabled Students",
    ):
        if marker in raw:
            raw = raw.split(marker, 1)[0]
    for marker in ("▷ Evaluation Method", "Evaluation Method"):
        if marker in raw:
            raw = "Evaluation Method " + raw.split(marker, 1)[1]
            break
    raw = re.sub(r"\[page\s*\d+\]", " ", raw, flags=re.IGNORECASE)
    raw = re.sub(r"평가방법이\s*입력\s*되지\s*않았습니다", " ", raw)
    raw = re.sub(
        r"※\s*Evaluation items can be freely designated.*",
        " ",
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    )
    compact = re.sub(r"\s+", " ", raw).strip()
    if len(compact) > max_len:
        compact = compact[: max_len - 3].rstrip() + "..."
    return compact


def build_contextual_course_set_answer(
    question: str,
    history: list[Any],
    syllabi: list[Syllabus],
) -> dict[str, Any] | None:
    if not history or not is_contextual_followup_question(question):
        return None
    if extract_course_codes(question) or detect_course_hint(question, syllabi):
        return None

    previous_courses = latest_assistant_courses(history, syllabi)
    if not previous_courses:
        return None

    context_payload = {
        "type": "course_set_reference",
        "course_codes": [course.course_code for course in previous_courses],
        "original_question": question,
    }

    # Count the filtered subset before interpreting a count as the whole prior
    # answer. Keep the original scope even when no course matches a condition.
    count_request = contains_any(question, {"몇 개", "몇개", "개수", "몇 과목", "몇과목"})
    if count_request and is_filter_list_question(question):
        filtered = filtered_courses_for_question(question, previous_courses)
        if filtered is not None:
            matched, subject, source_note, _ = filtered
            courses = [course for course, _ in matched]
            lines = [f"- {course.course_code} {course.course_name}" for course in courses]
            return {
                "answer": (
                    f"직전 답변에 포함된 과목을 대상으로 확인했습니다.\n\n"
                    f"{subject}은 총 {len(courses)}개입니다."
                    + ("\n\n" + "\n".join(lines) if lines else "")
                ),
                "sources": syllabus_metadata_sources(courses, source_note),
                "mode": "structured_followup",
                "context_resolved": context_payload,
            }

    # A subset request is a filter, not a request for every course's metadata.
    # Resolve it before the generic follow-up count and metadata branches.
    compact_question = re.sub(r"\s+", "", question)
    completion_filter = any(
        label in compact_question
        for label in ("전공필수", "전공선택", "교양필수", "교양선택", "일반선택")
    )
    detail_request = contains_any(
        question, {"교수", "평가", "시험", "배점", "비율", "요일", "시간", "교시", "학습", "내용", "과제"}
    )
    if completion_filter and not detail_request and not contains_any(question, {"아닌", "제외", "말고"}):
        result = build_structured_catalog_answer(question, previous_courses, 0)
        if result:
            result["answer"] = "직전 답변에 포함된 과목을 대상으로 확인했습니다.\n\n" + result["answer"]
            result["mode"] = "structured_followup"
            result["context_resolved"] = context_payload
            return result

    if contains_any(question, {"교수", "담당교수", "교수님", "담당"}):
        lines = [
            f"직전 답변의 {len(previous_courses)}개 과목 담당교수는 다음과 같습니다.",
            "",
        ]
        for course in previous_courses:
            lines.append(
                f"- {course.course_code} {course.course_name}: "
                f"{course.professor or '담당교수 미기재'}"
            )
        return {
            "answer": "\n".join(lines),
            "sources": syllabus_metadata_sources(
                previous_courses,
                "직전 답변에 포함된 과목의 담당교수 메타데이터입니다.",
            ),
            "mode": "structured_followup",
            "context_resolved": context_payload,
        }

    if contains_any(
        question,
        {"평가", "성적", "시험", "중간", "기말", "출석", "배점", "비율"},
    ):
        lines = [
            f"직전 답변의 {len(previous_courses)}개 과목 평가방법을 확인했습니다.",
            "",
        ]
        sources: list[dict[str, Any]] = []
        for course in previous_courses:
            summary = contextual_assessment_summary(course)
            lines.append(f"### {course.course_code} {course.course_name}")
            if summary:
                lines.append(f"- {summary}")
                snippet = summary
            else:
                lines.append("- 강의계획서에서 구체적인 평가방법을 확인하지 못했습니다.")
                snippet = "구체적인 평가방법을 확인하지 못했습니다."
            lines.append("")
            sources.extend(syllabus_metadata_sources([course], snippet))
        return {
            "answer": "\n".join(lines).strip(),
            "sources": sources,
            "mode": "structured_followup",
            "context_resolved": context_payload,
        }

    if contains_any(question, {"몇 개", "몇개", "개수", "몇 과목"}) and not contains_any(
        question, {"요일", "시간", "교시", "평가", "시험", "과제", "출석"}
    ):
        return {
            "answer": (
                f"직전 답변에서 이어진 과목은 총 {len(previous_courses)}개입니다.\n\n"
                + "\n".join(
                    f"- {course.course_code} {course.course_name}"
                    for course in previous_courses
                )
            ),
            "sources": syllabus_metadata_sources(
                previous_courses,
                "직전 답변에 포함된 과목 목록입니다.",
            ),
            "mode": "structured_followup",
            "context_resolved": context_payload,
        }

    scoped_question = (
        " ".join(course.course_code for course in previous_courses)
        + " "
        + question
    ).strip()

    builders = (
        lambda: build_structured_schedule_answer(question, previous_courses),
        lambda: build_structured_learning_answer(scoped_question, previous_courses),
        lambda: build_structured_activity_answer(question, previous_courses),
        lambda: build_structured_feature_answer(question, previous_courses),
    )
    for build_result in builders:
        result = build_result()
        if not result:
            continue
        result = copy.deepcopy(result)
        result["mode"] = "structured_followup"
        result["context_resolved"] = {
            **context_payload,
            "resolved_question": scoped_question,
        }
        return result
    return None


def build_contextual_set_answer(
    question: str,
    history: list[Any],
    syllabi: list[Syllabus],
) -> dict[str, Any] | None:
    if not history:
        return None

    exclusion_cues = (
        "이 답변에 없는",
        "위 답변에 없는",
        "앞 답변에 없는",
        "이 목록에 없는",
        "위 목록에 없는",
        "목록에서 빠진",
        "답변에서 빠진",
        "나머지",
    )
    if not contains_phrase(question, exclusion_cues):
        return None

    category = detect_fixed_title_category(question, syllabi)
    if not category:
        return None
    category_name, category_courses = category

    previous_courses = latest_assistant_courses(history, syllabi)
    if not previous_courses:
        return None
    previous_codes = {course.course_code for course in previous_courses}
    missing_courses = [
        course for course in category_courses if course.course_code not in previous_codes
    ]

    if not missing_courses:
        return {
            "answer": (
                f"직전 답변과 과목명에 '{category_name}'이 포함된 과목을 비교하면, "
                "빠진 과목은 없습니다."
            ),
            "sources": syllabus_metadata_sources(
                category_courses,
                f"직전 답변에 {category_name} 과목이 모두 포함되어 있습니다.",
            ),
            "mode": "structured_followup",
        }

    asks_teamwork = contains_phrase(question, TEAMWORK_QUERY_TERMS)
    lines = [
        (
            f"직전 답변의 과목 목록과 과목명에 '{category_name}'이 포함된 전체 과목을 "
            f"비교하면, 빠진 과목은 {len(missing_courses)}개입니다."
        ),
        "",
    ]
    sources: list[dict[str, Any]] = []
    for course in missing_courses:
        evidence = teamwork_evidence(course) if asks_teamwork else ""
        lines.append(
            f"- {course.course_code} {course.course_name} "
            f"({course.professor or '담당교수 미기재'})"
        )
        if asks_teamwork and evidence:
            lines.append(f"  - 팀 활동 근거: {evidence}")
            snippet = evidence
        elif asks_teamwork:
            lines.append(
                "  - 현재 수집한 강의계획서에서는 팀프로젝트·조별활동·그룹 프로젝트가 "
                "명시된 근거를 확인하지 못했습니다."
            )
            snippet = "강의계획서에서 명시적인 팀 활동 근거를 확인하지 못했습니다."
        else:
            snippet = f"직전 답변에 포함되지 않은 {category_name} 과목입니다."
        sources.extend(syllabus_metadata_sources([course], snippet))

    if asks_teamwork:
        lines.extend(
            [
                "",
                (
                    "따라서 이 과목에 팀플이 없다고 단정할 수는 없습니다. 정확한 의미는 "
                    "'현재 강의계획서만으로는 팀플 여부를 확인할 수 없다'입니다. "
                    "과목명이 PBL이라는 사실만으로 팀플 과목에 자동 포함하지 않았습니다."
                ),
            ]
        )

    return {
        "answer": "\n".join(lines),
        "sources": sources,
        "mode": "structured_followup",
        "context_resolved": {
            "type": "set_difference",
            "category": category_name,
            "previous_codes": sorted(previous_codes),
            "missing_codes": [course.course_code for course in missing_courses],
        },
    }


def resolve_contextual_question(
    question: str,
    history: list[Any],
    syllabi: list[Syllabus],
) -> tuple[str, Syllabus | None]:
    reference_terms = ("그거", "이거", "저거", "아까", "그 수업", "그 과목", "해당 과목")
    if not any(term in question.casefold() for term in reference_terms):
        return question, None
    if detect_course_hint(question, syllabi) is not None or detect_course_code_text(question):
        return question, None

    parsed = [history_message_text(item) for item in history]
    ordered_texts = [
        text for role, text in reversed(parsed) if role == "user" and text.strip()
    ]
    ordered_texts.extend(
        text for role, text in reversed(parsed) if role != "user" and text.strip()
    )
    for text in ordered_texts:
        mentioned = courses_mentioned_in_text(text, syllabi)
        if len(mentioned) != 1:
            continue
        course = mentioned[0]
        resolved = f"{course.course_name} {course.course_code}에 대해 {question}"
        return resolved, course
    return question, None


def diversify_matches(matches: list[Match], top_k: int, per_course_limit: int = 2) -> list[Match]:
    """Select relevant but non-redundant chunks with token-based MMR."""
    if not matches:
        return []

    selected: list[Match] = []
    per_course: defaultdict[str, int] = defaultdict(int)
    remaining = list(matches)
    max_score = max(match.score for match in remaining) or 1.0
    lambda_relevance = 0.78

    while remaining and len(selected) < top_k:
        best_match: Match | None = None
        best_mmr = float("-inf")
        for match in remaining:
            key = match.chunk.syllabus.course_key
            if per_course[key] >= per_course_limit:
                continue
            relevance = match.score / max_score
            redundancy = max(
                (token_jaccard(match.chunk.tokens, item.chunk.tokens) for item in selected),
                default=0.0,
            )
            mmr_score = lambda_relevance * relevance - (1.0 - lambda_relevance) * redundancy
            if mmr_score > best_mmr:
                best_mmr = mmr_score
                best_match = match
        if best_match is None:
            break
        selected.append(best_match)
        per_course[best_match.chunk.syllabus.course_key] += 1
        remaining.remove(best_match)
    return selected


def build_extractive_answer(question: str, matches: list[Match]) -> str:
    grouped: dict[str, list[Match]] = defaultdict(list)
    for match in matches:
        grouped[match.chunk.syllabus.course_key].append(match)

    lines = [
        "강의계획서에서 확인한 관련 내용입니다.",
        "",
    ]
    for course_key, course_matches in list(grouped.items())[:5]:
        syllabus = course_matches[0].chunk.syllabus
        snippet = best_snippet(question, course_matches[0].chunk.text)
        lines.append(f"- {syllabus.course_code}-{syllabus.class_no} {syllabus.course_name} ({syllabus.professor})")
        lines.append(f"  {snippet}")

    lines.append("")
    lines.append("문서에 없는 내용은 추측하지 않았습니다. 더 정확한 비교가 필요하면 과목명이나 평가 항목을 함께 물어봐 주세요.")
    return "\n".join(lines)


def best_snippet(question: str, text: str, max_len: int = 260) -> str:
    query_tokens = set(expand_query_tokens(tokenize(question)))
    sentences = re.split(r"(?<=[.!?。])\s+|\n+", text)
    best = ""
    best_score = -1
    for sentence in sentences:
        compact = sentence.strip()
        if not compact:
            continue
        score = sum(1 for token in query_tokens if token in compact.lower())
        if score > best_score:
            best_score = score
            best = compact
    if not best:
        best = text.strip()
    best = re.sub(r"\s+", " ", best)
    return best[:max_len] + ("..." if len(best) > max_len else "")


def source_payload(matches: list[Match]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    payload: list[dict[str, Any]] = []
    for match in matches:
        key = match.chunk.syllabus.course_key
        if key in seen:
            continue
        seen.add(key)
        syllabus = match.chunk.syllabus
        payload.append(
            {
                "course_code": syllabus.course_code,
                "class_no": syllabus.class_no,
                "course_name": syllabus.course_name,
                "professor": syllabus.professor,
                "completion_type": syllabus.completion_type,
                "credit": syllabus.credit,
                "class_hours": syllabus.class_hours,
                "schedule_summary": syllabus.schedule_summary,
                "schedule_entries": syllabus.schedule_entries,
                "text_path": syllabus.text_path,
                "syllabus_url": syllabus.syllabus_url,
                "score": round(match.score, 3),
                "semantic_score": round(match.semantic_score, 3),
                "lexical_score": round(match.lexical_score, 3),
                "evidence_score": round(match.evidence_score, 3),
                "section": match.chunk.section,
                "snippet": best_snippet("", match.chunk.text, 220),
            }
        )
    return payload


RAG = SyllabusRag(DATA_PATH)


def reload_rag() -> SyllabusRag:
    global RAG
    RAG = SyllabusRag(DATA_PATH)
    return RAG


HTML_PAGE = r"""
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>수강메이트</title>
  <style>
    :root {
      --bg: #f6f4ef;
      --surface: #ffffff;
      --surface-2: #f0eee8;
      --ink: #202124;
      --muted: #68645d;
      --line: #d8d3c8;
      --brand: #8b1e2d;
      --brand-2: #f2d6da;
      --accent: #2f6f5e;
      --disabled: #a9a39a;
      --shadow: 0 12px 32px rgba(32, 33, 36, 0.12);
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Arial, "Malgun Gothic", sans-serif;
      color: var(--ink);
      background: var(--bg);
      letter-spacing: 0;
    }
    button, input, textarea { font: inherit; }
    button {
      border: 0;
      cursor: pointer;
      transition: background .15s ease, transform .15s ease, border-color .15s ease;
    }
    button:active { transform: translateY(1px); }
    .app-shell {
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    .topbar {
      height: 64px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
      padding: 0 28px;
      background: var(--surface);
      border-bottom: 1px solid var(--line);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      min-width: 0;
    }
    .brand-mark {
      width: 34px;
      height: 34px;
      display: grid;
      place-items: center;
      color: white;
      background: var(--brand);
      border-radius: 6px;
      font-weight: 800;
    }
    .brand-title {
      font-size: 18px;
      font-weight: 800;
      white-space: nowrap;
    }
    .status-pill {
      display: inline-flex;
      align-items: center;
      min-height: 30px;
      padding: 0 12px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--muted);
      background: var(--surface-2);
      font-size: 13px;
    }
    main { flex: 1; }
    .view { display: none; }
    .view.active { display: block; }

    .select-layout {
      max-width: 1180px;
      margin: 0 auto;
      padding: 38px 28px 54px;
    }
    .select-head {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 24px;
      align-items: end;
      margin-bottom: 24px;
    }
    h1 {
      margin: 0;
      font-size: 34px;
      line-height: 1.2;
      letter-spacing: 0;
    }
    .subcopy {
      margin: 10px 0 0;
      color: var(--muted);
      line-height: 1.6;
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(3, 96px);
      gap: 10px;
    }
    .metric {
      padding: 12px;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      text-align: center;
    }
    .metric strong {
      display: block;
      font-size: 21px;
      color: var(--brand);
    }
    .metric span {
      display: block;
      margin-top: 4px;
      color: var(--muted);
      font-size: 12px;
    }
    .college-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }
    .college-section {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 2px 10px rgba(32, 33, 36, 0.04);
    }
    .college-title {
      padding: 16px 18px;
      font-weight: 800;
      border-bottom: 1px solid var(--line);
      background: #faf9f6;
    }
    .department-list {
      display: grid;
      gap: 0;
    }
    .department-button {
      width: 100%;
      min-height: 52px;
      padding: 0 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      background: var(--surface);
      color: var(--ink);
      border-bottom: 1px solid #ece8df;
      text-align: left;
    }
    .department-button:last-child { border-bottom: 0; }
    .department-button.enabled:hover {
      background: var(--brand-2);
    }
    .department-button.disabled {
      cursor: not-allowed;
      color: var(--disabled);
      background: #fbfaf7;
    }
    .dept-name {
      overflow-wrap: anywhere;
      line-height: 1.35;
    }
    .dept-badge {
      flex: 0 0 auto;
      font-size: 12px;
      padding: 4px 8px;
      border-radius: 999px;
      color: var(--brand);
      border: 1px solid rgba(139, 30, 45, .25);
      background: #fff7f8;
    }
    .chat-layout {
      height: calc(100vh - 64px);
      display: grid;
      grid-template-columns: 340px minmax(0, 1fr);
      min-height: 660px;
    }
    .sidebar {
      border-right: 1px solid var(--line);
      background: #faf9f6;
      overflow: auto;
      padding: 18px;
    }
    .back-button {
      width: 100%;
      min-height: 42px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 7px;
      background: var(--surface);
      border: 1px solid var(--line);
      color: var(--ink);
      margin-bottom: 18px;
    }
    .panel-title {
      margin: 20px 0 10px;
      font-size: 14px;
      font-weight: 800;
      color: var(--muted);
    }
    .course-list {
      display: grid;
      gap: 8px;
    }
    .course-item {
      padding: 10px 12px;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .course-code {
      font-weight: 800;
      color: var(--brand);
      font-size: 13px;
    }
    .course-name {
      margin-top: 4px;
      line-height: 1.35;
      font-size: 14px;
      overflow-wrap: anywhere;
    }
    .course-prof {
      margin-top: 5px;
      color: var(--muted);
      font-size: 12px;
    }
    .example-list {
      display: grid;
      gap: 8px;
    }
    .example-button {
      min-height: 40px;
      padding: 9px 11px;
      text-align: left;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--ink);
      line-height: 1.35;
    }
    .example-button:hover { border-color: var(--brand); }
    .chat-main {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      min-width: 0;
      background: var(--surface);
    }
    .chat-head {
      padding: 22px 28px 18px;
      border-bottom: 1px solid var(--line);
    }
    .chat-head h2 {
      margin: 0;
      font-size: 24px;
      letter-spacing: 0;
    }
    .chat-head p {
      margin: 7px 0 0;
      color: var(--muted);
      line-height: 1.5;
    }
    .messages {
      overflow: auto;
      padding: 24px 28px;
      background: #fffdf8;
    }
    .message {
      max-width: 900px;
      margin-bottom: 16px;
      display: flex;
    }
    .message.user { justify-content: flex-end; margin-left: auto; }
    .bubble {
      max-width: min(760px, 100%);
      padding: 14px 16px;
      border-radius: 8px;
      border: 1px solid var(--line);
      white-space: pre-wrap;
      line-height: 1.6;
      overflow-wrap: anywhere;
    }
    .user .bubble {
      color: white;
      background: var(--brand);
      border-color: var(--brand);
    }
    .assistant .bubble {
      background: var(--surface);
    }
    .source-block {
      margin-top: 10px;
      padding-top: 10px;
      border-top: 1px solid #eee6dc;
      color: var(--muted);
      font-size: 13px;
    }
    .source-row {
      margin-top: 7px;
      padding: 8px 9px;
      border-radius: 7px;
      background: #faf8f2;
      border: 1px solid #ece6dc;
    }
    .composer {
      border-top: 1px solid var(--line);
      padding: 18px 28px;
      background: var(--surface);
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
    }
    .question-input {
      min-height: 48px;
      max-height: 140px;
      resize: vertical;
      padding: 12px 13px;
      border: 1px solid var(--line);
      border-radius: 8px;
      outline: none;
    }
    .question-input:focus { border-color: var(--brand); }
    .send-button {
      min-width: 96px;
      min-height: 48px;
      border-radius: 8px;
      color: white;
      background: var(--accent);
      font-weight: 800;
    }
    .send-button:disabled {
      background: var(--disabled);
      cursor: wait;
    }
    @media (max-width: 920px) {
      .select-head {
        grid-template-columns: 1fr;
      }
      .metrics {
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }
      .college-grid {
        grid-template-columns: 1fr;
      }
      .chat-layout {
        height: auto;
        min-height: calc(100vh - 64px);
        grid-template-columns: 1fr;
      }
      .sidebar {
        max-height: 360px;
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }
    }
    @media (max-width: 620px) {
      .topbar { padding: 0 16px; }
      .status-pill { display: none; }
      .select-layout { padding: 28px 16px 40px; }
      h1 { font-size: 27px; }
      .metrics { grid-template-columns: 1fr; }
      .composer {
        grid-template-columns: 1fr;
        padding: 14px 16px;
      }
      .messages, .chat-head { padding-left: 16px; padding-right: 16px; }
    }
  </style>
</head>
<body>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand">
        <div class="brand-mark">KU</div>
        <div class="brand-title">수강메이트</div>
      </div>
      <div class="status-pill" id="statusPill">강의계획서 데이터 로드 중</div>
    </header>

    <main>
      <section id="selectView" class="view active">
        <div class="select-layout">
          <div class="select-head">
            <div>
              <h1>학과를 선택하세요</h1>
              <p class="subcopy">고려대학교 세종캠퍼스 강의계획서 기반 수강 도우미</p>
            </div>
            <div class="metrics">
              <div class="metric"><strong id="courseCount">0</strong><span>과목</span></div>
              <div class="metric"><strong id="docCount">0</strong><span>문서</span></div>
              <div class="metric"><strong id="deptCount">1</strong><span>활성 학과</span></div>
            </div>
          </div>
          <div id="collegeGrid" class="college-grid"></div>
        </div>
      </section>

      <section id="chatView" class="view">
        <div class="chat-layout">
          <aside class="sidebar">
            <button class="back-button" id="backButton">학과 다시 선택</button>
            <div class="panel-title">예시 질문</div>
            <div class="example-list" id="exampleList"></div>
            <div class="panel-title">수집 과목</div>
            <div class="course-list" id="courseList"></div>
          </aside>
          <section class="chat-main">
            <div class="chat-head">
              <h2>빅데이터사이언스학부 챗봇</h2>
              <p>2026년 1학기 세종캠퍼스 빅데이터사이언스학부 강의계획서를 기준으로 답변합니다.</p>
            </div>
            <div class="messages" id="messages"></div>
            <form class="composer" id="chatForm">
              <textarea class="question-input" id="questionInput" rows="2" placeholder="강의계획서에 대해 질문하세요"></textarea>
              <button class="send-button" id="sendButton" type="submit">질문</button>
            </form>
          </section>
        </div>
      </section>
    </main>
  </div>

  <script>
    const state = {
      catalog: [],
      courses: [],
      stats: {},
      activeDepartment: null,
      busy: false,
      history: [],
    };

    const $ = (id) => document.getElementById(id);

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function showView(name) {
      $("selectView").classList.toggle("active", name === "select");
      $("chatView").classList.toggle("active", name === "chat");
    }

    function renderCatalog() {
      const grid = $("collegeGrid");
      grid.innerHTML = state.catalog.map((college) => `
        <section class="college-section">
          <div class="college-title">${escapeHtml(college.college)}</div>
          <div class="department-list">
            ${college.departments.map((dept) => `
              <button
                class="department-button ${dept.enabled ? "enabled" : "disabled"}"
                ${dept.enabled ? `data-slug="${escapeHtml(dept.slug)}"` : "disabled"}
              >
                <span class="dept-name">${escapeHtml(dept.name)}</span>
                ${dept.badge ? `<span class="dept-badge">${escapeHtml(dept.badge)}</span>` : ""}
              </button>
            `).join("")}
          </div>
        </section>
      `).join("");

      grid.querySelectorAll(".department-button.enabled").forEach((button) => {
        button.addEventListener("click", () => {
          state.activeDepartment = button.dataset.slug;
          openChat();
        });
      });
    }

    function renderCourses() {
      $("courseList").innerHTML = state.courses.map((course) => `
        <div class="course-item">
          <div class="course-code">${escapeHtml(course.course_code)}-${escapeHtml(course.class_no)}</div>
          <div class="course-name">${escapeHtml(course.course_name)}</div>
          <div class="course-prof">${escapeHtml(course.professor || "담당교수 미기재")}</div>
        </div>
      `).join("");
    }

    function renderExamples() {
      $("exampleList").innerHTML = state.stats.examples.map((question) => `
        <button class="example-button" type="button">${escapeHtml(question)}</button>
      `).join("");

      $("exampleList").querySelectorAll("button").forEach((button) => {
        button.addEventListener("click", () => {
          $("questionInput").value = button.textContent;
          askQuestion(button.textContent);
        });
      });
    }

    function renderStats() {
      $("courseCount").textContent = state.stats.course_count ?? 0;
      $("docCount").textContent = state.stats.document_count ?? 0;
      const modeLabel = state.stats.rag_mode === "chroma_gemini" ? "Chroma + BM25 + MMR + Gemini" :
        state.stats.rag_mode === "keyword_gemini" ? "키워드 + Gemini" : "키워드 검색";
      $("statusPill").textContent = `${state.stats.course_count ?? 0}개 강의계획서 · ${modeLabel}`;
    }

    function addMessage(role, content, sources = []) {
      const wrapper = document.createElement("div");
      wrapper.className = `message ${role}`;
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      bubble.textContent = content;

      if (sources && sources.length) {
        const sourceBlock = document.createElement("div");
        sourceBlock.className = "source-block";
        sourceBlock.innerHTML = `<strong>참고 근거</strong>`;
        sources.slice(0, 5).forEach((source) => {
          const row = document.createElement("div");
          row.className = "source-row";
          row.textContent = `${source.course_code}-${source.class_no} ${source.course_name} / ${source.professor} / score ${source.score}`;
          sourceBlock.appendChild(row);
        });
        bubble.appendChild(sourceBlock);
      }

      wrapper.appendChild(bubble);
      $("messages").appendChild(wrapper);
      $("messages").scrollTop = $("messages").scrollHeight;
    }

    function openChat() {
      showView("chat");
      if (!$("messages").children.length) {
        addMessage("assistant", "빅데이터사이언스학부 강의계획서를 불러왔습니다. 과목 내용, 평가방식, 시험, 과제, 사용 도구를 물어볼 수 있습니다.");
      }
    }

    async function askQuestion(question) {
      const trimmed = question.trim();
      if (!trimmed || state.busy) return;
      state.busy = true;
      $("sendButton").disabled = true;
      addMessage("user", trimmed);
      $("questionInput").value = "";

      try {
        const response = await fetch("/api/chat", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            department: state.activeDepartment || "bigdata",
            question: trimmed,
            history: state.history.slice(-10),
          }),
        });
        const data = await response.json();
        const answer = data.answer || "답변을 생성하지 못했습니다.";
        addMessage("assistant", answer, data.sources || []);
        state.history.push(
          {role: "user", content: trimmed},
          {role: "assistant", content: answer},
        );
        state.history = state.history.slice(-12);
      } catch (error) {
        addMessage("assistant", "서버 응답 중 오류가 발생했습니다.");
      } finally {
        state.busy = false;
        $("sendButton").disabled = false;
      }
    }

    async function init() {
      const response = await fetch("/api/bootstrap");
      const data = await response.json();
      state.catalog = data.catalog;
      state.courses = data.courses;
      state.stats = data.stats;
      renderStats();
      renderCatalog();
      renderCourses();
      renderExamples();
    }

    $("backButton").addEventListener("click", () => showView("select"));
    $("chatForm").addEventListener("submit", (event) => {
      event.preventDefault();
      askQuestion($("questionInput").value);
    });

    init();
  </script>
</body>
</html>
"""


class AppHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text: str, status: int = 200) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._html(HTML_PAGE)
            return
        if self.path == "/api/bootstrap":
            self._json(
                {
                    "catalog": DEPARTMENT_CATALOG,
                    "courses": [
                        {
                            "course_code": item.course_code,
                            "class_no": item.class_no,
                            "course_name": item.course_name,
                            "professor": item.professor,
                            "completion_type": item.completion_type,
                            "schedule_summary": item.schedule_summary,
                            "schedule_entries": item.schedule_entries,
                        }
                        for item in RAG.syllabi
                    ],
                    "stats": {
                        "course_count": len(RAG.syllabi),
                        "document_count": len(RAG.syllabi),
                        "chunk_count": len(RAG.chunks),
                        "rag_mode": RAG.rag_mode,
                        "chroma_ready": bool(RAG.chroma_collection),
                        "gemini_ready": bool(RAG.gemini_client),
                        "examples": EXAMPLE_QUESTIONS,
                    },
                }
            )
            return
        self._json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self._json({"error": "not found"}, status=404)
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length).decode("utf-8")
        try:
            payload = json.loads(raw_body or "{}")
        except json.JSONDecodeError:
            self._json({"error": "invalid json"}, status=400)
            return
        question = str(payload.get("question", "")).strip()
        department = str(payload.get("department", "")).strip()
        raw_history = payload.get("history", [])
        history: list[dict[str, str]] = []
        if isinstance(raw_history, list):
            for item in raw_history[-12:]:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role", "")).strip().lower()
                content = item.get("content", "")
                if role not in {"user", "assistant"} or not isinstance(content, str):
                    continue
                content = content.strip()
                if content:
                    history.append({"role": role, "content": content[:4000]})
        if department != "bigdata":
            self._json({"answer": "현재 프로토타입에서는 빅데이터사이언스학부만 지원합니다.", "sources": []})
            return
        if not question:
            self._json({"answer": "질문을 입력해 주세요.", "sources": []})
            return
        result = RAG.answer(question, history)
        print(
            f"/api/chat mode={result.get('mode')} rag_mode={RAG.rag_mode} question={question[:40]}",
            file=sys.stderr,
            flush=True,
        )
        self._json(result)


def run(host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"수강메이트 실행 중: http://{host}:{port}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Sugang Mate prototype.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()
    run(args.host, args.port)


if __name__ == "__main__":
    main()
