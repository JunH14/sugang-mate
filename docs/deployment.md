# 공개 데모 배포

[문서 모아보기](README.md) · [일반 실행·평가 재현](running.md)

**[공개 데모 열기](https://sugang-mate.onrender.com)** · [데이터 준비 검사](../evaluation/hosted-data-checks.json)

공개 데모는 **2026학년도 1학기에 수집한 실제 과목 25개와 Gemini API**로 운영합니다. 최신 개설 정보를 다시 수집한 서비스는 아닙니다. 방문자는 설치·API 키·학교 로그인 없이 주소만 열어 사용할 수 있으며, 서비스는 개발자의 PC와 독립된 Render 서버에서 실행됩니다. 브라우저에서 실제 과목의 Gemini 답변·비교·후속 질문과 새 대화 초기화를 확인했습니다.

학교 원본은 로컬에 보존합니다. 연락처와 로컬 경로를 제외해 준비한 서버용 JSONL만 Render의 Secret File로 전달하며, 원문·서버용 파일·벡터 DB·API 키는 GitHub에 넣지 않습니다. 답변에 필요한 강의계획서 발췌는 공개 화면에서 확인할 수 있습니다.

## 서버용 데이터 준비

본인이 사용할 수 있는 원본 정제 데이터를 `data/processed/syllabus_texts.jsonl`에 둔 뒤 실행합니다.

```powershell
python scripts/prepare_hosted_data.py
```

출력은 Git에서 제외되는 `artifacts/hosted/syllabus_texts.jsonl`이며, 공개 검사 기록은 `evaluation/hosted-data-checks.json`입니다. 경로는 `--input`, `--output`, `--report`로 지정할 수 있습니다. 원본 파일은 수정하지 않습니다. 실행기는 검사 기록의 `hosted_dataset_sha256`·`document_count`와 서버 파일을 대조하므로, 데이터 파일과 해당 검사 기록은 함께 갱신해야 합니다.

## Render 설정

Render에서 **New → Web Service → Public Git Repository**를 선택하고 다음과 같이 설정합니다.

| 항목 | 값 |
|---|---|
| Repository URL | `https://github.com/JunH14/sugang-mate` |
| Branch | `main` |
| Language | `Python 3` |
| Python Version | `3.12.12` |
| Region | `Singapore` |
| Instance Type | `Free` |
| Build Command | `pip install -r requirements-online.txt` |
| Start Command | `python run_public.py` |
| Environment | `GOOGLE_API_KEY`를 서버 비밀 환경변수로 등록 |
| Secret File | `sugang-syllabi.jsonl`에 준비한 JSONL 내용 등록 |

Secret File은 `/etc/secrets/sugang-syllabi.jsonl`로 읽습니다. 기본 `collected` 모드에서 파일이 없거나 검사 기록과 맞지 않으면 실행을 중단합니다. 가상 데이터로 조용히 바뀌지 않습니다.

키의 값은 저장소·명령어·문서에 넣지 않습니다. `run_public.py`가 `0.0.0.0`과 Render의 `PORT` 환경변수에 맞춰 서버를 엽니다. 공개 저장소 URL 방식은 자동 배포를 지원하지 않으므로, GitHub에 변경 사항을 올린 뒤 Render에서 **Manual Deploy → Deploy latest commit**을 실행합니다. [Render 웹 서비스 안내](https://render.com/docs/web-services)

## 같은 설정으로 로컬 실행

저장소 루트에서 실행합니다. 다음은 Windows PowerShell 기준입니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-online.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

로컬 `.env`의 `GOOGLE_API_KEY`에 본인 키를 설정한 뒤 실행합니다.

```powershell
$env:SUGANG_PUBLIC_DATA_PATH="artifacts/hosted/syllabus_texts.jsonl"
$env:PORT="7861"
.\.venv\Scripts\python.exe run_public.py
```

[localhost:7861](http://127.0.0.1:7861)을 엽니다. macOS/Linux에서는 `.venv/bin/python`을 사용하고 `SUGANG_PUBLIC_DATA_PATH=artifacts/hosted/syllabus_texts.jsonl PORT=7861 .venv/bin/python run_public.py`로 실행합니다.

원본이 없는 저장소 복제본에서 **가상 과목 6개를 온라인으로** 실행하려면 `$env:SUGANG_PUBLIC_DATA_MODE="sample"`을 명시하고 `run_public.py`를 실행합니다. macOS/Linux에서는 `SUGANG_PUBLIC_DATA_MODE=sample PORT=7861 .venv/bin/python run_public.py`입니다. 외부 API를 사용하지 않는 가상 데이터 시연은 기존 `run_demo.py`를 사용합니다.

## 공개 실행의 범위

- 공개 실행기는 검사된 데이터와 온라인 모드를 사용하고 관리자 기능을 차단합니다. 실제 학교 정보는 수집 당시 2026학년도 1학기 범위로 안내합니다.
- 시간표·목록처럼 계산 가능한 질문은 직접 조회하고, 내용 질문에는 BM25 검색 근거를 Gemini에 전달합니다. 공개 실행에는 Chroma 설치나 임베딩 인덱스 생성이 필요하지 않습니다.
- 공개 실행의 고정 제한은 클라이언트당 분당 6개 질문, 서버 프로세스당 시간당 60회 모델 요청입니다. 클라이언트는 서버 임의값을 섞어 해시한 IP로 구분하며, 프록시 환경에서는 여러 사용자가 같은 한도를 공유할 수 있습니다. 대기 시간이 길어지지 않도록 Gemini 요청 제한 시간을 20초로 두고 SDK와 앱의 시도를 각각 1회로 제한합니다.
- 횟수 제한은 서버 메모리에 저장됩니다. 재시작하면 초기화되며, 여러 프로세스 사이에서 공유되지 않습니다. 계정 전체의 과금 상한을 보장하는 장치는 아닙니다.

Render 무료 호스팅과 Gemini API의 사용량·과금은 별개입니다. API 쿼터나 일시적 오류가 발생하면 화면의 응답 상태를 확인합니다.

## 무료 호스팅에서 예상할 동작

15분 동안 요청이 없으면 서비스가 쉬고, 다음 접속에서 다시 켜지는 데 약 1분이 걸릴 수 있습니다. 실행 중 만든 임시 파일과 파일 변경은 재배포·재시작·휴면 때 사라집니다. 무료 실행 시간은 워크스페이스 전체에서 월 750시간을 공유하며, 소진하면 다음 달까지 무료 서비스가 중단됩니다. 이 배포는 포트폴리오 체험용입니다. [Render 무료 서비스 조건](https://render.com/docs/free)

과목 JSONL은 Render 서비스 설정에 저장한 Secret File로 실행 시 제공됩니다. 임시 파일처럼 직접 재업로드할 필요가 없으며, 시작할 때마다 같은 해시·문서 수 검사를 거칩니다. 실제 서버 재시작 후에도 **같은 25개 자료와 파일 해시가 유지되는 것**을 독립 접속으로 확인했습니다. [Render Secret File 안내](https://render.com/docs/configure-environment-variables#secret-files)

## 공개 실행 확인

브라우저에서는 BDSC205의 SAS 근거 질문, BDSC201·BDSC203 평가 비교와 전공필수 후속 질문, 대화 초기화를 확인했습니다.

서버를 재시작한 뒤 GitHub가 제공하는 별도 Ubuntu·Python 3.12 환경에서 **근거 질문·시간표·범위 밖 안내 3개가 모두 통과**했습니다. 검사 클라이언트는 로그인·쿠키·API 키 없이 공개 주소만 사용했고, BDSC205 질문은 캐시를 쓰지 않은 실제 Gemini 응답이었습니다. 데이터 25개와 해시 일치를 확인했으며 비밀 데이터 파일의 직접 다운로드 요청은 HTTP 403으로 차단됐습니다. [성공한 독립 실행](https://github.com/JunH14/sugang-mate/actions/runs/34437384036) · [배포 확인 집계](../evaluation/hosted-deployment-checks.json)

이 검사는 [공개 서버 검사 워크플로](https://github.com/JunH14/sugang-mate/actions/workflows/hosted-demo.yml)에서 수동으로 다시 실행할 수 있습니다. 대표 질문의 정상 동작 확인이며 장기간 운영 안정성이나 전체 답변 정확도를 측정한 결과는 아닙니다.

[기존 접속 확인 기록](../evaluation/deployment-checks.json)은 가상 과목 6개로 운영하던 당시의 기록입니다. [기존 API 검증](live-api-validation.md)의 가상·실제 과목 및 로컬 Chroma 검사는 각각의 환경에서 수행한 별도 진단입니다. 실제 25개가 공개 서버에서 작동한다는 확인과 혼동하지 않습니다.
