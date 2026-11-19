import sys
import tempfile
import unittest
from unittest import mock
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
        self.assertEqual(e["location"], s.COORDS["Old VIDA"])
        self.assertTrue(e["description"].startswith("Old VIDA\n\nMon 12-Oct") or e["description"].startswith("Old VIDA\n\n"))

    def test_line_location_overrides(self):
        self.assertEqual(self.one("20260914-10u@nsd-calendar")["location"], s.COORDS["The Wave"])
        self.assertEqual(self.one("20260908-10u@nsd-calendar")["location"], s.COORDS["VMMS"])

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

    def test_each_day_saturday_note_spans_sunday(self):
        e = self.one("20261003-note@nsd-calendar")
        self.assertEqual(e["all_day_end"], date(2026, 10, 5))
        self.assertTrue(e["description"].startswith("Sat 3-Oct - Sun 4-Oct"))
        self.assertEqual(self.one("20261114-note@nsd-calendar")["all_day_end"], date(2026, 11, 15))
        self.assertEqual(self.one("20260920-note@nsd-calendar")["all_day_end"], date(2026, 9, 21))

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

    def test_two_digit_season_year(self):
        csv_text = "WINTER CALENDAR 26/27,,,,,,11/17/2026 Version\n30-Nov,1-Dec,2-Dec,3-Dec,4-Dec,5-Dec,6-Dec\n"
        days = s.parse_grid(csv_text, today=date(2027, 1, 15))
        self.assertEqual(days[0]["date"], date(2026, 11, 30))

    def test_short_last_week(self):
        csv_text = ("Cal 2027\n12-Jul,13-Jul,14-Jul,15-Jul,16-Jul,17-Jul,18-Jul\n"
                    "19-Jul,20-Jul,,,,,\nA,B,C,,,,\n")
        days = s.parse_grid(csv_text)
        self.assertEqual([d["date"] for d in days[-2:]], [date(2027, 7, 19), date(2027, 7, 20)])
        self.assertEqual(days[-2]["lines"], ["A"])
        self.assertEqual(days[-8]["lines"], [])  # 18-Jul: the dates are not text

    def test_date_cell_typos(self):
        csv_text = ("Cal 2027\n18-Jan,19-Jan,20-Jan,21-Jan,22-Jan,23-Jan,24-Jan\nA,,,,,,\n"
                    "25-Jan,26-Jan,27-Jan,28-Jan,Jan 29,30- Jan,31-Jan\nB,,,,,,\n")
        days = s.parse_grid(csv_text)
        self.assertEqual([d["date"] for d in days[7:]],
                         [date(2027, 1, n) for n in range(25, 32)])
        self.assertEqual((days[0]["lines"], days[7]["lines"]), (["A"], ["B"]))

    def test_header_and_title_rows_are_skipped(self):
        csv_text = ("Cal 2027\n22-Feb,23-Feb,24-Feb,25-Feb,26-Feb,27-Feb,28-Feb\n"
                    "Note,,,,,,\nNSD Stars Spring Calendar 2027 (more info soon),,,,,,\n"
                    "Mon,Tue,Wed,Thur,Fri,Sat,Sun\nMONDAY,TUESDAY,WEDNESDAY,THURSDAY,FRIDAY,SATURDAY,SUNDAY\n")
        days = s.parse_grid(csv_text)
        self.assertEqual(days[0]["lines"], ["Note"])
        self.assertTrue(all(not d["lines"] for d in days[1:]))

    def test_16u_18u_and_team_lists(self):
        csv_text = ("Cal 2027\n31-May,1-Jun,2-Jun,3-Jun,4-Jun,5-Jun,6-Jun\n"
                    "10u/12u/14u are OFF this week,16u 6:00-7:30pm at VMMS,,,,,16u & 18u Regional Junior\n"
                    "16u/18u 4:00-6:00pm at Wave,18u 7:30-9:00pm at VMMS,,,,,\n")
        events = s.build_events(s.parse_grid(csv_text))
        by_uid = {e["uid"]: e for e in events}
        for team in ("16u", "18u"):
            e = by_uid[f"20270531-{team}@nsd-calendar"]
            self.assertEqual((e["team"], e["start"].hour, e["end"].hour), (team, 16, 18))
        self.assertEqual(by_uid["20270601-16u@nsd-calendar"]["start"].hour, 18)
        self.assertEqual(by_uid["20270601-18u@nsd-calendar"]["start"].hour, 19)
        self.assertNotIn("20270531-note@nsd-calendar", by_uid)  # "are OFF": no note
        self.assertEqual(by_uid["20270606-note@nsd-calendar"]["summary"], "16u & 18u Regional Junior")

    def test_wac_and_servite(self):
        csv_text = ("Cal 2026\n14-Dec,15-Dec,16-Dec,17-Dec,18-Dec,19-Dec,20-Dec\n"
                    '16u 6:15-8:00pm at WAC,14u 8:00-9:00am WAC Oside,,,,"at SERVITE HS, Anaheim",\n'
                    ",,,,,12u/14u/16u/18u Scrimmages,\n")
        by_uid = {e["uid"]: e for e in s.build_events(s.parse_grid(csv_text))}
        self.assertEqual(by_uid["20261214-16u@nsd-calendar"]["location"], s.COORDS["WAC"])
        self.assertEqual(by_uid["20261215-14u@nsd-calendar"]["location"], s.COORDS["WAC"])
        note = by_uid["20261219-note@nsd-calendar"]
        self.assertEqual(note["summary"], "12u/14u/16u/18u Scrimmages")
        self.assertEqual(note["location"], s.COORDS["Servite HS"])
        self.assertTrue(note["description"].startswith("Servite HS\n\n"))

    def test_practice_title_has_short_place(self):
        self.assertEqual(self.one("20261012-12u@nsd-calendar")["summary"], "12u Water Polo VIDA")
        self.assertEqual(self.one("20260914-10u@nsd-calendar")["summary"], "10u Water Polo Wave")
        self.assertEqual(self.one("20260908-10u@nsd-calendar")["summary"], "10u Water Polo VMMS")
        csv_text = "Cal 2026\n7-Dec,8-Dec,9-Dec,10-Dec,11-Dec,12-Dec,13-Dec\n16u 6:00-7:30pm,18u 6:15-8:00pm at WAC,,,,,\n"
        by_uid = {e["uid"]: e for e in s.build_events(s.parse_grid(csv_text))}
        self.assertEqual(by_uid["20261207-16u@nsd-calendar"]["summary"], "16u Water Polo")  # no place
        self.assertEqual(by_uid["20261208-18u@nsd-calendar"]["summary"], "18u Water Polo WAC")
        self.assertEqual(set(s.SHORT), set(s.COORDS))
        self.assertTrue(all(len(v) <= 4 for v in s.SHORT.values()))

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

    def test_new_sheet_keeps_events_from_before_it(self):
        week = (",".join(["10u 6:00-7:00pm at VMMS"] * 3 + [""] * 4) + "\n" +
                ",".join(["12u 6:00-7:30pm at VMMS"] * 3 + [""] * 4))
        later = ("Winter Calendar 2026\n9-Nov,10-Nov,11-Nov,12-Nov,13-Nov,14-Nov,15-Nov\n" + week +
                 "\n16-Nov,17-Nov,18-Nov,19-Nov,20-Nov,21-Nov,22-Nov\n" + week +
                 "\n23-Nov,24-Nov,25-Nov,26-Nov,27-Nov,28-Nov,29-Nov\n" + week + "\n")

        def blocks(path):
            text = path.read_bytes().decode("utf-8")
            return {b.split("\r\n", 2)[1]: b for b in text.split("BEGIN:VEVENT")[1:]}

        with tempfile.TemporaryDirectory() as d:
            csv_path = Path(d) / "later.csv"
            csv_path.write_text(later, encoding="utf-8")
            with mock.patch.object(sys, "argv", ["x", "--csv", str(ROOT / "tests" / "fixture.csv"), "--out", d]):
                s.main()
            fall = {f: blocks(Path(d) / f) for f in ("nsd-all.ics", "nsd-12u.ics")}
            with mock.patch.object(sys, "argv", ["x", "--csv", str(csv_path), "--out", d]):
                s.main()
            for f, old in fall.items():
                new = blocks(Path(d) / f)
                before = {u: b for u, b in old.items() if u[4:12] < "20261109"}
                self.assertTrue(before)
                for uid, block in before.items():
                    self.assertEqual(new.get(uid), block)  # copied unchanged
                self.assertNotIn("UID:20261112-12u@nsd-calendar", new)  # fall sheet's, now replaced
                self.assertIn("UID:20261114-note@nsd-calendar", old)
                self.assertNotIn("UID:20261114-note@nsd-calendar", new)
            self.assertIn("UID:20261109-10u@nsd-calendar", blocks(Path(d) / "nsd-all.ics"))
            self.assertNotIn("UID:20260908-10u@nsd-calendar", fall["nsd-12u.ics"])

    def test_changed_event_gets_new_stamp_and_sequence(self):
        events = s.build_events(s.parse_grid(FIXTURE))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x.ics"
            path.write_bytes(s.to_ics(events, "x", now="20260101T000000Z").encode("utf-8"))
            again = s.to_ics(events, "x", s.read_stamps(path), now="20270101T000000Z")
            self.assertEqual(again, path.read_bytes().decode("utf-8"))  # unchanged: no churn

            uid = events[0]["uid"]
            events[0] = {**events[0], "description": "changed"}
            path.write_bytes(again.encode("utf-8"))
            stamps = s.read_stamps(path)
            path.write_bytes(s.to_ics(events, "x", stamps, now="20270101T000000Z").encode("utf-8"))
            new = s.read_stamps(path)
            self.assertEqual(new[uid][:2], ("20270101T000000Z", 1))
            other = events[1]["uid"]
            self.assertEqual(new[other][:2], ("20260101T000000Z", 0))


if __name__ == "__main__":
    unittest.main()
