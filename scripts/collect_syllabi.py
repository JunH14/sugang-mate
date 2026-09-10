from __future__ import annotations

import argparse
import csv
import html
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


LECTURE_LIST_URL = "https://sugang.korea.ac.kr/view?attribute=lectHakbuData"
SYLLABUS_BASE_URL = "https://infodepot.korea.ac.kr/lecture1/lecsubjectPlanView.jsp"
INFODEPOT_ORIGIN = "https://infodepot.korea.ac.kr"

TARGET = {
    "pYear": "2026",
    "pTerm": "1R",
    "pCampus": "2",
    "pGradCd": "0136",
    "pCourDiv": "00",
    "pCol": "6112",
    "pDept": "6732",
}


@dataclass
class Course:
    year: str
    term: str
    campus_code: str
    grad_cd: str
    cour_div: str
    college_code: str
    dept_code: str
    course_code: str
    class_no: str
    course_name: str = ""
    professor: str = ""
    completion_type: str = ""
    credit: str = ""
    class_hours: str = ""
    schedule_raw: str = ""
    schedule_summary: str = ""
    schedule_entries: list[dict[str, Any]] | None = None
    raw_cells: list[str] | None = None
    syllabus_url: str = ""
    html_path: str = ""
    attachment_url: str = ""
    attachment_path: str = ""
    attachment_ext: str = ""
    pdf_url: str = ""
    pdf_path: str = ""
    collection_status: str = "pending"


def decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def request_text(url: str, data: dict[str, str] | None = None, referer: str | None = None) -> tuple[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
    }
    if referer:
        headers["Referer"] = referer

    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"

    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
    with urllib.request.urlopen(req, timeout=30) as response:
        final_url = response.geturl()
        return decode_bytes(response.read()), final_url


def request_bytes(url: str, referer: str | None = None) -> tuple[bytes, str, dict[str, str]]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
        ),
        "Accept": "application/pdf,text/html,*/*",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
    }
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read(), response.geturl(), dict(response.headers.items())


def strip_tags(value: str) -> str:
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def period_clock_range(start_period: int, end_period: int) -> tuple[str, str]:
    """Convert Korea University numbered 50-minute periods to clock times."""
    start_minutes = 9 * 60 + (start_period - 1) * 60
    end_minutes = 9 * 60 + (end_period - 1) * 60 + 50
    return (
        f"{start_minutes // 60:02d}:{start_minutes % 60:02d}",
        f"{end_minutes // 60:02d}:{end_minutes % 60:02d}",
    )


def parse_schedule_entries(value: str, class_no: str) -> list[dict[str, Any]]:
    normalized = html.unescape(value or "")
    normalized = re.sub(r"(?i)<br\s*/?>", "\n", normalized)
    normalized = re.sub(r"<[^>]+>", " ", normalized)
    entries: list[dict[str, Any]] = []
    day_names = {
        "월": "월요일",
        "화": "화요일",
        "수": "수요일",
        "목": "목요일",
        "금": "금요일",
        "토": "토요일",
        "일": "일요일",
    }
    for raw_line in normalized.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        match = re.match(r"([월화수목금토일])\s*\((\d+)(?:\s*-\s*(\d+))?\)\s*(.*)", line)
        if not match:
            continue
        day, start_text, end_text, room = match.groups()
        start_period = int(start_text)
        end_period = int(end_text or start_text)
        start_time, end_time = period_clock_range(start_period, end_period)
        period_text = (
            str(start_period)
            if start_period == end_period
            else f"{start_period}-{end_period}"
        )
        entries.append(
            {
                "class_no": class_no.zfill(2),
                "day": day,
                "day_name": day_names[day],
                "start_period": start_period,
                "end_period": end_period,
                "periods": period_text,
                "start_time": start_time,
                "end_time": end_time,
                "room": room.strip(),
                "display": (
                    f"{day_names[day]} {period_text}교시 "
                    f"({start_time}-{end_time})"
                    + (f", {room.strip()}" if room.strip() else "")
                ),
            }
        )
    return entries


def schedule_summary(entries: list[dict[str, Any]]) -> str:
    return " / ".join(
        f"{entry.get('class_no', '')}분반 {entry.get('display', '')}".strip()
        for entry in entries
        if entry.get("display")
    )


def extract_completion_type(page_html: str) -> str:
    page_text = strip_tags(page_html)
    match = re.search(
        r"이수구분\s*(전공필수|전공선택|교양필수|교양선택|일반선택)",
        page_text,
        flags=re.I,
    )
    return match.group(1) if match else ""


