#!/usr/bin/env python3
"""Convert the NSD water polo schedule (a free-text weekly grid in a Google Sheet)
into iCalendar feeds that Google Calendar can subscribe to. Standard library only.

    python3 sheet_to_ics.py --out docs                  # fetch the live sheet
    python3 sheet_to_ics.py --csv tests/fixture.csv --out /tmp/x

Exits non-zero (and leaves existing files untouched) if the sheet cannot be fetched
or parses to something suspicious, so a changed layout never publishes a bad feed.
"""
import argparse
import csv
import io
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SHEET_ID = "16m2TA4lobnudPF24B8g2P_KckVENp7HIsP-isMSCvyk"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?gid=0"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0"
TZ = ZoneInfo("America/Los_Angeles")
TEAMS = ("10u", "12u", "14u")
CAL_NAME = "NSD Water Polo"

DATE_RE = re.compile(r"^\d{1,2}-[A-Za-z]{3}$")
TEAM_RE = re.compile(r"^(10u|12u|14u)\b\s*(.*)$", re.I)
TIME_RE = re.compile(
    r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*-\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b\s*(.*)$", re.I
)
# (pattern, name). Order matters only for lines naming two places.
LOCATIONS = [
    (re.compile(r"old\s+vida", re.I), "Old VIDA"),
    (re.compile(r"\bvmms\b", re.I), "VMMS"),
    (re.compile(r"\bwave\b", re.I), "The Wave"),
]
# Parking-lot coordinates used as the LOCATION. No venue name: Google resolves names
# and street addresses to the wrong lot. The name goes at the top of the description.
COORDS = {
    "Old VIDA": "33.204396,-117.2575485",
    "VMMS": "33.2046006,-117.2373799",
    "The Wave": "33.199571,-117.245795",
}
# A Saturday note like "2 games each day" spans Sunday too; the sheet text just spills
# over the empty Sunday box, so nothing in the CSV says so.
TWO_DAY_RE = re.compile(r"each day|both days|saturday\s*(and|&|-)\s*sunday|sat\s*[-&/]\s*sun", re.I)
CLOSED_RE = re.compile(r"closed|cancel|no practice|no school", re.I)


def fetch_csv(url=CSV_URL):
    req = urllib.request.Request(url, headers={"User-Agent": "nsd-calendar-sync"})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode("utf-8")
    if "<html" in text[:500].lower():
        raise RuntimeError("got an HTML page instead of CSV (sheet no longer link-viewable?)")
    return text


def find_location(line):
    """Location named in a line, ignoring lines that say a place is closed."""
    if CLOSED_RE.search(line):
        return None
    for rx, name in LOCATIONS:
        if rx.search(line):
            return name
    return None


def is_location_only(line):
    """A line like  At VMMS  or  at "Old VIDA"  that just sets the day's location."""
    stripped = re.sub(r'^\s*at\s+', "", line.strip(), flags=re.I).strip(' "“”')
    return find_location(line) is not None and any(
        rx.fullmatch(stripped) for rx, _ in LOCATIONS
    )


def to_24h(h, m, suffix):
    h = int(h) % 12
    if suffix.lower() == "pm":
        h += 12
    return h, int(m or 0)


def parse_time(m):
    """(start, end) as (hour, minute) 24h tuples. Start inherits pm unless that is after the end."""
    sh, sm, ssuf, eh, em, esuf = m.group(1, 2, 3, 4, 5, 6)
    end = to_24h(eh, em, esuf)
    start = to_24h(sh, sm, ssuf or esuf)
    if start >= end and not ssuf:
        start = to_24h(sh, sm, "am")
    return start, end


