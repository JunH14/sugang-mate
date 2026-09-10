# 수강메이트 · Sugang Mate

**강의계획서를 찾아 읽는 과정을, 근거를 확인할 수 있는 수강 상담으로.**

강의계획서의 본문과 시간표·학점·이수구분 같은 정형 정보를 함께 활용하는 Python 서비스입니다. 질문 유형에 따라 구조화 조회와 문서 검색을 연결해, 과목 탐색 → 비교 → 후속 질문을 한 대화에서 처리합니다.

[![Offline checks](https://github.com/JunH14/sugang-mate/actions/workflows/checks.yml/badge.svg)](https://github.com/JunH14/sugang-mate/actions/workflows/checks.yml)

`Python 3.11 / 3.12` · `Gradio` · `BM25` · `선택적 Chroma + Gemini`

[프로젝트 소개](docs/portfolio.md) · [3분 시연](docs/demo.md) · [설계 결정](docs/decisions.md) · [평가 보고서](docs/evaluation.md) · [실행 안내](docs/running.md)

## 왜 만들었나요?

수강할 과목을 고르려면 강의계획서마다 평가방식과 수업 내용을 읽고, 별도로 시간표와 이수구분을 대조해야 합니다. 문서 검색만으로는 “전공필수 **전체** 목록”이나 “방금 비교한 과목 **중에서**” 같은 질문을 정확히 처리하기 어렵습니다.

| 사용자의 질문 | 해결 방법 |
|---|---|
| “전공필수 과목을 전부 알려줘” | 과목 메타데이터를 필터링해 목록·개수를 계산 |
| “두 과목의 평가방식을 비교해줘” | 과목별 문서 근거를 찾아 비교하고 출처 표시 |
| “그중 전공필수는 뭐야?” | 직전 대화의 과목 집합 안에서 조건 적용 |
| “이 답변은 어디서 확인할 수 있어?” | 답변에 사용한 과목과 근거를 화면에서 확인 |

2026학년도 1학기 고려대학교 세종캠퍼스 빅데이터사이언스학부 강의계획서를 다룬 기말 프로젝트에서 출발했습니다. 수집·정제·검색·화면 구현에 더해 데이터 품질 감사, 기능 회귀 검사, 검색 비교 실험을 함께 정리했습니다.

## 실행 화면

![가상 과목의 평가방식을 비교하고 근거를 확인하는 화면](docs/assets/demo-comparison.png)

공개 데모는 직접 작성한 **가상 과목 6개**로 동작합니다. API 키, 학교 원문, 벡터 DB 없이 실행할 수 있으며 실제 대학의 개설 정보를 제공하는 화면은 아닙니다.

## 빠른 시작

Python 3.11 또는 3.12가 필요합니다.

~~~text
git clone https://github.com/JunH14/sugang-mate.git
cd sugang-mate
python -m venv .venv
~~~

**Windows PowerShell**

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_demo.py
~~~

**macOS / Linux**

~~~bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run_demo.py
~~~

브라우저에서 [localhost:7861](http://127.0.0.1:7861)을 엽니다. 종료는 실행 창에서 Ctrl+C입니다. 포트 변경, 실제 데이터 연결, 선택적 Gemini·Chroma 설정은 [실행 안내](docs/running.md)를 참고하세요. `requirements-lock.txt`는 Windows / Python 3.12에서 검증한 정확한 버전 목록입니다.

같은 대화에서 다음 순서로 질문하면 조회·비교·맥락 처리를 확인할 수 있습니다.

1. `DEMO201과 DEMO202의 평가방식을 비교해줘`
2. `그중 전공필수는 뭐야?`
3. 새 대화를 열고 `전공필수 과목을 알려줘`

2번은 직전에 비교한 과목 중 **DEMO201**, 3번은 전체 데이터의 **DEMO101·DEMO201**을 반환합니다. [예상 결과와 시연 절차](docs/demo.md)에서 확인할 수 있습니다.

## 구조와 핵심 설계

~~~mermaid
flowchart LR
    A[강의계획서와 과목 목록] --> B[수집 · 형식별 추출 · 중복 제거]
    B --> C[과목 메타데이터]
    B --> D[문서 구역별 청크]
    Q[질문과 대화 맥락] --> R[질문 유형 판정]
    R --> C
    R --> S[BM25 · 선택적 Chroma 검색]
    D --> S
    C --> O[답변 · 근거 · 응답 상태]
    S --> G[근거 추출 · 선택적 Gemini 생성]
    G --> O
~~~

| 결정 | 해결하려는 문제 | 구현 |
|---|---|---|
| 구조화 조회와 내용 검색 분리 | 검색 상위 일부를 전체 목록·개수로 오해하는 문제 | [app.py](app.py) |
| 분반 정보 보존 | 중복 본문 제거 중 시간표 유실 | [데이터 품질 보고서](docs/data-quality.md) |
| 대화 맥락을 포함한 캐시, 반환 객체 복사 | 같은 후속 질문이 다른 대화의 답변을 재사용하는 문제 | [캐시](sugang_mate/cache.py), [격리 검사](tests/test_core.py) |
| 데이터 해시·모델·청크 수 검사 | 데이터 교체 후 오래된 벡터 인덱스 사용 | [인덱스 생성](scripts/build_vector_db.py) |
| 명시적 오프라인 실행 | 키·외부 서비스 없이 프로젝트를 재현해야 하는 상황 | [공개 실행기](run_demo.py), [설정](sugang_mate/config.py) |

구현 과정의 선택과 남은 기술 과제는 [설계 결정](docs/decisions.md)에 기록했습니다.

## 검증 결과

| 검증 범위 | 결과 | 근거 |
|---|---|---|
| 데이터 정제 감사 | 26개 분반 레코드 → 25개 문서, 중복 과목의 시간표 3개 보존 | [데이터 보고서](docs/data-quality.md) |
| 자동 테스트 | 32개 통과: 설정·캐시·대화 격리·화면·평가 지표 | [로컬 검증 기록](evaluation/release-checks.json) |
| 현재 코드 기능 회귀 | API·답변 캐시를 끄고 기존 질문 **103/103** 조건 통과 | [집계와 코드 해시](evaluation/release-regression.json) |
| 기존 개선 기록 감사 | 같은 300문항·검사 조건에서 **273 → 300** 통과 | [과거 기록 재집계](evaluation/legacy-audit.json) |
| 신규 검색 비교 | 30문항의 근거 포함 과목 Recall@5 chunks: 고정 길이 **50.0%**, 구역별 **33.3%** | [질문별 결과](evaluation/retrieval-results.json) |

기능 회귀의 통과율은 지정된 출처·문자열·응답 모드 조건의 충족률입니다. 생성 답변 전체의 사실성 점수가 아닙니다. 신규 검색 진단에서는 **구역별 분할만으로 성능이 개선된다는 가정이 지지되지 않았습니다.** 실패 사례를 포함해 데이터·설정·질문 해시와 함께 [평가 보고서](docs/evaluation.md)에 남겼습니다.

신규 30문항은 원문을 확인한 에이전트가 작성한 진단 세트로, 독립적인 사용자 평가가 아닙니다. 실제 코퍼스는 공개하지 않으며 공개본에서 재현할 수 있는 범위는 가상 데이터 실행·자동 테스트·지표 계산입니다.

## 테스트

~~~text
python -m unittest discover -s tests -v
python scripts/verify_release.py
~~~

위 `python`은 의존성을 설치한 가상환경의 실행 파일을 사용합니다. GitHub Actions 설정은 Python 3.11·3.12에서 자동 테스트, 공개 예시 시나리오, 문서 링크와 공개 파일 검사를 수행합니다. 실행 상태는 상단 배지에서 확인할 수 있습니다.

## 프로젝트 둘러보기

| 경로 | 내용 |
|---|---|
| `app.py`, `retrieval_core.py` | 질문 분류·조회·검색·맥락·답변 생성 |
| `gradio_app.py`, `run_demo.py` | 사용자 화면과 공개 데모 |
| `sugang_mate/` | 환경 설정·데이터 식별·TTL/LRU 캐시 |
| `scripts/` | 수집·추출·인덱싱·평가·공개 패키지 생성 |
| `tests/`, `evaluation/` | 자동 검사·진단 라벨·결과·재현 조건 |
| `docs/` | 프로젝트 소개·시연·설계·데이터 품질·실험 |

## 범위와 다음 단계

현재는 한 학과의 수집 스냅샷을 대상으로 한 프로토타입입니다. 실제 Gemini API 재검증, 동일 조건의 하이브리드 검색 비교, 독립 평가 질문, 실제 학생 사용성 실험이 다음 검증 과제입니다. [사용자 검증 절차](docs/user-study.md)를 마련했습니다.

학교 원문·정제 본문·벡터 DB·키·사용자 대화·로그는 공개 대상에서 제외합니다. 개발·검증·문서화에는 AI 도구를 활용했습니다. 저장소 소개 형식을 검토하며 참고한 유사 프로젝트는 [참고 자료](docs/references.md)에 기록했습니다.
