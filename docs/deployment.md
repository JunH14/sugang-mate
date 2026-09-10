# 공개 데모 배포

[문서 모아보기](README.md) · [일반 실행·평가 재현](running.md)

Gradio 화면을 Render의 무료 Python 웹 서비스로 실행합니다. 공개 데모는 **가상 과목 6개와 실제 Gemini API**를 사용합니다. 학교 원문·정제 본문·로컬 벡터 DB를 서버에 올리지 않습니다. 공개 접속 주소와 검증 완료 상태는 저장소 첫 화면에 기록합니다.

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
$env:PORT="7861"
.\.venv\Scripts\python.exe run_public.py
```

[localhost:7861](http://127.0.0.1:7861)을 엽니다. macOS/Linux에서는 `.venv/bin/python`을 사용하고 `PORT=7861 .venv/bin/python run_public.py`로 실행합니다. 외부 API를 사용하지 않는 시연은 기존 `run_demo.py`를 사용합니다.

## 공개 실행의 범위

- 공개 실행기는 가상 데이터와 온라인 모드를 고정하고 관리자 기능을 차단합니다. 로컬 실제 과목 검증에는 [별도 실행 경로](running.md)를 사용합니다.
- 시간표·목록처럼 계산 가능한 질문은 직접 조회하고, 내용 질문에는 BM25 검색 근거를 Gemini에 전달합니다. 공개 실행에는 Chroma 설치나 임베딩 인덱스 생성이 필요하지 않습니다.
- 공개 실행의 고정 제한은 클라이언트당 분당 6개 질문, 서버 프로세스당 시간당 60회 모델 요청입니다. 클라이언트는 서버 임의값을 섞어 해시한 IP로 구분하며, 프록시 환경에서는 여러 사용자가 같은 한도를 공유할 수 있습니다. 대기 시간이 길어지지 않도록 Gemini 요청 제한 시간을 20초로 두고 SDK와 앱의 시도를 각각 1회로 제한합니다.
- 횟수 제한은 서버 메모리에 저장됩니다. 재시작하면 초기화되며, 여러 프로세스 사이에서 공유되지 않습니다. 계정 전체의 과금 상한을 보장하는 장치는 아닙니다.

Render 무료 호스팅과 Gemini API의 사용량·과금은 별개입니다. API 쿼터나 일시적 오류가 발생하면 화면의 응답 상태를 확인합니다.

## 무료 호스팅에서 예상할 동작

15분 동안 요청이 없으면 서비스가 쉬고, 다음 접속에서 다시 켜지는 데 약 1분이 걸릴 수 있습니다. 파일 변경은 재배포·재시작·휴면 때 사라집니다. 무료 실행 시간은 워크스페이스 전체에서 월 750시간을 공유하며, 소진하면 다음 달까지 무료 서비스가 중단됩니다. 이 배포는 포트폴리오 체험용입니다. [Render 무료 서비스 조건](https://render.com/docs/free)

배포 후에는 새 접속에서 화면 로딩, 과목 비교, 후속 질문, 출처와 응답 상태를 확인합니다. 실행 성공과 답변 내용 검증은 구분하며, 실제 모델 요청 결과는 [API 검증 기록](live-api-validation.md), 전체 평가 범위는 [평가 보고서](evaluation.md)에서 확인할 수 있습니다.