def parse_grid(csv_text, today=None):
    """Return list of days: {date, lines}, in sheet order. lines = the day's cell text."""
    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows:
        raise ValueError("sheet is empty")
    title_year = re.search(r"\b(20\d\d)\b", rows[0][0] if rows[0] else "")
    year = int(title_year.group(1)) if title_year else (today or date.today()).year

    days, week_dates, week_cells, prev_month = [], None, None, None

    def flush():
        if week_dates:
            for i, d in enumerate(week_dates):
                lines = [ln.strip() for cell in week_cells[i] for ln in cell.splitlines() if ln.strip()]
                days.append({"date": d, "lines": lines})

    for row in rows[1:]:
        row = (row + [""] * 7)[:7]
        cells = [c.strip() for c in row]
        if all(DATE_RE.match(c) for c in cells):
            flush()
            week_dates = []
            for c in cells:
                dt = datetime.strptime(f"{c}-{year}", "%d-%b-%Y").date()
                if prev_month is not None and dt.month < prev_month - 6:
                    year += 1
                    dt = dt.replace(year=year)
                prev_month = dt.month
                week_dates.append(dt)
            week_cells = [[] for _ in range(7)]
        elif week_dates:
            for i, c in enumerate(cells):
                if c:
                    week_cells[i].append(c)
    flush()
    return days


def build_events(days):
    """Practice events (one per team per day) and all-day notes."""
    events = []
    for i, day in enumerate(days):
        d, lines = day["date"], day["lines"]
        day_loc = next((find_location(ln) for ln in lines if is_location_only(ln)), None)
        header = f"{d:%a %-d-%b}"
        next_day = days[i + 1] if i + 1 < len(days) else None
        spans_sunday = (d.weekday() == 5 and next_day and not next_day["lines"]
                        and any(TWO_DAY_RE.search(ln) for ln in lines))
        if spans_sunday:
            header = f"{d:%a %-d-%b} - {next_day['date']:%a %-d-%b}"
        base = "\n".join([header, *lines, "", f"Spreadsheet: {SHEET_URL}"])

        def describe(place):
            return f"{place}\n\n{base}" if place else base

        notes = []
        for ln in lines:
            tm = TEAM_RE.match(ln)
            if tm:
                team, rest = tm.group(1).lower(), tm.group(2)
                time_m = TIME_RE.match(rest)
                if not time_m:
                    continue  # e.g. "10u Tue OFF": no event, still shown in the description
                (sh, sm), (eh, em) = parse_time(time_m)
                loc = find_location(time_m.group(7)) or day_loc
                events.append({
                    "uid": f"{d:%Y%m%d}-{team}@nsd-calendar",
                    "team": team,
                    "summary": f"{team} Water Polo",
                    "start": datetime(d.year, d.month, d.day, sh, sm, tzinfo=TZ),
                    "end": datetime(d.year, d.month, d.day, eh, em, tzinfo=TZ),
                    "place": loc,
                    "location": COORDS.get(loc),
                    "description": describe(loc),
                })
            elif not is_location_only(ln):
                notes.append(ln)
        if notes:
            title = notes[0]
            if re.fullmatch(r"cancell?ed!?", title, re.I):
                title = "Practice CANCELED"
            events.append({
                "uid": f"{d:%Y%m%d}-note@nsd-calendar",
                "team": None,
                "summary": title[:120],
                "all_day": d,
                "all_day_end": d + timedelta(days=2 if spans_sunday else 1),
                "place": day_loc,
                "location": COORDS.get(day_loc),
                "description": describe(day_loc),
            })
    return events


# ---- iCalendar output -------------------------------------------------------------

def esc(text):
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r", "").replace("\n", "\\n"))


