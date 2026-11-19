# NSD water polo calendar

Turns the team's Google Sheet (a free-text weekly grid we don't control) into calendar
feeds Google Calendar can subscribe to. A GitHub Action rebuilds them daily.

| Feed | Contents |
|---|---|
| `docs/nsd-all.ics` | 10u, 12u, 14u, 16u and 18u practices plus all-day notes |
| `docs/nsd-10u.ics` `nsd-12u.ics` `nsd-14u.ics` `nsd-16u.ics` `nsd-18u.ics` | one team, plus all-day notes |

Subscribe: Google Calendar (web) -> Other calendars (+) -> From URL -> paste
`https://selbereth.github.io/nsd-calendar/nsd-all.ics`. Share that same URL with others.
Google refreshes subscribed calendars every several hours to a day.

Rules: one event per team per day; the description holds that whole day's text from
the sheet plus a link. Location is the pool's parking-lot coordinates only (Google resolves names and street addresses to the wrong lot); the place name (VMMS, The Wave, Old VIDA, WAC, Servite HS) is the first line of the description, then a blank line, then the day's text. A place on the
team's line wins over the day's "At ..." line; otherwise "Check the spreadsheet: <link>".
A practice title ends with the pool's short name: "12u Water Polo VMMS" (VMMS, Wave, VIDA,
WAC, Serv). With no known pool, the title is just "12u Water Polo".
A line for several teams ("16u/18u 4:00-6:00pm") gives each team an event. Non-practice text (tournaments, closures) becomes an all-day event, and so does a team line with no time unless it says OFF. A Saturday note that says "each day" (or "both days") with an empty Sunday spans Saturday and Sunday: the sheet text just spills over the Sunday box, so the sheet itself doesn't say so.

Edits: Google ignores a changed event unless its SEQUENCE or DTSTAMP changes, so each run
compares every event with the feed already in `docs/` and, if its content changed, bumps
SEQUENCE and sets DTSTAMP to now. Unchanged events keep their old values.

Past seasons: each run copies, unchanged, every event in the old feed dated before the
sheet's first day. So a new sheet for a new season adds to the feeds and does not wipe
the old season. A fix to a past event in an old sheet no longer reaches the feeds.

If the sheet layout changes so it no longer parses, the job fails (GitHub emails you)
and the last good feeds stay up. Run locally: `python3 sheet_to_ics.py --out docs`;
tests: `python3 tests/test_sheet_to_ics.py`. Times assume America/Los_Angeles.
