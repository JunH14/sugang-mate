"""Independently verify the hosted chat-screen assets and stateless JSON API.

Standard library only. Makes one anonymous timetable request after verifying
the reviewed 25-course dataset and the new screen/API contracts. The report
stores aggregates only, without answers, source excerpts or conversation data.
This checks delivery and backend behavior; visual browser verification remains
a separate check.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import sys
import time
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_hosted_demo import (
    AnonymousClient, DEFAULT_URL, SmokeFailure, read_complete_event,
    validate_origin, verify_hosted_status,
)

QUESTION = "수리통계학 수업시간을 알려줘"
SOURCE_FIELDS = {
    "id", "course_code", "class_no", "course_name", "professor", "completion_type",
    "credit", "class_hours", "schedule_summary", "snippet", "syllabus_url",
}
VIEW_FIELDS = {"answer", "sources", "mode", "timings", "warning", "course_card", "mode_label", "timing_label", "error"}
TIMING_FIELDS = {"context_seconds", "search_seconds", "generation_seconds", "total_seconds", "cache_hit"}


def verify_screen_contract(config: dict, info: dict) -> str:
    shells = [component for component in config.get("components", [])
              if component.get("type") == "html" and component.get("props", {}).get("elem_id") == "sugang-shell"]
    if len(shells) != 1:
        raise SmokeFailure("chat_screen_component_missing")
    props = shells[0].get("props", {})
    markup = props.get("value", "")
    if not isinstance(markup, str) or not re.search(r'class=["\'][^"\']*\bsm-app\b', markup) or not props.get("js_on_load"):
        raise SmokeFailure("chat_screen_assets_missing")
    prefix = config.get("api_prefix")
    if not isinstance(prefix, str) or not re.fullmatch(r"/[A-Za-z0-9_-]+", prefix):
        raise SmokeFailure("unsupported_api_prefix")
    endpoint = info.get("named_endpoints", {}).get("/respond", {})
    parameters = endpoint.get("parameters", [])
    if endpoint.get("api_visibility") != "public" or [item.get("parameter_name") for item in parameters] != ["message", "history"] or len(endpoint.get("returns", [])) != 1:
        raise SmokeFailure("respond_api_contract_changed")
    components = {item.get("id"): item.get("type") for item in config.get("components", [])}
    dependencies = [item for item in config.get("dependencies", []) if item.get("api_name") == "respond"]
    if len(dependencies) != 1:
        raise SmokeFailure("respond_raw_contract_changed")
    dependency = dependencies[0]
    if ([components.get(item) for item in dependency.get("inputs", [])] != ["api", "api"]
            or [components.get(item) for item in dependency.get("outputs", [])] != ["api"]):
        raise SmokeFailure("respond_raw_contract_changed")
    return prefix


class ScreenClient(AnonymousClient):
    def respond(self, prefix: str, message: str, history: list):
        event = self.get_json(prefix + "/call/respond", {"data": [message, history]})
        event_id = event.get("event_id", "")
        if not isinstance(event_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event_id):
            raise SmokeFailure("invalid_respond_event_id")
        with self._request(prefix + "/call/respond/" + event_id, stream=True) as response:
            payload = read_complete_event(response, deadline=time.monotonic() + 120)
        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
            raise SmokeFailure("invalid_respond_wire_shape")
        return payload[0]


def verify_screen_result(view: dict) -> dict:
    if not isinstance(view, dict) or set(view) - VIEW_FIELDS:
        raise SmokeFailure("unapproved_response_fields")
    if not isinstance(view.get("answer"), str) or "BDSC201" not in view["answer"]:
        raise SmokeFailure("expected_course_answer_missing")
    if view.get("error") is not False or view.get("warning") or view.get("mode") != "structured_schedule":
        raise SmokeFailure("unexpected_schedule_result")
    sources = view.get("sources")
    if not isinstance(sources, list) or len(sources) != 1:
        raise SmokeFailure("expected_single_course_source_missing")
    source = sources[0]
    if not isinstance(source, dict) or set(source) - SOURCE_FIELDS or any(not isinstance(value, str) for value in source.values()):
        raise SmokeFailure("unapproved_source_fields")
    if source.get("course_code") != "BDSC201" or not source.get("schedule_summary") or not source.get("id"):
        raise SmokeFailure("expected_timetable_evidence_missing")
    card = view.get("course_card")
    allowed_card = (SOURCE_FIELDS - {"id", "snippet"}) | {"source_id"}
    if not isinstance(card, dict) or set(card) - allowed_card or any(not isinstance(value, str) for value in card.values()):
        raise SmokeFailure("invalid_course_card")
    if card.get("source_id") != source["id"] or any(card.get(field) != source.get(field) for field in SOURCE_FIELDS - {"id", "snippet"}):
        raise SmokeFailure("course_card_does_not_match_evidence")
    timings = view.get("timings")
    if not isinstance(timings, dict) or set(timings) - TIMING_FIELDS or type(timings.get("cache_hit")) is not bool:
        raise SmokeFailure("invalid_timing_provenance")
    return {
        "id": "anonymous_json_schedule", "passed": True,
        "expected_course": "BDSC201", "mode": "structured_schedule",
        "source_count": 1, "course_card_matches_source": True,
        "private_fields_excluded": True, "cache_hit": timings["cache_hit"],
    }


def run_checks(client, expected: dict, report: dict) -> None:
    config = client.get_json("/config")
    report["dataset"] = verify_hosted_status(config, expected)
    prefix = config.get("api_prefix")
    if not isinstance(prefix, str) or not re.fullmatch(r"/[A-Za-z0-9_-]+", prefix):
        raise SmokeFailure("unsupported_api_prefix")
    info = client.get_json(prefix + "/info")
    prefix = verify_screen_contract(config, info)
    report["chat_screen"] = {"assets_present": True, "stateless_json_api": True, "visual_rendering_tested": False}
    started = time.monotonic()
    check = verify_screen_result(client.respond(prefix, QUESTION, []))
    report["checks"].append({**check, "seconds": round(time.monotonic() - started, 3)})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_URL)
    parser.add_argument("--expected-data", type=Path, default=ROOT / "evaluation/hosted-data-checks.json")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/chat-screen-smoke.json")
    args = parser.parse_args(argv)
    report = {
        "schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed", "checks": [],
        "runtime": {"os": platform.system(), "python": platform.python_version(), "github_actions": os.environ.get("GITHUB_ACTIONS") == "true"},
        "authentication": {"cookies_used": False, "api_keys_used": False, "login_used": False},
        "evidence_scope": "Screen asset delivery and one stateless JSON timetable request. No answers or source excerpts are retained. Browser rendering is verified separately.",
    }
    try:
        report["base_url"] = validate_origin(args.base_url)
        expected = json.loads(args.expected_data.read_text(encoding="utf-8"))
        run_checks(ScreenClient(report["base_url"]), expected, report)
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
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
            summary.write("\n## Anonymous chat-screen check\n\n```json\n" + json.dumps(report, ensure_ascii=False, indent=2) + "\n```\n")
    print(json.dumps({"status": report["status"], "passed_checks": len(report["checks"]), "failure_code": report.get("failure_code")}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