# Temporary: lets us see whether and how Google picks up feed changes. Each event
# uses different properties so a later round can change each one a different way
# (content only / SEQUENCE bump / DTSTAMP bump). Delete these when done.
_TEST = {"team": None, "location": ""}
V4 = "UPDATED v4 description. Temporary test of Google's feed refresh."
TEST_EVENTS = [
    {**_TEST, "uid": "20270112-feedtest-a@nsd-calendar", "summary": "FEED TEST v3 A plain (ignore)",
     "start": datetime(2027, 1, 12, 10, 0, tzinfo=TZ), "end": datetime(2027, 1, 12, 11, 0, tzinfo=TZ),
     "description": f"A: content change only.\n{V4}"},
    {**_TEST, "uid": "20270113-feedtest-b@nsd-calendar", "summary": "FEED TEST v3 B seq (ignore)",
     "start": datetime(2027, 1, 13, 10, 0, tzinfo=TZ), "end": datetime(2027, 1, 13, 11, 0, tzinfo=TZ),
     "sequence": 1, "description": f"B: SEQUENCE bumped 0 -> 1.\n{V4}"},
    {**_TEST, "uid": "20270114-feedtest-c@nsd-calendar", "summary": "FEED TEST v3 C stamp (ignore)",
     "all_day": date(2027, 1, 14), "all_day_end": date(2027, 1, 15), "dtstamp": "20261025T000000Z",
     "description": f"C: DTSTAMP bumped 20261023 -> 20261025.\n{V4}"},
    {**_TEST, "uid": "20270116-feedtest-d@nsd-calendar", "summary": "FEED TEST v3 D 2-day (ignore)",
     "all_day": date(2027, 1, 16), "all_day_end": date(2027, 1, 18),
     "location": COORDS["VMMS"], "sequence": 1, "dtstamp": "20261025T000000Z",
     "description": f"VMMS\n\nD: SEQUENCE added (1) and DTSTAMP bumped.\n{V4}"},
]


def fold(line):
    """Fold to 75 octets per RFC 5545, without splitting a UTF-8 character."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, cur, limit = [], b"", 75
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > limit:
            out.append(cur.decode("utf-8"))
            cur, limit = b"", 74
        cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n ".join(out)


def utc(dt):
    return dt.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")


def to_ics(events, name):
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//nsd-calendar//EN", "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH", f"X-WR-CALNAME:{esc(name)}", "X-WR-TIMEZONE:America/Los_Angeles",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H",
    ]
    for e in events:
        loc = e["location"] or f"Check the spreadsheet: {SHEET_URL}"
        lines += ["BEGIN:VEVENT", f"UID:{e['uid']}", f"DTSTAMP:{e.get('dtstamp', '20260101T000000Z')}"]
        if "sequence" in e:
            lines.append(f"SEQUENCE:{e['sequence']}")
        if "all_day" in e:
            d = e["all_day"]
            lines += [f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{e['all_day_end']:%Y%m%d}",
                      "TRANSP:TRANSPARENT"]
        else:
            lines += [f"DTSTART:{utc(e['start'])}", f"DTEND:{utc(e['end'])}"]
        lines += [f"SUMMARY:{esc(e['summary'])}", f"LOCATION:{esc(loc)}",
                  f"DESCRIPTION:{esc(e['description'])}", f"URL:{SHEET_URL}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(ln) for ln in lines) + "\r\n"


def count_events(path):
    return path.read_text(encoding="utf-8").count("BEGIN:VEVENT") if path.exists() else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="read this CSV file instead of fetching the sheet")
    ap.add_argument("--out", default="docs", help="output directory")
    args = ap.parse_args()

    text = Path(args.csv).read_text(encoding="utf-8") if args.csv else fetch_csv()
    days = parse_grid(text)
    events = build_events(days)

    practices = [e for e in events if e["team"]]
    if len(days) < 14 or len(practices) < 10:
        sys.exit(f"refusing to publish: parsed {len(days)} days and {len(practices)} practices; "
                 "did the sheet layout change?")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    feeds = {"nsd-all.ics": (None, CAL_NAME)}
    feeds.update({f"nsd-{t}.ics": (t, f"{CAL_NAME} {t}") for t in TEAMS})
    old_total = count_events(out / "nsd-all.ics")
    if old_total and len(events) < old_total * 0.5:
        sys.exit(f"refusing to publish: {len(events)} events vs {old_total} before (more than halved)")

    for fname, (team, name) in feeds.items():
        subset = [e for e in events if team is None or e["team"] in (team, None)]
        if team is None:
            subset += TEST_EVENTS
        (out / fname).write_bytes(to_ics(subset, name).encode("utf-8"))
    print(f"{len(days)} days, {len(practices)} practices, {len(events) - len(practices)} notes -> {out}/")


if __name__ == "__main__":
    main()
