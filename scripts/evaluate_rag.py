from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app import RAG  # noqa: E402


TEST_CASES = [
    {
        "id": "exact_assessment",
        "question": "수리통계학 평가방식 알려줘.",
        "expected_codes": ["BDSC201"],
    },
    {
        "id": "course_code",
        "question": "BDSC205에서는 어떤 소프트웨어를 사용해?",
        "expected_codes": ["BDSC205"],
    },
    {
        "id": "comparison",
        "question": "BDSC201과 BDSC205의 평가방식을 비교해줘.",
        "expected_codes": ["BDSC201", "BDSC205"],
    },
    {
        "id": "recommendation",
        "question": "팀 프로젝트나 발표가 있는 과목을 추천해줘.",
        "expected_codes": [],
        "forbidden_codes": ["BDSC121", "BDSC122", "BDSC123"],
        "expect_unique_sources": True,
    },
    {
        "id": "duplicate_section",
        "question": "데이터사이언스를위한수학 수업 내용을 알려줘.",
        "expected_codes": ["BDSC155"],
    },
    {
        "id": "professor",
        "question": "이지수 교수님 수업 알려줘.",
        "expected_codes": ["BDSC155", "BDSC201"],
    },
    {
        "id": "title_pbl_shorthand",
        "question": "pbl과목 알려줘.",
        "expected_codes": ["BDSC315", "BDSC325", "BDSC407"],
        "expected_mode": "structured_title",
    },
    {
        "id": "title_pbl_explicit",
        "question": "수업 이름에 PBL이라고 적혀 있는 수업 알려줘.",
        "expected_codes": ["BDSC315", "BDSC325", "BDSC407"],
        "expected_mode": "structured_title",
    },
    {
        "id": "title_pbl_whats_available",
        "question": "PBL과목 뭐가 있어?",
        "expected_codes": ["BDSC315", "BDSC325", "BDSC407"],
        "expected_mode": "structured_title",
        "expected_answer_terms": ["3개", "통계학과인공지능PBL"],
    },
    {
        "id": "title_pbl_colloquial",
        "question": "PBL 과목은 뭐가 있냐?",
        "expected_codes": ["BDSC315", "BDSC325", "BDSC407"],
        "expected_mode": "structured_title",
        "expected_answer_terms": ["3개", "통계학과인공지능PBL"],
    },
    {
        "id": "title_pbl_which_classes",
        "question": "어떤 PBL 수업이 있어?",
        "expected_codes": ["BDSC315", "BDSC325", "BDSC407"],
        "expected_mode": "structured_title",
    },
    {
        "id": "title_capstone_shorthand",
        "question": "캡스톤 과목 알려줘.",
        "expected_codes": ["BDSC319", "BDSC414"],
        "expected_mode": "structured_title",
    },
    {
        "id": "title_capstone_whats_available",
        "question": "캡스톤 과목 뭐가 있어?",
        "expected_codes": ["BDSC319", "BDSC414"],
        "expected_mode": "structured_title",
        "expected_answer_terms": ["2개"],
    },
    {
        "id": "title_machine_learning_shorthand",
        "question": "머신러닝 수업 알려줘.",
        "expected_codes": ["BDSC302"],
        "expected_mode": "structured_title",
    },
    {
        "id": "title_pbl_detail",
        "question": "PBL 과목들의 평가방식을 비교해줘.",
        "expected_codes": ["BDSC315", "BDSC325", "BDSC407"],
    },
    {
        "id": "no_midterm",
        "question": "중간고사 없는 과목 알려줘.",
        "expected_codes": [],
        "expected_mode": "structured",
    },
    {
        "id": "out_of_scope",
        "question": "이번 학기 등록금 납부일은 언제야?",
        "expected_codes": [],
        "expected_mode": "out_of_scope",
    },
    {
        "id": "complete_catalog",
        "question": "현재 수집된 전체 과목 목록을 모두 알려줘.",
        "expected_codes": [
            "BDSC121", "BDSC122", "BDSC123", "BDSC155", "BDSC201",
            "BDSC203", "BDSC205", "BDSC208", "BDSC211", "BDSC212",
            "BDSC302", "BDSC303", "BDSC309", "BDSC315", "BDSC317",
            "BDSC319", "BDSC325", "BDSC327", "BDSC331", "BDSC401",
            "BDSC407", "BDSC414", "BDSC441", "BDSC442", "BDSC443",
        ],
        "expected_mode": "structured_catalog",
        "expected_answer_terms": ["25개"],
    },
    {
        "id": "course_count",
        "question": "현재 수집된 과목은 총 몇 개야?",
        "expected_codes": [],
        "expected_mode": "structured_numeric",
        "expected_answer_terms": ["25개"],
    },
    {
        "id": "pbl_count",
        "question": "PBL 과목은 몇 개야?",
        "expected_codes": ["BDSC315", "BDSC325", "BDSC407"],
        "expected_mode": "structured_title",
        "expected_answer_terms": ["3개"],
    },
    {
        "id": "professor_complete",
        "question": "이지수 교수님 담당 과목을 전부 알려줘.",
        "expected_codes": ["BDSC155", "BDSC201"],
        "expected_mode": "structured_catalog",
    },
    {
        "id": "required_major_courses",
        "question": "전공 필수 과목을 모두 알려줘.",
        "expected_codes": ["BDSC155", "BDSC201", "BDSC205"],
        "expected_mode": "structured_catalog",
        "expected_answer_terms": ["3개", "데이터분석소프트웨어초급"],
    },
    {
        "id": "required_major_count",
        "question": "전공필수는 몇 개야?",
        "expected_codes": ["BDSC155", "BDSC201", "BDSC205"],
        "expected_mode": "structured_catalog",
        "expected_answer_terms": ["3개"],
    },
    {
        "id": "ambiguous_recommendation",
        "question": "좋은 과목 추천해줘.",
        "expected_codes": [],
        "expected_mode": "clarification",
    },
    {
        "id": "ambiguous_reference",
        "question": "그거 알려줘.",
        "expected_codes": [],
        "expected_mode": "clarification",
    },
    {
        "id": "schedule_course_name",
        "question": "수리통계학은 무슨 요일 몇 시에 수업해?",
        "expected_codes": ["BDSC201"],
        "expected_mode": "structured_schedule",
        "expected_answer_terms": ["화요일", "2-4교시", "10:00-12:50", "공공정책관 220호"],
    },
    {
        "id": "schedule_course_code",
        "question": "BDSC205는 언제 어디서 수업해?",
        "expected_codes": ["BDSC205"],
        "expected_mode": "structured_schedule",
        "expected_answer_terms": ["수요일", "2-4교시", "공공정책관 120호(전산실)"],
    },
    {
        "id": "schedule_duplicate_sections",
        "question": "데이터사이언스를위한수학 분반별 시간표 알려줘.",
        "expected_codes": ["BDSC155"],
        "expected_mode": "structured_schedule",
        "expected_answer_terms": ["01분반", "02분반", "월요일 5-7교시"],
    },
    {
        "id": "schedule_monday",
        "question": "월요일 수업은 뭐가 있어?",
        "expected_codes": [
            "BDSC155", "BDSC208", "BDSC211", "BDSC212", "BDSC302",
            "BDSC315", "BDSC317", "BDSC325", "BDSC414",
        ],
        "expected_mode": "structured_schedule",
        "expected_answer_terms": ["월요일", "9개"],
    },
    {
        "id": "schedule_wednesday",
        "question": "수요일에 진행되는 과목을 전부 알려줘.",
        "expected_codes": ["BDSC205", "BDSC208", "BDSC315", "BDSC325", "BDSC414"],
        "expected_mode": "structured_schedule",
        "expected_answer_terms": ["수요일", "5개"],
    },
    {
        "id": "schedule_thursday_period_3",
        "question": "목요일 3교시에 겹치는 과목 알려줘.",
        "expected_codes": ["BDSC319", "BDSC327", "BDSC407"],
        "expected_mode": "structured_schedule",
        "expected_answer_terms": ["목요일 3교시", "3개"],
    },
    {
        "id": "schedule_friday",
        "question": "금요일 강의 목록 보여줘.",
        "expected_codes": ["BDSC331", "BDSC401", "BDSC407"],
        "expected_mode": "structured_schedule",
        "expected_answer_terms": ["금요일", "3개"],
    },
    {
        "id": "schedule_machine_learning",
        "question": "머신러닝 수업 시간과 강의실 알려줘.",
        "expected_codes": ["BDSC302"],
        "expected_mode": "structured_schedule",
        "expected_answer_terms": ["월요일 9교시", "화요일 4-5교시"],
    },
    {
        "id": "schedule_pbl_course",
        "question": "통계학과인공지능PBL은 언제 수업이야?",
        "expected_codes": ["BDSC325"],
        "expected_mode": "structured_schedule",
        "expected_answer_terms": ["월요일 2-3교시", "수요일 1교시"],
    },
    {
        "id": "schedule_no_regular_time",
        "question": "현장실습I은 언제 수업해?",
        "expected_codes": ["BDSC441"],
        "expected_mode": "structured_schedule",
        "expected_answer_terms": ["정규 요일·교시가 기재되어 있지 않습니다"],
    },
    {
        "id": "schedule_all",
        "question": "전체 과목 시간표를 알려줘.",
        "expected_codes": ["BDSC155", "BDSC201", "BDSC205", "BDSC414"],
        "expected_mode": "structured_schedule",
        "expected_answer_terms": ["전체 시간표", "19개"],
    },
    {
        "id": "schedule_period_only",
        "question": "1교시에 진행되는 수업이 뭐야?",
        "expected_codes": ["BDSC211", "BDSC325", "BDSC331", "BDSC401"],
        "expected_mode": "structured_schedule",
        "expected_answer_terms": ["1교시", "4개"],
    },
    {
        "id": "title_pbl_casual_short",
        "question": "피비엘 수업 말고 PBL 붙은 과목 뭐야?",
        "expected_codes": ["BDSC315", "BDSC325", "BDSC407"],
        "expected_mode": "structured_title",
    },
    {
        "id": "title_capstone_explicit_name",
        "question": "과목명에 캡스톤이 들어간 수업 전부 보여줘.",
        "expected_codes": ["BDSC319", "BDSC414"],
        "expected_mode": "structured_title",
        "expected_answer_terms": ["2개"],
    },
    {
        "id": "title_deep_learning",
        "question": "이름에 딥러닝이 들어간 과목 알려줘.",
        "expected_codes": ["BDSC401"],
        "expected_mode": "structured_title",
    },
    {
        "id": "title_field_practice",
        "question": "현장실습 과목 목록을 모두 알려줘.",
        "expected_codes": ["BDSC441", "BDSC442", "BDSC443"],
        "expected_mode": "structured_title",
    },
    {
        "id": "elective_major_count",
        "question": "전공선택 과목은 총 몇 개야?",
        "expected_codes": ["BDSC121", "BDSC414", "BDSC443"],
        "expected_mode": "structured_catalog",
        "expected_answer_terms": ["22개"],
    },
    {
        "id": "professor_kim_gihwan_complete",
        "question": "김기환 교수님 담당 과목을 모두 보여줘.",
        "expected_codes": ["BDSC317", "BDSC325"],
        "expected_mode": "structured_catalog",
    },
    {
        "id": "catalog_wording_variant",
        "question": "보유한 교과목을 전부 나열해줘.",
        "expected_codes": ["BDSC121", "BDSC205", "BDSC443"],
        "expected_mode": "structured_catalog",
        "expected_answer_terms": ["25개"],
    },
    {
        "id": "catalog_count_wording_variant",
        "question": "데이터에 들어 있는 강의 수가 총 몇 개인가요?",
        "expected_codes": [],
        "expected_mode": "structured_numeric",
        "expected_answer_terms": ["25개"],
    },
    {
        "id": "required_major_spacing",
        "question": "전공필수인 수업들을 전부 보여줘.",
        "expected_codes": ["BDSC155", "BDSC201", "BDSC205"],
        "expected_mode": "structured_catalog",
    },
    {
        "id": "rag_python_course",
        "question": "통계학과파이썬에서는 어떤 프로그래밍 언어를 사용해?",
        "expected_codes": ["BDSC317"],
    },
    {
        "id": "rag_deep_learning_content",
        "question": "딥러닝이론에서는 무엇을 배우는지 알려줘.",
        "expected_codes": ["BDSC401"],
    },
    {
        "id": "rag_health_pbl_assessment",
        "question": "보건의료빅데이터분석PBL 평가방식을 알려줘.",
        "expected_codes": ["BDSC315"],
    },
    {
        "id": "rag_capstone_comparison",
        "question": "BDSC319와 BDSC414의 수업 내용을 비교해줘.",
        "expected_codes": ["BDSC319", "BDSC414"],
    },
    {
        "id": "rag_professor_comparison",
        "question": "이지수 교수님 과목들의 평가방식을 비교해줘.",
        "expected_codes": ["BDSC155", "BDSC201"],
    },
    {
        "id": "out_of_scope_dormitory",
        "question": "기숙사 신청 기간이 언제야?",
        "expected_codes": [],
        "expected_mode": "out_of_scope",
    },
    {
        "id": "out_of_scope_graduation",
        "question": "졸업하려면 총 몇 학점이 필요해?",
        "expected_codes": [],
        "expected_mode": "out_of_scope",
    },
    {
        "id": "clarification_easy_course",
        "question": "제일 쉬운 수업 알려줘.",
        "expected_codes": [],
        "expected_mode": "clarification",
    },
    {
        "id": "clarification_reference_course",
        "question": "그 과목 시험은 어때?",
        "expected_codes": [],
        "expected_mode": "clarification",
    },
    {
        "id": "context_followup_assessment",
        "question": "그 과목 시험은 어때?",
        "history": [
            {"role": "user", "content": "수리통계학 수업을 알려줘."},
            {"role": "assistant", "content": "수리통계학(BDSC201)에 대한 안내입니다."},
        ],
        "expected_codes": ["BDSC201"],
    },
    {
        "id": "context_followup_schedule",
        "question": "그 수업은 언제 해?",
        "history": [
            {"role": "user", "content": "통계학과인공지능PBL 알려줘."},
            {"role": "assistant", "content": "통계학과인공지능PBL(BDSC325)에 대한 안내입니다."},
        ],
        "expected_codes": ["BDSC325"],
        "expected_mode": "structured_schedule",
        "expected_answer_terms": ["월요일 2-3교시", "수요일 1교시"],
    },
    {
        "id": "context_followup_missing_pbl_teamwork",
        "question": "이 답변에 없는 PBL수업은 팀플이 없어?",
        "history": [
            {"role": "user", "content": "팀플 수업 뭐야?"},
            {
                "role": "assistant",
                "content": (
                    "팀 활동이 확인된 과목은 BDSC309 공공데이터활용, "
                    "BDSC319 시공간데이터분석캡스톤디자인, "
                    "BDSC325 통계학과인공지능PBL, "
                    "BDSC407 소셜네트워크분석PBL, "
                    "BDSC414 인터랙티브데이터사이언스캡스톤디자인(영강)입니다."
                ),
            },
        ],
        "expected_codes": ["BDSC315"],
        "expect_exact_codes": True,
        "expected_mode": "structured_followup",
        "expected_answer_terms": [
            "보건의료빅데이터분석PBL",
            "팀플이 없다고 단정할 수는 없습니다",
            "현재 강의계획서만으로는 팀플 여부를 확인할 수 없다",
        ],
    },
    {
        "id": "context_followup_generic_professor",
        "question": "그러면 교수님은 누구야?",
        "history": [
            {"role": "user", "content": "데이터마이닝에서는 뭘 배워?"},
            {
                "role": "assistant",
                "content": "BDSC303 데이터마이닝 과목의 학습 내용을 안내했습니다.",
            },
        ],
        "expected_codes": ["BDSC303"],
        "expected_answer_terms": ["데이터마이닝"],
    },
    {
        "id": "context_followup_generic_subset",
        "question": "그중 월요일 수업은 뭐야?",
        "history": [
            {"role": "user", "content": "팀플 수업 뭐야?"},
            {
                "role": "assistant",
                "content": (
                    "BDSC309 공공데이터활용, BDSC319 시공간데이터분석캡스톤디자인, "
                    "BDSC325 통계학과인공지능PBL, BDSC407 소셜네트워크분석PBL, "
                    "BDSC414 인터랙티브데이터사이언스캡스톤디자인(영강)입니다."
                ),
            },
        ],
        "expected_codes": ["BDSC325", "BDSC414"],
        "expected_answer_terms": ["월요일"],
    },
    {
        "id": "negative_blockchain",
        "question": "블록체인 스마트컨트랙트를 배우는 과목 있어?",
        "expected_codes": [],
        "expected_mode": "fallback",
        "expect_no_sources": True,
    },
    {
        "id": "negative_astrophysics",
        "question": "천체물리학 실험 수업이 있어?",
        "expected_codes": [],
        "expected_mode": "fallback",
        "expect_no_sources": True,
    },
    {
        "id": "negative_frontend",
        "question": "자바스크립트 프론트엔드 과목 추천해줘",
        "expected_codes": [],
        "expected_mode": "fallback",
        "expect_no_sources": True,
    },
]


