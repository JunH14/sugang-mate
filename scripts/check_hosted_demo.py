"""Check the deployed Gradio app from an anonymous, independent Python process.

Uses only the standard library and public HTTPS endpoints. No browser cookies,
Google key, GitHub token, local corpus or app dependencies are read. Up to three
chat requests are made, only after the hosted 25-course dataset fingerprint has
matched its committed audit. Evidence contains checks and timings, never answer
text, source excerpts, request/session identifiers or private documents.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import platform
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "https://sugang-mate.onrender.com"
MAX_RESPONSE_BYTES = 1_048_576
CASES = (
    ("semantic_grounding", "BDSC205에서 SAS를 다룬다는 근거가 있어?", "BDSC205", "gemini"),
    ("course_schedule", "BDSC201 수업시간을 알려줘", "BDSC201", "schedule"),
    ("outside_scope", "다음 학기 등록금이 얼마야?", None, "outside_scope"),
)


class SmokeFailure(RuntimeError):
    """Fixed diagnostic codes that never contain remote response text."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A login redirect is a failed anonymous check, not a new destination.
        return None


def validate_origin(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https" or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or parsed.path not in ("", "/")
        or parsed.port not in (None, 443)
    ):
        raise SmokeFailure("invalid_https_origin")
    return f"https://{parsed.netloc}"


class StatusParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.statuses = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "status-strip" not in (attributes.get("class") or "").split():
            return
        self.statuses.append({
            "dataset_sha256": attributes.get("data-dataset-sha256"),
            "course_count": attributes.get("data-course-count"),
            "dataset_kind": attributes.get("data-dataset-kind"),
        })


