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

SHEET_ID = "1GuipSrA5YecQpop4lqkkVYEVdJee-1ZDyq6qvewhLl0"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?gid=0"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0"
TZ = ZoneInfo("America/Los_Angeles")
TEAMS = ("10u", "12u", "14u", "16u", "18u")
CAL_NAME = "NSD Water Polo"

# "30-Nov", plus typos seen in the sheet: "30- Jan", "Jan 29"
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
DATE_RE = re.compile(rf"^(?:(\d{{1,2}})\s*-\s*{_MON}|{_MON}\s+(\d{{1,2}}))$", re.I)
WEEKDAY_RE = re.compile(r"^(mon|tue|wed|thu|fri|sat|sun)[a-z]*$", re.I)
TITLE_RE = re.compile(r"\bcalendar\b", re.I)
# One team or a list: "16u 6:00-7:30pm", "16u/18u 4:00-6:00pm", "16u & 18u Regional ..."
TEAM_RE = re.compile(r"^(1[02468]u(?:\s*(?:/|&|,|and)\s*1[02468]u)*)\b\s*(.*)$", re.I)
OFF_RE = re.compile(r"\boff\b", re.I)
TIME_RE = re.compile(
    r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*-\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b\s*(.*)$", re.I
)
# (pattern, name). Order matters only for lines naming two places.
LOCATIONS = [
    (re.compile(r"old\s+vida", re.I), "Old VIDA"),
    (re.compile(r"\bvmms\b", re.I), "VMMS"),
    (re.compile(r"\bwave\b", re.I), "The Wave"),
    (re.compile(r"\bwac\b(\s+oside)?", re.I), "WAC"),  # "WAC" and "WAC Oside": one pool
    (re.compile(r"\bservite\b(\s+hs)?(\s*,\s*anaheim)?", re.I), "Servite HS"),
]
# Parking-lot coordinates used as the LOCATION. No venue name: Google resolves names
# and street addresses to the wrong lot. The name goes at the top of the description.
COORDS = {
    "Old VIDA": "33.204396,-117.2575485",
    "VMMS": "33.2046006,-117.2373799",
    "The Wave": "33.199571,-117.245795",
    "WAC": "33.2055438,-117.3175868",
    "Servite HS": "33.8457608,-117.9494694",
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


def title_year(title):
    """Start year from a title like "Fall Calendar 2026" or "WINTER CALENDAR 26/27"."""
    m = re.search(r"\b(20\d\d)\b", title)
    if m:
        return int(m.group(1))
    for a, b in re.findall(r"\b(\d\d)\s*/\s*(\d\d)\b", title):
        if int(b) == int(a) + 1:
            return 2000 + int(a)
    return None


def is_date_row(cells):
    """All dates, left to right. The last week can be short: 19-Jul,20-Jul,,,,,"""
    filled = [c for c in cells if c]
    return bool(filled) and cells[:len(filled)] == filled and all(DATE_RE.match(c) for c in filled)


def is_header_row(cells):
    """A repeated weekday header (Mon,Tue,... or MONDAY,...) or a section title alone in
    the first column (NSD Stars Spring Calendar 2027). Not schedule text."""
    filled = [c for c in cells if c]
    if not filled:
        return False
    if all(WEEKDAY_RE.match(c) for c in filled):
        return True
    return len(filled) == 1 and cells[0] and TITLE_RE.search(cells[0]) is not None


def parse_grid(csv_text, today=None):
    """Return list of days: {date, lines}, in sheet order. lines = the day's cell text."""
    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows:
        raise ValueError("sheet is empty")
    year = title_year(rows[0][0] if rows[0] else "") or (today or date.today()).year

    days, week_dates, week_cells, prev_month = [], None, None, None

    def flush():
        if week_dates:
            for i, d in enumerate(week_dates):
                lines = [ln.strip() for cell in week_cells[i] for ln in cell.splitlines() if ln.strip()]
                days.append({"date": d, "lines": lines})

    for row in rows[1:]:
        row = (row + [""] * 7)[:7]
        cells = [c.strip() for c in row]
        if is_header_row(cells):
            continue
        if is_date_row(cells):
            flush()
            week_dates = []
            for c in filter(None, cells):
                m = DATE_RE.match(c)
                day, mon = m.group(1) or m.group(4), m.group(2) or m.group(3)
                dt = datetime.strptime(f"{day}-{mon}-{year}", "%d-%b-%Y").date()
                if prev_month is not None and dt.month < prev_month - 6:
                    year += 1
                    dt = dt.replace(year=year)
                prev_month = dt.month
                week_dates.append(dt)
            week_cells = [[] for _ in week_dates]
        elif week_dates:
            for i, c in enumerate(cells[:len(week_dates)]):
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
                time_m = TIME_RE.match(tm.group(2))
                if not time_m:
                    # "10u Tue OFF": no event, still shown in the description.
                    # "16u & 18u Regional Junior Olympics": an all-day note.
                    if not OFF_RE.search(ln):
                        notes.append(ln)
                    continue
                (sh, sm), (eh, em) = parse_time(time_m)
                loc = find_location(time_m.group(7)) or day_loc
                for team in re.findall(r"1[02468]u", tm.group(1).lower()):
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


def read_stamps(path):
    """UID -> (DTSTAMP, SEQUENCE, other lines) for each event in a previously written feed."""
    if not path.exists():
        return {}
    stamps, cur = {}, None
    for ln in path.read_bytes().decode("utf-8").replace("\r\n ", "").split("\r\n"):
        if ln == "BEGIN:VEVENT":
            cur = {"seq": 0, "body": []}
        elif ln == "END:VEVENT" and cur:
            stamps[cur["uid"]] = (cur["stamp"], cur["seq"], cur["body"])
            cur = None
        elif cur is not None:
            key, _, val = ln.partition(":")
            if key == "DTSTAMP":
                cur["stamp"] = val
            elif key == "SEQUENCE":
                cur["seq"] = int(val)
            else:
                cur["body"].append(ln)
                if key == "UID":
                    cur["uid"] = val
    return stamps


def kept_uids(previous, first_day):
    """UIDs in the last feed dated before the sheet's first day. An older sheet made them
    and the current sheet no longer covers them, so the feed keeps them unchanged."""
    cutoff = f"{first_day:%Y%m%d}"
    return sorted(u for u in previous if u[:8].isdigit() and u[:8] < cutoff)


def to_ics(events, name, previous=None, now=None, keep=()):
    """previous: read_stamps() of the last feed. Google ignores an edited event unless its
    SEQUENCE or DTSTAMP changes, so an event whose content changed gets SEQUENCE + 1 and
    DTSTAMP = now; an unchanged one keeps its old values so the file doesn't churn.
    keep: UIDs from previous to copy as they are (see kept_uids)."""
    previous = previous or {}
    now = now or utc(datetime.now(TZ))
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//nsd-calendar//EN", "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH", f"X-WR-CALNAME:{esc(name)}", "X-WR-TIMEZONE:America/Los_Angeles",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H",
    ]
    for uid in keep:
        stamp, seq, body = previous[uid]
        lines += ["BEGIN:VEVENT", body[0], f"DTSTAMP:{stamp}", f"SEQUENCE:{seq}", *body[1:], "END:VEVENT"]
    for e in events:
        loc = e["location"] or f"Check the spreadsheet: {SHEET_URL}"
        body = [f"UID:{e['uid']}"]
        if "all_day" in e:
            d = e["all_day"]
            body += [f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{e['all_day_end']:%Y%m%d}",
                     "TRANSP:TRANSPARENT"]
        else:
            body += [f"DTSTART:{utc(e['start'])}", f"DTEND:{utc(e['end'])}"]
        body += [f"SUMMARY:{esc(e['summary'])}", f"LOCATION:{esc(loc)}",
                 f"DESCRIPTION:{esc(e['description'])}", f"URL:{SHEET_URL}"]
        stamp, seq = now, 0
        if e["uid"] in previous:
            old_stamp, old_seq, old_body = previous[e["uid"]]
            stamp, seq = (old_stamp, old_seq) if old_body == body else (now, old_seq + 1)
        lines += ["BEGIN:VEVENT", body[0], f"DTSTAMP:{stamp}", f"SEQUENCE:{seq}", *body[1:], "END:VEVENT"]
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
    first_day = days[0]["date"]
    old_total = count_events(out / "nsd-all.ics")
    kept_all = len(kept_uids(read_stamps(out / "nsd-all.ics"), first_day))
    new_total = len(events) + kept_all
    if old_total and new_total < old_total * 0.5:
        sys.exit(f"refusing to publish: {new_total} events vs {old_total} before (more than halved)")

    for fname, (team, name) in feeds.items():
        subset = [e for e in events if team is None or e["team"] in (team, None)]
        previous = read_stamps(out / fname)
        keep = kept_uids(previous, first_day)
        (out / fname).write_bytes(to_ics(subset, name, previous, keep=keep).encode("utf-8"))
    print(f"{len(days)} days, {len(practices)} practices, {len(events) - len(practices)} notes, "
          f"{kept_all} kept from before {first_day} -> {out}/")


if __name__ == "__main__":
    main()