TEAMWORK_CODES = ["BDSC309", "BDSC319", "BDSC325", "BDSC407", "BDSC414"]
CODING_CODES = [
    "BDSC203", "BDSC205", "BDSC208", "BDSC211", "BDSC212", "BDSC303", "BDSC309",
    "BDSC315", "BDSC317", "BDSC319", "BDSC325", "BDSC331", "BDSC407", "BDSC414",
]
PRACTICE_CODES = [
    "BDSC205", "BDSC208", "BDSC303", "BDSC309", "BDSC315",
    "BDSC319", "BDSC327", "BDSC401", "BDSC407", "BDSC414",
]
PRESENTATION_CODES = [
    "BDSC155", "BDSC201", "BDSC205", "BDSC309", "BDSC315",
    "BDSC319", "BDSC325", "BDSC331", "BDSC407", "BDSC414",
]
DISCUSSION_CODES = ["BDSC327", "BDSC407", "BDSC414"]
ASSIGNMENT_CODES = [
    "BDSC155", "BDSC201", "BDSC203", "BDSC208", "BDSC211", "BDSC302", "BDSC303",
    "BDSC309", "BDSC315", "BDSC317", "BDSC319", "BDSC327", "BDSC414",
]
PROJECT_CODES = [
    "BDSC121", "BDSC122", "BDSC123", "BDSC302", "BDSC309", "BDSC315",
    "BDSC319", "BDSC325", "BDSC331", "BDSC407", "BDSC414",
]
QUIZ_CODES = ["BDSC208", "BDSC211", "BDSC302", "BDSC315"]