def verify_hosted_status(config: dict, expected: dict) -> dict:
    expected_hash = expected.get("hosted_dataset_sha256")
    if (
        expected.get("document_count") != 25
        or not isinstance(expected_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
    ):
        raise SmokeFailure("invalid_expected_dataset_audit")
    if config.get("auth_required"):
        raise SmokeFailure("authentication_required")
    statuses = []
    for component in config.get("components", []):
        if not isinstance(component, dict) or component.get("type") != "html":
            continue
        value = component.get("props", {}).get("value")
        if isinstance(value, str):
            parser = StatusParser()
            parser.feed(value)
            statuses.extend(parser.statuses)
    if len(statuses) != 1:
        raise SmokeFailure("hosted_dataset_status_missing_or_ambiguous")
    status = statuses[0]
    if status["dataset_kind"] != "collected" or status["course_count"] != "25":
        raise SmokeFailure("hosted_dataset_is_not_collected_25")
    if status["dataset_sha256"] != expected_hash:
        raise SmokeFailure("hosted_dataset_fingerprint_mismatch")
    return {"dataset_kind": "collected", "course_count": 25, "dataset_sha256": expected_hash}


def verify_chat_contract(config: dict, info: dict) -> str:
    prefix = config.get("api_prefix")
    if not isinstance(prefix, str) or not re.fullmatch(r"/[A-Za-z0-9_-]+", prefix):
        raise SmokeFailure("unsupported_api_prefix")
    endpoint = info.get("named_endpoints", {}).get("/chat", {})
    parameters = endpoint.get("parameters", [])
    if (
        endpoint.get("api_visibility") != "public"
        or len(parameters) != 1
        or parameters[0].get("parameter_name") != "message"
        or len(endpoint.get("returns", [])) != 3
    ):
        raise SmokeFailure("public_chat_contract_changed")
    # /info describes the SDK interface, which hides State components. Raw
    # /call requests/responses still contain their positional placeholders.
    components = {item.get("id"): item.get("type") for item in config.get("components", [])}
    dependencies = [item for item in config.get("dependencies", []) if item.get("api_name") == "chat"]
    if len(dependencies) != 1:
        raise SmokeFailure("raw_chat_contract_changed")
    dependency = dependencies[0]
    input_types = [components.get(item) for item in dependency.get("inputs", [])]
    output_types = [components.get(item) for item in dependency.get("outputs", [])]
    if input_types != ["textbox", "state"] or output_types != ["json", "state", "markdown", "markdown"]:
        raise SmokeFailure("raw_chat_contract_changed")
    return prefix


def read_complete_event(stream, *, deadline: float, clock=time.monotonic):
    """Read Gradio's bounded SSE stream; discard progress and error payloads."""
    event = ""
    data = []
    received = 0
    while True:
        if clock() >= deadline:
            raise SmokeFailure("chat_event_timeout")
        line = stream.readline(MAX_RESPONSE_BYTES + 1)
        received += len(line)
        if received > MAX_RESPONSE_BYTES:
            raise SmokeFailure("chat_event_too_large")
        if not line:
            raise SmokeFailure("chat_event_incomplete")
        text = line.decode("utf-8").rstrip("\r\n")
        if text == "":
            if event == "error":
                raise SmokeFailure("remote_chat_event_error")
            if event == "complete":
                try:
                    return json.loads("\n".join(data))
                except (ValueError, TypeError):
                    raise SmokeFailure("invalid_chat_event_json") from None
            event, data = "", []
        elif text.startswith("event:"):
            event = text[6:].strip()
        elif text.startswith("data:"):
            data.append(text[5:].lstrip(" "))


class AnonymousClient:
    def __init__(self, origin: str, timeout: float = 45):
        self.origin = validate_origin(origin)
        self.timeout = timeout
        # A fresh opener has neither a cookie jar nor authentication handlers.
        self.opener = urllib.request.build_opener(NoRedirect())

    def _request(self, path, payload=None, *, stream=False):
        if not path.startswith("/") or path.startswith("//"):
            raise SmokeFailure("invalid_endpoint_path")
        headers = {
            "User-Agent": "sugang-mate-hosted-smoke/1.0",
            "Accept": "text/event-stream" if stream else "application/json",
        }
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.origin + path, data=data, headers=headers)
        return self.opener.open(request, timeout=self.timeout)

    def get_json(self, path, payload=None):
        with self._request(path, payload) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise SmokeFailure("json_response_too_large")
        value = json.loads(body)
        if not isinstance(value, dict):
            raise SmokeFailure("invalid_json_response")
        return value

    def get_http_status(self, path):
        """Inspect only status; never read the protected dataset response body."""
        try:
            with self._request(path) as response:
                return response.status
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            return status

    def chat(self, prefix: str, question: str):
        # Gradio 6.17.3's raw API requires the hidden State input placeholder.
        # Do not set session_hash: /call/.../{event_id} reads the queue keyed by
        # event_id, which is the server's default session when none is supplied.
        # Each scenario is independent; no browser cookies or session are reused.
        event = self.get_json(prefix + "/call/chat", {
            "data": [question, None],
        })
        event_id = event.get("event_id", "")
        if not isinstance(event_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event_id):
            raise SmokeFailure("invalid_chat_event_id")
        with self._request(prefix + "/call/chat/" + event_id, stream=True) as response:
            payload = read_complete_event(response, deadline=time.monotonic() + 120)
        if not isinstance(payload, list) or len(payload) != 4 or payload[1] is not None:
            raise SmokeFailure("invalid_raw_chat_result_shape")
        # Mirror the official SDK's skipped-State output handling explicitly.
        return [payload[0], payload[2], payload[3]]


