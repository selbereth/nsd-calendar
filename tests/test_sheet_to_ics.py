import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import sheet_to_ics as s  # noqa: E402

FIXTURE = (ROOT / "tests" / "fixture.csv").read_text(encoding="utf-8")


class Parse(unittest.TestCase):
    def setUp(self):
        self.events = s.build_events(s.parse_grid(FIXTURE))

    def one(self, uid):
        return next(e for e in self.events if e["uid"] == uid)

    def test_counts(self):
        self.assertEqual(len(s.parse_grid(FIXTURE)), 70)

    def test_time_and_default_location(self):
        e = self.one("20261012-12u@nsd-calendar")
        self.assertEqual((e["start"].hour, e["start"].minute, e["end"].hour, e["end"].minute), (19, 0, 20, 30))
        self.assertEqual(e["location"], "Old VIDA")

    def test_line_location_overrides(self):
        self.assertEqual(self.one("20260914-10u@nsd-calendar")["location"], "The Wave")
        self.assertEqual(self.one("20260908-10u@nsd-calendar")["location"], "VMMS")

    def test_off_line_makes_no_event(self):
        uids = {e["uid"] for e in self.events}
        self.assertNotIn("20260915-10u@nsd-calendar", uids)
        self.assertIn("20260915-14u@nsd-calendar", uids)

    def test_notes_are_all_day_without_guessed_location(self):
        e = self.one("20261018-note@nsd-calendar")
        self.assertEqual(e["summary"], "SPA League Game date")
        self.assertIsNone(e["location"])
        self.assertIn("each team plays 2 games", e["description"])
        self.assertIsNone(self.one("20261005-note@nsd-calendar")["location"])

    def test_canceled_title(self):
        self.assertEqual(self.one("20261001-note@nsd-calendar")["summary"], "Practice CANCELED")

    def test_description_has_whole_day_and_link(self):
        d = self.one("20261012-10u@nsd-calendar")["description"]
        for part in ("12u 7:00-8:30pm", '"Old VIDA"', s.SHEET_URL):
            self.assertIn(part, d)

    def test_year_rollover(self):
        csv_text = "Cal 2026\n" + ",".join(["28-Dec", "29-Dec", "30-Dec", "31-Dec", "1-Jan", "2-Jan", "3-Jan"]) + "\n"
        days = s.parse_grid(csv_text)
        self.assertEqual(days[0]["date"], date(2026, 12, 28))
        self.assertEqual(days[-1]["date"], date(2027, 1, 3))

    def test_time_edge_cases(self):
        m = s.TIME_RE.match("11:30-1:00pm")
        self.assertEqual(s.parse_time(m), ((11, 30), (13, 0)))
        m = s.TIME_RE.match("6-7pm")
        self.assertEqual(s.parse_time(m), ((18, 0), (19, 0)))


class Ics(unittest.TestCase):
    def test_structure(self):
        events = s.build_events(s.parse_grid(FIXTURE))
        text = s.to_ics(events, "x")
        self.assertTrue(text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n"))
        self.assertEqual(text.count("BEGIN:VEVENT"), text.count("END:VEVENT"))
        self.assertEqual(text.count("BEGIN:VEVENT"), len(events))
        for line in text.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75)
        uids = [ln for ln in text.split("\r\n") if ln.startswith("UID:")]
        self.assertEqual(len(uids), len(set(uids)))
        self.assertIn("Check the spreadsheet", text)


if __name__ == "__main__":
    unittest.main()