BLIND_CASES = [
    {
        "id": "blind_teamwork_teamplay",
        "question": "팀플 수업 뭐야?",
        "expected_codes": TEAMWORK_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_activity",
        "expected_answer_terms": ["5개", "팀 활동"],
        "synonym_group": "teamwork",
        "evaluation_type": "blind",
    },
    {
        "id": "blind_teamwork_project",
        "question": "팀프로젝트 진행하는 과목 알려줘",
        "expected_codes": TEAMWORK_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_activity",
        "expected_answer_terms": ["5개", "팀 활동"],
        "synonym_group": "teamwork",
        "evaluation_type": "blind",
    },
    {
        "id": "blind_teamwork_activity",
        "question": "팀활동 있는 수업을 전부 보여줘",
        "expected_codes": TEAMWORK_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_activity",
        "expected_answer_terms": ["5개", "조별"],
        "synonym_group": "teamwork",
        "evaluation_type": "blind",
    },
    {
        "id": "blind_teamwork_collaboration",
        "question": "팀협력 하는 강의가 뭐야?",
        "expected_codes": TEAMWORK_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_activity",
        "expected_answer_terms": ["5개", "팀 기반"],
        "synonym_group": "teamwork",
        "evaluation_type": "blind",
    },
    {
        "id": "blind_teamwork_group",
        "question": "조별 활동 있는 과목 추천해줘",
        "expected_codes": TEAMWORK_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_activity",
        "expected_answer_terms": ["5개", "그룹 활동"],
        "synonym_group": "teamwork",
        "evaluation_type": "blind",
    },
    {
        "id": "blind_teamwork_english",
        "question": "group project 과목 있어?",
        "expected_codes": TEAMWORK_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_activity",
        "expected_answer_terms": ["5개", "group projects"],
        "synonym_group": "teamwork",
        "evaluation_type": "blind",
    },
    {
        "id": "blind_teamwork_slang",
        "question": "팀프젝 있는 수업 뭐임?",
        "expected_codes": TEAMWORK_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_activity",
        "expected_answer_terms": ["5개"],
        "synonym_group": "teamwork",
        "evaluation_type": "blind",
    },
    {
        "id": "blind_learning_data_mining",
        "question": "데이터마이닝에서는 뭘 배워?",
        "expected_codes": ["BDSC303"],
        "expect_exact_codes": True,
        "expected_mode": "structured_learning",
        "expected_answer_terms": ["R 입문", "수치형데이터 분석"],
        "evaluation_type": "blind",
    },
    {
        "id": "blind_learning_deep_learning",
        "question": "딥러닝이론 수업에서 무엇을 배우나요?",
        "expected_codes": ["BDSC401"],
        "expect_exact_codes": True,
        "expected_mode": "structured_learning",
        "expected_answer_terms": ["합성곱신경망", "순환신경망"],
        "evaluation_type": "blind",
    },
    {
        "id": "blind_learning_machine_learning",
        "question": "머신러닝 주차별 학습내용을 알려줘",
        "expected_codes": ["BDSC302"],
        "expect_exact_codes": True,
        "expected_mode": "structured_learning",
        "expected_answer_terms": ["Decision Trees", "Support Vector Machine"],
        "evaluation_type": "blind",
    },
    {
        "id": "blind_learning_seminar",
        "question": "데이터사이언스세미나I 커리큘럼 알려줘",
        "expected_codes": ["BDSC331"],
        "expect_exact_codes": True,
        "expected_mode": "structured_learning",
        "expected_answer_terms": ["NumPy", "TF-IDF"],
        "evaluation_type": "blind",
    },
    {
        "id": "blind_learning_social_network",
        "question": "소셜네트워크분석PBL에서는 어떤 내용을 다뤄?",
        "expected_codes": ["BDSC407"],
        "expect_exact_codes": True,
        "expected_mode": "structured_learning",
        "expected_answer_terms": ["중심성", "가중 네트워크"],
        "evaluation_type": "blind",
    },
    {
        "id": "blind_learning_public_data",
        "question": "공공데이터활용 수업 내용 알려줘",
        "expected_codes": ["BDSC309"],
        "expect_exact_codes": True,
        "expected_mode": "structured_learning",
        "expected_answer_terms": ["KOSIS", "PYTHON"],
        "evaluation_type": "blind",
    },
    {
        "id": "blind_synonym_coding",
        "question": "코딩 많이 하는 수업 알려줘",
        "expected_codes": CODING_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "coding",
        "evaluation_type": "blind",
    },
    {
        "id": "blind_synonym_programming",
        "question": "프로그래밍 많이 하는 과목 알려줘",
        "expected_codes": CODING_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "coding",
        "evaluation_type": "blind",
    },
    {
        "id": "blind_synonym_assignment",
        "question": "과제 있는 수업 알려줘",
        "expected_codes": ASSIGNMENT_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "assignment",
        "evaluation_type": "blind",
    },
    {
        "id": "blind_synonym_homework",
        "question": "숙제 있는 강의 알려줘",
        "expected_codes": ASSIGNMENT_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "assignment",
        "evaluation_type": "blind",
    },
    {
        "id": "blind_synonym_english_lecture",
        "question": "영강 목록 알려줘",
        "expected_codes": ["BDSC208", "BDSC211", "BDSC212", "BDSC302", "BDSC315", "BDSC414"],
        "expect_exact_codes": True,
        "expected_mode": "structured_title",
        "synonym_group": "english_lecture",
        "evaluation_type": "blind",
    },
    {
        "id": "blind_synonym_english_words",
        "question": "영어 강의 목록을 전부 알려줘",
        "expected_codes": ["BDSC208", "BDSC211", "BDSC212", "BDSC302", "BDSC315", "BDSC414"],
        "expect_exact_codes": True,
        "expected_mode": "structured_title",
        "synonym_group": "english_lecture",
        "evaluation_type": "blind",
    },
]

