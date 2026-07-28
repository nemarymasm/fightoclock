import json
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def load_json(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


class DataContractTests(unittest.TestCase):
    def test_events_are_recent_and_well_formed(self):
        data = load_json("events.json")
        generated = datetime.fromisoformat(data["generated_at"])
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - generated.astimezone(timezone.utc)).total_seconds() / 3600
        self.assertLess(age_hours, 72, "events.json is older than 72 hours")
        events = data.get("events", [])
        self.assertTrue(events, "at least one upcoming event is required")
        for event in events + data.get("past_events", []):
            self.assertRegex(event.get("date_iso", ""), r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(event.get("name"))
            start = event.get("start_time_utc")
            if start:
                parsed = datetime.fromisoformat(start.replace("Z", "+00:00"))
                self.assertIsNotNone(parsed.tzinfo, "start_time_utc must contain a timezone")

    def test_fighters_and_rankings_are_not_empty(self):
        fighters = load_json("fighters.json")
        rankings = load_json("rankings.json")
        self.assertGreater(len(fighters.get("fighters", [])), 50)
        self.assertGreaterEqual(len(rankings.get("divisions", [])), 8)
        ids = [fighter["id"] for fighter in fighters["fighters"]]
        self.assertEqual(len(ids), len(set(ids)), "fighter ids must be unique")


class FrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "index.html").read_text(encoding="utf-8")

    def test_no_fabricated_utc_time(self):
        self.assertNotIn("date_iso + 'T22:00:00Z'", self.html)
        self.assertIn("start_time_utc", self.html)

    def test_initial_render_waits_for_real_data(self):
        self.assertRegex(self.html, r"await loadRealData\(\);\s*route\(\);")
        self.assertIn("appState==='loading'", self.html)

    def test_accessibility_and_metadata(self):
        for required in (
            'class="skip-link"',
            'aria-label="주요 메뉴"',
            'rel="canonical"',
            'application/ld+json',
            'id="appDialog"',
        ):
            self.assertIn(required, self.html)

    def test_schedule_controls_are_buttons(self):
        self.assertIn('role="group" aria-label="일정 필터"', self.html)
        self.assertIn("setScheduleFilter", self.html)

    def test_supporting_public_files_exist(self):
        for name in ("robots.txt", "sitemap.xml", "site.webmanifest", "favicon.svg", "og-image.svg", "vercel.json"):
            self.assertTrue((ROOT / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