def verify_chat_result(payload, course_code: str | None, expected_mode: str) -> dict:
    if not isinstance(payload, list) or len(payload) != 3 or not all(isinstance(x, str) for x in payload):
        raise SmokeFailure("invalid_chat_result_shape")
    answer, sources, diagnostics = payload
    if not answer.strip():
        raise SmokeFailure("empty_answer")
    if course_code and (course_code not in answer or course_code not in sources):
        raise SmokeFailure("expected_course_evidence_missing")
    if "DEMO" in answer or "DEMO" in sources:
        raise SmokeFailure("synthetic_course_leaked_into_collected_demo")
    if "안내:" in diagnostics or "요청 제한" in diagnostics or "처리 실패" in diagnostics:
        raise SmokeFailure("fallback_or_limit_warning")
    labels = {"gemini": "Gemini", "schedule": "시간표", "outside_scope": "강의계획서 범위 밖 질문"}
    if labels[expected_mode] not in diagnostics:
        raise SmokeFailure("expected_answer_mode_missing")
    cache_hit = None
    if "캐시 사용: **예**" in diagnostics:
        cache_hit = True
    elif "캐시 사용: **아니오**" in diagnostics:
        cache_hit = False
    if cache_hit is None:
        raise SmokeFailure("cache_provenance_missing")
    return {
        "answer_nonempty": True,
        "expected_course_evidence": course_code,
        "response_mode": expected_mode,
        "cache_hit": cache_hit,
        "fresh_gemini_generation": expected_mode == "gemini" and not cache_hit,
        "fallback_or_limit_warning": False,
    }


def run_checks(client, expected: dict, report: dict) -> None:
    config = client.get_json("/config")
    report["dataset"] = verify_hosted_status(config, expected)
    prefix = config.get("api_prefix")
    if not isinstance(prefix, str) or not re.fullmatch(r"/[A-Za-z0-9_-]+", prefix):
        raise SmokeFailure("unsupported_api_prefix")
    info = client.get_json(prefix + "/info")
    prefix = verify_chat_contract(config, info)
    report["anonymous_access"] = {"configuration": True, "public_chat_contract": True}
    protected_status = client.get_http_status(prefix + "/file=/etc/secrets/sugang-syllabi.jsonl")
    if protected_status not in (403, 404):
        raise SmokeFailure("protected_dataset_download_not_blocked")
    report["protected_dataset_file"] = {"download_blocked": True, "http_status": protected_status}
    for identifier, question, code, mode in CASES:
        started = time.monotonic()
        result = verify_chat_result(client.chat(prefix, question), code, mode)
        report["checks"].append({"id": identifier, "passed": True, "seconds": round(time.monotonic() - started, 3), **result})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_URL)
    parser.add_argument("--expected-data", type=Path, default=ROOT / "evaluation/hosted-data-checks.json")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/hosted-smoke.json")
    args = parser.parse_args(argv)
    report = {
        "schema_version": 1,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed",
        "checks": [],
        "runtime": {"os": platform.system(), "python": platform.python_version(), "github_actions": os.environ.get("GITHUB_ACTIONS") == "true"},
        "authentication": {"cookies_used": False, "api_keys_used": False, "login_used": False},
        "evidence_scope": "Anonymous hosted access and three behavioral checks; cached Gemini output is explicitly distinguished from fresh generation. No answer or source text is saved.",
    }
    try:
        report["base_url"] = validate_origin(args.base_url)
        expected = json.loads(args.expected_data.read_text(encoding="utf-8"))
        run_checks(AnonymousClient(report["base_url"]), expected, report)
        report["status"] = "passed"
    except SmokeFailure as error:
        report["failure_code"] = str(error)
    except urllib.error.HTTPError as error:
        report["failure_code"] = "http_request_failed"
        report["http_status"] = error.code
    except Exception as error:
        report["failure_code"] = "transport_or_report_input_failed"
        report["error_type"] = type(error).__name__
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(os.environ["GITHUB_STEP_SUMMARY"]).write_text(
            "## Anonymous public demo check\n\n```json\n"
            + json.dumps(report, ensure_ascii=False, indent=2) + "\n```\n", encoding="utf-8"
        )
    print(json.dumps({"status": report["status"], "passed_checks": len(report["checks"]), "failure_code": report.get("failure_code")}, ensure_ascii=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
