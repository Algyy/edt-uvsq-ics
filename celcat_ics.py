#!/usr/bin/env python3
"""Generate an iCalendar feed from a CELCAT Calendar instance.

CELCAT exposes an unauthenticated JSON endpoint (``/Home/GetCalendarData``)
that its own web UI calls. This script queries it and writes a ``.ics`` file
that Google Calendar, Proton Calendar, Thunderbird or Apple Calendar can
subscribe to.

Example:
    python3 celcat_ics.py --fid HISL2TD1 --out public/hisl2td1.ics
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_BASE_URL = "https://edt.uvsq.fr"
# CELCAT resource types. 103 is "group", 100 is "staff", 104 is "student".
RESOURCE_TYPE_GROUP = "103"
LOCAL_TZ = ZoneInfo("Europe/Paris")
PRODID = "-//socal//CELCAT to iCalendar//FR"
# CELCAT joins the six description sections with this exact separator.
SECTION_SEP = "\r\n\r\n<br />\r\n\r\n"
HASH_PROPERTY = "X-CELCAT-CONTENT-HASH"
USER_AGENT = "socal-celcat-ics/1.0"


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def post_form(url: str, fields: list[tuple[str, str]], *, retries: int = 3) -> bytes:
    """POST a urlencoded form and return the raw response body."""
    body = urllib.parse.urlencode(fields).encode("utf-8")
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "User-Agent": USER_AGENT,
    }
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt < retries - 1:
                time.sleep(2**attempt)
    raise RuntimeError(f"POST {url} failed after {retries} attempts: {last_error}")


def fetch_events(
    base_url: str,
    federation_ids: list[str],
    resource_type: str,
    start: date,
    end: date,
) -> list[dict]:
    fields = [
        ("start", start.isoformat()),
        ("end", end.isoformat()),
        ("resType", resource_type),
        ("calView", "month"),
        ("colourScheme", "3"),
    ]
    fields += [("federationIds[]", fid) for fid in federation_ids]
    raw = post_form(f"{base_url}/Home/GetCalendarData", fields)
    events = json.loads(raw)
    if not isinstance(events, list):
        raise RuntimeError(f"unexpected payload from GetCalendarData: {type(events)}")
    return events


def fetch_display_names(
    base_url: str, federation_ids: list[str], resource_type: str
) -> dict[str, str]:
    """Return {federationId: human readable name}. Best effort, never raises."""
    fields = [("resType", resource_type)]
    fields += [("federationIds[]", fid) for fid in federation_ids]
    try:
        raw = post_form(f"{base_url}/Home/LoadDisplayNames", fields, retries=2)
        entries = json.loads(raw)
    except Exception:
        return {}
    return {
        entry["federationId"]: entry["displayName"]
        for entry in entries
        if isinstance(entry, dict) and entry.get("federationId")
    }


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


@dataclass
class Course:
    celcat_id: str
    start: datetime
    end: datetime
    category: str
    rooms: list[str]
    modules: list[str]
    groups: list[str]
    note: str
    sites: list[str]
    cancelled: bool

    @property
    def title(self) -> str:
        # The category already reads "TD annule" when a class is dropped,
        # so there is nothing to prefix.
        if self.modules:
            return f"{self.category} - {' / '.join(self.modules)}"
        return self.category

    @property
    def location(self) -> str:
        return ", ".join(self.rooms + self.sites)

    @property
    def details(self) -> str:
        lines = [f"Type : {self.category}"]
        if self.modules:
            lines.append("Matiere : " + " / ".join(self.modules))
        if self.rooms:
            lines.append("Salle : " + " / ".join(self.rooms))
        if self.sites:
            lines.append("Site : " + " / ".join(self.sites))
        if self.note:
            lines.append("Note : " + self.note)
        if self.groups:
            lines.append("Groupes : " + ", ".join(self.groups))
        return "\n".join(lines)


def clean(fragment: str) -> str:
    """Turn one CELCAT HTML fragment into plain text."""
    text = html.unescape(fragment)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def split_lines(fragment: str) -> list[str]:
    """Split a section into its individual entries, dropping CELCAT's ellipsis."""
    cleaned = clean(fragment)
    return [
        line
        for line in cleaned.splitlines()
        if line and not re.fullmatch(r"\(\d+ more\.\.\.\)", line)
    ]


def unique(values: list[str]) -> list[str]:
    """Drop empties and duplicates, keeping the original order."""
    return list(dict.fromkeys(value for value in values if value))


def strip_code(label: str) -> str:
    """'LHHIS311-Histoire ancienne 2 [LHHIS311]' becomes 'Histoire ancienne 2'."""
    label = re.sub(r"\s*\[[^\]]*\]\s*$", "", label).strip()
    label = re.sub(r"^[A-Z0-9_]{4,}\s*-\s*", "", label).strip()
    return re.sub(r"\s{2,}", " ", label)


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=LOCAL_TZ)


def parse_event(raw: dict) -> Course | None:
    if not raw.get("start") or not raw.get("end"):
        return None

    sections = raw.get("description", "").split(SECTION_SEP)
    sections += [""] * (6 - len(sections))
    category = clean(sections[0]) or raw.get("eventCategory") or "Cours"
    # Rooms carry a type suffix, "B219 [Salle de cours]". Drop it.
    rooms = [strip_code(line) for line in split_lines(sections[1])]
    modules = [strip_code(line) for line in split_lines(sections[2])]
    groups = [strip_code(line) for line in split_lines(sections[3])]
    note = clean(sections[4]).replace("\n", " - ")

    return Course(
        celcat_id=str(raw.get("id") or ""),
        start=parse_timestamp(raw["start"]),
        end=parse_timestamp(raw["end"]),
        category=category,
        rooms=unique(rooms),
        modules=unique(modules),
        groups=unique(groups),
        note=note,
        sites=list(raw.get("sites") or []),
        cancelled="annul" in category.lower(),
    )


