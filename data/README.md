# 데이터

저장소에 포함한 `sample/syllabus_texts.jsonl`은 직접 작성한 **가상 과목 6개**입니다. `python run_demo.py`는 항상 이 파일을 사용하며 API 키가 필요 없습니다.

공개 서버는 **2026학년도 1학기에 수집한 고려대학교 세종캠퍼스 빅데이터사이언스학부 과목 25개**를 사용하도록 구성합니다. 수집 당시의 강의계획서이며 최신 개설 정보로 갱신한 자료는 아닙니다. 실제 원문·정제 본문·벡터 DB는 GitHub에서 제외합니다.

## 서버에 전달하는 데이터

본인이 사용할 수 있는 원본을 `processed/syllabus_texts.jsonl`에 두고 `python scripts/prepare_hosted_data.py`를 실행합니다. 원본은 보존하고 연락처·로컬 경로를 제외한 서버용 파일을 `artifacts/hosted/syllabus_texts.jsonl`에 만듭니다. 공개 기록에는 문서 수와 파일 해시 등 검사 결과를 남깁니다.

서버용 JSONL은 Render Secret File로 전달합니다. `run_public.py`는 기본적으로 `/etc/secrets/sugang-syllabi.jsonl`과 [검사 기록](../evaluation/hosted-data-checks.json)을 대조하며, 파일 누락·불일치 시 실행을 중단합니다. 로컬 준비 파일은 `SUGANG_PUBLIC_DATA_PATH`로 지정합니다. 원본을 갖고 있지 않으면 `SUGANG_PUBLIC_DATA_MODE=sample`로 가상 과목의 온라인 실행을 명시할 수 있습니다. [설치·배포 방법](../docs/deployment.md)

서버에 보관한 전체 JSONL을 다운로드 파일로 제공하지 않으며, 답변에 사용한 강의계획서 발췌는 공개 화면에 표시합니다. 서버의 BM25·Gemini 실행과 로컬 실제 데이터의 Chroma 검증은 별개입니다.

## 원본 수집과 형식

재수집: `python scripts/collect_syllabi.py --output-dir . --limit 0 --delay 0.7`

수집은 학교 사이트의 현재 접근 가능 상태에 영향을 받습니다. 원본 출처는 [고려대학교 수강신청](https://sugang.korea.ac.kr/)과 [InfoDepot](https://infodepot.korea.ac.kr/)입니다. 수집 산출물은 Git에서 제외합니다.

각 JSONL 행은 `course_code`, `class_no`, `course_name`, `professor`, `completion_type`, `credit`, `class_hours`, `schedule_summary`, `schedule_entries`, `syllabus_url`, `text_path`, `sources`, `text` 필드를 사용합니다. 구체적인 형식은 예시 데이터에서 확인할 수 있습니다.
