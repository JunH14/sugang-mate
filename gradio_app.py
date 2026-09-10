from __future__ import annotations

import argparse
import hashlib
import html
import inspect
import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from types import SimpleNamespace


PROJECT_DIR = Path(__file__).resolve().parent
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

import gradio as gr

import app as core
from sugang_mate.public_limits import MeteredModels, PublicLimitError, PublicLimits
from sugang_mate.public_http import normalize_history, RequestSizeLimit
from sugang_mate.chat_presentation import present_answer
from starlette.middleware import Middleware


DEPARTMENT = "공공정책대학 · 빅데이터사이언스학부"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()
UPDATE_LOCK = threading.Lock()
PUBLIC_DEMO = os.environ.get("SUGANG_PUBLIC_DEMO") == "1"
PUBLIC_LIMITS = PublicLimits() if PUBLIC_DEMO else None
CLIENT_SALT = secrets.token_bytes(32)
if PUBLIC_LIMITS is not None and core.RAG.gemini_client is not None:
    original_client = core.RAG.gemini_client
    core.RAG.gemini_client = SimpleNamespace(
        models=MeteredModels(original_client.models, PUBLIC_LIMITS),
        underlying_client=original_client,
    )


def is_sample_data() -> bool:
    return core.RAG.data_path.resolve() == (PROJECT_DIR / "data/sample/syllabus_texts.jsonl").resolve()


def dataset_label() -> str:
    return "가상 과목 6개 · 공개 실행 예시" if is_sample_data() else "2026학년도 1학기 · 빅데이터사이언스학부"


def example_questions() -> list[str]:
    if is_sample_data():
        return ["CSV 파일에서 결측치를 처리하려면 어떤 과목이 관련돼?", "DEMO201과 DEMO202의 평가방식을 비교해줘", "전공필수 과목을 알려줘", "월요일 수업을 알려줘"]
    if PUBLIC_DEMO:
        return ["BDSC205에서 SAS를 다룬다는 근거가 있어?", "수리통계학은 무슨 요일 몇 시에 수업해?", "전공필수 과목을 알려줘", "BDSC401의 강의계획서를 근거로 딥러닝이론의 특징을 설명해줘"]
    return core.EXAMPLE_QUESTIONS