def split_js_args(arg_string: str) -> list[str]:
    args: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escape = False
    for char in arg_string:
        if escape:
            current.append(char)
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if quote:
            if char == quote:
                quote = None
            else:
                current.append(char)
            continue
        if char in ("'", '"'):
            quote = char
            continue
        if char == ",":
            args.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    args.append("".join(current).strip())
    return [html.unescape(item) for item in args]


def make_syllabus_url(course: Course) -> str:
    query = {
        "year": course.year,
        "term": course.term,
        "grad_cd": course.grad_cd,
        "col_cd": "9999",
        "dept_cd": course.dept_code,
        "cour_cd": course.course_code,
        "cour_cls": course.class_no,
        "cour_nm": "",
        "std_id": "",
        "device": "WW",
    }
    return f"{SYLLABUS_BASE_URL}?{urllib.parse.urlencode(query)}"


def normalize_course(
    course_code: str,
    class_no: str,
    course_name: str = "",
    professor: str = "",
    completion_type: str = "",
    credit: str = "",
    class_hours: str = "",
    schedule_raw: str = "",
    raw_cells: list[str] | None = None,
) -> Course:
    class_no = class_no.zfill(2)
    entries = parse_schedule_entries(schedule_raw, class_no)
    course = Course(
        year=TARGET["pYear"],
        term=TARGET["pTerm"],
        campus_code=TARGET["pCampus"],
        grad_cd=TARGET["pGradCd"],
        cour_div=TARGET["pCourDiv"],
        college_code=TARGET["pCol"],
        dept_code=TARGET["pDept"],
        course_code=course_code.strip(),
        class_no=class_no.strip(),
        course_name=course_name.strip(),
        professor=professor.strip(),
        completion_type=completion_type.strip(),
        credit=credit.strip(),
        class_hours=class_hours.strip(),
        schedule_raw=strip_tags(re.sub(r"(?i)<br\s*/?>", " / ", schedule_raw)),
        schedule_summary=schedule_summary(entries),
        schedule_entries=entries,
        raw_cells=raw_cells,
    )
    course.syllabus_url = make_syllabus_url(course)
    return course


def parse_courses_from_plan_calls(text: str) -> list[Course]:
    courses: list[Course] = []
    seen: set[tuple[str, str]] = set()
    for match in re.finditer(r"fnPlanView\s*\((.*?)\)", text, flags=re.S):
        args = split_js_args(match.group(1))
        course_code = next((item for item in args if re.fullmatch(r"[A-Z]{2,}[A-Z0-9]*\d{3,}", item)), "")
        class_no = next((item for item in reversed(args) if re.fullmatch(r"\d{2}", item)), "")
        if not course_code or not class_no:
            continue
        key = (course_code, class_no)
        if key in seen:
            continue
        seen.add(key)
        courses.append(normalize_course(course_code, class_no))
    return courses


def parse_courses_from_table_rows(text: str) -> list[Course]:
    courses: list[Course] = []
    seen: set[tuple[str, str]] = set()
    for row_html in re.findall(r"<tr\b[^>]*>(.*?)</tr>", text, flags=re.I | re.S):
        cells = [strip_tags(cell) for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row_html, flags=re.I | re.S)]
        cells = [cell for cell in cells if cell]
        joined = " ".join(cells)
        code_match = re.search(r"\b([A-Z]{2,}[A-Z0-9]*\d{3,})\b", joined)
        if not code_match:
            continue
        class_match = re.search(r"(?:분반|class|cls)?\s*\b(\d{2})\b", joined, flags=re.I)
        class_no = class_match.group(1) if class_match else "00"
        course_code = code_match.group(1)
        key = (course_code, class_no)
        if key in seen:
            continue
        seen.add(key)

        code_index = next((index for index, cell in enumerate(cells) if course_code in cell), -1)
        course_name = ""
        professor = ""
        if code_index >= 0:
            for cell in cells[code_index + 1 :]:
                if cell and not re.fullmatch(r"[\d\s:~(),.-]+", cell) and course_code not in cell:
                    course_name = cell
                    break
        for cell in cells:
            if re.search(r"(교수|강사|담당)", cell):
                professor = cell
                break
        courses.append(
            normalize_course(
                course_code,
                class_no,
                course_name,
                professor,
                raw_cells=cells,
            )
        )
    return courses


