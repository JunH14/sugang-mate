# 데이터

`sample/syllabus_texts.jsonl`은 포트폴리오 실행을 위해 직접 작성한 가상 과목 6개입니다. 실제 대학·교수·시간표와 무관하며 자유롭게 실행·변경할 수 있습니다. 실제 데이터가 없으면 앱이 자동으로 이 파일을 읽습니다.

원래 프로젝트는 2026학년도 1학기 고려대학교 세종캠퍼스 빅데이터사이언스학부 강의계획서를 사용했습니다. 실제 수집 원문·정제 본문·벡터 DB는 공개 패키지에서 제외합니다. 직접 접근 권한을 확인한 데이터는 `processed/syllabus_texts.jsonl`에 두거나 `SUGANG_DATA_PATH`로 지정합니다.

재수집: `python scripts/collect_syllabi.py --output-dir . --limit 0 --delay 0.7`

수집은 학교 사이트의 현재 접근 가능 상태에 영향을 받습니다. 원본 출처: https://sugang.korea.ac.kr/ 및 https://infodepot.korea.ac.kr/. 로그·연락처·원문을 무심코 공개하지 않도록 수집 산출물은 Git에서 제외합니다.

각 JSONL 행은 `course_code`, `class_no`, `course_name`, `professor`, `completion_type`, `credit`, `class_hours`, `schedule_summary`, `schedule_entries`, `syllabus_url`, `text_path`, `sources`, `text` 필드를 사용합니다. 구체적인 형식은 예시 데이터에서 확인할 수 있습니다.