def format_sources(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return "### 근거 자료\n\n이 답변에는 별도의 강의계획서 출처가 없습니다."

    lines = ["### 근거 자료", ""]
    seen: set[str] = set()
    for source in sources:
        course_key = f"{source.get('course_code', '')}-{source.get('class_no', '')}"
        if course_key in seen:
            continue
        seen.add(course_key)

        course_name = html.escape(str(source.get("course_name", "과목명 미기재")))
        professor = html.escape(str(source.get("professor", "담당교수 미기재")))
        completion_type = html.escape(str(source.get("completion_type", "")) or "미기재")
        schedule = html.escape(str(source.get("schedule_summary", "")) or "미정/별도 운영")
        snippet = html.escape(str(source.get("snippet", "")).strip())
        syllabus_url = str(source.get("syllabus_url", "")).strip()
        lines.extend(
            [
                f"#### {len(seen)}. `{course_key}` {course_name}",
                f"담당교수 {professor} · {completion_type}  ",
                f"수업시간 {schedule}",
            ]
        )
        if snippet:
            lines.append(f"> {snippet}")
            lines.append("")
        if syllabus_url.startswith(("https://", "http://")):
            lines.append(f"[강의계획안 열기]({syllabus_url})")
        lines.append("")

    return "\n".join(lines)


def mode_label(result: dict[str, Any]) -> str:
    mode = str(result.get("mode", "unknown"))
    model = str(result.get("model", "")).strip()
    if mode == "chroma_gemini":
        return f"Chroma + BM25 + MMR + Gemini{f' ({model})' if model else ''}"
    if mode == "extractive":
        return "검색 근거 직접 추출"
    if mode == "keyword_gemini":
        return f"키워드 검색 + Gemini{f' ({model})' if model else ''}"
    if mode == "out_of_scope":
        return "강의계획서 범위 밖 질문"
    if mode == "structured":
        return "전체 강의계획서 구조화 판정"
    if mode == "structured_title":
        return "과목명 구조화 검색"
    if mode == "structured_catalog":
        return "전체 과목 메타데이터 조회"
    if mode == "structured_numeric":
        return "구조화 데이터 직접 계산"
    if mode == "structured_schedule":
        return "수강신청 시간표 데이터 직접 조회"
    if mode == "clarification":
        return "질문 조건 확인"
    return {
        "structured_course_assessment": "과목별 평가방식 조회",
        "structured_course_metadata": "과목 기본정보 조회",
        "structured_learning": "수업 내용 조회",
        "structured_filter": "조건에 맞는 과목 조회",
        "structured_filter_assessment": "조건별 평가방식 비교",
        "structured_filter_learning": "조건별 수업 내용 조회",
        "structured_feature": "수업 특징별 조회",
        "structured_activity": "수업 활동별 조회",
        "structured_followup": "앞서 확인한 과목에 대한 후속 조회",
        "fallback": "관련 근거를 찾지 못함",
    }.get(mode, "근거 기반 정보 조회")


def timing_label(result: dict[str, Any]) -> str:
    timings = result.get("timings", {}) or {}
    total = float(timings.get("total_seconds", 0.0))
    if timings.get("cache_hit"):
        return f"캐시 응답 · {total:.3f}초"
    search = float(timings.get("search_seconds", 0.0))
    generation = float(timings.get("generation_seconds", 0.0))
    context = float(timings.get("context_seconds", 0.0))
    return f"전체 {total:.2f}초 · 문맥 {context:.2f}초 / 검색 {search:.2f}초 / 생성 {generation:.2f}초"


def diagnostics_markdown(result: dict[str, Any]) -> str:
    sources = result.get("sources", []) or []
    timings = result.get("timings", {}) or {}
    warning = str(result.get("warning", "")).strip()
    lines = [
        "### 응답 상태",
        "",
        f"- 처리 방식: **{mode_label(result)}**",
        f"- 처리 시간: **{timing_label(result)}**",
        f"- 근거 과목: **{len({source.get('course_code', '') for source in sources if source.get('course_code')})}개**",
        f"- 캐시 사용: **{'예' if timings.get('cache_hit') else '아니오'}**",
    ]
    context = result.get("context_resolved") or {}
    if context:
        if context.get("type") == "course_set_reference":
            course_codes = ", ".join(str(code) for code in context.get("course_codes", []))
            lines.append(f"- 대화 맥락: **직전 답변 과목 집합 ({html.escape(course_codes)})**")
        elif context.get("type") == "set_difference":
            missing_codes = ", ".join(str(code) for code in context.get("missing_codes", []))
            lines.append(f"- 대화 맥락: **직전 답변과 비교한 누락 과목 ({html.escape(missing_codes)})**")
        else:
            lines.append(
                f"- 대화 맥락: **{html.escape(str(context.get('course_name', '')))} "
                f"({html.escape(str(context.get('course_code', '')))})**"
            )
    if warning:
        lines.extend(["", f"> 안내: {html.escape(warning)}"])
    return "\n".join(lines)


def answer_result(message: str, history: list[dict[str, Any]], request: gr.Request = None) -> dict[str, Any]:
    """Shared request boundary for both the legacy API and the chat screen."""
    question = message.strip() if isinstance(message, str) else ""
    if not question:
        return {"answer": "질문을 입력해 주세요.", "sources": [], "mode": "input_error", "error": True}
    if len(question) > 500:
        return {"answer": "질문은 500자 이내로 입력해 주세요.", "sources": [], "mode": "input_error", "error": True}
    try:
        if PUBLIC_LIMITS is not None:
            client = getattr(request, "client", None)
            address = getattr(client, "host", None) or "unknown"
            client_key = hashlib.sha256(CLIENT_SALT + str(address).encode("utf-8")).hexdigest()
            PUBLIC_LIMITS.check_chat(client_key)
        # The JSON endpoint accepts raw input; bound it even in local demo mode.
        return core.RAG.answer(question, normalize_history(history))
    except PublicLimitError as error:
        return {"answer": str(error), "sources": [], "mode": "request_limit", "error": True}
    except Exception as error:
        print(f"Chat request failed: {type(error).__name__}", file=sys.stderr)
        return {"answer": "답변을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.", "sources": [], "mode": "request_error", "error": True}


def chat(message: str, history: list[dict[str, Any]], request: gr.Request = None) -> tuple[str, str, str]:
    """Preserve the original /chat response contract for external checks."""
    result = answer_result(message, history, request)
    answer = str(result.get("answer", "답변을 생성하지 못했습니다.")).strip()
    sources = result.get("sources", []) or []
    return answer, format_sources(sources), diagnostics_markdown(result)


def respond(message: str, history: list[dict[str, str]], request: gr.Request = None) -> dict[str, Any]:
    """Return one answer and its own citations for the conversation screen."""
    result = answer_result(message, history, request)
    view = present_answer(result)
    view.update({"mode_label": mode_label(result), "timing_label": timing_label(result), "error": bool(result.get("error"))})
    return view


def course_table_markdown() -> str:
    lines = [
        "| 학수번호 | 분반 | 과목명 | 이수구분 | 담당교수 | 수업시간·강의실 |",
        "|---|---:|---|---|---|---|",
    ]
    for syllabus in core.RAG.syllabi:
        lines.append(
            f"| {syllabus.course_code} | {syllabus.class_no} | "
            f"{syllabus.course_name} | {syllabus.completion_type or '-'} | "
            f"{syllabus.professor or '-'} | {syllabus.schedule_summary or '미정/별도 운영'} |"
        )
    return "\n".join(lines)


def status_html() -> str:
    if PUBLIC_DEMO:
        kind = "sample" if is_sample_data() else "collected"
        fingerprint = html.escape(os.environ.get("SUGANG_PUBLIC_DATA_SHA256", ""), quote=True)
        label = "가상 과목" if is_sample_data() else "실제 수집 과목"
        return f"""
        <div class="status-strip" data-dataset-kind="{kind}" data-course-count="{len(core.RAG.syllabi)}" data-dataset-sha256="{fingerprint}">
          <div><strong>{len(core.RAG.syllabi)}개</strong><span>{label}</span></div>
          <div><strong>비교·후속 질문</strong><span>대화로 탐색</span></div>
          <div><strong>강의계획서</strong><span>답변 근거 확인</span></div>
          <div><strong>Gemini 연결</strong><span>본문 질문 AI 답변</span></div>
        </div>
        """
    mode = {
        "chroma_gemini": "Chroma + BM25 + MMR + Gemini",
        "keyword_gemini": "키워드 + Gemini",
        "keyword_extract": "키워드 검색",
    }.get(core.RAG.rag_mode, core.RAG.rag_mode)
    return f"""
    <div class="status-strip">
      <div><strong>{len(core.RAG.syllabi)}</strong><span>강의계획서</span></div>
      <div><strong>{len(core.RAG.chunks)}</strong><span>검색 청크</span></div>
      <div><strong>7</strong><span>Retriever top-k</span></div>
      <div><strong>{mode}</strong><span>현재 RAG 모드</span></div>
    </div>
    """


def latest_data_status() -> str:
    data_path = core.DATA_PATH
    if not data_path.exists():
        return "데이터 파일을 찾을 수 없습니다."
    updated = datetime.fromtimestamp(data_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    return f"현재 데이터 적용 시각: **{updated}**"


def evaluation_summary_markdown() -> str:
    path = PROJECT_DIR / "data" / "evaluation" / "latest_evaluation.json"
    if not path.exists():
        return "자동 평가 결과 파일이 없습니다."
    try:
        summary = json.loads(path.read_text(encoding="utf-8")).get("summary", {})
    except Exception:
        return "자동 평가 결과를 읽지 못했습니다."
    case_count = int(summary.get("case_count", 0))
    accuracy = float(summary.get("full_case_accuracy", 0.0)) * 100
    retrieval = float(summary.get("expected_retrieval_accuracy", 0.0)) * 100
    mode = float(summary.get("expected_mode_accuracy", 0.0)) * 100
    return (
        f"- 평가 질문: **{case_count}개**\n"
        f"- 전체 통과율: **{accuracy:.1f}%**\n"
        f"- 기대 과목 검색 정확도: **{retrieval:.1f}%**\n"
        f"- 응답 모드 정확도: **{mode:.1f}%**\n"
        f"- 평균 응답시간: **{float(summary.get('average_seconds', 0.0)):.3f}초**\n"
        f"- 최대 응답시간: **{float(summary.get('max_seconds', 0.0)):.3f}초**\n"
        f"- 최근 평가: `{summary.get('created_at', '미확인')}`\n\n"
        "과목명·학수번호, PBL·캡스톤, 전공필수, 교수별 과목, 시간표, "
        "모호한 질문, 범위 밖 질문을 포함해 검사합니다."
    )


def _run_update_step(command: list[str], label: str) -> None:
    completed = subprocess.run(
        command,
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    if completed.returncode == 0:
        return
    detail = (completed.stderr or completed.stdout or "알 수 없는 오류").strip()
    detail = detail[-1200:]
    raise RuntimeError(f"{label} 실패: {detail}")


def update_data(
    password: str,
    progress: gr.Progress = gr.Progress(track_tqdm=False),
) -> tuple[str, str, str]:
    if PUBLIC_DEMO or is_sample_data():
        return "공개 예시에서는 데이터 갱신을 제공하지 않습니다.", status_html(), course_table_markdown()
    if not ADMIN_PASSWORD:
        return (
            "관리자 갱신이 비활성화되어 있습니다. `.env` 또는 Space Secrets에 `ADMIN_PASSWORD`를 설정하세요.",
            status_html(),
            course_table_markdown(),
        )
    if not hmac.compare_digest((password or "").strip(), ADMIN_PASSWORD):
        return "관리자 비밀번호가 올바르지 않습니다.", status_html(), course_table_markdown()
    if not UPDATE_LOCK.acquire(blocking=False):
        return "이미 데이터 갱신이 진행 중입니다.", status_html(), course_table_markdown()

    try:
        python = sys.executable
        progress(0.05, desc="과목 목록과 강의계획안 수집 중")
        _run_update_step(
            [
                python,
                str(PROJECT_DIR / "scripts" / "collect_syllabi.py"),
                "--output-dir",
                str(PROJECT_DIR),
                "--delay",
                "0.4",
                "--skip-extraction",
            ],
            "강의계획안 수집",
        )
        progress(0.42, desc="강의계획서 텍스트 추출과 중복 제거 중")
        _run_update_step(
            [
                python,
                str(PROJECT_DIR / "scripts" / "extract_texts.py"),
                "--project-dir",
                str(PROJECT_DIR),
                "--duplicate-threshold",
                "0.90",
            ],
            "텍스트 추출",
        )
        progress(0.70, desc="Chroma 벡터 DB 갱신 중")
        _run_update_step(
            [
                python,
                str(PROJECT_DIR / "scripts" / "build_vector_db.py"),
                "--project-dir",
                str(PROJECT_DIR),
            ],
            "벡터 DB 갱신",
        )
        progress(0.94, desc="챗봇에 새 데이터 적용 중")
        core.reload_rag()
        progress(1.0, desc="완료")
        completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return (
            f"데이터 갱신 완료: **{completed_at}** · 과목 {len(core.RAG.syllabi)}개 · 청크 {len(core.RAG.chunks)}개",
            status_html(),
            course_table_markdown(),
        )
    except subprocess.TimeoutExpired:
        return "데이터 갱신 시간이 30분을 초과해 중단했습니다.", status_html(), course_table_markdown()
    except Exception as error:
        return f"데이터 갱신 실패: {html.escape(str(error))}", status_html(), course_table_markdown()
    finally:
        UPDATE_LOCK.release()


CSS = """
html, body { margin: 0 !important; overflow: hidden !important; background: #fff !important; }
.gradio-container { width: 100% !important; max-width: none !important; margin: 0 !important; padding: 0 !important; }
.gradio-container > .main, .gradio-container .wrap { padding: 0 !important; gap: 0 !important; }
.gradio-container > .main { max-width: none !important; margin: 0 !important; width: 100% !important; }
#sugang-shell { margin: 0 !important; padding: 0 !important; border: 0 !important; width: 100% !important; }
#sugang-shell .html-container { padding: 0 !important; }
#admin-update { position: fixed; bottom: 80px; right: 24px; z-index: 50; max-width: 560px; max-height: 70vh; overflow: auto; }
footer, .built-with { display: none !important; }
"""


def build_theme() -> gr.Theme:
    return gr.themes.Base(primary_hue="red", secondary_hue="gray", neutral_hue="gray", radius_size="sm")


def screen_boot() -> dict[str, Any]:
    sample = is_sample_data()
    catalog = [{field: str(getattr(course, field, "") or "") for field in (
        "course_code", "class_no", "course_name", "professor", "completion_type", "schedule_summary",
    )} for course in core.RAG.syllabi]
    return {
        "scope": f"가상 과목 {len(catalog)}개 · 실행 예시" if sample else f"2026-1 · {len(catalog)}개 과목",
        "catalog": catalog,
        "online": not core.offline_enabled(),
        "sample": sample,
        "examples": example_questions()[:3] if sample else [
            "수리통계학은 언제 수업해?", "전공필수 과목을 알려줘", "BDSC205에서는 어떤 내용을 배워?",
        ],
        "data_notice": (
            "직접 작성한 가상 과목을 사용하는 실행 예시입니다. 실제 수강신청 정보가 아닙니다."
            if sample else
            "프로젝트에서 직접 수집한 2026학년도 1학기 고려대학교 세종캠퍼스 빅데이터사이언스학부 강의계획서의 정제본을 사용합니다. 수집 당시 자료이며 실시간 수강신청 정보가 아닙니다. 일부 첨부 문서의 추출 품질과 미기재 항목에 한계가 있습니다."
        ),
    }


def build_demo() -> gr.Blocks:
    ui_dir = PROJECT_DIR / "ui"
    with gr.Blocks(title="수강메이트 · 근거를 확인하는 수강 상담") as demo:
        gr.HTML(
            value=(ui_dir / "chat.html").read_text(encoding="utf-8"),
            css_template=(ui_dir / "chat.css").read_text(encoding="utf-8"),
            js_on_load=(ui_dir / "chat.js").read_text(encoding="utf-8"),
            apply_default_css=False, elem_id="sugang-shell", boot=screen_boot(),
        )
        # Retain the public /chat wire contract and dataset identity for existing clients.
        # The custom screen uses /respond; both are queued in the same concurrency group.
        with gr.Column(visible=False):
            status_component = gr.HTML(status_html())
            chatbot = gr.Chatbot()
            textbox = gr.Textbox()
            source_panel = gr.Markdown()
            diagnostics_panel = gr.Markdown()
            gr.ChatInterface(
                fn=chat, chatbot=chatbot, textbox=textbox,
                additional_outputs=[source_panel, diagnostics_panel],
                flagging_mode="never", save_history=False, api_name="chat",
                concurrency_limit=2 if PUBLIC_DEMO else 3,
            )
        gr.api(respond, api_name="respond", queue=True, concurrency_limit=2 if PUBLIC_DEMO else 3,
               concurrency_id="sugang-chat", api_description="A bounded question and recent history; returns an answer with its own citations.")

        if bool(ADMIN_PASSWORD) and not PUBLIC_DEMO and not core.offline_enabled() and not is_sample_data():
            with gr.Accordion("관리자 데이터 갱신", open=False, elem_id="admin-update"):
                admin_password = gr.Textbox(label="관리자 비밀번호", type="password")
                update_button = gr.Button("강의계획안 다시 수집하고 적용")
                update_status = gr.Markdown(latest_data_status())
                course_table_component = gr.Markdown(course_table_markdown())
                update_button.click(fn=update_data, inputs=[admin_password],
                    outputs=[update_status, status_component, course_table_component],
                    api_name="update_data", api_visibility="private", concurrency_limit=1)
    for dependency in demo.fns.values():
        if dependency.api_name in {"chat", "respond"} or (
            dependency.fn is not None and inspect.unwrap(dependency.fn) in {chat, respond}
        ):
            dependency.concurrency_id = "sugang-chat"
    if PUBLIC_DEMO:
        demo.queue(max_size=16, default_concurrency_limit=2)
    return demo


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Gradio Sugang Mate prototype.")
    default_host = os.environ.get(
        "GRADIO_SERVER_NAME", "0.0.0.0" if os.environ.get("SPACE_ID") else "127.0.0.1"
    )
    default_port = int(os.environ.get("PORT", os.environ.get("GRADIO_SERVER_PORT", "7861")))
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", type=int, default=default_port)
    parser.add_argument("--share", action="store_true")
    parser.add_argument("--inbrowser", action="store_true")
    args = parser.parse_args()

    demo = build_demo()
    _, local_url, share_url = demo.launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
        inbrowser=args.inbrowser,
        show_error=False,
        prevent_thread_lock=True,
        footer_links=[],
        theme=build_theme(),
        css=CSS,
        head='<meta name="theme-color" content="#8b0029">',
        app_kwargs={"middleware": [Middleware(RequestSizeLimit)]} if PUBLIC_DEMO else None,
        blocked_paths=[
            "/etc/secrets", str(core.RAG.data_path.resolve()),
            str(PROJECT_DIR / "sugang-syllabi.jsonl"), str(PROJECT_DIR / ".env"),
        ] if PUBLIC_DEMO else None,
    )
    print(f"Local URL: {local_url}", flush=True)
    if share_url:
        print(f"Public URL: {share_url}", flush=True)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        demo.close()


if __name__ == "__main__":
    main()