FEATURE_CASES = [
    {
        "id": "feature_coding_development",
        "question": "개발과 구현을 하는 수업 목록 알려줘",
        "expected_codes": CODING_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "coding",
        "evaluation_type": "blind",
    },
    {
        "id": "feature_coding_english",
        "question": "coding 중심 과목 뭐가 있어?",
        "expected_codes": CODING_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "coding",
        "evaluation_type": "blind",
    },
    {
        "id": "feature_coding_code",
        "question": "코드 작성하는 강의 알려줘",
        "expected_codes": CODING_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "coding",
        "evaluation_type": "blind",
    },
    {
        "id": "feature_practice_korean",
        "question": "실습 위주 수업을 전부 보여줘",
        "expected_codes": PRACTICE_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "practice",
        "evaluation_type": "blind",
    },
    {
        "id": "feature_practice_hands_on",
        "question": "hands-on 과목 알려줘",
        "expected_codes": PRACTICE_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "practice",
        "evaluation_type": "blind",
    },
    {
        "id": "feature_practice_direct",
        "question": "직접 해보는 강의가 뭐야?",
        "expected_codes": PRACTICE_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "practice",
        "evaluation_type": "blind",
    },
    {
        "id": "feature_presentation_korean",
        "question": "발표 있는 과목을 모두 알려줘",
        "expected_codes": PRESENTATION_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "presentation",
        "evaluation_type": "blind",
    },
    {
        "id": "feature_presentation_loanword",
        "question": "프레젠테이션 하는 수업 알려줘",
        "expected_codes": PRESENTATION_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "presentation",
        "evaluation_type": "blind",
    },
    {
        "id": "feature_presentation_english",
        "question": "presentation 과목 뭐가 있어?",
        "expected_codes": PRESENTATION_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "presentation",
        "evaluation_type": "blind",
    },
    {
        "id": "feature_discussion_korean",
        "question": "토론하는 수업 알려줘",
        "expected_codes": DISCUSSION_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "discussion",
        "evaluation_type": "blind",
    },
    {
        "id": "feature_discussion_synonym",
        "question": "토의 중심 과목 알려줘",
        "expected_codes": DISCUSSION_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "discussion",
        "evaluation_type": "blind",
    },
    {
        "id": "feature_discussion_english",
        "question": "discussion 수업 뭐야?",
        "expected_codes": DISCUSSION_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "discussion",
        "evaluation_type": "blind",
    },
    {
        "id": "feature_assignment_report",
        "question": "레포트 있는 수업 알려줘",
        "expected_codes": ASSIGNMENT_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "assignment",
        "evaluation_type": "blind",
    },
    {
        "id": "feature_assignment_english",
        "question": "homework 있는 과목 알려줘",
        "expected_codes": ASSIGNMENT_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "assignment",
        "evaluation_type": "blind",
    },
    {
        "id": "feature_assignment_document",
        "question": "보고서 과제가 있는 강의 알려줘",
        "expected_codes": ASSIGNMENT_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "assignment",
        "evaluation_type": "blind",
    },
    {
        "id": "feature_project_korean",
        "question": "프로젝트 수업 목록 알려줘",
        "expected_codes": PROJECT_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "project",
        "evaluation_type": "blind",
    },
    {
        "id": "feature_project_style",
        "question": "프로젝트형 과목 뭐가 있어?",
        "expected_codes": PROJECT_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "project",
        "evaluation_type": "blind",
    },
    {
        "id": "feature_project_english",
        "question": "project 기반 강의 알려줘",
        "expected_codes": PROJECT_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "project",
        "evaluation_type": "blind",
    },
    {
        "id": "feature_quiz_korean",
        "question": "퀴즈 있는 과목 알려줘",
        "expected_codes": QUIZ_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "quiz",
        "evaluation_type": "blind",
    },
    {
        "id": "feature_quiz_short_test",
        "question": "쪽지시험 보는 강의가 뭐야?",
        "expected_codes": QUIZ_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "quiz",
        "evaluation_type": "blind",
    },
    {
        "id": "feature_quiz_english",
        "question": "quiz 포함된 수업 보여줘",
        "expected_codes": QUIZ_CODES,
        "expect_exact_codes": True,
        "expected_mode": "structured_feature",
        "synonym_group": "quiz",
        "evaluation_type": "blind",
    },
]

