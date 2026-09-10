"""JSON presentation of a core answer, without changing routing or retrieval.

Values are plain strings, not HTML. Render them with textContent (or a properly
sanitized Markdown renderer for answer text), never by interpolating raw HTML.
The view model deliberately excludes document bodies and local storage paths.
"""
from __future__ import annotations

from collections.abc import Mapping
import ipaddress
import math
import re
import unicodedata
from urllib.parse import unquote, urlsplit


SOURCE_FIELDS = (
    "course_code", "class_no", "course_name", "professor", "completion_type",
    "credit", "class_hours", "schedule_summary", "snippet",
)
CARD_MODES = frozenset({"structured_course_metadata", "structured_schedule"})
TIMING_FIELDS = ("context_seconds", "search_seconds", "generation_seconds", "total_seconds")


def _text(value: object) -> str:
    """Do not stringify nested objects that could carry unapproved metadata."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value) if math.isfinite(value) else ""
    return ""


def safe_http_url(value: object) -> str:
    """Accept absolute HTTP(S) links without credentials or parser ambiguities."""
    if not isinstance(value, str) or not value:
        return ""
    # urlsplit silently strips several leading control characters. Check first.
    if any(unicodedata.category(char).startswith("C") for char in value):
        return ""
    candidate = value.strip()
    if not candidate or "\\" in candidate or any(char.isspace() for char in candidate):
        return ""
    decoded = unquote(candidate)
    if "\\" in decoded or any(unicodedata.category(char).startswith("C") for char in decoded):
        return ""
    try:
        parsed = urlsplit(candidate)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return ""
        if parsed.username is not None or parsed.password is not None:
            return ""
        hostname = parsed.hostname
        if not hostname or "%" in hostname:
            return ""
        _ = parsed.port  # Reject malformed and out-of-range ports.
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            ascii_host = hostname.encode("idna").decode("ascii").rstrip(".")
            if not ascii_host or any(
                not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                for label in ascii_host.split(".")
            ):
                return ""
    except (ValueError, UnicodeError):
        return ""
    return candidate


def present_sources(raw_sources: object) -> list[dict[str, str]]:
    """Keep one citation per course/section, preserving first-seen ordering."""
    if not isinstance(raw_sources, (list, tuple)):
        return []
    sources: list[dict[str, str]] = []
    by_identity: dict[tuple[str, str, str], dict[str, str]] = {}
    for raw in raw_sources:
        if not isinstance(raw, Mapping):
            continue
        source = {field: _text(raw.get(field)) for field in SOURCE_FIELDS}
        code, name = source["course_code"], source["course_name"]
        if not code and not name:
            continue
        source["syllabus_url"] = safe_http_url(raw.get("syllabus_url"))
        identity = ("code" if code else "name", (code or name).casefold(), source["class_no"].casefold())
        previous = by_identity.get(identity)
        if previous is not None:
            # A later chunk may provide the link or excerpt missing from the first.
            # Do not replace conflicting metadata already used in the citation.
            for field, value in source.items():
                if not previous[field] and value:
                    previous[field] = value
            continue
        source["id"] = f"source-{len(sources) + 1}"
        by_identity[identity] = source
        sources.append(source)
    return sources


def _seconds(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return seconds if math.isfinite(seconds) and seconds >= 0 else 0.0


def present_answer(result: Mapping[str, object]) -> dict[str, object]:
    """Build a per-answer view model from a single already-computed core result.

    A card is shown only for an explicit metadata/schedule route with exactly
    one deduplicated source. Multiple sections remain separate citations and
    do not get compressed into a potentially misleading single-section card.
    """
    sources = present_sources(result.get("sources"))
    mode = _text(result.get("mode")) or "unknown"
    raw_timings = result.get("timings")
    raw_timings = raw_timings if isinstance(raw_timings, Mapping) else {}
    timings = {field: _seconds(raw_timings.get(field)) for field in TIMING_FIELDS}
    timings["cache_hit"] = raw_timings.get("cache_hit") is True
    card = None
    if mode in CARD_MODES and len(sources) == 1 and sources[0]["course_code"]:
        source = sources[0]
        card = {"source_id": source["id"], **{
            field: value for field, value in source.items() if field not in {"id", "snippet"}
        }}
    return {
        "answer": _text(result.get("answer")), "sources": sources, "mode": mode,
        "timings": timings, "warning": _text(result.get("warning")), "course_card": card,
    }
