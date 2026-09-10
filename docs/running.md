# 실행·재현·공개 안내

## 1. 공개 예시 실행

Windows PowerShell:

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_demo.py
~~~

http://127.0.0.1:7861 을 열고 종료할 때 Ctrl+C를 누른다. 포트가 사용 중이면 `run_demo.py --port 7870`으로 바꾼다. 가상 과목 6개를 사용하며 외부 모델 요청을 하지 않는다. API 키는 필요 없다.

macOS/Linux에서는 `.venv/bin/python`을 사용한다. `requirements.txt`는 Python 3.11·3.12 공통 설치용이며 Gradio 버전을 고정한다. `requirements-lock.txt`는 Windows/Python 3.12 검증 환경의 정확한 설치 버전 목록으로, Python 3.11 공통 잠금 파일은 아니다.

## 2. 실제 데이터의 로컬 실행

접근할 수 있는 정제 JSONL을 `data/processed/syllabus_texts.jsonl`에 둔다.

~~~powershell
$env:SUGANG_OFFLINE="1"
$env:SUGANG_DATA_PATH="data/processed/syllabus_texts.jsonl"
.\.venv\Scripts\python.exe gradio_app.py
~~~

`SUGANG_DATA_PATH`를 생략하면 로컬 정제 파일이 있을 때 우선 읽는다. 명시한 파일이 없으면 오류를 반환한다. 공개 데모 실행기 `run_demo.py`는 항상 가상 데이터를 선택하므로 실제 데이터 실행에는 `gradio_app.py`를 사용한다.

## 3. 선택적 수집·Gemini/Chroma 실행

아래 경로는 이번 공개본 정리에서 외부 호출로 재검증하지 않았다. 학교 사이트의 현재 접근 상태와 이용 가능한 API 모델에 영향을 받는다.

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-rag.txt
Copy-Item .env.example .env
~~~

`.env`에 본인 키를 넣고 `SUGANG_OFFLINE=0`으로 변경한다. 셸에 `SUGANG_OFFLINE=1`을 앞서 설정했다면 셸 환경변수가 우선하므로 `$env:SUGANG_OFFLINE="0"`으로 바꾼다. 예시 경로를 강제로 지정한 환경변수도 실제 경로로 변경한다.

~~~powershell
.\.venv\Scripts\python.exe scripts/collect_syllabi.py --output-dir . --limit 0 --delay 0.7
.\.venv\Scripts\python.exe scripts/build_vector_db.py
.\.venv\Scripts\python.exe gradio_app.py
~~~

수집기는 대상 학기·학과가 코드에 명시되어 있다. 다른 학기·학과로의 수집은 설정과 페이지 형식을 검토해야 한다. 별도 변환만 수행하려면 `python scripts/extract_texts.py --project-dir .`을 사용한다.

벡터 생성에는 API 사용량이 발생한다. 오래된 데이터/모델/청크 수의 인덱스는 앱이 사용하지 않는다. 다른 임베딩 모델의 인덱스를 초기화할 때만 내용을 확인하고 `--reset`을 사용한다. 현재 코드 기본 모델은 `gemini-3.1-flash-lite`, 임베딩은 `gemini-embedding-001`이다. 모델 가용성은 실행 계정에서 확인해야 한다.

## 4. 검사와 보고서 재현

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/verify_release.py
~~~

실제 정제 코퍼스를 보유한 경우:

~~~powershell
$env:SUGANG_OFFLINE="1"
$env:SUGANG_DATA_PATH="data/processed/syllabus_texts.jsonl"
$env:ANSWER_CACHE_MAX="0"
.\.venv\Scripts\python.exe scripts/evaluate_rag.py --output-dir data/evaluation/release_regression
.\.venv\Scripts\python.exe scripts/benchmark_retrieval.py --corpus data/processed/syllabus_texts.jsonl
~~~

저장된 수집 폴더와 과거 평가 결과를 별도로 보유한 경우:

~~~text
python scripts/analyze_data.py --source ORIGINAL_PROJECT --output evaluation
python scripts/audit_legacy_evaluation.py --legacy-root ORIGINAL_PROJECT/data/evaluation
~~~

원문이 없는 공개 예시에서 실제 과목 평가를 돌려 성공한 것처럼 해석하지 않는다. 공개 예시는 기능 테스트용 데이터이며 실제 과목의 평가 숫자와 연결되지 않는다.

## 5. GitHub 업로드용 파일 생성

~~~powershell
.\.venv\Scripts\python.exe scripts/export_public.py
~~~

상위 폴더에 `sugang-mate-public.zip`을 생성한다. 허용 목록의 코드·문서·샘플·집계만 포함하고 비밀키 패턴과 개인 PC 경로를 검사한다. `RELEASE_MANIFEST.json`에 각 파일의 SHA-256이 들어 있다. 검사는 모든 종류의 비밀 정보 탐지를 보증하지 않으므로 새 자료를 추가할 때 내용을 확인한다.

ZIP을 풀어 나온 `sugang-mate` 폴더를 GitHub 저장소로 사용한다. 이미 프로젝트 코드가 있는 폴더를 올릴 때 GitHub에는 README를 자동 생성하지 않은 빈 저장소를 만든다. ZIP 파일 하나만 올리지 않고 내부 코드 파일을 올린다.

~~~text
git init
git add .
git commit -m "Prepare Sugang Mate portfolio release"
git branch -M main
git remote add origin https://github.com/JunH14/sugang-mate.git
git push -u origin main
~~~

실제 사용자명·저장소 주소로 변경한다. 원본 기말 제출 폴더 전체를 선택하지 않는다. GitHub Actions는 첫 push 이후 실제 실행 결과를 확인한다.

공식 안내: [기존 로컬 코드 업로드](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github), [Gradio ChatInterface](https://www.gradio.app/docs/gradio/chatinterface).