TEST_CASES.extend(BLIND_CASES)
TEST_CASES.extend(FEATURE_CASES)


def source_codes(result: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for source in result.get("sources", []) or []:
        code = str(source.get("course_code", ""))
        if code and code not in codes:
            codes.append(code)
    return codes


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    result = RAG.answer(str(case["question"]), case.get("history", []))
    elapsed = time.perf_counter() - started

    codes = source_codes(result)
    expected = [str(code) for code in case.get("expected_codes", [])]
    expected_hit = all(code in codes for code in expected)
    exact_codes_hit = set(codes) == set(expected) if case.get("expect_exact_codes") else True
    expected_mode = str(case.get("expected_mode", ""))
    mode_hit = not expected_mode or result.get("mode") == expected_mode
    forbidden = [str(code) for code in case.get("forbidden_codes", [])]
    forbidden_clear = not any(code in codes for code in forbidden)
    expected_answer_terms = [str(term) for term in case.get("expected_answer_terms", [])]
    answer = str(result.get("answer", ""))
    answer_terms_hit = all(term in answer for term in expected_answer_terms)
    expected_answer_any_groups = [
        [str(term) for term in group]
        for group in case.get("expected_answer_any_groups", [])
    ]
    answer_any_groups_hit = all(
        any(term in answer for term in group) for group in expected_answer_any_groups
    )
    forbidden_answer_terms = [
        str(term) for term in case.get("forbidden_answer_terms", [])
    ]
    forbidden_answer_clear = not any(term in answer for term in forbidden_answer_terms)
    sources = result.get("sources", []) or []
    expected_set = set(expected)
    hits = [code for code in codes if code in expected_set]
    recall_at_k = len(set(hits)) / len(expected_set) if expected_set else None
    reciprocal_rank = 0.0
    for rank, code in enumerate(codes, 1):
        if code in expected_set:
            reciprocal_rank = 1.0 / rank
            break
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, code in enumerate(codes, 1)
        if code in expected_set
    )
    ideal_count = min(len(expected_set), len(codes))
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    ndcg_at_k = dcg / ideal_dcg if expected_set and ideal_dcg else None
    no_sources_hit = not sources if case.get("expect_no_sources") else True
    course_keys = {
        f"{source.get('course_code', '')}-{source.get('class_no', '')}"
        for source in sources
    }
    return {
        "id": case["id"],
        "question": case["question"],
        "elapsed_seconds": round(elapsed, 3),
        "mode": result.get("mode", ""),
        "model": result.get("model", ""),
        "source_codes": codes,
        "source_count": len(sources),
        "unique_course_count": len(course_keys),
        "expected_codes": expected,
        "expected_hit": expected_hit,
        "exact_codes_expected": bool(case.get("expect_exact_codes")),
        "exact_codes_hit": exact_codes_hit,
        "recall_at_k": round(recall_at_k, 3) if recall_at_k is not None else "",
        "reciprocal_rank": round(reciprocal_rank, 3) if expected_set else "",
        "ndcg_at_k": round(ndcg_at_k, 3) if ndcg_at_k is not None else "",
        "expected_mode": expected_mode,
        "mode_hit": mode_hit,
        "forbidden_codes": forbidden,
        "forbidden_clear": forbidden_clear,
        "expected_answer_terms": expected_answer_terms,
        "answer_terms_hit": answer_terms_hit,
        "expected_answer_any_groups": expected_answer_any_groups,
        "answer_any_groups_hit": answer_any_groups_hit,
        "forbidden_answer_terms": forbidden_answer_terms,
        "forbidden_answer_clear": forbidden_answer_clear,
        "synonym_group": str(case.get("synonym_group", "")),
        "synonym_consistency_hit": True,
        "evaluation_type": str(case.get("evaluation_type", "regression")),
        "unique_sources_hit": (
            len(sources) == len(course_keys)
            if case.get("expect_unique_sources")
            else True
        ),
        "no_sources_expected": bool(case.get("expect_no_sources")),
        "no_sources_hit": no_sources_hit,
        "answer_length": len(str(result.get("answer", ""))),
        "answer": answer,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate RAG latency and retrieval quality.")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_DIR / "data" / "evaluation")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    cases = TEST_CASES[: args.limit] if args.limit > 0 else TEST_CASES
    args.output_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases, 1):
        print(f"[{index}/{len(cases)}] {case['id']}: {case['question']}", flush=True)
        result = run_case(case)
        results.append(result)
        print(
            f"  {result['elapsed_seconds']:.3f}s mode={result['mode']} "
            f"model={result['model']} sources={','.join(result['source_codes'])}",
            flush=True,
        )

    synonym_groups: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        group = result["synonym_group"]
        if group:
            synonym_groups.setdefault(group, []).append(result)
    for group_results in synonym_groups.values():
        expected_set = set(group_results[0]["source_codes"])
        consistent = all(set(result["source_codes"]) == expected_set for result in group_results)
        for result in group_results:
            result["synonym_consistency_hit"] = consistent

    latencies = [float(result["elapsed_seconds"]) for result in results]
    expected_cases = [result for result in results if result["expected_codes"]]
    mode_cases = [result for result in results if result["expected_mode"]]
    no_source_cases = [result for result in results if result["no_sources_expected"]]
    recall_values = [
        float(result["recall_at_k"])
        for result in expected_cases
        if result["recall_at_k"] != ""
    ]
    reciprocal_rank_values = [
        float(result["reciprocal_rank"])
        for result in expected_cases
        if result["reciprocal_rank"] != ""
    ]
    ndcg_values = [
        float(result["ndcg_at_k"])
        for result in expected_cases
        if result["ndcg_at_k"] != ""
    ]
    passed_cases = [
        result
        for result in results
        if result["expected_hit"]
        and result["mode_hit"]
        and result["exact_codes_hit"]
        and result["forbidden_clear"]
        and result["answer_terms_hit"]
        and result["answer_any_groups_hit"]
        and result["forbidden_answer_clear"]
        and result["synonym_consistency_hit"]
        and result["unique_sources_hit"]
        and result["no_sources_hit"]
    ]
    summary = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "case_count": len(results),
        "average_seconds": round(statistics.mean(latencies), 3) if latencies else 0,
        "median_seconds": round(statistics.median(latencies), 3) if latencies else 0,
        "max_seconds": round(max(latencies), 3) if latencies else 0,
        "expected_retrieval_accuracy": round(
            sum(1 for result in expected_cases if result["expected_hit"])
            / max(len(expected_cases), 1),
            3,
        ),
        "expected_mode_accuracy": round(
            sum(1 for result in mode_cases if result["mode_hit"])
            / max(len(mode_cases), 1),
            3,
        ),
        "mean_recall_at_k": round(
            statistics.mean(recall_values), 3
        ) if recall_values else 0,
        "mean_reciprocal_rank": round(
            statistics.mean(reciprocal_rank_values), 3
        ) if reciprocal_rank_values else 0,
        "mean_ndcg_at_k": round(
            statistics.mean(ndcg_values), 3
        ) if ndcg_values else 0,
        "no_source_accuracy": round(
            sum(1 for result in no_source_cases if result["no_sources_hit"])
            / max(len(no_source_cases), 1),
            3,
        ),
        "answer_content_accuracy": round(
            sum(
                1
                for result in results
                if result["answer_terms_hit"]
                and result["answer_any_groups_hit"]
                and result["forbidden_answer_clear"]
            )
            / max(len(results), 1),
            3,
        ),
        "synonym_consistency_accuracy": round(
            sum(
                1
                for result in results
                if not result["synonym_group"] or result["synonym_consistency_hit"]
            )
            / max(len(results), 1),
            3,
        ),
        "full_case_accuracy": round(len(passed_cases) / max(len(results), 1), 3),
        "rag_mode": RAG.rag_mode,
        "course_count": len(RAG.syllabi),
        "chunk_count": len(RAG.chunks),
    }

    payload = {"summary": summary, "results": results}
    (args.output_dir / "latest_evaluation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    csv_path = args.output_dir / "latest_evaluation.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fieldnames = [
            "id",
            "question",
            "elapsed_seconds",
            "mode",
            "model",
            "source_codes",
            "source_count",
            "unique_course_count",
            "expected_codes",
            "expected_hit",
            "exact_codes_expected",
            "exact_codes_hit",
            "recall_at_k",
            "reciprocal_rank",
            "ndcg_at_k",
            "expected_mode",
            "mode_hit",
            "forbidden_codes",
            "forbidden_clear",
            "expected_answer_terms",
            "answer_terms_hit",
            "expected_answer_any_groups",
            "answer_any_groups_hit",
            "forbidden_answer_terms",
            "forbidden_answer_clear",
            "synonym_group",
            "synonym_consistency_hit",
            "evaluation_type",
            "unique_sources_hit",
            "no_sources_expected",
            "no_sources_hit",
            "answer_length",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            row = {key: result.get(key, "") for key in fieldnames}
            row["source_codes"] = ",".join(result["source_codes"])
            row["expected_codes"] = ",".join(result["expected_codes"])
            row["forbidden_codes"] = ",".join(result["forbidden_codes"])
            row["expected_answer_terms"] = ",".join(result["expected_answer_terms"])
            row["expected_answer_any_groups"] = json.dumps(
                result["expected_answer_any_groups"], ensure_ascii=False
            )
            row["forbidden_answer_terms"] = ",".join(result["forbidden_answer_terms"])
            writer.writerow(row)

    failed_results = [result for result in results if result not in passed_cases]
    markdown_lines = [
        "# 과목 조회·출처·응답 조건 회귀 검사",
        "",
        "이 검사는 기존 질문의 명시적 조건을 검사합니다. 최종 출처의 과목 단위 적중을 측정하며, 검색 청크 품질·답변 전체 사실성·독립 사용자 정확도를 의미하지 않습니다.",
        "",
        f"- 평가 시각: {summary['created_at']}",
        f"- 전체 질문: {summary['case_count']}개",
        f"- 전체 통과: {len(passed_cases)}개",
        f"- 전체 통과율: {summary['full_case_accuracy'] * 100:.1f}%",
        f"- 기대 과목 검색 정확도: {summary['expected_retrieval_accuracy'] * 100:.1f}%",
        f"- 응답 모드 정확도: {summary['expected_mode_accuracy'] * 100:.1f}%",
        f"- 평균 Recall@k: {summary['mean_recall_at_k']:.3f}",
        f"- MRR: {summary['mean_reciprocal_rank']:.3f}",
        f"- 평균 nDCG@k: {summary['mean_ndcg_at_k']:.3f}",
        f"- 무근거 출처 억제 정확도: {summary['no_source_accuracy'] * 100:.1f}%",
        f"- 답변 내용 검사 정확도: {summary['answer_content_accuracy'] * 100:.1f}%",
        f"- 동의어 결과 일관성: {summary['synonym_consistency_accuracy'] * 100:.1f}%",
        f"- 평균 응답시간: {summary['average_seconds']:.3f}초",
        f"- 최대 응답시간: {summary['max_seconds']:.3f}초",
        f"- 수집 과목: {summary['course_count']}개",
        f"- 검색 청크: {summary['chunk_count']}개",
        f"- RAG 모드: `{summary['rag_mode']}`",
        "",
        "## 평가 범위",
        "",
        "과목명·학수번호 검색, PBL·캡스톤, 이수구분, 교수별 과목, 평가방식, 과목 비교, "
        "요일·교시·강의실, 중복 분반 시간표, 모호한 질문, 범위 밖 질문을 포함합니다.",
        "",
    ]
    if failed_results:
        markdown_lines.extend(["## 실패 질문", ""])
        markdown_lines.extend(
            f"- `{result['id']}`: {result['question']}" for result in failed_results
        )
    else:
        markdown_lines.extend(["## 결과", "", "모든 평가 질문이 통과했습니다."])
    (args.output_dir / "latest_evaluation_summary.md").write_text(
        "\n".join(markdown_lines) + "\n", encoding="utf-8"
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if failed_results:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