def parse_courses_from_json(value: Any) -> list[Course]:
    rows: list[Any] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            if any(re.search(r"cour|course|haksu|subj|cls|bunban", str(key), re.I) for key in item):
                rows.append(item)
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    courses: list[Course] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        flat = {str(key).lower(): "" if val is None else str(val) for key, val in row.items()}

        if flat.get("cour_cd"):
            course_code = flat["cour_cd"].strip()
            class_no = (flat.get("cour_cls") or "").strip()
            if not class_no and flat.get("params"):
                parts = flat["params"].split("@", 1)
                if len(parts) == 2:
                    class_no = parts[1]
            class_no = class_no or "00"
            key = (course_code, class_no.zfill(2))
            if key in seen:
                continue
            seen.add(key)
            courses.append(
                normalize_course(
                    course_code,
                    class_no,
                    flat.get("cour_nm", ""),
                    flat.get("prof_nm", ""),
                    flat.get("isu_nm", ""),
                    flat.get("credit", ""),
                    flat.get("time", ""),
                    flat.get("time_room", ""),
                )
            )
            continue

        joined = " ".join(flat.values())
        code_match = re.search(r"\b([A-Z]{2,}[A-Z0-9]*\d{3,})\b", joined)
        if not code_match:
            continue
        course_code = code_match.group(1)
        class_no = ""
        for key, val in flat.items():
            if re.search(r"cls|class|bunban|ban|cour_cls", key, re.I) and re.fullmatch(r"\d{1,2}", val.strip()):
                class_no = val.strip()
                break
        class_no = class_no or "00"
        key = (course_code, class_no.zfill(2))
        if key in seen:
            continue
        seen.add(key)
        name = next((val for key, val in flat.items() if re.search(r"name|nm|kor|subject", key, re.I) and val), "")
        professor = next((val for key, val in flat.items() if re.search(r"prof|교수|담당", key, re.I) and val), "")
        completion_type = next((val for key, val in flat.items() if re.search(r"isu|이수", key, re.I) and val), "")
        schedule_raw = next((val for key, val in flat.items() if re.search(r"time_room|시간.*강의실", key, re.I) and val), "")
        courses.append(
            normalize_course(
                course_code,
                class_no,
                name,
                professor,
                completion_type,
                flat.get("credit", ""),
                flat.get("time", ""),
                schedule_raw,
            )
        )
    return courses


def parse_courses(text: str) -> list[Course]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None:
        courses = parse_courses_from_json(parsed)
        if courses:
            return courses

    courses = parse_courses_from_plan_calls(text)
    if courses:
        return courses

    return parse_courses_from_table_rows(text)


def extract_attachment_url(page_html: str, page_url: str) -> str:
    candidates: list[str] = []
    for match in re.finditer(r"""(?:href|src)\s*=\s*["']([^"']+)["']""", page_html, flags=re.I):
        link = html.unescape(match.group(1))
        if "filedownloader" in link.lower() or ".pdf" in link.lower():
            candidates.append(link)
    for match in re.finditer(r"(/weblogic/filedownloader\?[^\"'\s<>]+)", page_html, flags=re.I):
        candidates.append(html.unescape(match.group(1)))
    for link in candidates:
        absolute = urllib.parse.urljoin(INFODEPOT_ORIGIN if link.startswith("/") else page_url, link)
        if "pdf" in absolute.lower() or "filedownloader" in absolute.lower():
            return absolute
    return ""


def extension_from_attachment(url: str, content: bytes, headers: dict[str, str]) -> str:
    disposition = headers.get("Content-Disposition", "") or headers.get("content-disposition", "")
    filename_match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition, flags=re.I)
    if filename_match:
        filename = urllib.parse.unquote(filename_match.group(1))
        ext = Path(filename).suffix.lower()
        if ext:
            return ext

    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("filename"):
        ext = Path(urllib.parse.unquote(query["filename"][0])).suffix.lower()
        if ext:
            return ext
    ext = Path(urllib.parse.unquote(parsed.path)).suffix.lower()
    if ext:
        return ext

    if content.startswith(b"%PDF"):
        return ".pdf"
    if content.startswith(b"PK\x03\x04"):
        return ".docx"
    if content.startswith(b"\xd0\xcf\x11\xe0"):
        return ".hwp"
    return ".bin"


def safe_stem(course: Course) -> str:
    name = re.sub(r"[^A-Za-z0-9가-힣_.-]+", "_", course.course_name).strip("_")
    stem = f"{course.course_code}_{course.class_no}"
    return f"{stem}_{name}" if name else stem