# --------------------------------------------------------------------------
# iCalendar output
# --------------------------------------------------------------------------


def escape_text(value: str) -> str:
    value = value.replace("\\", "\\\\")
    value = value.replace(";", "\\;").replace(",", "\\,")
    return value.replace("\n", "\\n")


def fold(line: str) -> str:
    """Fold a content line to 75 octets, per RFC 5545 section 3.1."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    chunks: list[bytes] = []
    limit = 75
    while len(encoded) > limit:
        cut = limit
        # Never split in the middle of a multi-byte character.
        while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
            cut -= 1
        chunks.append(encoded[:cut])
        encoded = encoded[cut:]
        limit = 74  # continuation lines carry a leading space
    chunks.append(encoded)
    return "\r\n ".join(chunk.decode("utf-8") for chunk in chunks)


def as_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def make_uid(course: Course, namespace: str) -> str:
    """Stable UID so calendar clients update events instead of duplicating them."""
    seed = course.celcat_id or f"{course.start.isoformat()}|{course.title}"
    digest = hashlib.sha1(f"{namespace}|{seed}".encode("utf-8")).hexdigest()
    return f"{digest}@socal.celcat"


def build_calendar(
    courses: list[Course], calendar_name: str, namespace: str, source_url: str
) -> str:
    courses = sorted(courses, key=lambda course: (course.start, course.title))
    stamp = as_utc(datetime.now(timezone.utc))

    # Hash everything except the per-run timestamp, so an unchanged schedule
    # produces an unchanged file and the cron job commits nothing.
    payload = "\n".join(
        f"{c.start.isoformat()}|{c.end.isoformat()}|{c.title}|{c.location}|{c.details}"
        for c in courses
    )
    content_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text(calendar_name)}",
        "X-WR-TIMEZONE:Europe/Paris",
        f"X-WR-CALDESC:{escape_text('Emploi du temps CELCAT - ' + source_url)}",
        f"{HASH_PROPERTY}:{content_hash}",
    ]

    for course in courses:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{make_uid(course, namespace)}",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{as_utc(course.start)}",
            f"DTEND:{as_utc(course.end)}",
            f"SUMMARY:{escape_text(course.title)}",
        ]
        if course.location:
            lines.append(f"LOCATION:{escape_text(course.location)}")
        lines.append(f"DESCRIPTION:{escape_text(course.details)}")
        lines.append(f"CATEGORIES:{escape_text(course.category)}")
        lines.append("TRANSP:TRANSPARENT" if course.cancelled else "TRANSP:OPAQUE")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"


def extract_hash(calendar: str) -> str:
    for line in calendar.splitlines():
        if line.startswith(f"{HASH_PROPERTY}:"):
            return line.split(":", 1)[1].strip()
    return ""


def read_existing_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    return extract_hash(path.read_text(encoding="utf-8")) or None


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--fid",
        action="append",
        required=True,
        metavar="ID",
        help="CELCAT federation id, e.g. HISL2TD1. Repeat to merge several.",
    )
    parser.add_argument("--out", required=True, type=Path, help="output .ics path")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--res-type",
        default=RESOURCE_TYPE_GROUP,
        help="103=group, 100=staff, 104=student (default 103)",
    )
    parser.add_argument("--name", help="calendar name (default: CELCAT display name)")
    parser.add_argument(
        "--days-back", type=int, default=120, help="how far back to fetch (default 120)"
    )
    parser.add_argument(
        "--days-ahead", type=int, default=400, help="how far ahead to fetch (default 400)"
    )
    parser.add_argument(
        "--min-events",
        type=int,
        default=1,
        help="refuse to write below this count, so a bad response cannot wipe the feed",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    today = datetime.now(LOCAL_TZ).date()
    start = today - timedelta(days=args.days_back)
    end = today + timedelta(days=args.days_ahead)

    raw_events = fetch_events(args.base_url, args.fid, args.res_type, start, end)
    courses = [course for course in map(parse_event, raw_events) if course]

    if len(courses) < args.min_events:
        print(
            f"refusing to write {args.out}: got {len(courses)} events, "
            f"minimum is {args.min_events}",
            file=sys.stderr,
        )
        return 1

    name = args.name
    if not name:
        display = fetch_display_names(args.base_url, args.fid, args.res_type)
        name = " + ".join(display.get(fid, fid) for fid in args.fid)

    namespace = f"{args.base_url}|{args.res_type}|{','.join(sorted(args.fid))}"
    query = urllib.parse.urlencode(
        [("vt", "month"), ("et", "group")]
        + [(f"fid{index}", fid) for index, fid in enumerate(args.fid)]
    )
    calendar = build_calendar(courses, name, namespace, f"{args.base_url}/cal?{query}")

    if read_existing_hash(args.out) == extract_hash(calendar):
        print(f"{args.out}: unchanged ({len(courses)} events)")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(calendar, encoding="utf-8", newline="")
    print(f"{args.out}: written ({len(courses)} events, {start} to {end})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
