"""Prepare a deterministic server snapshot without publishing the dataset to Git.

Keep real course facts and schedules. Remove recognizable contact details and
local paths, restrict citation URL queries, and remove only known extraction
markers. This is a bounded redaction check, not an anonymization guarantee.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
EMAIL_RE = re.compile(r"(?:mailto:)?[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9.-]*[A-Z0-9])?\.[A-Z]{2,}", re.I)
PHONE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:tel:)?(?:"
    r"(?:\+82|0082)[ .\t-]*(?:0?2|0?1[016789]|0?[3-6][1-5]|0?70)"
    r"|0(?:2|1[016789]|[3-6][1-5]|70|50[2-8]?)"
    r")[ .\t-]*\d{3,4}[ .\t-]*\d{4}(?!\d)", re.I
)
RESIDENT_ID_RE = re.compile(r"(?<!\d)\d{6}[ \t]*-[ \t]*[1-8]\d{6}(?!\d)")
LABELED_ID_RE = re.compile(
    r"(?i)(?P<label>(?:student[ _-]*id|staff[ _-]*id|user[ _-]*id|std_id|학번|사번|주민등록번호)"
    r"[ \t]*[:=][ \t]*)(?P<value>[A-Za-z0-9_-]{4,})"
)
LOCAL_PATH_RE = re.compile(
    r"(?i)(?:file://|(?<![A-Za-z0-9])[A-Z]:[\\/]|\\\\[A-Za-z0-9_.-]+\\|/(?:Users|home|mnt|tmp)/)"
    r"[^\r\n<>\[\]\"|]+"
)
URL_RE = re.compile(r"https?://[^\s<>\[\]\"'‘’“”()]+", re.I)
KNOWN_MOJIBAKE = ("氠瑢", "漠杳", "捤獥", "汤捯", "湰灧", "慤桥", "潴景")
COURSE_URL_QUERY = {"year", "term", "grad_cd", "col_cd", "dept_cd", "cour_cd", "cour_cls"}
ACADEMIC_FIELDS = (
    "course_code", "class_no", "course_name", "professor", "completion_type",
    "completion_type_source", "credit", "class_hours", "schedule_raw",
    "schedule_summary", "schedule_entries",
)
DATA_FIELDS = (*ACADEMIC_FIELDS, "syllabus_url", "text_path", "sources", "text", "char_count", "duplicate_sections")
SCHEDULE_FIELDS = ("class_no", "day", "day_name", "start_period", "end_period", "periods",
                   "start_time", "end_time", "room", "display")
DUPLICATE_FIELDS = ("course_code", "class_no", "professor", "schedule_summary",
                    "schedule_entries", "syllabus_url", "similarity")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def safe_url(value: str, counts: Counter) -> str:
    """Keep citation locations; discard user/session data and fragments."""
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        if parsed.scheme.lower() not in {"https", "http"} or not hostname:
            raise ValueError("Unsupported citation URL")
        if parsed.username or parsed.password or hostname == "localhost" or "." not in hostname:
            raise ValueError("Private citation URL")
        try:
            if not ipaddress.ip_address(hostname).is_global:
                raise ValueError("Private address")
        except ValueError as error:
            # Non-IP host names are accepted; private IP addresses are not.
            if str(error) == "Private address":
                raise
        allowed = []
        params = parse_qsl(parsed.query, keep_blank_values=True)
        academic_lookup = (hostname.casefold() == "infodepot.korea.ac.kr"
                           and parsed.path == "/lecture1/lecsubjectPlanView.jsp")
        if params and not academic_lookup:
            counts["url_query_parameters_removed"] += len(params)
            counts["unsafe_urls_removed"] += 1
            return ""
        for key, content in params:
            if (academic_lookup and key in COURSE_URL_QUERY
                    and re.fullmatch(r"[A-Za-z0-9_-]{1,20}", content)):
                allowed.append((key, content))
            else:
                counts["url_query_parameters_removed"] += 1
        if academic_lookup and not {"year", "term", "cour_cd", "cour_cls"} <= {key for key, _ in allowed}:
            counts["unsafe_urls_removed"] += 1
            return ""
        if parsed.fragment:
            counts["url_fragments_removed"] += 1
        result = urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, urlencode(allowed), ""))
        if result != value:
            counts["urls_sanitized"] += 1
        return result
    except (ValueError, UnicodeError):
        counts["unsafe_urls_removed"] += 1
        return ""


def sanitize_text(value: str, counts: Counter) -> str:
    def change(pattern: re.Pattern, replacement: str, counter: str, text: str) -> str:
        result, number = pattern.subn(replacement, text)
        counts[counter] += number
        return result

    value = change(LOCAL_PATH_RE, "[로컬 경로 제거]", "local_paths_removed", value)

    def replace_url(match: re.Match) -> str:
        original = match.group().rstrip(".,;:")
        suffix = match.group()[len(original):]
        return (safe_url(original, counts) or "[비공개 주소]") + suffix

    value = URL_RE.sub(replace_url, value)
    value = change(EMAIL_RE, "[이메일 비공개]", "emails_removed", value)
    value = change(PHONE_RE, "[연락처 비공개]", "phone_numbers_removed", value)
    value = change(RESIDENT_ID_RE, "[개인 식별번호 비공개]", "resident_ids_removed", value)
    value = change(LABELED_ID_RE, r"\g<label>[개인 식별번호 비공개]", "labeled_ids_removed", value)
    for marker in KNOWN_MOJIBAKE:
        count = value.count(marker)
        if count:
            counts["known_extraction_markers_removed"] += count
            value = value.replace(marker, "")
    value = re.sub(r"[ \t]+\n", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def sanitize_value(value, counts: Counter):
    if isinstance(value, str):
        return sanitize_text(value, counts)
    if isinstance(value, list):
        return [sanitize_value(item, counts) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_value(item, counts) for key, item in value.items()}
    return value


def schedule_projection(entries: list) -> list:
    return [{key: entry[key] for key in SCHEDULE_FIELDS if key in entry} for entry in entries]


def sanitize_records(records: list[dict]) -> tuple[list[dict], dict]:
    if not records:
        raise ValueError("The source snapshot is empty")
    counts: Counter = Counter()
    cleaned = []
    for source in records:
        if not source.get("course_code") or not source.get("text"):
            raise ValueError("Every record needs a course code and nonempty text")
        record = {key: copy.deepcopy(source[key]) for key in DATA_FIELDS if key in source}
        counts["unrecognized_metadata_fields_removed"] += len(set(source) - set(DATA_FIELDS))
        record["schedule_entries"] = schedule_projection(record.get("schedule_entries", []))
        if "duplicate_sections" in record:
            record["duplicate_sections"] = [
                {key: entry[key] for key in DUPLICATE_FIELDS if key in entry}
                for entry in record["duplicate_sections"]
            ]
            for entry in record["duplicate_sections"]:
                entry["schedule_entries"] = schedule_projection(entry.get("schedule_entries", []))
        if record.get("text_path"):
            counts["text_path_fields_cleared"] += 1
        record["text_path"] = ""
        record = sanitize_value(record, counts)
        record["char_count"] = len(record["text"])
        cleaned.append(record)

    preserved = all(all(before.get(key) == after.get(key) for key in ACADEMIC_FIELDS)
                    for before, after in zip(records, cleaned))
    duplicates_preserved = all(
        [{key: item.get(key) for key in DUPLICATE_FIELDS if key != "syllabus_url"}
         for item in before.get("duplicate_sections", [])]
        == [{key: item.get(key) for key in DUPLICATE_FIELDS if key != "syllabus_url"}
            for item in after.get("duplicate_sections", [])]
        for before, after in zip(records, cleaned)
    )
    # The output must retain academic metadata exactly; do not silently damage it.
    if not preserved or not duplicates_preserved:
        raise ValueError("Redaction changed academic metadata; review the snapshot before hosting")
    serialized = json.dumps(cleaned, ensure_ascii=False)
    residual = {
        "emails": len(EMAIL_RE.findall(serialized)), "phone_numbers": len(PHONE_RE.findall(serialized)),
        "resident_ids": len(RESIDENT_ID_RE.findall(serialized)),
        "local_paths": len(LOCAL_PATH_RE.findall(serialized)),
        "known_extraction_markers": sum(serialized.count(marker) for marker in KNOWN_MOJIBAKE),
    }
    if any(residual.values()):
        raise ValueError("Recognizable contact data or local paths remain after redaction")
    report = {
        "redaction_counts": dict(sorted(counts.items())), "residual_pattern_counts": residual,
        "preservation_checks": {"academic_metadata_unchanged": preserved,
                                "duplicate_sections_unchanged_except_citation_url": duplicates_preserved,
                                "course_order_preserved": [r["course_code"] for r in records]
                                    == [r["course_code"] for r in cleaned]},
        "extraction_quality": {
            "documents_with_known_markers_before": sum(any(m in r["text"] for m in KNOWN_MOJIBAKE) for r in records),
            "known_markers_removed_only": list(KNOWN_MOJIBAKE),
            "remedy": "Remove exact recurring extraction markers only; do not reconstruct missing words, grades or table alignment.",
            "remaining_limitations": "Other OCR/HWP corruption, table interpretation and conflicting source metadata still require original-file review.",
        },
    }
    return cleaned, report


def prepare(source: Path, output: Path, report_path: Path) -> dict:
    source, output, report_path = source.resolve(), output.resolve(), report_path.resolve()
    paths = (source, output, report_path)
    if any(left == right or (left.exists() and right.exists() and left.samefile(right))
           for index, left in enumerate(paths) for right in paths[index + 1:]):
        raise ValueError("Input, dataset output and report must be distinct files")
    source_bytes = source.read_bytes()
    records = [json.loads(line) for line in source_bytes.decode("utf-8-sig").splitlines() if line.strip()]
    cleaned, audit = sanitize_records(records)
    payload = ("\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in cleaned) + "\n").encode("utf-8")
    if len(payload) >= 1_000_000:
        raise ValueError("Hosted snapshot exceeds the configured 1 MB secret-file budget")
    report = {
        "schema_version": 1, "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
        "hosted_dataset_sha256": sha_bytes(payload), "document_count": len(cleaned),
        "source_dataset_sha256": sha_bytes(source_bytes),
        "preparer_sha256": sha_bytes(Path(__file__).read_bytes()),
        "course_count": len({row["course_code"] for row in cleaned}), "byte_count": len(payload),
        "schedule_entry_count": sum(len(row.get("schedule_entries", [])) for row in cleaned),
        "represented_section_count": len({(row["course_code"], row["class_no"]) for row in cleaned}
            | {(row["course_code"], item["class_no"]) for row in cleaned for item in row.get("duplicate_sections", [])}),
        "purpose": "Private server secret-file snapshot for the user-authorized public course demo; dataset excluded from Git.",
        **audit,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data/processed/syllabus_texts.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/hosted/syllabus_texts.jsonl")
    parser.add_argument("--report", type=Path, default=ROOT / "evaluation/hosted-data-checks.json")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / "artifacts/hosted").resolve()):
        parser.error("Dataset output must remain inside ignored artifacts/hosted")
    report = prepare(args.input.resolve(), output, args.report.resolve())
    print(json.dumps({key: report[key] for key in (
        "hosted_dataset_sha256", "document_count", "source_dataset_sha256", "byte_count", "preservation_checks"
    )}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