def save_courses(courses: list[Course], output_dir: Path) -> None:
    processed = output_dir / "data" / "processed"
    processed.mkdir(parents=True, exist_ok=True)

    json_path = processed / "courses.json"
    json_path.write_text(
        json.dumps([asdict(course) for course in courses], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    csv_path = processed / "courses.csv"
    fieldnames = [
        "year",
        "term",
        "campus_code",
        "grad_cd",
        "cour_div",
        "college_code",
        "dept_code",
        "course_code",
        "class_no",
        "course_name",
        "professor",
        "completion_type",
        "credit",
        "class_hours",
        "schedule_raw",
        "schedule_summary",
        "schedule_entries_json",
        "syllabus_url",
        "html_path",
        "attachment_url",
        "attachment_path",
        "attachment_ext",
        "pdf_url",
        "pdf_path",
        "collection_status",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for course in courses:
            row = asdict(course)
            row.pop("raw_cells", None)
            row["schedule_entries_json"] = json.dumps(
                row.pop("schedule_entries", []) or [], ensure_ascii=False
            )
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def collect(limit: int | None, delay: float, output_dir: Path) -> list[Course]:
    debug_dir = output_dir / "data" / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    print("Fetching course list...")
    list_text, final_url = request_text(LECTURE_LIST_URL, TARGET, referer="https://sugang.korea.ac.kr/")
    (debug_dir / "lectHakbuData_response.txt").write_text(list_text, encoding="utf-8")
    print(f"Course list response saved from {final_url}")

    courses = parse_courses(list_text)
    if not courses:
        raise RuntimeError("No courses parsed. See data/debug/lectHakbuData_response.txt")

    total_courses = len(courses)
    if limit and limit > 0:
        courses = courses[:limit]
        print(f"Parsed {len(courses)} of {total_courses} courses. Downloading syllabus HTML/attachments...")
    else:
        print(f"Parsed all {len(courses)} courses. Downloading syllabus HTML/attachments...")

    html_dir = output_dir / "data" / "raw" / "html"
    attachment_dir = output_dir / "data" / "raw" / "attachments"
    html_dir.mkdir(parents=True, exist_ok=True)
    attachment_dir.mkdir(parents=True, exist_ok=True)

    for index, course in enumerate(courses, 1):
        stem = safe_stem(course)
        html_path = html_dir / f"{stem}.html"
        try:
            page_html, page_url = request_text(course.syllabus_url, referer="https://sugang.korea.ac.kr/")
            html_path.write_text(page_html, encoding="utf-8")
            course.html_path = str(html_path)
            course.completion_type = extract_completion_type(page_html) or course.completion_type
            course.collection_status = "html_saved"

            attachment_url = extract_attachment_url(page_html, page_url)
            if attachment_url:
                course.attachment_url = attachment_url
                attachment_bytes, _, headers = request_bytes(attachment_url, referer=page_url)
                ext = extension_from_attachment(attachment_url, attachment_bytes, headers)
                attachment_path = attachment_dir / f"{stem}{ext}"
                attachment_path.write_bytes(attachment_bytes)
                course.attachment_ext = ext
                course.attachment_path = str(attachment_path)
                if ext == ".pdf":
                    course.pdf_url = attachment_url
                    course.pdf_path = str(attachment_path)
                course.collection_status = "html_attachment_saved"
            print(f"[{index}/{len(courses)}] {course.course_code}-{course.class_no} {course.collection_status}")
        except Exception as error:
            course.collection_status = f"failed: {error}"
            print(f"[{index}/{len(courses)}] {course.course_code}-{course.class_no} failed: {error}", file=sys.stderr)
        time.sleep(delay)

    save_courses(courses, output_dir)
    return courses


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect KU Sejong Big Data Science syllabi.")
    parser.add_argument("--limit", type=int, default=0, help="Number of courses to collect. Use 0 for all courses.")
    parser.add_argument("--delay", type=float, default=0.7, help="Delay between syllabus requests.")
    parser.add_argument("--output-dir", type=Path, default=Path("."), help="Workspace output directory.")
    parser.add_argument(
        "--duplicate-threshold",
        type=float,
        default=0.90,
        help="Same-name syllabus similarity threshold used by the extraction step.",
    )
    parser.add_argument(
        "--skip-extraction",
        action="store_true",
        help="Collect raw files only without text extraction and duplicate removal.",
    )
    args = parser.parse_args()

    try:
        courses = collect(args.limit, args.delay, args.output_dir)
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)

    saved_html = sum(1 for course in courses if course.html_path)
    saved_attachment = sum(1 for course in courses if course.attachment_path)
    saved_pdf = sum(1 for course in courses if course.attachment_ext == ".pdf")
    print(f"Done. courses={len(courses)} html={saved_html} attachments={saved_attachment} pdf={saved_pdf}")

    if not args.skip_extraction:
        project_dir = args.output_dir.resolve()
        extract_script = Path(__file__).resolve().with_name("extract_texts.py")
        print(
            "Running text extraction and same-name duplicate removal "
            f"(threshold={args.duplicate_threshold:.2f})..."
        )
        subprocess.run(
            [
                sys.executable,
                str(extract_script),
                "--project-dir",
                str(project_dir),
                "--duplicate-threshold",
                str(args.duplicate_threshold),
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
